"""
Unit tests for query_table tool handler — Slice A + Slice B (Plans 03-02 + 03-03).

Covers happy-path REQs:
- QUERY-01: filter / sort / pagination knobs translate to Datasette URL params
- QUERY-02: default rows carry the light set only (no heavy columns, no rowid)
- QUERY-03: heavy columns surface under `retrieved_content` (D3-05 / D3-19)
- QUERY-04: all 13 filter ops translate end-to-end through the handler
- QUERY-07: limit defaults to 50, max 200; 201 rejected before any upstream call
- QUERY-08: qhash cursor walk — first call returns next_cursor; second call
  consumes it and walks the page boundary
- QUERY-10: contains / startswith / endswith documented as case-insensitive

URL-matching note (pytest_httpx 0.36): `httpx_mock.add_response(url=...)` matches
the full URL INCLUDING query parameters. Our handler issues
`?_shape=objects&_col=...&_size=50` style requests, so the table-row response
is registered against a regex matcher (see `_table_url_re` below).
We then capture the actual request via `httpx_mock.get_requests()` and assert
URL params with `httpx.URL(req.url).params` — this keeps both the response
match and the param assertion explicit.
"""

from __future__ import annotations

import re

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


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for /{database}/{table}.json with any query string.

    pytest_httpx 0.36 matches add_response(url=str) on the FULL URL (including
    query params). Since query_table always issues at least _shape=objects, we
    can't use a bare URL string — every test would have to spell out the exact
    query params. A regex matcher matches the path regardless of query string.
    """
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _judgments_db_payload() -> dict:
    """zeeker-judgements.json — judgments table with heavy + light columns + hidden id.

    `id` is HIDDEN_COLUMNS["*"] (global); `content_text` / `html_raw` are
    HEAVY_COLUMNS. Used by every test here for visibility set construction.
    """
    return {
        "tables": [
            {
                "name": "judgments",
                "hidden": False,
                "count": 100,
                "columns": [
                    "id",
                    "citation",
                    "case_name",
                    "decision_date",
                    "court",
                    "source_url",
                    "summary",
                    "content_text",
                    "html_raw",
                ],
                "primary_keys": ["id"],
            },
        ]
    }


def _judgments_rows_payload(rows: list[dict] | None = None) -> dict:
    """Datasette _shape=objects payload for table-row responses."""
    return {
        "rows": rows
        or [
            {
                "citation": "2026 SGDC 136",
                "case_name": "Test v Test",
                "decision_date": "2026-01-01",
                "court": "SGDC",
                "source_url": "https://example.com/x",
                "summary": "stub",
            }
        ],
        "columns": ["citation", "case_name", "decision_date", "court", "source_url", "summary"],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 1,
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


def _table_requests(httpx_mock: pytest_httpx.HTTPXMock, database: str, table: str) -> list:
    """Return all captured requests that target /{database}/{table}.json."""
    suffix = f"/{database}/{table}.json"
    return [r for r in httpx_mock.get_requests() if r.url.path.endswith(suffix)]


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.fixture
async def metadata_cache(httpx_mock: pytest_httpx.HTTPXMock):
    # query_table does not call MetadataCache directly (the metadata pathway is
    # describe_table / list_tables territory). Mark the stub as optional so
    # tests that never trigger /-/metadata.json don't trip the teardown check.
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
# QUERY-02 / D3-04 — light projection by default; no heavy / no rowid
# ---------------------------------------------------------------------------


async def test_default_light_columns_only(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-02 / D3-04: with no `columns=`, response excludes heavy columns + rowid."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    # Upstream returns rows with a rowid + heavy column too — our handler must strip them.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_rows_payload(
            rows=[
                {
                    "rowid": 1,
                    "citation": "2026 SGDC 136",
                    "case_name": "Test v Test",
                    "decision_date": "2026-01-01",
                    "court": "SGDC",
                    "source_url": "https://example.com/x",
                    "summary": "stub",
                    "content_text": "should not surface",
                }
            ]
        ),
    )

    envelope = await query_table("zeeker-judgements", "judgments")

    assert envelope.data, "expected at least one row"
    for row in envelope.data:
        assert set(row.keys()) & config.HEAVY_COLUMNS == set(), (
            f"heavy columns leaked into light projection: {set(row.keys()) & config.HEAVY_COLUMNS}"
        )
        assert "rowid" not in row, "rowid must be stripped from light projection"
        assert "retrieved_content" not in row, "Slice A must NOT emit retrieved_content"


# ---------------------------------------------------------------------------
# QUERY-07 — limit defaults / clamp
# ---------------------------------------------------------------------------


async def test_limit_default_50_passed_to_upstream(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-07: omitting limit issues _size=50 to Datasette (DEFAULT_QUERY_LIMIT)."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    await query_table("zeeker-judgements", "judgments")

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert len(table_reqs) == 1, "expected exactly one upstream table call"
    sizes = table_reqs[0].url.params.get_list("_size")
    assert sizes == [str(config.DEFAULT_QUERY_LIMIT)], (
        f"expected _size={config.DEFAULT_QUERY_LIMIT}, got {sizes}"
    )


async def test_limit_max_200_accepted(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-07: limit=200 (MAX_QUERY_LIMIT) is accepted by pydantic and forwarded."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    envelope = await query_table("zeeker-judgements", "judgments", limit=200)
    assert envelope is not None

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert table_reqs[0].url.params.get_list("_size") == ["200"]


async def test_limit_201_rejected_pydantic_before_upstream(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-07: limit=201 violates Field(le=200) — rejected BEFORE any upstream call.

    FastMCP wraps the Pydantic ValidationError or re-raises directly depending on
    code path; we accept either (when dispatched via MCP). When called directly
    as a Python function the handler's belt-and-suspenders clamp raises a
    ToolError("invalid_filter_op: ...") instead. Both paths satisfy QUERY-07
    so long as no upstream request is issued.
    """
    # Stubs are optional — the limit clamp must short-circuit before any HTTP call.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_judgments_db_payload(),
        is_reusable=True,
        is_optional=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
        is_optional=True,
    )

    with pytest.raises((ValidationError, ToolError)):
        await query_table("zeeker-judgements", "judgments", limit=201)

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert table_reqs == [], (
        f"limit=201 must reject before upstream call, but issued {len(table_reqs)} requests"
    )


