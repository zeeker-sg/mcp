"""
Counter-patch tests for sole-emission helpers in search — GREEN (Plan 04-02).

Mirrors `tests/tools/test_retrieval_side_channel.py` pattern at the search
handler level. Proves CODE-PATH IDENTITY: every `invalid_query` trigger
routes through the SAME `raise_invalid_query` helper — no inline ToolError
strings.

The patch target is `mcp_zeeker.tools.search.raise_invalid_query` (NOT the
visibility module) because Python's `unittest.mock.patch` rewrites the
binding at the import site.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.visibility import raise_invalid_query


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


async def test_invalid_query_single_emission(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-09 / WR-02: every invalid_query path goes through `raise_invalid_query`.

    Counter-patches `mcp_zeeker.tools.search.raise_invalid_query` and asserts
    the counter increments exactly 4 times for the 4 trigger paths:
      1. query=""           — D4-19 step 1 empty-query guard
      2. query="   "        — same guard (strip())
      3. limit=0            — D4-11 belt-and-suspenders
      4. limit=101          — D4-11 belt-and-suspenders
    """
    from mcp_zeeker.tools.search import search

    counter = {"n": 0}
    original = raise_invalid_query

    def counting() -> None:
        counter["n"] += 1
        original()

    with patch("mcp_zeeker.tools.search.raise_invalid_query", counting):
        with pytest.raises(ToolError, match="invalid_query"):
            await search(query="")
        with pytest.raises(ToolError, match="invalid_query"):
            await search(query="   ")
        with pytest.raises(ToolError, match="invalid_query"):
            await search(query="appeal", limit=0)
        with pytest.raises(ToolError, match="invalid_query"):
            await search(query="appeal", limit=101)

    assert counter["n"] == 4, (
        f"Expected 4 raise_invalid_query calls (one per trigger path), got {counter['n']}"
    )


def _odd_db_payload() -> dict:
    """A DB whose only FTS-indexed table has columns matching NO preview defaults.

    Forces resolve_preview_columns to return None → searchable_tables_for emits
    `search_table_no_preview_columns` warning and skips the table.
    """
    return {
        "tables": [
            {
                "name": "weird_table",
                "hidden": False,
                "count": 0,
                "columns": ["weird_col_1", "weird_col_2", "weird_col_3"],
                "primary_keys": [],
                "fts_table": "weird_table_fts",
            },
        ]
    }


async def test_no_preview_columns_log_emitted(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """CONTEXT line 338 / D4-12: searchable_tables_for emits a structured
    `search_table_no_preview_columns` warning when a discovered FTS-indexed
    table fails resolve_preview_columns.

    Patches the module-level structlog `log.warning` to capture the call.
    """
    from mcp_zeeker.tools.search import search

    # Single DB scope so the test doesn't depend on multi-DB fixtures.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_odd_db_payload(),
        is_reusable=True,
    )

    with patch("mcp_zeeker.core.search.log.warning") as mock_warn:
        envelope = await search(query="appeal", databases=["zeeker-judgements"])

    # No table was searchable; envelope is empty.
    assert envelope.data == []
    # Assert the structured warning fired with the expected event name and bindings.
    # Bindings expose database + table only — never the query (INJ-05).
    fired = False
    for call in mock_warn.call_args_list:
        args, kwargs = call
        event = args[0] if args else None
        if event == "search_table_no_preview_columns":
            fired = True
            assert kwargs.get("database") == "zeeker-judgements"
            assert kwargs.get("table") == "weird_table"
            # No query string bound.
            assert "query" not in kwargs
            assert "search" not in kwargs  # defensive — kwarg name like "search_query"
    assert fired, "expected at least one search_table_no_preview_columns warning"
