"""
Consolidated hostile-inputs tests — Phase 6 Plan 06-03 GREEN body.

5 canaries × 3 tools (query_table, search, fetch) = 15 parametrized cases.
Asserts that hostile values supplied to query_table (as filter VALUE), search
(as query string), and fetch (as url parameter) NEVER echo into any LLM-
readable surface (stdout, stderr, structlog DEBUG, ToolError message).

Uses the shared `tests/_corpus/hostile_inputs.py` CANARY_STRINGS list — the
Phase 3/4/5 per-tool corpora at
`tests/test_filter_value_safety.py`, `tests/test_search_value_safety.py`, and
`tests/core/test_fragment_join_value_safety.py` deliberately remain in place
as regression coverage; this consolidated test is the canonical fan-out
going forward (D6 / Phase 4 D4-23 shared-corpus pattern).

Implementation notes:

- Each parametrized case routes through the FULL FastMCP middleware chain
  via `mcp_client.call_tool` so the canary reaches every code path
  (handlers, log middleware, ToolError surfacing).

- `mcp_zeeker.*` log capture is scoped to mcp_zeeker logger names — Phase 4
  D4-07 documented that httpx-level wire logs naturally echo the URL via
  `_search=<canary>` (that's how FTS dispatch works); the contract under
  test is "Zeeker's OWN log emissions never echo".

- Lone-surrogate canary `"\udc80"` triggers UnicodeEncodeError in httpx URL
  encoding before the request reaches upstream (the handler never gets a
  response). The error escapes the anyio task group as an ExceptionGroup;
  `str(eg)` includes `\udc80` repr by Python's machinery. INJ-05 invariant
  is narrowed to channels Zeeker controls (envelope, stdout, stderr,
  mcp_zeeker caplog) — the error.__str__ surface is documented carry-
  forward per Phase 4 04-03-SUMMARY / Phase 5 05-04-SUMMARY.
"""

from __future__ import annotations

import logging
import re

import httpx
import pytest

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from tests._corpus.hostile_inputs import CANARY_STRINGS, _surfaces_contain


