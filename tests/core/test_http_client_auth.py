"""
Upstream owner-token auth (ZEEKER_FULL_ACCESS_TOKEN).

The zeeker-datasette catalogue lockdown (plugins/strip_columns.py) strips
heavy/full-text columns and 403s CSV/FTS/SQL for anonymous callers. This
server's heavy-column retrieval path needs the owner bearer token on every
upstream request. These tests pin:

- UPSTREAM_TOKEN is env-driven (ZEEKER_FULL_ACCESS_TOKEN), default empty.
- build_headers() adds "Authorization: Bearer <token>" iff the token is set.
- build_http_client() carries the header on its default headers.
"""

import importlib

import mcp_zeeker.config as cfg_module
from mcp_zeeker.core import http_client


def _reload_config(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("ZEEKER_FULL_ACCESS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ZEEKER_FULL_ACCESS_TOKEN", token)
    importlib.reload(cfg_module)


def test_upstream_token_env_driven(monkeypatch):
    _reload_config(monkeypatch, "tok-123")
    assert cfg_module.UPSTREAM_TOKEN == "tok-123"

    _reload_config(monkeypatch, None)
    assert cfg_module.UPSTREAM_TOKEN == ""


def test_headers_carry_bearer_token_when_set(monkeypatch):
    _reload_config(monkeypatch, "tok-123")
    try:
        headers = http_client.build_headers()
        assert headers["Authorization"] == "Bearer tok-123"
        assert headers["User-Agent"] == cfg_module.USER_AGENT
    finally:
        _reload_config(monkeypatch, None)


def test_headers_omit_authorization_when_unset(monkeypatch):
    _reload_config(monkeypatch, None)
    headers = http_client.build_headers()
    assert "Authorization" not in headers


async def test_client_default_headers_include_token(monkeypatch):
    _reload_config(monkeypatch, "tok-456")
    try:
        async with http_client.build_http_client() as client:
            assert client.headers["authorization"] == "Bearer tok-456"
    finally:
        _reload_config(monkeypatch, None)


async def test_client_default_headers_clean_when_unset(monkeypatch):
    _reload_config(monkeypatch, None)
    async with http_client.build_http_client() as client:
        assert "authorization" not in client.headers
