"""
Error-path tests for cross-DB search — GREEN (Plan 04-02 Task 2).

Tests every error branch in the search handler against the LOCKED Phase 4
error catalog (D3-12 / WR-02 / D4-09):
  - invalid_query   — empty/whitespace query, limit OOR, all-tables-400
  - unknown_database — databases=["not_a_db"]
  - upstream_unavailable — every per-table call fails with non-400 status

All-tables-400 promotion (04-RESEARCH §3.7 / D4-09 case c): when EVERY
per-table FTS call returns HTTP 400 (the captured
`zeeker_judgements__judgments__fts_error.json` body), the handler maps this
to `invalid_query` instead of `upstream_unavailable`. This is the defensive
catch for an FTS5 syntax error that escape_fts5 somehow missed — extremely
unlikely in practice but the safety net is required by D4-09.

Phase 2 LEARNING: transient-failure tests use EXPLICIT ORDERED
`add_response()` calls — the retry-once path (D-16) on 502/503 makes
reusable-response timing brittle. Status 500/400 surface immediately without
retry, but the discipline applies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "datasette" / "search"


def _fts_error_body() -> dict:
    """Captured 400 response body from a malformed FTS5 query against
    zeeker-judgements.judgments (research commit cb645bd)."""
    return json.loads((_FIXTURE_DIR / "zeeker_judgements__judgments__fts_error.json").read_text())


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _judgments_db_payload() -> dict:
    """Minimal /zeeker-judgements.json with one searchable table (judgments)."""
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
                    "source_url",
                    "summary",
                ],
                "primary_keys": ["id"],
                "fts_table": "judgments_fts",
            },
        ]
    }


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


async def test_empty_query_invalid_query(datasette_client) -> None:
    """D4-09 case (a) / D4-19 step 1: empty string → invalid_query."""
    from mcp_zeeker.tools.search import search

    with pytest.raises(ToolError, match="invalid_query"):
        await search(query="")


async def test_whitespace_query_invalid_query(datasette_client) -> None:
    """D4-09 case (a) / D4-19 step 1: whitespace-only string → invalid_query."""
    from mcp_zeeker.tools.search import search

    with pytest.raises(ToolError, match="invalid_query"):
        await search(query="   ")


async def test_limit_zero_invalid_query(datasette_client) -> None:
    """D4-09 case (b) / D4-11: limit=0 from a direct caller bypassing
    Pydantic's `ge=1` clamp → invalid_query (belt-and-suspenders)."""
    from mcp_zeeker.tools.search import search

    with pytest.raises(ToolError, match="invalid_query"):
        await search(query="appeal", limit=0)


async def test_limit_above_max_invalid_query(datasette_client) -> None:
    """D4-09 case (b) / D4-11: limit=101 → invalid_query (max is 100)."""
    from mcp_zeeker.tools.search import search

    with pytest.raises(ToolError, match="invalid_query"):
        await search(query="appeal", limit=101)


async def test_unknown_database(datasette_client) -> None:
    """D4-10: databases=["nonexistent"] raises ToolError("unknown_database: ...")."""
    from mcp_zeeker.tools.search import search

    with pytest.raises(ToolError, match="unknown_database"):
        await search(query="appeal", databases=["nonexistent"])


async def test_all_tables_400_invalid_query(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-09 case (c) / 04-RESEARCH §3.7: every per-table FTS call returns
    HTTP 400 → invalid_query.

    Uses the captured `zeeker_judgements__judgments__fts_error.json` body.
    Status 400 is NOT retried by _request_with_retry (only 502/503 are);
    one add_response call is sufficient.
    """
    from mcp_zeeker.tools.search import search

    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        status_code=400,
        json=_fts_error_body(),
    )

    with pytest.raises(ToolError, match="invalid_query"):
        await search(query="appeal", databases=["zeeker-judgements"])


async def test_all_tables_500_upstream_unavailable(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-09 / 04-RESEARCH §3.7: every per-table FTS call returns HTTP 500
    → upstream_unavailable (NOT invalid_query — only the all-400 path promotes)."""
    from mcp_zeeker.tools.search import search

    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        status_code=500,
        json={"error": "boom"},
    )

    with pytest.raises(ToolError, match="upstream_unavailable"):
        await search(query="appeal", databases=["zeeker-judgements"])
