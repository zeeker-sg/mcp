"""
Issue #12 Phase 3 — fragment passage search (MaxP rollup feeding RRF).

Covers the four new surfaces:
  1. `core.search.build_maxp_sql` — pure MaxP SQL builder: fragment-fts join /
     GROUP BY parent pk / per-source link-column correctness, MIN(bm25(...))
     rollup, weight lookup keyed on the FRAGMENT table, heavy-column
     exclusion, query-text-absent-from-SQL (INJ), zero-arg bm25 fallback.
  2. `core.search.fragment_sources_for` — discovery gates: legacy mode returns
     [] unconditionally; fragment fts must exist upstream; fragment table must
     be visible; the PARENT must be present, visible, and preview-resolvable.
  3. Fan-out + fusion — a fragment list participates in the RRF merge as
     PARENT rows (database=<db>, table=<parent>): the same url found via the
     parent table AND via its body fragments fuses upward and outranks
     single-list docs; per-source upstream totals use the distinct
     "<db>.<fragment_table>" key; legacy mode dispatches NO fragment queries;
     fragment failures are captured not raised, with INJ-05 log bindings.
  4. Handler end-to-end — a body-only-indexed corpus (parent WITHOUT its own
     fts) still fans out via its fragment source and surfaces parent rows.

Follows the suite discipline from tests/core/test_bm25_search.py /
test_rrf_merge.py: explicit ordered httpx_mock add_response, local
datasette_client fixture, distinct databases per concurrent SQL dispatch so
the URL matcher is unambiguous, counter-patch pattern for log-binding scans.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import patch

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatabaseSummary, DatasetteClient
from mcp_zeeker.core.search import (
    FragmentSource,
    build_maxp_sql,
    fan_out_search,
    fragment_sources_for,
)

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


def _sql_rows_payload(rows: list[dict]) -> dict:
    """Minimal _shape=objects SQL-endpoint payload."""
    return {"rows": rows, "columns": list(rows[0].keys()) if rows else [], "truncated": False}


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _judgments_source() -> FragmentSource:
    """The canonical live source: judgments_fragments → judgments."""
    return FragmentSource(
        fragment_table="judgments_fragments",
        fragment_fts="judgments_fragments_fts",
        fragment_fts_columns=["content_text"],
        parent_table="judgments",
        parent_link="judgment_id",
        parent_key="id",
        preview={
            "title": "case_name",
            "date": "decision_date",
            "summary": "summary",
            "url": "source_url",
        },
    )


def _generic_source(preview: dict[str, str | None] | None = None) -> FragmentSource:
    """Config-independent source for fan-out tests (docs_fragments → docs)."""
    return FragmentSource(
        fragment_table="docs_fragments",
        fragment_fts="docs_fragments_fts",
        fragment_fts_columns=["text"],
        parent_table="docs",
        parent_link="parent_id",
        parent_key="id",
        preview=preview or dict(_PREVIEW_TU),
    )


# ---------------------------------------------------------------------------
# 1. build_maxp_sql — pure builder
# ---------------------------------------------------------------------------


def test_maxp_builder_shape_join_group_by() -> None:
    """Structural contract: fragment fts JOIN fragment table ON rowid, JOIN
    parent on pk=link, MATCH bound param, GROUP BY parent pk,
    MIN(bm25(...)) AS _score (MaxP — bm25 is negative, MIN = best passage),
    window-function total, ORDER BY _score ASC, LIMIT literal."""
    sql, params = build_maxp_sql("zeeker-judgements", _judgments_source(), '"privacy"', 5)

    # No weights configured for the fragment table → single indexed body
    # column defaults to 1.0.
    assert 'MIN(bm25("judgments_fragments_fts", 1.0)) AS _score' in sql
    assert "count(*) OVER () AS _total" in sql
    assert (
        'FROM "judgments_fragments_fts" '
        'JOIN "judgments_fragments" fr '
        'ON fr.rowid = "judgments_fragments_fts".rowid' in sql
    )
    assert 'JOIN "judgments" p ON p."id" = fr."judgment_id"' in sql
    assert 'WHERE "judgments_fragments_fts" MATCH :search_query' in sql
    assert 'GROUP BY p."id"' in sql
    assert "ORDER BY _score ASC LIMIT 5" in sql
    assert params == {"search_query": '"privacy"'}
    # Preview + citation-placeholder columns resolve against the PARENT:
    # the judgments template references {citation}/{court} beyond the preview.
    assert 'p."case_name"' in sql and 'p."source_url"' in sql
    assert 'p."citation"' in sql and 'p."court"' in sql


@pytest.mark.parametrize("key", sorted(config.SEARCH_FRAGMENT_SOURCES))
def test_maxp_builder_link_column_per_configured_source(key: str) -> None:
    """Every configured fragment source produces a join on ITS declared
    parent_link / parent_key columns (the live-verified link columns)."""
    db, _, frag_table = key.partition(".")
    spec = config.SEARCH_FRAGMENT_SOURCES[key]
    source = FragmentSource(
        fragment_table=frag_table,
        fragment_fts=f"{frag_table}_fts",
        fragment_fts_columns=["text"],
        parent_table=spec["parent_table"],
        parent_link=spec["parent_link"],
        parent_key=spec["parent_key"],
        preview=dict(_PREVIEW_TU),
    )
    sql, _ = build_maxp_sql(db, source, '"x"', 20)
    parent = spec["parent_table"]
    assert f'JOIN "{parent}" p ON p."{spec["parent_key"]}" = fr."{spec["parent_link"]}"' in sql
    assert f'GROUP BY p."{spec["parent_key"]}"' in sql


def test_maxp_builder_heavy_columns_never_selected() -> None:
    """config.HEAVY_COLUMNS is filtered from the SELECT list defensively, even
    if the parent preview smuggles one in (D3-04 defense-in-depth). The
    fragment body columns (text / content_text) are matched via the fts index
    only — never selected by name."""
    smuggled = _generic_source(
        preview={
            "title": "title",
            "date": None,
            "summary": "full_text",  # heavy — must be dropped
            "url": "source_url",
        }
    )
    sql, _ = build_maxp_sql("dbA", smuggled, '"x"', 20)
    for heavy in config.HEAVY_COLUMNS:
        assert f'"{heavy}"' not in sql, f"heavy column {heavy} leaked into SQL: {sql}"
    assert 'p."title"' in sql and 'p."source_url"' in sql


def test_maxp_builder_query_text_only_in_params() -> None:
    """INJ: the user query string appears ONLY in the params dict (bound as
    :search_query) — NEVER in the SQL text."""
    escaped = '"ZEEKER_CANARY_42" "second"'
    sql, params = build_maxp_sql("dbA", _generic_source(), escaped, 20)
    assert "ZEEKER_CANARY_42" not in sql
    assert ":search_query" in sql
    assert params == {"search_query": escaped}


def test_maxp_builder_weights_keyed_on_fragment_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weight lookup key is '<db>.<fragment_table>' (NOT the parent) and
    weights are emitted in FTS column order; non-numeric values fall back to
    1.0 and are NEVER interpolated."""
    monkeypatch.setitem(
        config.SEARCH_BM25_WEIGHTS,
        "dbA.res_fragments",
        {"heading": "4.0; DROP TABLE x", "text": 2.0},
    )
    source = FragmentSource(
        fragment_table="res_fragments",
        fragment_fts="res_fragments_fts",
        fragment_fts_columns=["heading", "text"],  # two-column fragment fts
        parent_table="res",
        parent_link="parent_id",
        parent_key="id",
        preview=dict(_PREVIEW_TU),
    )
    sql, _ = build_maxp_sql("dbA", source, '"x"', 20)
    assert 'MIN(bm25("res_fragments_fts", 1.0, 2.0)) AS _score' in sql
    assert "DROP TABLE" not in sql


