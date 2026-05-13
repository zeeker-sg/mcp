"""
Unit tests for list_tables tool handler — DISC-02.

Covers:
- Visibility filter: upstream-hidden flag + config.HIDDEN_TABLES both applied
- sglawwatch additional hidden tables (metadata, schema_versions)
- Unknown database raises ToolError with unknown_database prefix
- row_count=None passthrough (D2-13 honesty)
- Description merge: upstream metadata wins over config fallback (D2-01)
- Description fallback: config.TABLE_DESCRIPTIONS used when upstream is absent/empty
"""

from __future__ import annotations

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.discovery import list_tables
from fastmcp.exceptions import ToolError


def _db_url(name: str) -> str:
    """Build the full upstream URL for a given DB name."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


def _tables_payload(table_defs: list[dict]) -> dict:
    """Build a Datasette /{db}.json payload from explicit table defs."""
    return {"tables": table_defs}


def _simple_tables(names: list[str], *, hidden: list[str] | None = None) -> list[dict]:
    """Build table defs; entries in hidden list get hidden=True."""
    hidden_set = set(hidden or [])
    return [
        {"name": n, "hidden": n in hidden_set, "count": None, "columns": [], "primary_keys": []}
        for n in names
    ]


def _empty_metadata_stub() -> dict:
    """Minimal /-/metadata.json with no table-level metadata."""
    return {"databases": {}}


@pytest.fixture
def datasette_client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:
    """Bind a DatasetteClient without pre-stubbing upstream (tests supply custom payloads)."""
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    dc = DatasetteClient(http)
    token = DatasetteClient.bind(dc)
    yield dc
    DatasetteClient.reset(token)


@pytest.fixture
def metadata_cache(httpx_mock: pytest_httpx.HTTPXMock) -> MetadataCache:
    """Bind a MetadataCache with empty metadata (tests use config fallback)."""
    httpx_mock.add_response(
        url=_metadata_url(),
        json=_empty_metadata_stub(),
        is_reusable=True,
    )
    mc = MetadataCache(httpx.AsyncClient(base_url=config.UPSTREAM_URL), config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(mc)
    yield mc
    MetadataCache.reset(token)
    MetadataCache.clear_singleton()


async def test_visible_tables_only(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """DISC-02: visible tables only — upstream-hidden flag + config-denylist both applied.

    6 table payload:
    - judgments, judgments_fragments: visible
    - _zeeker_schemas, _zeeker_updates: config.HIDDEN_TABLES (platform-internal)
    - fts_aux1, fts_aux2: upstream hidden=True
    Expected: 2 rows in envelope.data (judgments + judgments_fragments)
    """
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            _simple_tables(
                ["judgments", "judgments_fragments", "_zeeker_schemas", "_zeeker_updates"],
            )
            + _simple_tables(["fts_aux1", "fts_aux2"], hidden=["fts_aux1", "fts_aux2"])
        ),
    )
    envelope = await list_tables("zeeker-judgements")

    assert len(envelope.data) == 2
    names = {row["name"] for row in envelope.data}
    assert names == {"judgments", "judgments_fragments"}
    assert envelope.provenance.database == "zeeker-judgements"
    assert envelope.provenance.table is None


async def test_sglawwatch_filters_metadata_and_schema_versions(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """DISC-02: sglawwatch-specific hidden tables (metadata, schema_versions) filtered out."""
    httpx_mock.add_response(
        url=_db_url("sglawwatch"),
        json=_tables_payload(
            _simple_tables([
                "metadata", "schema_versions", "_zeeker_schemas", "_zeeker_updates",
                "headlines", "commentaries",
            ])
        ),
    )
    envelope = await list_tables("sglawwatch")

    names = {row["name"] for row in envelope.data}
    assert "metadata" not in names
    assert "schema_versions" not in names
    assert "_zeeker_schemas" not in names
    assert "_zeeker_updates" not in names
    assert "headlines" in names
    assert "commentaries" in names
    assert len(envelope.data) == 2


async def test_unknown_database_raises() -> None:
    """DISC-02: unknown database raises ToolError before any HTTP call.

    No HTTP fixtures needed — the database check is the first line of list_tables
    and raises before any DatasetteClient or MetadataCache access.
    """
    with pytest.raises(ToolError, match=r"^unknown_database: Database not found: not-a-db$"):
        await list_tables("not-a-db")


async def test_row_count_null_passthrough(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-13: row_count passes through as None — not substituted with -1 or 0."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload([{"name": "judgments", "hidden": False, "count": None, "columns": [], "primary_keys": []}]),
    )
    envelope = await list_tables("zeeker-judgements")

    assert len(envelope.data) == 1
    assert envelope.data[0]["row_count"] is None


async def test_description_uses_upstream_when_present(
    datasette_client: DatasetteClient,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-01: upstream metadata description wins over config fallback."""
    httpx_mock.add_response(
        url=_metadata_url(),
        json={
            "databases": {
                "zeeker-judgements": {
                    "tables": {
                        "judgments": {"description": "Upstream description wins"}
                    }
                }
            }
        },
        is_reusable=True,
    )
    mc = MetadataCache(httpx.AsyncClient(base_url=config.UPSTREAM_URL), config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(mc)

    try:
        httpx_mock.add_response(
            url=_db_url("zeeker-judgements"),
            json=_tables_payload([{"name": "judgments", "hidden": False, "count": 100, "columns": [], "primary_keys": []}]),
        )
        envelope = await list_tables("zeeker-judgements")

        assert envelope.data[0]["description"] == "Upstream description wins"
    finally:
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()


async def test_description_falls_back_to_config(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-01: config.TABLE_DESCRIPTIONS fallback used when upstream returns empty/None."""
    httpx_mock.add_response(
        url=_db_url("pdpc"),
        json=_tables_payload([{"name": "enforcement_decisions", "hidden": False, "count": 50, "columns": [], "primary_keys": []}]),
    )
    envelope = await list_tables("pdpc")

    assert envelope.data[0]["description"] == config.TABLE_DESCRIPTIONS["pdpc"]["enforcement_decisions"]
