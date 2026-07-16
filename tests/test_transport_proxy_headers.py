"""
F-1 proxy-headers regression test.

Proves that requests carrying X-Forwarded-Proto / X-Forwarded-For headers do NOT
cause a 500 in the Starlette application pipeline. This closes the Phase 1 LEARNINGS
F-1 test gap.

Background:
  F-1 (01-LEARNINGS.md): First curl against POST /mcp returned a 307 redirect with
  location: http:// (wrong scheme). Root cause: Caddy terminates TLS and forwards
  plain HTTP; without --proxy-headers on uvicorn, Starlette ignores X-Forwarded-Proto
  and emits the redirect with the internal HTTP scheme. Fix commit: 349a739.

  The production fix lives in the Dockerfile CMD flags (--proxy-headers
  --forwarded-allow-ips=*). This test guards against APPLICATION-LAYER regressions
  — i.e., code changes that would break the Starlette pipeline when proxy headers
  are present. It does NOT test the redirect-scheme correction itself (that requires
  the real uvicorn + --proxy-headers flag path).

Scope: TRANSPORT-06 boundary (application ASGI layer only, no uvicorn flag path).
Uses the asgi_client fixture from conftest.py (httpx.ASGITransport — full Starlette
middleware pipeline, no real HTTP socket).
"""

from __future__ import annotations


def _initialize_payload() -> dict:
    """Valid MCP JSON-RPC initialize payload."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "phase2-proxy-test", "version": "1"},
        },
    }


async def test_x_forwarded_proto_does_not_500(asgi_client):
    """F-1 regression: X-Forwarded-Proto header must not crash the ASGI pipeline.

    Sends a POST /mcp with X-Forwarded-Proto: https and X-Forwarded-For: 203.0.113.1
    (a TEST-NET address, RFC 5737). Asserts the response is NOT 500. Any of
    {200, 307, 400, 406} is acceptable — the proxy header should be transparent
    to the application layer; it must not trigger an unhandled exception.
    """
    resp = await asgi_client.post(
        "/mcp",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.1",
            "Content-Type": "application/json",
        },
        json=_initialize_payload(),
    )
    assert resp.status_code != 500, (
        f"X-Forwarded-Proto header caused a 500 — application pipeline regression. "
        f"Status: {resp.status_code}, Body: {resp.text[:200]}"
    )
    assert resp.status_code in {200, 307, 400, 406}, (
        f"Unexpected status {resp.status_code} — expected one of {{200, 307, 400, 406}}. "
        f"Body: {resp.text[:200]}"
    )


async def test_no_proxy_headers_baseline(asgi_client):
    """Control test: same POST without proxy headers must also NOT 500.

    Proves the presence/absence of proxy headers does not toggle the failure mode —
    both paths must be non-500.
    """
    resp = await asgi_client.post(
        "/mcp",
        headers={
            "Content-Type": "application/json",
        },
        json=_initialize_payload(),
    )
    assert resp.status_code != 500, (
        f"Baseline (no proxy headers) caused a 500 — pipeline regression. "
        f"Status: {resp.status_code}, Body: {resp.text[:200]}"
    )
    assert resp.status_code in {200, 307, 400, 406}, (
        f"Unexpected status {resp.status_code} for baseline. Body: {resp.text[:200]}"
    )
