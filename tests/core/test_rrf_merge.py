"""
Issue #12 Phase 2 — Reciprocal Rank Fusion merge (`core.search._rrf_merge`)
and the SEARCH_RANKING merge-strategy routing inside `fan_out_search`.

Covers:
  1. `_rrf_merge` pure-function unit tests — hand-computed k=60 math,
     cross-list dedup by non-null `url` (contributions SUM, the kept row is
     the one from the list where the doc ranked best), null-url rows never
     merging, deterministic tie-break, `_fused_score` attachment, limit slice,
     custom-k parameter.
  2. Flag routing through `fan_out_search` — "bm25_rrf" produces the fused
     (deduped, relevance-fused) order with `_fused_score` on every row;
     "bm25" preserves the pre-Phase-2 round-robin order byte-for-byte
     (acceptance criterion: round-robin stays available behind the flag)
     and rows carry NO `_fused_score` key.

Follows the suite discipline from tests/core/test_bm25_search.py: explicit
ordered httpx_mock add_response, local datasette_client fixture, distinct
databases per table so the SQL-endpoint URL matcher is unambiguous under
concurrent dispatch.
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.search import _rrf_merge, fan_out_search

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


def _row(title: str, url: str | None, db: str = "dbA", table: str = "t1") -> dict:
    """Minimal normalized-row stand-in — only the keys _rrf_merge reads."""
    return {"title": title, "url": url, "database": db, "table": table}


# ---------------------------------------------------------------------------
# 1. _rrf_merge — pure-function unit tests
# ---------------------------------------------------------------------------


def test_rrf_k60_math_and_cross_list_dedup() -> None:
    """Hand-computed k=60 spot-check with a cross-list duplicate.

    List A (dbA.t1): [a1, x@rank2]; list B (dbB.t2): [x@rank1, b1].
    Doc x (same url in both lists) fuses to 1/62 + 1/61 and the KEPT row is
    the one from list B (rank 1 beats rank 2). a1 = 1/61, b1 = 1/62.
    Fused order: [x, a1, b1].
    """
    rows_by_table = {
        ("dbA", "t1"): [
            _row("A1", "https://e/a1"),
            _row("XA", "https://e/x"),
        ],
        ("dbB", "t2"): [
            _row("XB", "https://e/x", db="dbB", table="t2"),
            _row("B1", "https://e/b1", db="dbB", table="t2"),
        ],
    }
    out = _rrf_merge(rows_by_table, limit=10)

    assert [r["title"] for r in out] == ["XB", "A1", "B1"]
    # Duplicate merged ONCE — contributions from BOTH lists sum.
    assert out[0]["_fused_score"] == pytest.approx(1 / 62 + 1 / 61)
    assert out[1]["_fused_score"] == pytest.approx(1 / 61)
    assert out[2]["_fused_score"] == pytest.approx(1 / 62)
    for r in out:
        assert isinstance(r["_fused_score"], float)


def test_rrf_dedup_keeps_best_rank_from_first_list_too() -> None:
    """Symmetric to the test above: when the doc ranks BEST in the first list
    processed, that list's row is kept (rank comparison, not last-wins)."""
    rows_by_table = {
        ("dbA", "t1"): [_row("X-first", "https://e/x")],  # rank 1
        ("dbB", "t2"): [
            _row("other", "https://e/o", db="dbB", table="t2"),
            _row("X-second", "https://e/x", db="dbB", table="t2"),  # rank 2
        ],
    }
    out = _rrf_merge(rows_by_table, limit=10)
    x = next(r for r in out if r["url"] == "https://e/x")
    assert x["title"] == "X-first"
    assert x["_fused_score"] == pytest.approx(1 / 61 + 1 / 62)


def test_rrf_exact_rank_tie_keeps_first_encountered() -> None:
    """Rank-1-in-both duplicate: strict `<` comparison keeps the row from the
    first list in rows_by_table insertion order (deterministic)."""
    rows_by_table = {
        ("dbA", "t1"): [_row("X-dbA", "https://e/x")],
        ("dbB", "t2"): [_row("X-dbB", "https://e/x", db="dbB", table="t2")],
    }
    out = _rrf_merge(rows_by_table, limit=10)
    assert len(out) == 1
    assert out[0]["title"] == "X-dbA"
    assert out[0]["_fused_score"] == pytest.approx(2 / 61)


def test_rrf_null_url_rows_never_merge() -> None:
    """Rows with url=None are their own identity — identical content in two
    lists still yields TWO output rows, each with its own contribution."""
    rows_by_table = {
        ("dbA", "t1"): [_row("same-title", None)],
        ("dbB", "t2"): [_row("same-title", None, db="dbB", table="t2")],
    }
    out = _rrf_merge(rows_by_table, limit=10)
    assert len(out) == 2
    for r in out:
        assert r["_fused_score"] == pytest.approx(1 / 61)
    # Equal score + empty-url tie-break falls to (database, table) — dbA first.
    assert [r["database"] for r in out] == ["dbA", "dbB"]


