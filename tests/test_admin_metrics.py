"""Tests for /admin/metrics endpoint.

Verifies:
  - 404 when SOAK_BYPASS_TOKEN unset (default-safe — no surface)
  - 404 when header missing (token configured)
  - 404 when header wrong
  - 200 + {"rss_kb": int} when header matches
  - Token never appears in response body or headers
  - Route is registered on the Starlette app
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_zeeker.core.admin import admin_metrics


@pytest.fixture
def admin_app():
    """Isolated Starlette app with only /admin/metrics — no rate limit, no MCP mount.

    Tests the route in isolation; integration with the full app is implicit via
    the import-time registration in src/mcp_zeeker/app.py:99 (assertion at end
    of this file: `test_admin_metrics_registered_on_production_app`).
    """
    return Starlette(routes=[Route("/admin/metrics", admin_metrics)])


@pytest.fixture
def client(admin_app):
    transport = httpx.ASGITransport(app=admin_app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_returns_404_when_env_unset(client, monkeypatch):
    monkeypatch.delenv("SOAK_BYPASS_TOKEN", raising=False)
    async with client as c:
        resp = await c.get("/admin/metrics", headers={"X-Soak-Bypass": "anything"})
    assert resp.status_code == 404
    assert resp.text == ""


async def test_returns_404_when_header_missing(client, monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    async with client as c:
        resp = await c.get("/admin/metrics")
    assert resp.status_code == 404
    assert resp.text == ""


async def test_returns_404_when_token_mismatch(client, monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    async with client as c:
        resp = await c.get("/admin/metrics", headers={"X-Soak-Bypass": "wrong-token"})
    assert resp.status_code == 404
    assert resp.text == ""


async def test_returns_200_with_rss_kb_when_authenticated(client, monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    async with client as c:
        resp = await c.get("/admin/metrics", headers={"X-Soak-Bypass": "expected-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"rss_kb"}, f"unexpected fields: {list(body.keys())}"
    assert isinstance(body["rss_kb"], int)
    assert body["rss_kb"] > 0


async def test_token_never_in_response(client, monkeypatch):
    secret = "very-secret-soak-token-do-not-leak"
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", secret)
    async with client as c:
        resp = await c.get("/admin/metrics", headers={"X-Soak-Bypass": secret})
    assert resp.status_code == 200
    assert secret not in resp.text
    for header_value in resp.headers.values():
        assert secret not in header_value


async def test_admin_metrics_registered_on_production_app():
    """The route must be present on the imported production app.

    Catches accidental removal during refactors (the integration that
    actually matters in CI).
    """
    from mcp_zeeker.app import app

    paths = []
    for route in app.routes:
        if hasattr(route, "path"):
            paths.append(route.path)
    assert "/admin/metrics" in paths, (
        f"/admin/metrics not registered on production app; routes: {paths}"
    )
