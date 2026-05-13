"""
Tests for the Starlette app: /healthz liveness and Origin allowlist matrix.

Covers:
- OBS-01: /healthz returns {"status": "ok"} without any upstream call.
- TRANSPORT-06: Origin allowlist policy matrix.
  * Missing Origin ALLOW (CLI/server-side clients do not send it).
  * Allowlisted Origin ALLOW (https://claude.ai, https://claude.com).
  * Foreign Origin DENY 403 with {"error": "origin_not_allowed"}.
  * OPTIONS preflight with allowed Origin returns 204 with CORS headers.

Note on ASGI transport: the ASGITransport(app) does not initialize FastMCP's
session manager (that requires the lifespan to run). Origin middleware tests
that result in ALLOW are tested via /healthz (which does not touch FastMCP) and
via OPTIONS preflight (which is short-circuited at the middleware layer).
The DENY 403 case is also middleware-level and works for any path.
"""

from __future__ import annotations


async def test_healthz_returns_ok_without_upstream(asgi_client):
    """OBS-01: /healthz returns {"status": "ok"} without any upstream call.

    The ASGITransport does not hit a real network; if /healthz attempted an
    upstream call there would be no stub and it would raise.
    """
    resp = await asgi_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_origin_missing_allowed(asgi_client):
    """TRANSPORT-06: Missing Origin header is allowed (MCP spec §3.1).

    Tested via /healthz — if origin middleware blocks missing-origin requests
    this would return 403; /healthz should return 200.
    """
    resp = await asgi_client.get("/healthz")
    assert resp.status_code != 403, (
        f"Missing Origin should be ALLOWED (not 403), got {resp.status_code}"
    )


async def test_origin_allowlisted_allowed(asgi_client):
    """TRANSPORT-06: Allowlisted Origin (https://claude.ai) is allowed.

    Tested via /healthz with Origin header — if origin middleware rejects
    allowlisted origins this would return 403; /healthz should return 200.
    """
    resp = await asgi_client.get("/healthz", headers={"origin": "https://claude.ai"})
    assert resp.status_code != 403, (
        f"Allowlisted Origin should be ALLOWED (not 403), got {resp.status_code}"
    )


async def test_origin_foreign_rejected_403(asgi_client):
    """TRANSPORT-06: Foreign Origin is rejected with 403 and origin_not_allowed body.

    The OriginAllowlistMiddleware short-circuits at the ASGI layer before FastMCP
    processes the request, so this test works regardless of lifespan state.
    """
    resp = await asgi_client.post(
        "/mcp/",
        content=b'{"jsonrpc":"2.0","method":"initialize","id":1}',
        headers={
            "content-type": "application/json",
            "origin": "https://evil.example.com",
        },
    )
    assert resp.status_code == 403
    assert resp.json() == {"error": "origin_not_allowed"}


async def test_origin_preflight_options_allowed(asgi_client):
    """TRANSPORT-06: OPTIONS preflight with allowlisted Origin returns 204 with CORS headers.

    OPTIONS preflight is handled entirely by OriginAllowlistMiddleware before
    FastMCP sees the request — works without the lifespan initialized.
    """
    resp = await asgi_client.options(
        "/mcp/",
        headers={
            "origin": "https://claude.ai",
            "access-control-request-method": "POST",
        },
    )
    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == "https://claude.ai"
    assert "POST" in resp.headers.get("access-control-allow-methods", "")