def test_maxp_builder_empty_fts_columns_uses_default_weights() -> None:
    """Defensive: fts sidecar absent from the summary → [] columns → zero-arg
    bm25(<fts>) (default 1.0 weights) rather than invalid SQL."""
    source = FragmentSource(
        fragment_table="docs_fragments",
        fragment_fts="docs_fragments_fts",
        fragment_fts_columns=[],
        parent_table="docs",
        parent_link="parent_id",
        parent_key="id",
        preview=dict(_PREVIEW_TU),
    )
    sql, _ = build_maxp_sql("dbA", source, '"x"', 20)
    assert 'MIN(bm25("docs_fragments_fts")) AS _score' in sql


# ---------------------------------------------------------------------------
# 2. fragment_sources_for — discovery gates
# ---------------------------------------------------------------------------


def _judgments_summary(
    *,
    parent_fts: str | None = None,
    frag_fts: str | None = "judgments_fragments_fts",
    include_fragment_table: bool = True,
    parent_columns: list[str] | None = None,
) -> DatabaseSummary:
    """Summary fixture mirroring the live zeeker-judgements shape."""
    tables: list[dict] = [
        {
            "name": "judgments",
            "hidden": False,
            "columns": parent_columns
            or ["id", "case_name", "decision_date", "summary", "source_url"],
            "fts_table": parent_fts,
        }
    ]
    if include_fragment_table:
        tables.append(
            {
                "name": "judgments_fragments",
                "hidden": False,
                "columns": ["id", "judgment_id", "ordinal", "content_text"],
                "fts_table": frag_fts,
            }
        )
    if frag_fts is not None:
        tables.append(
            {
                "name": frag_fts,
                "hidden": True,
                "columns": ["content_text", frag_fts, "rank"],
                "fts_table": frag_fts,
            }
        )
    return DatabaseSummary.model_validate({"tables": tables})


