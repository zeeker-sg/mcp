"""
Shared test fixtures for the mcp-zeeker test suite.

Live fixtures replacing the Wave-0 stubs. Provides:
- mcp_client: FastMCP in-memory client (async context manager)
- asgi_client: httpx.AsyncClient backed by ASGITransport
- stub_upstream: pre-registers the 4 upstream DB responses via httpx_mock
"""

from __future__ import annotations

import httpx
import pytest
import pytest_httpx
from fastmcp import Client

from mcp_zeeker import config
from mcp_zeeker.app import app
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.server import mcp


def _db_url(name: str) -> str:
    """Build the full upstream URL for a given DB name."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _tables_payload(names: list[str]) -> dict:
    """Build a minimal Datasette /{db}.json payload."""
    return {"tables": [{"name": n} for n in names]}


@pytest.fixture
async def mcp_client():
    """
    Async fixture yielding a FastMCP in-memory client bound to the MCP app.

    The context manager entry triggers the MCP initialize handshake automatically.
    """
    async with Client(mcp) as c:
        yield c


@pytest.fixture
async def asgi_client():
    """
    Async fixture yielding an httpx.AsyncClient backed by ASGITransport(app).

    Uses the real Starlette app (including all middleware). Suitable for
    testing /healthz and the Origin allowlist without a live server.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.fixture
def stub_upstream(httpx_mock: pytest_httpx.HTTPXMock):
    """
    Pre-register the four upstream /{db}.json responses.

    sglawwatch gets 4 tables (2 hidden + 2 visible); all other DBs get 2 tables.
    Returns the httpx_mock fixture for further customization by individual tests.
    """
    for db in config.ALLOWED_DATABASES:
        if db == "sglawwatch":
            tables = ["metadata", "schema_versions", "t1", "t2"]
        else:
            tables = ["t1", "t2"]
        httpx_mock.add_response(
            url=_db_url(db),
            json=_tables_payload(tables),
        )
    return httpx_mock


@pytest.fixture
def bound_datasette_client(stub_upstream):
    """
    Bind a DatasetteClient backed by a real httpx.AsyncClient to the current context.

    The stub_upstream fixture is depended on to ensure upstream calls are intercepted.
    Tears down the binding on fixture teardown.
    """
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    dc = DatasetteClient(http)
    token = DatasetteClient.bind(dc)
    yield dc
    DatasetteClient.reset(token)
