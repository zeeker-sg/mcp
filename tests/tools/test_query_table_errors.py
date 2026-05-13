"""
Error-path tests for query_table — Slice A + Slice B (Plan 03-02 + 03-03).

Covers:
- QUERY-05 / QUERY-06: unknown_column on filter / sort / columns paths — hidden
  AND nonexistent columns both fail through `raise_unknown_column` (identity
  proven separately by `test_retrieval_side_channel.py`).
- D3-02: invalid op on the Filter pydantic model is rejected (Literal mismatch).
- invalid_filter_op: gt without value → fixed-literal error (no value echo).
- D3-03 cursor errors (Plan 03-03):
  - shape-mismatch → invalid_cursor (does not match current request shape)
  - malformed → invalid_cursor (cursor is malformed)
  - invalid cursor decode happens BEFORE any upstream call (no /-table.json req)
- QUERY-07 belt-and-suspenders: limit=201 rejected before any upstream call.

URL/Schema fixtures mirror `test_query_table.py`; the `_zeeker_schemas` stub
is registered as `is_optional=True` because error paths short-circuit before
the column-type merge call in many cases.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError
from pydantic import ValidationError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
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
    """pdpc.enforcement_decisions — `id` is global-hidden (HIDDEN_COLUMNS['*'])."""
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
    # query_table does NOT call MetadataCache directly; mark optional so error
    # paths that short-circuit don't fail at teardown.
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
# QUERY-05 / QUERY-06 — unknown_column across filter / sort / columns
# ---------------------------------------------------------------------------


async def test_filter_on_nonexistent_column_raises_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-05: filter on a column not in the schema raises ToolError(unknown_column)."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^unknown_column:"):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            filters=[{"column": "does_not_exist", "op": "exact", "value": "x"}],
        )


async def test_filter_on_hidden_column_raises_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-06: filter on hidden `id` raises unknown_column — same code path as nonexistent."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^unknown_column:"):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            filters=[{"column": "id", "op": "exact", "value": "1"}],
        )


async def test_sort_on_nonexistent_column_raises_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-05: sort='does_not_exist' raises unknown_column."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^unknown_column:"):
        await query_table("pdpc", "enforcement_decisions", sort="does_not_exist")


async def test_sort_on_hidden_column_raises_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-06: sort='id' (hidden) raises unknown_column — same code path."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^unknown_column:"):
        await query_table("pdpc", "enforcement_decisions", sort="id")


async def test_columns_with_nonexistent_raises_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-05: columns=['does_not_exist'] raises unknown_column."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^unknown_column:"):
        await query_table("pdpc", "enforcement_decisions", columns=["does_not_exist"])


async def test_columns_with_hidden_raises_unknown_column(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-06: columns=['id'] (hidden) raises unknown_column — same code path."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^unknown_column:"):
        await query_table("pdpc", "enforcement_decisions", columns=["id"])


# ---------------------------------------------------------------------------
# D3-02 / invalid_filter_op
# ---------------------------------------------------------------------------


async def test_invalid_filter_op_unsupported_pydantic(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-02: ops outside the 13-string FilterOp Literal are rejected by Pydantic."""
    httpx_mock.add_response(
        url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True, is_optional=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    # Filter.model_validate inside the handler raises pydantic ValidationError;
    # FastMCP wraps that as ToolError on the public boundary.
    with pytest.raises((ValidationError, ToolError)):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            filters=[{"column": "title", "op": "regex", "value": ".*"}],
        )


async def test_invalid_filter_op_value_required_for_gt(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """compile_filters: gt without value → ToolError(invalid_filter_op) fixed literal."""
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_reusable=True
    )

    with pytest.raises(ToolError, match=r"^invalid_filter_op:"):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            filters=[{"column": "penalty_amount", "op": "gt", "value": None}],
        )


# ---------------------------------------------------------------------------
# D3-03 cursor error paths (Plan 03-03 — replaces Slice A's scope-boundary test)
# ---------------------------------------------------------------------------


async def test_invalid_cursor_on_shape_mismatch(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-03: a cursor encoded for shape_a, decoded under shape_b → invalid_cursor.

    Concrete shape change: encode under sort=None, decode under sort='decision_date'.
    The handler MUST raise ToolError(invalid_cursor) BEFORE issuing any upstream
    request — the cursor decode is the first non-trivial step after the limit
    clamp and the table-resolution gate. The error message is the FIXED literal
    "invalid_cursor: cursor does not match current request shape" — no cursor
    contents echoed (T-03-12 / INJ-05).
    """
    from mcp_zeeker.core.cursor import canonical_shape_str, encode_cursor

    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    # Build a cursor under shape_a (sort=None), then call with shape_b (sort=decision_date)
    shape_a = canonical_shape_str("pdpc", "enforcement_decisions", None, [], None)
    bad_cursor = encode_cursor(shape_a, "2")

    with pytest.raises(ToolError, match=r"^invalid_cursor: cursor does not match"):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            cursor=bad_cursor,
            sort="decision_date",  # shape change vs. encode
        )


async def test_invalid_cursor_on_malformed(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-03: passing a non-base64 token raises invalid_cursor (malformed) — fixed literal."""
    httpx_mock.add_response(
        url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True, is_optional=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^invalid_cursor: cursor is malformed"):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            cursor="!!!not-base64-at-all!!!",
        )


async def test_invalid_cursor_short_circuits_before_upstream(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-03: a malformed cursor causes ZERO upstream /-table.json requests.

    Cursor decode is the SECOND validation gate (after the limit clamp) and
    runs before any compile_filters / upstream call. This is the contract that
    makes shape-mismatch errors safe — an attacker cannot use cursor reuse to
    smoke out columns or run repeated upstream queries; the rejection happens
    locally.
    """
    httpx_mock.add_response(
        url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True, is_optional=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises(ToolError, match=r"^invalid_cursor:"):
        await query_table(
            "pdpc",
            "enforcement_decisions",
            cursor="garbage",
        )

    # Critical assertion: NO request to /pdpc/enforcement_decisions.json was issued.
    table_reqs = [
        r for r in httpx_mock.get_requests() if r.url.path.endswith("/enforcement_decisions.json")
    ]
    assert table_reqs == [], (
        f"invalid_cursor must short-circuit before upstream, got {len(table_reqs)} requests"
    )


# ---------------------------------------------------------------------------
# QUERY-07 — limit clamp also enforced at handler boundary (belt-and-suspenders)
# ---------------------------------------------------------------------------


async def test_limit_201_rejected_before_upstream(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-07: limit=201 raises before any upstream HTTP call is issued.

    Pydantic Field(le=200) is the primary gate via MCP dispatch; the handler's
    belt-and-suspenders clamp covers direct Python callers. Either path must
    fire before the table-row fetch.
    """
    httpx_mock.add_response(
        url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True, is_optional=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_empty_schema_payload(), is_optional=True
    )

    with pytest.raises((ValidationError, ToolError)):
        await query_table("pdpc", "enforcement_decisions", limit=201)

    table_reqs = [
        r for r in httpx_mock.get_requests() if r.url.path.endswith("/enforcement_decisions.json")
    ]
    assert table_reqs == [], (
        f"limit=201 must reject before upstream call, got {len(table_reqs)} requests"
    )