_BOTH_VISIBLE = {"judgments", "judgments_fragments"}


async def test_fragment_discovery_non_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-legacy mode: the configured judgments source is discovered with the
    parent-resolved preview and the fts indexed-column order from the SAME
    summary (no extra round-trips)."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    out = await fragment_sources_for(
        "zeeker-judgements", summary=_judgments_summary(), visible=_BOTH_VISIBLE
    )
    assert len(out) == 1
    src = out[0]
    assert src.fragment_table == "judgments_fragments"
    assert src.fragment_fts == "judgments_fragments_fts"
    assert src.fragment_fts_columns == ["content_text"]
    assert src.parent_table == "judgments"
    assert src.parent_link == "judgment_id"
    assert src.parent_key == "id"
    # Preview resolved against the PARENT columns (D4-12).
    assert src.preview["title"] == "case_name"
    assert src.preview["url"] == "source_url"


async def test_fragment_discovery_legacy_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy mode: NO fragment sources, unconditionally — the pre-#12
    denylist behavior is unchanged (anonymous ?sql= is 403 upstream)."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "legacy")
    out = await fragment_sources_for(
        "zeeker-judgements", summary=_judgments_summary(), visible=_BOTH_VISIBLE
    )
    assert out == []


async def test_fragment_discovery_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each discovery gate independently excludes the source: fragment table
    absent / fragment fts missing / fragment table hidden / parent not
    visible / parent preview unresolvable."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")

    # Fragment table absent from the summary entirely.
    out = await fragment_sources_for(
        "zeeker-judgements",
        summary=_judgments_summary(include_fragment_table=False),
        visible=_BOTH_VISIBLE,
    )
    assert out == []

    # Fragment table present but WITHOUT an fts index upstream.
    out = await fragment_sources_for(
        "zeeker-judgements",
        summary=_judgments_summary(frag_fts=None),
        visible=_BOTH_VISIBLE,
    )
    assert out == []

    # Fragment table hidden (HIDDEN_TABLES / hidden flag → not in visible).
    out = await fragment_sources_for(
        "zeeker-judgements", summary=_judgments_summary(), visible={"judgments"}
    )
    assert out == []

    # Parent not visible.
    out = await fragment_sources_for(
        "zeeker-judgements",
        summary=_judgments_summary(),
        visible={"judgments_fragments"},
    )
    assert out == []

    # Parent preview unresolvable (no url-candidate column) → dropped with
    # the search_fragment_parent_no_preview warning (database/table only).
    with patch("mcp_zeeker.core.search.log.warning") as mock_warn:
        out = await fragment_sources_for(
            "zeeker-judgements",
            summary=_judgments_summary(
                parent_columns=["id", "case_name", "decision_date", "summary"]
            ),
            visible=_BOTH_VISIBLE,
        )
    assert out == []
    fired = [
        c for c in mock_warn.call_args_list if c.args[:1] == ("search_fragment_parent_no_preview",)
    ]
    assert fired and set(fired[0].kwargs) == {"database", "table"}


