"""
F-3 stateless_http session edge-case regression tests.

Proves that the server, running with stateless_http=True (commit 4ce06d5), behaves
correctly in two critical scenarios that were NOT exercised by the Phase 1 smoke test:

  1. The server does NOT issue Mcp-Session-Id response headers (strongest statelessness
     invariant per 01-LEARNINGS.md F-3 line 54).
  2. A request bearing a fabricated / bogus Mcp-Session-Id does NOT get a 404
     "Session not found" — the server treats each request independently (TRANSPORT-03).

Background:
  F-3 (01-LEARNINGS.md): FastMCP's http_app() defaults to stateful streamable HTTP —
  it issues Mcp-Session-Id headers and tracks sessions in-memory. On container restart,
  the session table is wiped. Long-lived clients (Claude Desktop mcp-remote, Claude Code)
  cache the session ID and get 404'd on every call after a redeploy.
  Fix commit: 4ce06d5 — mcp.http_app(path="/", stateless_http=True).

  The Phase 1 smoke test (test_mcp_streamable_smoke.py::test_two_independent_sessions)
  only proved the happy single-session path within one server lifetime. It never tested:
  - "Server doesn't issue Mcp-Session-Id at all" (this file, test 1)
  - "Bogus session ID is silently ignored, not 404'd" (this file, test 2)

  TRANSPORT-03 (REQUIREMENTS.md): "Server is stateless (no persistent state between
  requests); Mcp-Session-Id honored when client supplies it."

Uses the live_server fixture from tests/conftest.py (random-port uvicorn daemon thread,
Pattern C). Tests are synchronous because live_server is a synchronous fixture backed
by a daemon thread (not an asyncio coroutine).
"""

from __future__ import annotations

import httpx


def _initialize_payload() -> dict:
    """Valid MCP JSON-RPC initialize payload."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "phase2-stateless-test", "version": "1"},
        },
    }


def _tools_list_payload() -> dict:
    """Valid MCP JSON-RPC tools/list payload."""
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }


def test_server_does_not_issue_mcp_session_id_header(live_server):
    """F-3 strongest invariant: stateless server MUST NOT issue Mcp-Session-Id headers.

    Sends an initialize request to the live uvicorn server. With stateless_http=True,
    FastMCP must NOT include 'mcp-session-id' in the response headers. If it does,
    the stateless_http=True flag has been removed or overridden — which would break
    every long-lived MCP client on the next container restart.

    httpx normalizes header names to lowercase; we check 'mcp-session-id'.
    """
    resp = httpx.post(
        live_server,
        json=_initialize_payload(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        timeout=10.0,
    )
    assert "mcp-session-id" not in resp.headers, (
        f"Server issued Mcp-Session-Id response header — stateless_http=True regression! "
        f"Header value: {resp.headers.get('mcp-session-id')!r}. "
        f"Commit 4ce06d5 fix has been undone."
    )


def test_bogus_session_id_does_not_404(live_server):
    """F-3: Bogus/fabricated Mcp-Session-Id header must NOT produce a 404 response.

    Simulates a long-lived client that still carries a cached session ID from a
    previous server instance (e.g., after a container restart). The stateless server
    must treat each request as independent — TRANSPORT-03 — rather than validating
    the session ID against an in-memory table.

    Protocol:
      1. Send an initialize request WITHOUT a session ID (sets up the MCP handshake).
      2. Send a tools/list request WITH a fabricated Mcp-Session-Id header. The server
         must accept this without returning 404 "Session not found".

    Uses a single httpx.Client for connection reuse but does NOT persist any
    session-id from the initialize response.
    """
    bogus_session_id = "bogus-uuid-deadbeef-not-a-real-session-00000000"

    with httpx.Client(timeout=10.0) as client:
        # Step 1: initialize (no session ID header — clean handshake)
        init_resp = client.post(
            live_server,
            json=_initialize_payload(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        # Initialize should succeed
        assert init_resp.status_code != 500, f"initialize failed with 500: {init_resp.text[:200]}"

        # Step 2: tools/list WITH a fabricated session ID the server doesn't know
        tools_resp = client.post(
            live_server,
            json=_tools_list_payload(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Mcp-Session-Id": bogus_session_id,
            },
        )
        assert tools_resp.status_code != 404, (
            f"Server returned 404 for bogus Mcp-Session-Id — stateless_http=True "
            f"regression! Status: {tools_resp.status_code}, "
            f"Body: {tools_resp.text[:200]}. Commit 4ce06d5 fix has been undone."
        )
        # Also confirm it's not a 500 — something more substantive should come back
        assert tools_resp.status_code != 500, (
            f"Server returned 500 for tools/list with bogus session: {tools_resp.text[:200]}"
        )
