"""
Unit tests for query_table tool handler — Slice A (Plan 03-02).

Covers happy-path REQs:
- QUERY-01: filter / sort / pagination knobs translate to Datasette URL params
- QUERY-02: default rows carry the light set only (no heavy columns, no rowid)
- QUERY-07: limit defaults to 50, max 200; 201 rejected before any upstream call
- QUERY-10: contains / startswith / endswith documented as case-insensitive

Heavy-column opt-in (QUERY-03) and qhash cursor walk (QUERY-04) ship in Plan
03-03 — those test files remain RED in Plan 03-02 by design.

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
