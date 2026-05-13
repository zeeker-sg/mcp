"""
Shared test fixtures for the mcp-zeeker test suite.

Live fixtures replacing the Wave-0 stubs. Provides:
- mcp_client: FastMCP in-memory client (async context manager)
- asgi_client: httpx.AsyncClient backed by ASGITransport
- stub_upstream: pre-registers the 4 upstream DB responses via httpx_mock
- bound_metadata_cache: MetadataCache bound to the current context with a stub
- pytest_collection_modifyitems: auto-skips @pytest.mark.live tests unless ZEEKER_LIVE=1
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_httpx
from fastmcp import Client

from mcp_zeeker import config
from mcp_zeeker.app import app
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.server import mcp

# Metadata stub for test fixtures.
# NOTE: DB key is "Zeeker-Judgements" (mixed-case) to deliberately exercise the
# D2-05 normalize-at-ingest path — MetadataCache._fetch_and_normalize lowercases
# it so lookups with "zeeker-judgements" will hit.
METADATA_STUB = {
    "databases": {
        "Zeeker-Judgements": {
            "tables": {
                "judgments": {"description": "Singapore court judgments"}
            }
        }
    }
}


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.live tests unless ZEEKER_LIVE env var is set."""
    if not os.getenv("ZEEKER_LIVE"):
        skip_live = pytest.mark.skip(reason="Set ZEEKER_LIVE=1 to run live tests")
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(skip_live)


def _db_url(name: str) -> str:
    """Build the full upstream URL for a given DB name."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _tables_payload(names: list[str]) -> dict:
    """Build a minimal Datasette /{db}.json payload with Phase 2 optional fields."""
    return {
        "tables": [
            {"name": n, "hidden": False, "count": None, "columns": [], "primary_keys": []}
            for n in names
        ]
    }


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

    sglawwatch gets 6 entries (4 hidden + 2 visible); all other DBs get 4 (2 platform + 2 visible).
    The hidden platform-internal tables (_zeeker_schemas, _zeeker_updates) are present
    in the upstream payload — the Phase 2 config denylist removes them at the handler layer.
    Returns the httpx_mock fixture for further customization by individual tests.
    """
    for db in config.ALLOWED_DATABASES:
        if db == "sglawwatch":
            # 4 hidden (2 legacy + 2 platform) + 2 visible
            tables = ["metadata", "schema_versions", "_zeeker_schemas", "_zeeker_updates", "t1", "t2"]
        else:
            # 2 platform-internal + 2 visible
            tables = ["_zeeker_schemas", "_zeeker_updates", "t1", "t2"]
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


@pytest.fixture
def bound_metadata_cache(httpx_mock: pytest_httpx.HTTPXMock):
    """
    Bind a MetadataCache backed by a real httpx.AsyncClient to the current context.

    Stubs /-/metadata.json with METADATA_STUB (is_reusable=True allows TTL/force_refresh tests).
    Uses ttl=0 to force re-fetch on every call (makes TTL-expiry tests simple).
    Tears down the binding and clears the singleton on fixture teardown.
    """
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json=METADATA_STUB,
        is_reusable=True,
    )
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(mc)
    yield mc
    MetadataCache.reset(token)
    MetadataCache.clear_singleton()