# ---------------------------------------------------------------------------
# 3. Fan-out + fusion
# ---------------------------------------------------------------------------


async def test_fusion_parent_and_fragment_lists_merge(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A document surfacing from BOTH a parent-table list and a fragment
    (passage) list fuses by url and outranks single-list docs; fragment rows
    ARE parent rows; totals use the distinct '<db>.<fragment_table>' key.

    dbA.t1 (table list):        [XA url=x @rank1]           — 1/61
    dbB docs_fragments (MaxP):  [XP url=x @rank1, D1 @rank2] — x +1/61, D1 1/62
    Fused: x = 2/61 (kept row: XA — exact rank tie, first list wins), D1 1/62.
    """
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    httpx_mock.add_response(
        url=_sql_url_re("dbA"),
        json=_sql_rows_payload(
            [{"title": "XA", "source_url": "https://e/x", "_score": -2.0, "_total": 1}]
        ),
    )
    httpx_mock.add_response(
        url=_sql_url_re("dbB"),
        json=_sql_rows_payload(
            [
                {"title": "XP", "source_url": "https://e/x", "_score": -9.0, "_total": 3},
                {"title": "D1", "source_url": "https://e/d1", "_score": -1.0, "_total": 3},
            ]
        ),
    )

    rows, totals, failed, statuses = await fan_out_search(
        '"x"',
        [("dbA", "t1", _PREVIEW_TU)],
        per_table_limit=6,
        fts_info={("dbA", "t1"): ("t1_fts", ["title"])},
        fragment_sources=[("dbB", _generic_source())],
    )

    assert failed == 0 and statuses == []
    assert [r["title"] for r in rows] == ["XA", "D1"]
    assert rows[0]["_fused_score"] == pytest.approx(2 / 61)
    assert rows[1]["_fused_score"] == pytest.approx(1 / 62)
    # Fragment-derived rows are PARENT rows (D4-13 / envelope coherence).
    assert rows[1]["database"] == "dbB" and rows[1]["table"] == "docs"
    assert rows[1]["_score"] == pytest.approx(-1.0)
    # Distinct observability key for passage hits: parent-count from the
    # GROUP BY window total, keyed on the FRAGMENT table.
    assert totals == {"dbA.t1": 1, "dbB.docs_fragments": 3}
    # The fragment dispatch went to dbB's SQL endpoint with the MaxP rollup.
    frag_reqs = [r for r in httpx_mock.get_requests() if r.url.path == "/dbB.json"]
    assert len(frag_reqs) == 1
    frag_sql = dict(frag_reqs[0].url.params)["sql"]
    assert "GROUP BY" in frag_sql and "MIN(bm25(" in frag_sql


async def test_fanout_legacy_ignores_fragment_sources(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensive gate: legacy mode dispatches NO fragment queries even when a
    direct caller passes fragment_sources explicitly (the SQL path needs the
    owner token; discovery already returns none in legacy mode)."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "legacy")
    rows, totals, failed, statuses = await fan_out_search(
        '"x"',
        [],
        per_table_limit=6,
        fragment_sources=[("dbB", _generic_source())],
    )
    assert rows == [] and totals == {} and failed == 0 and statuses == []
    assert httpx_mock.get_requests() == []


async def test_fragment_failure_captured_not_raised_inj05(
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-fragment failure is captured (fan_out_search NEVER raises) with the
    status surfaced; the log binding exposes database / table (the FRAGMENT
    table) / error_class only — the query string NEVER appears (INJ-05)."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    httpx_mock.add_response(url=_sql_url_re("dbB"), status_code=400, json={"error": "fts5"})

    escaped = '"ZEEKER_CANARY_42" "second_term"'
    with (
        caplog.at_level(logging.DEBUG, logger="mcp_zeeker"),
        patch("mcp_zeeker.core.search.log.warning") as mock_warn,
    ):
        rows, totals, failed, statuses = await fan_out_search(
            escaped,
            [],
            per_table_limit=6,
            fragment_sources=[("dbB", _generic_source())],
        )

    assert rows == [] and totals == {}
    assert failed == 1 and statuses == [400]
    fired = False
    for call in mock_warn.call_args_list:
        args, kwargs = call
        if args and args[0] == "search_table_failed":
            fired = True
            assert set(kwargs) == {"database", "table", "error_class"}
            assert kwargs["table"] == "docs_fragments"
            assert "ZEEKER_CANARY_42" not in repr(call)
    assert fired, "expected a search_table_failed warning"
    log_text = " ".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("mcp_zeeker") or r.name == "root"
    )
    assert "ZEEKER_CANARY_42" not in log_text


# ---------------------------------------------------------------------------
# 4. Handler end-to-end — body-only-indexed corpus
# ---------------------------------------------------------------------------


async def test_handler_end_to_end_body_only_parent(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through the search handler: a parent WITHOUT its own fts
    index (body text only indexed in *_fragments_fts) still fans out via its
    fragment source — the pre-Phase-3 short-circuit would have returned an
    empty envelope. Rows surface as parent rows with `_score` (MaxP) and
    `_fused_score`; the upstream total keys on the fragment table."""
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
                    "columns": [
                        "id",
                        "case_name",
                        "decision_date",
                        "summary",
                        "source_url",
                        "citation",
                        "court",
                    ],
                    # Body-only-indexed corpus: NO parent fts index.
                    "fts_table": None,
                },
                {
                    "name": "judgments_fragments",
                    "hidden": False,
                    "columns": ["id", "judgment_id", "ordinal", "content_text"],
                    "fts_table": "judgments_fragments_fts",
                },
                {
                    "name": "judgments_fragments_fts",
                    "hidden": True,
                    "columns": ["content_text", "judgments_fragments_fts", "rank"],
                    "fts_table": "judgments_fragments_fts",
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
                    "_score": -6.0,
                    "_total": 9,
                }
            ]
        ),
    )

    envelope = await search(query="privacy", databases=["zeeker-judgements"])

    sql_reqs = [r for r in httpx_mock.get_requests() if "sql" in dict(r.url.params)]
    assert len(sql_reqs) == 1
    qp = dict(sql_reqs[0].url.params)
    assert qp["search_query"] == '"privacy"'
    assert "GROUP BY" in qp["sql"] and "MIN(bm25(" in qp["sql"]
    # User text absent from the SQL string itself (bound param only).
    assert "privacy" not in qp["sql"]

    assert len(envelope.data) == 1
    row = envelope.data[0]
    # Parent row identity — the D4-13 post-filter and citation resolve
    # against the parent table.
    assert row["database"] == "zeeker-judgements" and row["table"] == "judgments"
    assert row["title"] == "Re Privacy"
    assert row["_score"] == pytest.approx(-6.0)
    assert row["_fused_score"] == pytest.approx(1 / 61)
    # Distinct passage-hits key: parent-document count from the MaxP query.
    assert envelope.pagination.upstream_total_hits == {"zeeker-judgements.judgments_fragments": 9}