def test_rrf_tie_break_is_url_order_not_insertion_order() -> None:
    """Two docs with EQUAL fused score sort by url ascending regardless of
    rows_by_table insertion order — stable output for tests."""
    rows_by_table = {
        # Insertion order deliberately puts the LATER-sorting url first.
        ("dbB", "t2"): [_row("B", "https://e/zzz", db="dbB", table="t2")],
        ("dbA", "t1"): [_row("A", "https://e/aaa")],
    }
    out = _rrf_merge(rows_by_table, limit=10)
    assert [r["url"] for r in out] == ["https://e/aaa", "https://e/zzz"]


def test_rrf_slices_to_limit_after_fusion() -> None:
    """Limit slices AFTER fusion — the top-scored doc survives even when it
    arrives from the later list."""
    rows_by_table = {
        ("dbA", "t1"): [
            _row("A1", "https://e/a1"),
            _row("X", "https://e/x"),
        ],
        ("dbB", "t2"): [_row("X", "https://e/x", db="dbB", table="t2")],
    }
    out = _rrf_merge(rows_by_table, limit=1)
    assert len(out) == 1
    assert out[0]["url"] == "https://e/x"  # fused 1/62 + 1/61 beats A1's 1/61


def test_rrf_custom_k_parameter() -> None:
    """k is a real parameter: k=1 gives rank 1 → 1/2, rank 2 → 1/3."""
    rows_by_table = {
        ("dbA", "t1"): [
            _row("first", "https://e/1"),
            _row("second", "https://e/2"),
        ],
    }
    out = _rrf_merge(rows_by_table, limit=10, k=1)
    assert out[0]["_fused_score"] == pytest.approx(1 / 2)
    assert out[1]["_fused_score"] == pytest.approx(1 / 3)


def test_rrf_empty_input() -> None:
    """Degenerate inputs: no tables / empty lists → empty output, no raise."""
    assert _rrf_merge({}, limit=10) == []
    assert _rrf_merge({("dbA", "t1"): []}, limit=10) == []


# ---------------------------------------------------------------------------
# 2. Flag routing through fan_out_search
# ---------------------------------------------------------------------------

# Two tables in DISTINCT databases so the concurrent SQL dispatches hit
# distinct URLs (unambiguous httpx_mock matching regardless of task order).
_FTS_INFO = {
    ("dbA", "t1"): ("t1_fts", ["title"]),
    ("dbB", "t2"): ("t2_fts", ["title"]),
}
_TARGET = [
    ("dbA", "t1", _PREVIEW_TU),
    ("dbB", "t2", _PREVIEW_TU),
]


def _stub_two_tables(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """dbA.t1 → [A1, XA(url x)]; dbB.t2 → [XB(url x), B1] — BM25-ordered."""
    httpx_mock.add_response(
        url=_sql_url_re("dbA"),
        json=_sql_rows_payload(
            [
                {"title": "A1", "source_url": "https://e/a1", "_score": -9.0, "_total": 2},
                {"title": "XA", "source_url": "https://e/x", "_score": -1.0, "_total": 2},
            ]
        ),
    )
    httpx_mock.add_response(
        url=_sql_url_re("dbB"),
        json=_sql_rows_payload(
            [
                {"title": "XB", "source_url": "https://e/x", "_score": -5.0, "_total": 2},
                {"title": "B1", "source_url": "https://e/b1", "_score": -3.0, "_total": 2},
            ]
        ),
    )


async def test_flag_bm25_rrf_routes_to_fused_merge(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEARCH_RANKING='bm25_rrf' → RRF merge: cross-list duplicate deduped
    (url x fuses 1/62 + 1/61, best-ranked row XB kept) and every output row
    carries the `_fused_score` float — the 11-key shape on this mode."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    _stub_two_tables(httpx_mock)

    rows, totals, failed, statuses = await fan_out_search(
        '"x"', _TARGET, per_table_limit=6, fts_info=_FTS_INFO
    )

    assert failed == 0 and statuses == []
    assert [r["title"] for r in rows] == ["XB", "A1", "B1"]
    assert rows[0]["_fused_score"] == pytest.approx(1 / 62 + 1 / 61)
    assert rows[1]["_fused_score"] == pytest.approx(1 / 61)
    assert rows[2]["_fused_score"] == pytest.approx(1 / 62)
    for r in rows:
        assert "_fused_score" in r and isinstance(r["_fused_score"], float)
    assert totals == {"dbA.t1": 2, "dbB.t2": 2}


async def test_flag_bm25_keeps_round_robin_merge(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEARCH_RANKING='bm25' → the pre-Phase-2 round-robin merge is preserved:
    interleaved slot order, NO dedup, NO `_fused_score` key on any row
    (acceptance criterion: round-robin stays available behind the flag)."""
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25")
    _stub_two_tables(httpx_mock)

    rows, _totals, failed, _statuses = await fan_out_search(
        '"x"', _TARGET, per_table_limit=6, fts_info=_FTS_INFO
    )

    assert failed == 0
    # Round-robin interleave in target order — duplicate url x appears TWICE.
    assert [r["title"] for r in rows] == ["A1", "XB", "XA", "B1"]
    for r in rows:
        assert "_fused_score" not in r