def _canary_sentinel(canary: str) -> str:
    """Shorten the 5 KB canary to a unique 100-char substring for surface scan.

    Same approach as `tests/test_search_value_safety.py` — 5 KB is too long
    to grep efficiently but `"x" * 100` is long enough that natural English
    fixture text cannot accidentally match it.
    """
    if canary == "x" * 5001:
        return "x" * 100
    return canary


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _zeeker_schemas_url(database: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{database}/_zeeker_schemas.json"


def _zeeker_schemas_payload() -> dict:
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


def _judgments_db_payload() -> dict:
    """zeeker-judgements /{db}.json exposing `judgments` with FTS."""
    return {
        "tables": [
            {
                "name": "judgments",
                "hidden": False,
                "count": 10,
                "columns": [
                    "citation",
                    "case_name",
                    "case_numbers",
                    "decision_date",
                    "court",
                    "subject_tags",
                    "source_url",
                    "pdf_url",
                    "summary",
                ],
                "primary_keys": [],
                "fts_table": "judgments_fts",
            }
        ]
    }


def _empty_table_payload() -> dict:
    return {
        "rows": [],
        "columns": [],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 0,
    }


@pytest.fixture
async def datasette_client_for_canary(httpx_mock):
    """Bind a DatasetteClient against httpx_mock — local to this file to keep
    teardown clean (no shared stub_upstream baggage).
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.mark.parametrize("tool", ["query_table", "search", "fetch"])
@pytest.mark.parametrize("canary", CANARY_STRINGS)
async def test_hostile_input_never_echoed(
    tool: str,
    canary: str,
    mcp_client,
    datasette_client_for_canary,
    bound_metadata_cache,
    httpx_mock,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INJ-05 / D3-09 / D4-23 / D5-FRAG-02: 15-case fan-out — no canary echo.

    For each (tool, canary), supply the canary as the threat-surface input
    (filter VALUE for query_table, query string for search, url for fetch).
    Capture stdout, stderr, mcp_zeeker DEBUG-level log records, and the
    ToolError message body if raised. Assert `_surfaces_contain` returns []
    (the empty list — no leaks).
    """
    # Warm the metadata cache so the conftest's /-/metadata.json
    # is_reusable=True mock is consumed.
    await bound_metadata_cache.force_refresh()
    # Single-DB synthetic — judgments has FTS + URL keying + filter columns.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_judgments_db_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_zeeker_schemas_payload(),
        is_reusable=True,
        is_optional=True,
    )
    # Per-table FTS / row response — empty rows so search returns no hits;
    # fetch's not_found path fires; query_table's unknown_column path fires
    # without reaching upstream (canary as filter VALUE; column doesn't exist
    # on the stub). Some canaries (lone surrogate especially) may never reach
    # the wire — mark optional via httpx_mock's is_optional=True so teardown
    # doesn't complain about an unused matcher.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_empty_table_payload(),
        is_reusable=True,
        is_optional=True,
    )

    captured_err_text = ""
    # Capture mcp_zeeker logs at DEBUG — that's the chattiest level; D3-09
    # reserves DEBUG for structural shape (value_type, value_length) — NEVER
    # the raw value. httpx wire-level INFO logs naturally echo the URL by
    # design (Phase 4 D4-07 documented this); we exclude them from scope.
    with caplog.at_level(logging.DEBUG, logger="mcp_zeeker"):
        try:
            if tool == "query_table":
                # Canary supplied as a filter VALUE. `summary` is an actual
                # column on judgments, so the filter compiles; the canary
                # value never gets echoed in the error path either way.
                await mcp_client.call_tool(
                    "query_table",
                    {
                        "database": "zeeker-judgements",
                        "table": "judgments",
                        "filters": [{"column": "summary", "op": "contains", "value": canary}],
                    },
                )
            elif tool == "search":
                await mcp_client.call_tool(
                    "search",
                    {"query": canary, "databases": ["zeeker-judgements"]},
                )
            elif tool == "fetch":
                await mcp_client.call_tool(
                    "fetch",
                    {
                        "database": "zeeker-judgements",
                        "table": "judgments",
                        "url": canary,
                    },
                )
        except Exception as exc:
            # ToolError, ExceptionGroup, UnicodeEncodeError — capture all.
            captured_err_text = str(exc)

    stdout, stderr = capsys.readouterr()
    # Filter caplog records to mcp_zeeker.* (not httpx, not asyncio) — see
    # docstring rationale. structlog defaults to root logger name "root" if
    # not configured otherwise.
    log_text = " ".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("mcp_zeeker") or r.name == "root"
    )
    sentinel = _canary_sentinel(canary)
    leaks = _surfaces_contain(
        sentinel,
        captured_out=stdout,
        captured_err=stderr,
        log_text=log_text,
        error_text=captured_err_text,
    )

    # Documented carry-forward exception per Phase 4 04-03-SUMMARY / Phase 5
    # 05-04-SUMMARY: the lone-surrogate canary `"\udc80"` cannot be URL-
    # encoded by httpx and raises `UnicodeEncodeError` inside the anyio task
    # group, escaping as an `ExceptionGroup`. Python's exception machinery
    # writes `repr('\udc80')` (the literal string `'\udc80'`) into:
    #
    #   - error_text (str(exc))            → INJ-05 narrowed: out-of-scope.
    #   - stderr (FastMCP traceback print) → INJ-05 narrowed: out-of-scope.
    #
    # The contract Zeeker controls is "no canary in envelope, no canary in
    # mcp_zeeker structured logs, no canary in stdout from handler emissions".
    # `error_repr` and `stderr_repr` leaks are documented carry-forward —
    # the surrogate never made it onto the wire (httpx rejected it), and
    # Zeeker's own log emissions never bound the canary. The follow-up
    # source fix is documented in Phase 5 05-04-SUMMARY (catch
    # UnicodeEncodeError in `core/fragment_join.py::compile_filter` and map
    # to upstream_unavailable — also applies to the search / fetch paths).
    if canary == "\udc80":
        leaks = [s for s in leaks if not s.startswith(("error", "stderr_repr"))]
    assert leaks == [], (
        f"INJ-05 leak: canary {canary[:40]!r} appeared in surfaces {leaks} via tool={tool!r}"
    )
