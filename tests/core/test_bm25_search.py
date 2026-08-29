"""
Issue #12 Phase 1 — BM25-ranked search via owner-token SQL, behind a flag.

Covers the four new surfaces:
  1. `core.search.build_bm25_sql` — pure SQL builder: weight ordering in FTS
     column order, unknown-column weight default, non-numeric weight
     fallback, heavy-column exclusion, query-text-absent-from-SQL (INJ),
     LIMIT / params correctness, identifier quoting.
  2. `DatasetteClient.execute_sql` — URL/params shape (GET /{db}.json with
     sql + _shape=objects + one query param per named parameter) and the
     shared UpstreamCallFailed error contract.
  3. `_one_table` SQL path via `fan_out_search(..., fts_info=...)` —
     normalized 10-key rows with float `_score`, `_total` stripped and
     surfaced as upstream_total_hits, per-table failure captured not raised,
     INJ-05 (query text never in any mcp_zeeker log line).
  4. Feature flag — SEARCH_RANKING="legacy" keeps the old `_search=` table-
     view dispatch byte-identical (rows carry `_score: None`); "bm25" and
     "bm25_rrf" both route through the SQL path in Phase 1; config default
     is token-conditional ("bm25_rrf" iff ZEEKER_FULL_ACCESS_TOKEN set).

Follows the existing suite discipline: explicit ordered httpx_mock
add_response (no is_reusable on failure paths), local datasette_client
fixture, caplog scoped to mcp_zeeker loggers for the INJ-05 scan.
"""

from __future__ import annotations

import importlib
import logging
import re

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed
from mcp_zeeker.core.search import build_bm25_sql, fan_out_search

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

_PREVIEW_TU: dict[str, str | None] = {
    "title": "title",
    "date": None,
    "summary": None,
    "url": "source_url",
}


def _sql_url_re(database: str) -> re.Pattern[str]:
    """Regex matcher for the SQL endpoint GET /{database}.json?sql=..."""
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}\.json\?.*$")


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for the legacy table view /{database}/{table}.json?..."""
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _sql_rows_payload(rows: list[dict]) -> dict:
    """Minimal _shape=objects SQL-endpoint payload (no filtered_table_rows_count)."""
    return {"rows": rows, "columns": list(rows[0].keys()) if rows else [], "truncated": False}


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


# ---------------------------------------------------------------------------
# 1. build_bm25_sql — pure builder
# ---------------------------------------------------------------------------


def test_builder_weights_in_fts_column_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """bm25() weights are emitted one per indexed column, IN FTS COLUMN ORDER
    (positional), regardless of config dict ordering."""
    monkeypatch.setitem(config.SEARCH_BM25_WEIGHTS, "dbA.t1", {"summary": 5.0, "title": 10.0})
    sql, _params = build_bm25_sql(
        "dbA", "t1", "t1_fts", ["title", "summary"], _PREVIEW_TU, '"x"', 20
    )
    assert 'bm25("t1_fts", 10.0, 5.0)' in sql


def test_builder_unknown_column_weight_defaults_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Columns absent from the table's weight entry (and tables absent from
    SEARCH_BM25_WEIGHTS entirely) default to 1.0."""
    monkeypatch.setitem(config.SEARCH_BM25_WEIGHTS, "dbA.t1", {"title": 10.0})
    sql, _ = build_bm25_sql("dbA", "t1", "t1_fts", ["title", "mystery_col"], _PREVIEW_TU, '"x"', 20)
    assert 'bm25("t1_fts", 10.0, 1.0)' in sql

    # Table with no config entry at all → every weight 1.0.
    sql2, _ = build_bm25_sql("dbZ", "unlisted", "unlisted_fts", ["a", "b"], _PREVIEW_TU, '"x"', 20)
    assert 'bm25("unlisted_fts", 1.0, 1.0)' in sql2


