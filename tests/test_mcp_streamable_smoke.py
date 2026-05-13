"""
Uvicorn random-port smoke tests — Pattern C.

Tests the real streamable HTTP transport path (the in-memory Pattern B tests skip
all ASGI/HTTP serialization). A live uvicorn server is spawned on a free random port,
then tested via the MCP streamablehttp_client.

Tests:
- TRANSPORT-01/02: initialize handshake completes over real HTTP wire.
- TRANSPORT-03: server is stateless — two independent sessions succeed independently.

Note on upstream stubbing: pytest-httpx patches httpx in the test process. Because
the uvicorn server shares the same process memory (daemon thread), the patch DOES
reach the server's httpx calls. However, per RESEARCH.md Pattern C caveat, we keep
the live smoke conservative: assert only the transport handshake and tools/list (no
upstream call required), not the full tools/call envelope shape — Pattern B already
proves that contract.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from mcp_zeeker.app import app


def _free_port() -> int:
    """Bind to port 0 to get an OS-assigned free port, then release the socket."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server():
    """Spawn uvicorn on a random port in a daemon thread; yield the /mcp/ URL.

    The thread is cleaned up after each test. The server uses asyncio loop so
    pytest-httpx patches (which also use asyncio) are visible in the thread.
    """
    port = _free_port()
    cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Poll until the server is ready (up to 2.5s)
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn did not start within 2.5s"
    yield f"http://127.0.0.1:{port}/mcp/"
    server.should_exit = True
    thread.join(timeout=5)


async def test_streamable_http_handshake_and_list_tools(live_server):
    """TRANSPORT-01/02: initialize handshake completes over real HTTP; tools/list works.

    Uses the mcp.client.streamable_http.streamablehttp_client to connect to the
    live uvicorn server. Asserts that initialize returns a non-empty serverInfo.name
    and that tools/list includes list_databases.
    """
    async with streamablehttp_client(live_server) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            # FastMCP advertises a server name derived from FastMCP(name="zeeker")
            assert init_result.serverInfo.name, "initialize returned empty serverInfo.name"
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            assert "list_databases" in tool_names, (
                f"list_databases not in tools/list response: {tool_names}"
            )


async def test_two_independent_sessions(live_server):
    """TRANSPORT-03: Server is stateless — two independent sessions succeed.

    Opens two SEPARATE streamablehttp_client contexts back-to-back. Each runs
    initialize independently. Both must succeed and return list_databases in
    tools/list. This proves sessions do not share or contaminate each other's state.
    """
    # Session 1
    async with streamablehttp_client(live_server) as (read1, write1, _get_sid1):
        async with ClientSession(read1, write1) as session1:
            init1 = await session1.initialize()
            assert init1.serverInfo.name, "Session 1 initialize failed"
            tools1 = await session1.list_tools()
            names1 = {t.name for t in tools1.tools}
            assert "list_databases" in names1, "Session 1 tools/list missing list_databases"

    # Session 2 — separate context; no shared state with Session 1
    async with streamablehttp_client(live_server) as (read2, write2, _get_sid2):
        async with ClientSession(read2, write2) as session2:
            init2 = await session2.initialize()
            assert init2.serverInfo.name, "Session 2 initialize failed"
            tools2 = await session2.list_tools()
            names2 = {t.name for t in tools2.tools}
            assert "list_databases" in names2, "Session 2 tools/list missing list_databases"

    # Both sessions returned the same tools (stateless, deterministic)
    assert names1 == names2, f"Sessions returned different tools: {names1} vs {names2}"
