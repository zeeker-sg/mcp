"""
DISC-05 code-path identity tests — no presence side-channel in describe_table.

Both hidden and nonexistent tables must:
1. Route through the SAME raise_unknown_table helper (counter-asserted)
2. Emit byte-for-byte identical error message format
3. NOT trigger any upstream _zeeker_schemas call (error path is cache-only)
4. NOT trigger any upstream DB call when database is unknown
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.discovery import describe_table, raise_unknown_table
from fastmcp.exceptions import ToolError


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


@pytest.fixture
def datasette_client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:
    """Bind a DatasetteClient without pre-stubbing upstream."""
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    dc = DatasetteClient(http)
    token = DatasetteClient.bind(dc)
    yield dc
    DatasetteClient.reset(token)


@pytest.fixture
def metadata_cache_empty() -> MetadataCache:
    """Bind a MetadataCache without registering any HTTP stub.

    The error paths tested here never reach MetadataCache (they fail at _resolve_table),
    so no HTTP stub is needed — just a bound instance to satisfy MetadataCache.current().
    """
    mc = MetadataCache(httpx.AsyncClient(base_url=config.UPSTREAM_URL), config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(mc)
    yield mc
    MetadataCache.reset(token)
    MetadataCache.clear_singleton()


async def test_hidden_and_nonexistent_share_helper(
    datasette_client: DatasetteClient,
    metadata_cache_empty: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """DISC-05: Both hidden and nonexistent tables route through raise_unknown_table.

    Counter assertion proves CODE PATH IDENTITY — not just message equality.
    sglawwatch.metadata is in config.HIDDEN_TABLES; sglawwatch.does_not_exist is genuinely absent.
    Both fail the `table not in visible` check in _resolve_table and call raise_unknown_table.
    """
    # sglawwatch with 'metadata' (hidden in HIDDEN_TABLES) + 'headlines' (visible)
    # 'does_not_exist' is absent entirely
    httpx_mock.add_response(
        url=_db_url("sglawwatch"),
        json={"tables": [
            {"name": "metadata", "hidden": False, "count": None, "columns": [], "primary_keys": []},
            {"name": "schema_versions", "hidden": False, "count": None, "columns": [], "primary_keys": []},
            {"name": "_zeeker_schemas", "hidden": False, "count": None, "columns": [], "primary_keys": []},
            {"name": "_zeeker_updates", "hidden": False, "count": None, "columns": [], "primary_keys": []},
            {"name": "headlines", "hidden": False, "count": 712, "columns": ["title"], "primary_keys": []},
        ]},
        is_reusable=True,
    )

    counter = {"n": 0}
    original_raise = raise_unknown_table

    def counting_raise(database: str, table: str) -> None:
        counter["n"] += 1
        original_raise(database, table)

    with patch("mcp_zeeker.tools.discovery.raise_unknown_table", counting_raise):
        with pytest.raises(ToolError) as exc_hidden:
            await describe_table("sglawwatch", "metadata")  # hidden in HIDDEN_TABLES
        with pytest.raises(ToolError) as exc_nonexistent:
            await describe_table("sglawwatch", "does_not_exist")  # genuinely absent

    # Both incremented the counter exactly once — code path identity proven
    assert counter["n"] == 2, f"Expected 2 raise_unknown_table calls, got {counter['n']}"

    # Both messages start with "unknown_table" — identical prefix
    assert exc_hidden.value.args[0].startswith("unknown_table: Table not found:")
    assert exc_nonexistent.value.args[0].startswith("unknown_table: Table not found:")

    # Messages differ only in the table identifier echo (no other difference)
    hidden_msg = exc_hidden.value.args[0]
    nonexistent_msg = exc_nonexistent.value.args[0]
    assert hidden_msg == "unknown_table: Table not found: sglawwatch.metadata"
    assert nonexistent_msg == "unknown_table: Table not found: sglawwatch.does_not_exist"
    # Verify format is identical except for the table name portion
    assert hidden_msg.replace(".metadata", ".X") == nonexistent_msg.replace(".does_not_exist", ".X")


async def test_no_upstream_zeeker_schemas_call_on_unknown(
    datasette_client: DatasetteClient,
    metadata_cache_empty: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-16: error path makes no upstream _zeeker_schemas calls — cache-only paths."""
    httpx_mock.add_response(
        url=_db_url("sglawwatch"),
        json={"tables": [
            {"name": "metadata", "hidden": False, "count": None, "columns": [], "primary_keys": []},
            {"name": "headlines", "hidden": False, "count": 712, "columns": ["title"], "primary_keys": []},
        ]},
        is_reusable=True,
    )

    with pytest.raises(ToolError):
        await describe_table("sglawwatch", "metadata")
    with pytest.raises(ToolError):
        await describe_table("sglawwatch", "does_not_exist")

    # No _zeeker_schemas request was made on either error path
    zeeker_schema_reqs = [
        r for r in httpx_mock.get_requests()
        if "_zeeker_schemas" in str(r.url)
    ]
    assert len(zeeker_schema_reqs) == 0, (
        f"Expected no _zeeker_schemas calls on error path, got: {[str(r.url) for r in zeeker_schema_reqs]}"
    )


async def test_unknown_database_does_not_call_get_database(
    datasette_client: DatasetteClient,
    metadata_cache_empty: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-17: unknown database check is first line — no DB HTTP call made."""
    with pytest.raises(ToolError, match=r"^unknown_database: Database not found: not-a-db$"):
        await describe_table("not-a-db", "any")

    # No requests to any /{db}.json should have been made
    db_reqs = [
        r for r in httpx_mock.get_requests()
        if r.url.path.endswith(".json") and "metadata" not in r.url.path
    ]
    assert len(db_reqs) == 0, (
        f"Expected no DB requests on unknown database, got: {[str(r.url) for r in db_reqs]}"
    )