def test_builder_non_numeric_weight_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-numeric (or bool) config weights are NEVER interpolated — they fall
    back to 1.0. Weights are the only config-sourced literals in the SQL."""
    monkeypatch.setitem(
        config.SEARCH_BM25_WEIGHTS,
        "dbA.t1",
        {"title": "10.0; DROP TABLE x", "summary": True},
    )
    sql, _ = build_bm25_sql("dbA", "t1", "t1_fts", ["title", "summary"], _PREVIEW_TU, '"x"', 20)
    assert 'bm25("t1_fts", 1.0, 1.0)' in sql
    assert "DROP TABLE" not in sql


def test_builder_heavy_columns_never_selected() -> None:
    """config.HEAVY_COLUMNS is filtered from the SELECT list defensively, even
    if a preview mapping smuggles one in (D3-04 defense-in-depth)."""
    heavy_preview: dict[str, str | None] = {
        "title": "title",
        "date": None,
        "summary": "full_text",  # heavy — must be dropped
        "url": "source_url",
    }
    sql, _ = build_bm25_sql("dbA", "t1", "t1_fts", ["title"], heavy_preview, '"x"', 20)
    for heavy in config.HEAVY_COLUMNS:
        assert f'"{heavy}"' not in sql, f"heavy column {heavy} leaked into SQL: {sql}"
    assert '"title"' in sql and '"source_url"' in sql


def test_builder_query_text_only_in_params() -> None:
    """INJ: the user query string appears ONLY in the params dict (bound as
    :search_query) — NEVER in the SQL text."""
    escaped = '"ZEEKER_CANARY_42" "second"'
    sql, params = build_bm25_sql("dbA", "t1", "t1_fts", ["title"], _PREVIEW_TU, escaped, 20)
    assert "ZEEKER_CANARY_42" not in sql
    assert ":search_query" in sql
    assert params == {"search_query": escaped}


def test_builder_shape_limit_and_join() -> None:
    """Structural contract: bm25 isolated in the innermost `_hits` SELECT
    (WR-260829 — a window function in the same SELECT as bm25 tears down the
    fts5 cursor context), MATCH bound param, window-function total at the
    `_ranked` level, ORDER BY _score ASC (bm25 is negative — best first),
    LIMIT literal, JOIN back to the content table on rowid, quoted
    identifiers."""
    sql, _ = build_bm25_sql(
        "zeeker-judgements",
        "judgments",
        "judgments_fts",
        ["case_name", "summary", "court_summary"],
        {"title": "case_name", "date": "decision_date", "summary": "summary", "url": "source_url"},
        '"privacy"',
        7,
    )
    # Real config weights for judgments: case_name 10.0, summary 5.0, court_summary 5.0.
    assert (
        'SELECT rowid AS _rid, bm25("judgments_fts", 10.0, 5.0, 5.0) AS _score '
        'FROM "judgments_fts" WHERE "judgments_fts" MATCH :search_query LIMIT -1' in sql
    )
    assert "count(*) OVER () AS _total" in sql
    # The window function must NOT share a SELECT level with the bm25 call.
    bm25_level = sql[sql.index("bm25(") : sql.index(") _hits")]
    assert "OVER ()" not in bm25_level
    assert 'JOIN "judgments" ON "judgments".rowid = _ranked._rid' in sql
    assert "ORDER BY _hits._score ASC LIMIT 7" in sql
    assert "ORDER BY _score ASC LIMIT 7" in sql
    # Citation-placeholder augmentation: judgments template references
    # {citation}/{court} beyond the preview columns — both selected.
    assert '"judgments"."citation"' in sql and '"judgments"."court"' in sql


def test_builder_empty_fts_columns_uses_default_weights() -> None:
    """Defensive: unknown fts column order → zero-arg bm25(<fts>) (default 1.0
    weights) rather than invalid SQL. Dispatch normally gates this out."""
    sql, _ = build_bm25_sql("dbA", "t1", "t1_fts", [], _PREVIEW_TU, '"x"', 20)
    assert 'bm25("t1_fts") AS _score' in sql


# ---------------------------------------------------------------------------
# 2. DatasetteClient.execute_sql
# ---------------------------------------------------------------------------


async def test_execute_sql_url_and_params_shape(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """GET /{db}.json with sql=, _shape=objects, and one query param per named
    SQL parameter."""
    httpx_mock.add_response(
        url=_sql_url_re("dbA"),
        json=_sql_rows_payload([{"title": "r1", "_score": -1.5, "_total": 3}]),
    )
    result = await datasette_client.execute_sql(
        "dbA", "SELECT 1 WHERE :search_query", {"search_query": '"x"'}
    )
    assert result["rows"][0]["title"] == "r1"

    (req,) = httpx_mock.get_requests()
    assert req.method == "GET"
    assert req.url.path == "/dbA.json"
    qp = dict(req.url.params)
    assert qp["sql"] == "SELECT 1 WHERE :search_query"
    assert qp["_shape"] == "objects"
    assert qp["search_query"] == '"x"'


async def test_execute_sql_error_contract(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """Non-2xx surfaces as UpstreamCallFailed with the status attached — same
    contract as get_table_rows (shared _request_with_retry policy)."""
    httpx_mock.add_response(url=_sql_url_re("dbA"), status_code=403, json={"error": "forbidden"})
    with pytest.raises(UpstreamCallFailed) as excinfo:
        await datasette_client.execute_sql("dbA", "SELECT 1", {})
    assert excinfo.value.status == 403


# ---------------------------------------------------------------------------
# 3. _one_table SQL path (driven through fan_out_search)
# ---------------------------------------------------------------------------

_FTS_INFO = {("dbA", "t1"): ("t1_fts", ["title", "summary"])}

_TEN_KEYS = {
    "title",
    "date",
    "summary",
    "url",
    "database",
    "table",
    "license",
    "license_url",
    "_citation",
    "_score",
}


async def test_sql_path_rows_normalized_with_score(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SQL path: normalized rows carry exactly the 10 keys, `_score` is the raw
    negative bm25 float, `_total` is stripped from rows and surfaced as the
    per-table upstream total."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25")
    httpx_mock.add_response(
        url=_sql_url_re("dbA"),
        json=_sql_rows_payload(
            [
                {"title": "best", "source_url": "https://e/1", "_score": -7.25, "_total": 42},
                {"title": "next", "source_url": "https://e/2", "_score": -3.5, "_total": 42},
            ]
        ),
    )

    rows, totals, failed, statuses = await fan_out_search(
        '"x"', [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6, fts_info=_FTS_INFO
    )

    assert failed == 0 and statuses == []
    assert [r["title"] for r in rows] == ["best", "next"]
    for r in rows:
        assert set(r.keys()) == _TEN_KEYS, f"row keys mismatch: {set(r.keys())}"
        assert "_total" not in r
    assert rows[0]["_score"] == pytest.approx(-7.25)
    assert isinstance(rows[0]["_score"], float)
    # Window-function count → upstream_total_hits, no second round-trip.
    assert totals == {"dbA.t1": 42}
    # Exactly ONE upstream request (single query carries rows + total).
    assert len(httpx_mock.get_requests()) == 1
    # The dispatch went to the SQL endpoint, not the table view.
    assert httpx_mock.get_requests()[0].url.path == "/dbA.json"


async def test_sql_path_zero_hit_total_is_zero(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero-hit SQL response → empty rows and total 0 for the table."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    httpx_mock.add_response(url=_sql_url_re("dbA"), json=_sql_rows_payload([]))
    rows, totals, failed, _ = await fan_out_search(
        '"x"', [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6, fts_info=_FTS_INFO
    )
    assert rows == [] and failed == 0
    assert totals == {"dbA.t1": 0}


async def test_sql_path_failure_captured_not_raised(
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-table SQL failure is captured (D4-07: fan_out_search NEVER raises)
    with the status surfaced for the all-400 → invalid_query mapping. INJ-05:
    the query string never appears in any mcp_zeeker log line."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25")
    httpx_mock.add_response(url=_sql_url_re("dbA"), status_code=400, json={"error": "fts5"})

    canary = "ZEEKER_CANARY_42 second_term"
    escaped = f'"{canary.split()[0]}" "{canary.split()[1]}"'
    # Patch the module-level structlog logger (test_search_side_channel
    # pattern) so the binding assertion works regardless of structlog config;
    # caplog additionally scans anything routed to stdlib logging.
    from unittest.mock import patch

    with (
        caplog.at_level(logging.DEBUG, logger="mcp_zeeker"),
        patch("mcp_zeeker.core.search.log.warning") as mock_warn,
    ):
        rows, totals, failed, statuses = await fan_out_search(
            escaped, [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6, fts_info=_FTS_INFO
        )

    assert rows == [] and totals == {}
    assert failed == 1 and statuses == [400]
    # INJ-05: the failure log fired, binding database/table/error_class only —
    # never the query string (neither as a binding value nor a kwarg name).
    fired = False
    for call in mock_warn.call_args_list:
        args, kwargs = call
        if args and args[0] == "search_table_failed":
            fired = True
            assert set(kwargs) == {"database", "table", "error_class"}
            assert "ZEEKER_CANARY_42" not in repr(call)
    assert fired, "expected a search_table_failed warning"
    # Belt-and-suspenders: nothing captured by stdlib logging carries the canary.
    log_text = " ".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("mcp_zeeker") or r.name == "root"
    )
    assert "ZEEKER_CANARY_42" not in log_text


# ---------------------------------------------------------------------------
# 4. Feature flag — legacy vs bm25 vs bm25_rrf
# ---------------------------------------------------------------------------


async def test_flag_legacy_keeps_table_view_dispatch(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEARCH_RANKING='legacy': even with fts_info supplied, dispatch stays on
    the old /{db}/{table}.json?_search= path and rows carry `_score: None`."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "legacy")
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        json={
            "rows": [{"title": "A1", "source_url": "https://e/a1"}],
            "columns": ["title", "source_url"],
            "next": None,
            "truncated": False,
            "filtered_table_rows_count": 9,
        },
    )

    rows, totals, failed, _ = await fan_out_search(
        '"x"', [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6, fts_info=_FTS_INFO
    )

    assert failed == 0
    (req,) = httpx_mock.get_requests()
    assert req.url.path == "/dbA/t1.json"
    qp = dict(req.url.params)
    assert qp["_search"] == '"x"'
    assert "sql" not in qp
    assert totals == {"dbA.t1": 9}
    assert rows[0]["_score"] is None
    assert set(rows[0].keys()) == _TEN_KEYS


@pytest.mark.parametrize("mode", ["bm25", "bm25_rrf"])
async def test_flag_bm25_modes_dispatch_sql(
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """Both bm25 modes dispatch identically — BM25-ordered per-table lists via
    the SQL endpoint. Phase 2 differentiates only the MERGE: bm25_rrf rows
    additionally carry `_fused_score` (see tests/core/test_rrf_merge.py for
    the merge-order routing tests)."""
    monkeypatch.setattr(config, "SEARCH_RANKING", mode)
    httpx_mock.add_response(
        url=_sql_url_re("dbA"),
        json=_sql_rows_payload(
            [{"title": "A1", "source_url": "https://e/a1", "_score": -2.0, "_total": 1}]
        ),
    )
    rows, totals, failed, _ = await fan_out_search(
        '"x"', [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6, fts_info=_FTS_INFO
    )
    assert failed == 0
    (req,) = httpx_mock.get_requests()
    assert req.url.path == "/dbA.json"
    assert "sql" in dict(req.url.params)
    assert rows[0]["_score"] == pytest.approx(-2.0)
    assert totals == {"dbA.t1": 1}


async def test_flag_bm25_without_fts_info_falls_back_to_legacy(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive: bm25 mode WITHOUT fts discovery metadata for a table (or the
    pre-#12 fan_out_search call shape) falls back to the legacy dispatch."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25")
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        json={
            "rows": [{"title": "A1", "source_url": "https://e/a1"}],
            "columns": ["title", "source_url"],
            "next": None,
            "truncated": False,
            "filtered_table_rows_count": 1,
        },
    )
    rows, _totals, failed, _ = await fan_out_search(
        '"x"', [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6, fts_info=None
    )
    assert failed == 0
    (req,) = httpx_mock.get_requests()
    assert req.url.path == "/dbA/t1.json"
    assert rows[0]["_score"] is None


# ---------------------------------------------------------------------------
# 5. Discovery — fts indexed-column derivation + handler end-to-end
# ---------------------------------------------------------------------------


async def test_discovery_derives_fts_columns_in_order() -> None:
    """searchable_tables_for derives the fts indexed-column ORDER from the
    same DatabaseSummary (fts sidecar columns minus the pseudo-columns named
    <fts_table> and 'rank') — no extra upstream round-trip."""
    from mcp_zeeker.core.datasette_client import DatabaseSummary
    from mcp_zeeker.core.search import searchable_tables_for

    summary = DatabaseSummary.model_validate(
        {
            "tables": [
                {
                    "name": "judgments",
                    "hidden": False,
                    "columns": ["id", "case_name", "decision_date", "summary", "source_url"],
                    "fts_table": "judgments_fts",
                },
                {
                    "name": "judgments_fts",
                    "hidden": True,
                    # Upstream order: indexed cols first, then the two
                    # pseudo-columns (<fts_table> itself and 'rank').
                    "columns": [
                        "case_name",
                        "summary",
                        "court_summary",
                        "judgments_fts",
                        "rank",
                    ],
                    "fts_table": "judgments_fts",
                },
            ]
        }
    )
    out = await searchable_tables_for("zeeker-judgements", summary=summary, visible={"judgments"})
    assert len(out) == 1
    table, preview, fts_table, fts_columns = out[0]
    assert table == "judgments"
    assert preview["title"] == "case_name"
    assert fts_table == "judgments_fts"
    assert fts_columns == ["case_name", "summary", "court_summary"]


async def test_handler_end_to_end_bm25(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the search handler in bm25_rrf mode: discovery
    derives the fts metadata, the term-level escape binds as search_query,
    dispatch hits the SQL endpoint, and envelope rows carry `_score`."""
    from mcp_zeeker.tools.search import search

    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    base = config.UPSTREAM_URL.rstrip("/")
    httpx_mock.add_response(
        url=f"{base}/zeeker-judgements.json",
        json={
            "tables": [
                {
                    "name": "judgments",
                    "hidden": False,
                    "columns": ["id", "case_name", "decision_date", "summary", "source_url"],
                    "fts_table": "judgments_fts",
                },
                {
                    "name": "judgments_fts",
                    "hidden": True,
                    "columns": [
                        "case_name",
                        "summary",
                        "court_summary",
                        "judgments_fts",
                        "rank",
                    ],
                    "fts_table": "judgments_fts",
                },
            ]
        },
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_sql_url_re("zeeker-judgements"),
        json=_sql_rows_payload(
            [
                {
                    "case_name": "Re Privacy",
                    "decision_date": "2026-01-01",
                    "summary": "s",
                    "source_url": "https://e/1",
                    "citation": "[2026] SGHC 1",
                    "court": "SGHC",
                    "_score": -4.0,
                    "_total": 12,
                }
            ]
        ),
    )

    envelope = await search(query="privacy law", databases=["zeeker-judgements"])

    sql_reqs = [r for r in httpx_mock.get_requests() if "sql" in dict(r.url.params)]
    assert len(sql_reqs) == 1
    qp = dict(sql_reqs[0].url.params)
    # Phrase-intent: unquoted multi-term query → term-level escape.
    assert qp["search_query"] == '"privacy" "law"'
    assert "bm25(" in qp["sql"]
    # User text absent from the SQL string itself (bound param only).
    assert "privacy" not in qp["sql"]

    assert len(envelope.data) == 1
    row = envelope.data[0]
    assert row["_score"] == pytest.approx(-4.0)
    # Issue #12 Phase 2: the bm25_rrf merge path attaches `_fused_score` —
    # 11th row key on this mode. Single list, rank 1 → 1/(60+1).
    assert row["_fused_score"] == pytest.approx(1 / 61)
    assert row["title"] == "Re Privacy"
    assert envelope.pagination.upstream_total_hits == {"zeeker-judgements.judgments": 12}