# ---------------------------------------------------------------------------
# QUERY-01 — sort and filter pass-through
# ---------------------------------------------------------------------------


async def test_sort_ascending(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-01: sort=col → _sort=col to upstream."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    await query_table("zeeker-judgements", "judgments", sort="decision_date")

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert table_reqs[0].url.params.get_list("_sort") == ["decision_date"]
    assert table_reqs[0].url.params.get_list("_sort_desc") == []


async def test_sort_descending_via_dash_prefix(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-01: sort='-col' → _sort_desc=col to upstream (descending mapping)."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    await query_table("zeeker-judgements", "judgments", sort="-decision_date")

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert table_reqs[0].url.params.get_list("_sort_desc") == ["decision_date"]
    assert table_reqs[0].url.params.get_list("_sort") == []


async def test_filter_contains_compiles_to_contains_op(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-01 / D3-02: contains compiles to __contains URL param (SQLite LIKE)."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    await query_table(
        "zeeker-judgements",
        "judgments",
        filters=[{"column": "case_name", "op": "contains", "value": "test"}],
    )

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert table_reqs[0].url.params.get_list("case_name__contains") == ["test"]


# ---------------------------------------------------------------------------
# QUERY-01 — columns allow-list passes _col=a&_col=b repeated keys
# ---------------------------------------------------------------------------


async def test_columns_allowlist_passed_as_repeated_col_keys(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-01: columns=[a,b] → _col=a&_col=b (repeated key) to Datasette."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    await query_table(
        "zeeker-judgements",
        "judgments",
        columns=["citation", "case_name"],
    )

    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    cols = table_reqs[0].url.params.get_list("_col")
    # Repeated _col keys preserved as a list — order matches caller's input.
    assert cols == ["citation", "case_name"], f"unexpected _col list: {cols}"


# ---------------------------------------------------------------------------
# QUERY-10 — description documents case-insensitive LIKE-family ops
# ---------------------------------------------------------------------------


async def test_description_documents_case_insensitive_contains() -> None:
    """QUERY-10: tool description string mentions case-insensitivity for LIKE family.

    Companion of the registry-introspection contract test in test_envelope_contract.py
    — explicit named test so the QUERY-10 traceability row is satisfied.
    """
    from mcp_zeeker.tools.retrieval import _QUERY_TABLE_DESCRIPTION

    assert "case-insensitive" in _QUERY_TABLE_DESCRIPTION.lower(), (
        "QUERY-10: query_table description must document case-insensitivity of "
        "contains/startswith/endswith for ASCII inputs"
    )


# ---------------------------------------------------------------------------
# QUERY-03 / D3-05 / D3-19 — heavy columns surface under retrieved_content
# ---------------------------------------------------------------------------


def _judgments_rows_with_heavy(rows: list[dict] | None = None) -> dict:
    """Datasette payload that includes heavy columns (content_text) inline.

    Real Datasette emits whatever the SELECT covers — `_col=content_text` causes
    that column to appear at the top level. The handler's row-reshape step is
    responsible for moving it under `retrieved_content` (D3-05).
    """
    return {
        "rows": rows
        or [
            {
                "citation": "2026 SGDC 136",
                "case_name": "Test v Test",
                "content_text": "The court considered the following submissions...",
            }
        ],
        "columns": ["citation", "case_name", "content_text"],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 1,
    }


async def test_heavy_columns_appear_under_retrieved_content(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-03 / D3-05 / D3-19: heavy columns nest under retrieved_content key.

    Snapshot assertions (D3-19 — foundation for Phase 8 TEST-03):
    - set(row.keys()) ∩ HEAVY_COLUMNS == ∅  (no heavy column at top level)
    - set(row['retrieved_content'].keys()) ⊆ HEAVY_COLUMNS  (only heavy under that key)
    """
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_with_heavy()
    )

    envelope = await query_table(
        "zeeker-judgements",
        "judgments",
        columns=["citation", "content_text"],
    )

    assert envelope.data, "expected at least one row"
    for row in envelope.data:
        # D3-19 snapshot — heavy columns must NOT appear at top level.
        assert set(row.keys()) & config.HEAVY_COLUMNS == set(), (
            f"heavy columns leaked at top level: {set(row.keys()) & config.HEAVY_COLUMNS}"
        )
        # retrieved_content carries ONLY heavy columns (D3-05 contract).
        assert "retrieved_content" in row
        rc = row["retrieved_content"]
        assert "content_text" in rc
        leaked = set(rc.keys()) - config.HEAVY_COLUMNS
        assert set(rc.keys()) <= config.HEAVY_COLUMNS, (
            f"non-heavy column leaked into retrieved_content: {leaked}"
        )
        # And the light column is still at the top level.
        assert row.get("citation") == "2026 SGDC 136"


async def test_default_response_has_no_retrieved_content_key(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-05: when columns is None (default-light), retrieved_content MUST be absent."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    envelope = await query_table("zeeker-judgements", "judgments")
    assert envelope.data
    for row in envelope.data:
        assert "retrieved_content" not in row


async def test_light_only_columns_omit_retrieved_content(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-05: explicit columns with only light columns → no retrieved_content key."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=_judgments_rows_payload()
    )

    envelope = await query_table(
        "zeeker-judgements",
        "judgments",
        columns=["citation", "case_name"],
    )
    assert envelope.data
    for row in envelope.data:
        assert "retrieved_content" not in row


# ---------------------------------------------------------------------------
# QUERY-08 — qhash cursor walk happy path
# ---------------------------------------------------------------------------


async def test_cursor_walk_round_trip(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """QUERY-08: first call emits next_cursor when upstream has more pages;
    second call consumes it (passes _next=... to upstream) and the second
    response can terminate the walk (next=None ⇒ next_cursor=None).
    """
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )

    # First-page response — upstream signals "more pages" via non-null `next`.
    page_1 = {
        "rows": [
            {
                "citation": "2026 SGDC 136",
                "case_name": "Test v Test",
                "decision_date": "2026-01-01",
                "court": "SGDC",
                "source_url": "https://example.com/x",
                "summary": "stub",
            }
        ],
        "columns": ["citation", "case_name", "decision_date", "court", "source_url", "summary"],
        "next": "PAGE2_TOKEN",
        "truncated": False,
        "filtered_table_rows_count": 2,
    }
    # Second-page response — upstream signals "last page" via next=None.
    page_2 = {
        "rows": [
            {
                "citation": "2026 SGCA 99",
                "case_name": "Second v Second",
                "decision_date": "2026-02-02",
                "court": "SGCA",
                "source_url": "https://example.com/y",
                "summary": "stub",
            }
        ],
        "columns": ["citation", "case_name", "decision_date", "court", "source_url", "summary"],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 2,
    }
    # Both pages share the same /table.json prefix; pytest_httpx returns responses
    # in FIFO order on the same matcher, so register page_1 first, then page_2.
    httpx_mock.add_response(url=_table_url_re("zeeker-judgements", "judgments"), json=page_1)
    httpx_mock.add_response(url=_table_url_re("zeeker-judgements", "judgments"), json=page_2)

    # First call — issued without a cursor.
    env_1 = await query_table("zeeker-judgements", "judgments", sort="-decision_date")
    assert env_1.pagination is not None
    assert env_1.pagination.next_cursor is not None, "expected a next_cursor on page 1"
    cursor_1 = env_1.pagination.next_cursor

    # Second call — uses the cursor from page 1. Same sort/filters/columns
    # MUST be reused — the qhash digest is identical so decode_cursor succeeds.
    env_2 = await query_table(
        "zeeker-judgements",
        "judgments",
        sort="-decision_date",
        cursor=cursor_1,
    )
    assert env_2.pagination is not None
    assert env_2.pagination.next_cursor is None, "expected last page → next_cursor=None"
    # Second call should have surfaced the page-2 row.
    assert any(r.get("citation") == "2026 SGCA 99" for r in env_2.data), (
        "page 2 row missing from second call's envelope"
    )

    # The second upstream request MUST carry _next=PAGE2_TOKEN.
    table_reqs = _table_requests(httpx_mock, "zeeker-judgements", "judgments")
    assert len(table_reqs) == 2, f"expected 2 upstream table calls, got {len(table_reqs)}"
    assert table_reqs[1].url.params.get_list("_next") == ["PAGE2_TOKEN"], (
        f"second call must forward _next=PAGE2_TOKEN, got "
        f"{table_reqs[1].url.params.get_list('_next')}"
    )


async def test_truncated_passed_through(
    datasette_client, metadata_cache, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-12: when upstream sets truncated=True, the envelope surfaces it honestly.

    Phase 5 FRAG-04 wires the consumer side; Phase 3's contract is just to
    propagate the value without dropping it.
    """
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json={
            "rows": [
                {
                    "citation": "2026 SGDC 136",
                    "case_name": "Test v Test",
                    "decision_date": "2026-01-01",
                    "court": "SGDC",
                    "source_url": "https://example.com/x",
                    "summary": "stub",
                }
            ],
            "columns": ["citation", "case_name", "decision_date", "court", "source_url", "summary"],
            "next": None,
            "truncated": True,
            "filtered_table_rows_count": 1,
        },
    )

    envelope = await query_table("zeeker-judgements", "judgments")
    assert envelope.pagination is not None
    assert envelope.pagination.truncated is True


# ---------------------------------------------------------------------------
# QUERY-04 — all 13 filter ops translate end-to-end through the handler
# ---------------------------------------------------------------------------


def _pdpc_db_payload_with_penalty() -> dict:
    """pdpc payload — includes penalty_amount column for numeric ops."""
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


def _pdpc_schema_with_integer_penalty() -> dict:
    """`_zeeker_schemas` payload that types penalty_amount as INTEGER.

    The handler merges this with config.COLUMN_TYPES (upstream wins per D2-07);
    config has no COLUMN_TYPES entry for `pdpc.enforcement_decisions`, so this
    is the sole source of the INTEGER classification used by gt/gte/lt/lte
    numeric coercion.
    """
    import json as _json

    return {
        "columns": [
            "resource_name",
            "schema_version",
            "schema_hash",
            "column_definitions",
            "created_at",
            "updated_at",
        ],
        "rows": [
            [
                "enforcement_decisions",
                1,
                "abc",
                _json.dumps(
                    {
                        "title": "TEXT",
                        "organisation": "TEXT",
                        "decision_type": "TEXT",
                        "decision_date": "TEXT",
                        "decision_url": "TEXT",
                        "penalty_amount": "INTEGER",
                        "summary": "TEXT",
                    }
                ),
                "2026-01-01",
                "2026-01-01",
            ]
        ],
    }


def _pdpc_rows_payload() -> dict:
    return {
        "rows": [
            {
                "title": "Decision A",
                "organisation": "Org A",
                "decision_type": "Financial Penalty",
                "decision_date": "2026-01-01",
                "decision_url": "https://example.com/a",
                "penalty_amount": 5000,
                "summary": "stub",
            }
        ],
        "columns": [
            "title",
            "organisation",
            "decision_type",
            "decision_date",
            "decision_url",
            "penalty_amount",
            "summary",
        ],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 1,
    }


# Op -> (filter dict, expected_url_substring fragment).
# The substring is what we assert appears in the captured request URL's params.
# Numeric ops use penalty_amount (INTEGER); list ops use title (TEXT).
_THIRTEEN_OPS_CASES = [
    (
        "exact",
        {"column": "title", "op": "exact", "value": "Decision A"},
        "title__exact",
        "Decision A",
    ),
    ("not", {"column": "title", "op": "not", "value": "Decision A"}, "title__not", "Decision A"),
    (
        "contains",
        {"column": "title", "op": "contains", "value": "Decision"},
        "title__contains",
        "Decision",
    ),
    (
        "startswith",
        {"column": "title", "op": "startswith", "value": "Dec"},
        "title__startswith",
        "Dec",
    ),
    ("endswith", {"column": "title", "op": "endswith", "value": "A"}, "title__endswith", "A"),
    ("gt", {"column": "penalty_amount", "op": "gt", "value": 1000}, "penalty_amount__gt", "1000"),
    (
        "gte",
        {"column": "penalty_amount", "op": "gte", "value": 1000},
        "penalty_amount__gte",
        "1000",
    ),
    ("lt", {"column": "penalty_amount", "op": "lt", "value": 1000}, "penalty_amount__lt", "1000"),
    (
        "lte",
        {"column": "penalty_amount", "op": "lte", "value": 1000},
        "penalty_amount__lte",
        "1000",
    ),
    ("in", {"column": "title", "op": "in", "value": ["A", "B"]}, "title__in", "A,B"),
    ("notin", {"column": "title", "op": "notin", "value": ["A", "B"]}, "title__notin", "A,B"),
    (
        "isnull",
        {"column": "penalty_amount", "op": "isnull", "value": None},
        "penalty_amount__isnull",
        "1",
    ),
    (
        "notnull",
        {"column": "penalty_amount", "op": "notnull", "value": None},
        "penalty_amount__notnull",
        "1",
    ),
]


@pytest.mark.parametrize(
    "op_name, filter_clause, param_key, param_value",
    _THIRTEEN_OPS_CASES,
    ids=[c[0] for c in _THIRTEEN_OPS_CASES],
)
async def test_thirteen_ops_end_to_end(
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
    op_name,
    filter_clause,
    param_key,
    param_value,
) -> None:
    """QUERY-04: every one of the 13 ops compiles end-to-end through query_table.

    Asserts (1) no ToolError raised, (2) captured request URL carries the right
    `col__op=` param, (3) response is a properly wrapped Envelope. This is the
    handler-level companion of `test_filter_compiler.py` (which exercises the
    pure compiler in isolation).
    """
    from mcp_zeeker.core.envelope import Envelope

    httpx_mock.add_response(
        url=_db_url("pdpc"), json=_pdpc_db_payload_with_penalty(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"),
        json=_pdpc_schema_with_integer_penalty(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("pdpc", "enforcement_decisions"), json=_pdpc_rows_payload()
    )

    envelope = await query_table(
        "pdpc",
        "enforcement_decisions",
        filters=[filter_clause],
    )
    assert isinstance(envelope, Envelope), f"op={op_name}: handler must return Envelope"

    table_reqs = _table_requests(httpx_mock, "pdpc", "enforcement_decisions")
    assert len(table_reqs) == 1, f"op={op_name}: expected 1 upstream call, got {len(table_reqs)}"
    values = table_reqs[0].url.params.get_list(param_key)
    assert values == [param_value], (
        f"op={op_name}: expected ?{param_key}={param_value}, got {values}"
    )
