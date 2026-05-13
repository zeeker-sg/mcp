"""
Counter-patch tests for column visibility (D3-07) — Slice A (Plan 03-02).

Mirrors `tests/tools/test_discovery_side_channel.py` (DISC-05 counter pattern)
at the column level. Proves CODE-PATH IDENTITY between hidden columns and
nonexistent columns: both invoke `raise_unknown_column` exactly once each — no
presence side-channel.

Three handler paths route through `raise_unknown_column` (D3-07):
- filter column reference
- sort column reference
- columns parameter entry

Each path is asserted separately via the counter-patch idiom — the patch
target is `mcp_zeeker.tools.retrieval.raise_unknown_column` (NOT the visibility
module), because Python's `unittest.mock.patch` rewrites the binding at the
import site. `query_table` imports `raise_unknown_column` from
`mcp_zeeker.core.visibility`, so the lookup happens against the retrieval
module's local binding.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.core.visibility import raise_unknown_column
from mcp_zeeker.tools.retrieval import query_table


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _zeeker_schemas_url(db: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{db}/_zeeker_schemas.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


def _pdpc_db_payload() -> dict:
    """pdpc.enforcement_decisions; global-hidden 'id' present in upstream column list."""
    return {
        "tables": [
            {
                "name": "enforcement_decisions",
                "hidden": False,
                "count": 100,
                "columns": [
                    "id",
                    "title",
                    "organisation",
                    "decision_type",
                    "decision_date",
                    "decision_url",
                    "penalty_amount",
                    "summary",
                ],
                "primary_keys": [],
            },
        ]
    }


def _empty_schema_payload() -> dict:
    return {
        "columns": [
            "resource_name",
            "schema_version",
            "schema_hash",
            "column_definitions",
            "created_at",
            "updated_at",
        ],
        "rows": [],
    }


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.fixture
async def metadata_cache(httpx_mock: pytest_httpx.HTTPXMock):
    httpx_mock.add_response(
        url=_metadata_url(), json={"databases": {}}, is_reusable=True, is_optional=True
    )
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
        token = MetadataCache.bind(mc)
        yield mc
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()


# ---------------------------------------------------------------------------
# D3-07 — three handler paths share raise_unknown_column
# ---------------------------------------------------------------------------


async def test_filter_column_routes_through_raise_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-07: filter on hidden + nonexistent column → 2 raise_unknown_column calls."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"),
        json=_empty_schema_payload(),
        is_reusable=True,
        is_optional=True,
    )

    counter = {"n": 0}
    original_raise = raise_unknown_column

    def counting_raise(database: str, table: str, column: str) -> None:
        counter["n"] += 1
        original_raise(database, table, column)

    # Patch at the retrieval call-site — query_table imports raise_unknown_column
    # from core.visibility into its own module namespace, and that namespace is
    # where the function name is looked up at call time. Patching
    # `core.visibility.raise_unknown_column` would NOT intercept this call.
    with patch("mcp_zeeker.tools.retrieval.raise_unknown_column", counting_raise):
        with pytest.raises(ToolError):
            await query_table(
                "pdpc",
                "enforcement_decisions",
                filters=[{"column": "id", "op": "exact", "value": "x"}],  # hidden
            )
        with pytest.raises(ToolError):
            await query_table(
                "pdpc",
                "enforcement_decisions",
                filters=[{"column": "does_not_exist", "op": "exact", "value": "x"}],  # absent
            )

    assert counter["n"] == 2, f"Expected 2 raise_unknown_column calls, got {counter['n']}"


async def test_sort_column_routes_through_raise_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-07: sort on hidden + nonexistent column → 2 raise_unknown_column calls."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"),
        json=_empty_schema_payload(),
        is_reusable=True,
        is_optional=True,
    )

    counter = {"n": 0}
    original_raise = raise_unknown_column

    def counting_raise(database: str, table: str, column: str) -> None:
        counter["n"] += 1
        original_raise(database, table, column)

    with patch("mcp_zeeker.tools.retrieval.raise_unknown_column", counting_raise):
        with pytest.raises(ToolError):
            await query_table("pdpc", "enforcement_decisions", sort="id")  # hidden
        with pytest.raises(ToolError):
            await query_table("pdpc", "enforcement_decisions", sort="does_not_exist")  # absent

    assert counter["n"] == 2, f"Expected 2 raise_unknown_column calls, got {counter['n']}"


async def test_columns_param_routes_through_raise_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-07: columns= with hidden + nonexistent → 2 raise_unknown_column calls."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"),
        json=_empty_schema_payload(),
        is_reusable=True,
        is_optional=True,
    )

    counter = {"n": 0}
    original_raise = raise_unknown_column

    def counting_raise(database: str, table: str, column: str) -> None:
        counter["n"] += 1
        original_raise(database, table, column)

    with patch("mcp_zeeker.tools.retrieval.raise_unknown_column", counting_raise):
        with pytest.raises(ToolError):
            await query_table(
                "pdpc",
                "enforcement_decisions",
                columns=["id"],  # hidden
            )
        with pytest.raises(ToolError):
            await query_table(
                "pdpc",
                "enforcement_decisions",
                columns=["does_not_exist"],  # absent
            )

    assert counter["n"] == 2, f"Expected 2 raise_unknown_column calls, got {counter['n']}"


# ---------------------------------------------------------------------------
# Side-channel: unknown_column error path does NOT trigger _zeeker_schemas
# ---------------------------------------------------------------------------


async def test_no_zeeker_schemas_call_on_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-07 + D2-16: unknown_column rejects BEFORE the column-types upstream call.

    The handler order (D3-08) puts visibility + per-field checks ahead of the
    `get_table_column_types(database)` invocation, so the error path makes no
    `_zeeker_schemas` request. Symmetric to Phase 2's discovery side-channel.
    """
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    # NOTE: deliberately NOT registering _zeeker_schemas — any request to it
    # would fail with httpx_mock 'no response found', surfaced as a 500-class
    # error in the test. The assertion below catches it positively as well.

    with pytest.raises(ToolError):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            filters=[{"column": "id", "op": "exact", "value": "x"}],
        )

    zeeker_schema_reqs = [r for r in httpx_mock.get_requests() if "_zeeker_schemas" in str(r.url)]
    assert len(zeeker_schema_reqs) == 0, (
        f"expected no _zeeker_schemas call on unknown_column path, got: "
        f"{[str(r.url) for r in zeeker_schema_reqs]}"
    )