async def test_handler_legacy_mode_keeps_phrase_escape(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEARCH_RANKING='legacy' is the rollback path and the eval's A0 baseline:
    the handler must reproduce the pre-#12 dispatch byte-identically —
    escape_fts5 phrase wrap of the WHOLE query (adjacency required), NOT the
    issue-#12 term-level escape ('"privacy" "law"')."""
    from mcp_zeeker.tools.search import search

    monkeypatch.setattr(config, "SEARCH_RANKING", "legacy")
    base = config.UPSTREAM_URL.rstrip("/")
    httpx_mock.add_response(
        url=f"{base}/zeeker-judgements.json",
        json={
            "tables": [
                {
                    "name": "judgments",
                    "hidden": False,
                    "columns": ["id", "case_name", "decision_date", "summary", "source_url"],
                    "fts_table": "judgments_fts",
                },
            ]
        },
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json={"rows": [], "filtered_table_rows_count": 0},
    )

    await search(query="privacy law", databases=["zeeker-judgements"])

    table_reqs = [r for r in httpx_mock.get_requests() if "/judgments.json" in r.url.path]
    assert len(table_reqs) == 1
    qp = dict(table_reqs[0].url.params)
    # Pre-#12 escape contract: one phrase wrap around the whole query.
    assert qp["_search"] == '"privacy law"'
    # And no SQL-endpoint dispatch happened in legacy mode.
    assert not any("sql" in dict(r.url.params) for r in httpx_mock.get_requests())


def test_search_ranking_default_is_token_conditional(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config default: 'bm25_rrf' when the owner token is set (SQL path
    available), 'legacy' otherwise (anonymous ?sql= is 403 upstream). Env var
    SEARCH_RANKING overrides both."""
    import mcp_zeeker.config as cfg_module

    try:
        monkeypatch.delenv("SEARCH_RANKING", raising=False)
        monkeypatch.setenv("ZEEKER_FULL_ACCESS_TOKEN", "tok-123")
        importlib.reload(cfg_module)
        assert cfg_module.SEARCH_RANKING == "bm25_rrf"

        monkeypatch.delenv("ZEEKER_FULL_ACCESS_TOKEN", raising=False)
        importlib.reload(cfg_module)
        assert cfg_module.SEARCH_RANKING == "legacy"

        monkeypatch.setenv("SEARCH_RANKING", "legacy")
        monkeypatch.setenv("ZEEKER_FULL_ACCESS_TOKEN", "tok-123")
        importlib.reload(cfg_module)
        assert cfg_module.SEARCH_RANKING == "legacy"
    finally:
        # Restore the module to the ambient-env state for the rest of the suite.
        monkeypatch.delenv("SEARCH_RANKING", raising=False)
        monkeypatch.delenv("ZEEKER_FULL_ACCESS_TOKEN", raising=False)
        importlib.reload(cfg_module)
