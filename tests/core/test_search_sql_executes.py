"""
Executing-SQL regression net for the ranked-search builders.

The builders (`build_bm25_sql` / `build_maxp_sql`) are pure functions and the
rest of the suite asserts their SQL *text*. That gap let an invalid query
shape ship: `MIN(bm25(<fts>, ...))` under a joined GROUP BY is rejected by
SQLite ("unable to use function bm25 in the requested context" — FTS5
auxiliary functions are only legal in a direct full-text query context), so
every fragment MaxP dispatch failed upstream while the text-shape tests
stayed green. These tests EXECUTE the generated SQL against a real in-memory
SQLite FTS5 corpus so the next illegal-context regression fails here, not in
production.

Also covers the `match_context` snippet feature end-to-end at the SQL layer:
snippet() present on both paths, the MaxP snippet comes from each parent's
BEST-matching passage (the documented sole-min()-aggregate bare-column rule),
and `_clean_snippet` normalization (whitespace collapse + char cap).
"""

from __future__ import annotations

import sqlite3

import pytest

from mcp_zeeker import config
from mcp_zeeker.core.search import (
    FragmentSource,
    _clean_snippet,
    build_bm25_sql,
    build_maxp_sql,
)

# ---------------------------------------------------------------------------
# In-memory corpus mirroring the live zeeker-judgements shape
# ---------------------------------------------------------------------------

_JUDGMENTS_PREVIEW: dict[str, str | None] = {
    "title": "case_name",
    "date": "decision_date",
    "summary": "summary",
    "url": "source_url",
}

_JUDGMENTS_FTS_COLUMNS = ["case_name", "summary", "court_summary"]


@pytest.fixture
def corpus() -> sqlite3.Connection:
    """Judgments-shaped corpus: 2 parents, 3 fragments, both FTS indexes."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE judgments(
            id INTEGER PRIMARY KEY,
            case_name TEXT, citation TEXT, court TEXT, decision_date TEXT,
            summary TEXT, court_summary TEXT, source_url TEXT
        );
        CREATE VIRTUAL TABLE judgments_fts USING fts5(
            case_name, summary, court_summary,
            content=judgments, content_rowid=id
        );
        CREATE TABLE judgments_fragments(
            id INTEGER PRIMARY KEY, judgment_id INTEGER, content_text TEXT
        );
        CREATE VIRTUAL TABLE judgments_fragments_fts USING fts5(
            content_text, content=judgments_fragments, content_rowid=id
        );

        INSERT INTO judgments VALUES
          (1, 'Tan v Lim', '[2026] SGHC 1', 'SGHC', '2026-01-05',
           'A negligence dispute over counsel conduct at trial.',
           'Court summary one', 'https://example.org/1'),
          (2, 'Ong v Ho', '[2026] SGCA 2', 'SGCA', '2026-02-10',
           'Contract appeal with no matching terms.',
           'Court summary two', 'https://example.org/2');
        INSERT INTO judgments_fts(rowid, case_name, summary, court_summary)
          SELECT id, case_name, summary, court_summary FROM judgments;

        INSERT INTO judgments_fragments VALUES
          (1, 1, 'counsel conduct was mentioned once in passing among many'
                 || ' other unrelated procedural observations about counsel'),
          (2, 1, 'the court criticised counsel conduct severely: counsel'
                 || ' conduct counsel conduct counsel conduct'),
          (3, 2, 'an unrelated paragraph that still mentions counsel'
                 || ' conduct exactly once');
        INSERT INTO judgments_fragments_fts(rowid, content_text)
          SELECT id, content_text FROM judgments_fragments;
        """
    )
    yield db
    db.close()


def _judgments_source() -> FragmentSource:
    return FragmentSource(
        fragment_table="judgments_fragments",
        fragment_fts="judgments_fragments_fts",
        fragment_fts_columns=["content_text"],
        parent_table="judgments",
        parent_link="judgment_id",
        parent_key="id",
        preview=dict(_JUDGMENTS_PREVIEW),
    )


# ---------------------------------------------------------------------------
# build_bm25_sql executes
# ---------------------------------------------------------------------------


def test_bm25_sql_executes_with_score_snippet_total(corpus: sqlite3.Connection) -> None:
    """The per-table BM25 SQL is valid SQLite: rows come back relevance-ordered
    with a negative float _score, a non-empty _snippet, and the pre-LIMIT
    MATCH count in _total on every row."""
    sql, params = build_bm25_sql(
        "zeeker-judgements",
        "judgments",
        "judgments_fts",
        _JUDGMENTS_FTS_COLUMNS,
        dict(_JUDGMENTS_PREVIEW),
        '"negligence"',
        5,
    )
    rows = [dict(r) for r in corpus.execute(sql, params).fetchall()]

    assert len(rows) == 1
    row = rows[0]
    assert row["case_name"] == "Tan v Lim"
    assert row["_score"] < 0
    assert row["_total"] == 1
    assert isinstance(row["_snippet"], str) and "negligence" in row["_snippet"]


def test_bm25_sql_zero_hits_executes(corpus: sqlite3.Connection) -> None:
    """A no-match query is still valid SQL and returns zero rows (the handler
    reads _total as 0 from the empty row list)."""
    sql, params = build_bm25_sql(
        "zeeker-judgements",
        "judgments",
        "judgments_fts",
        _JUDGMENTS_FTS_COLUMNS,
        dict(_JUDGMENTS_PREVIEW),
        '"zzzznomatch"',
        5,
    )
    assert corpus.execute(sql, params).fetchall() == []


# ---------------------------------------------------------------------------
# build_maxp_sql executes — THE regression net for the illegal-context bug
# ---------------------------------------------------------------------------


def test_maxp_sql_executes(corpus: sqlite3.Connection) -> None:
    """The MaxP rollup SQL is valid SQLite (the MATERIALIZED-CTE shape — the
    previous MIN(bm25(...))-under-GROUP-BY shape raised OperationalError:
    'unable to use function bm25 in the requested context'). One row per
    matched PARENT, best-passage MIN score first, distinct-parent _total."""
    sql, params = build_maxp_sql(
        "zeeker-judgements", _judgments_source(), '"counsel" "conduct"', 10
    )
    rows = [dict(r) for r in corpus.execute(sql, params).fetchall()]

    # 3 matching fragments roll up to 2 distinct parents.
    assert len(rows) == 2
    assert all(r["_total"] == 2 for r in rows)
    # Parent 1's best passage (5 hits) outranks parent 2's single hit —
    # relevance order, most negative MIN(bm25) first.
    assert [r["case_name"] for r in rows] == ["Tan v Lim", "Ong v Ho"]
    assert rows[0]["_score"] < rows[1]["_score"] < 0


def test_maxp_snippet_comes_from_best_passage(corpus: sqlite3.Connection) -> None:
    """Bare-column min()-aggregate rule: each parent's _snippet is the extract
    from its BEST-matching passage, not an arbitrary group member."""
    sql, params = build_maxp_sql(
        "zeeker-judgements", _judgments_source(), '"counsel" "conduct"', 10
    )
    rows = [dict(r) for r in corpus.execute(sql, params).fetchall()]

    best = rows[0]["_snippet"]
    assert "criticised" in best, f"snippet not from the best passage: {best!r}"


def test_maxp_limit_applies_after_rollup(corpus: sqlite3.Connection) -> None:
    """LIMIT bounds the number of PARENT rows; _total still reports the full
    distinct-parent match count before LIMIT."""
    sql, params = build_maxp_sql("zeeker-judgements", _judgments_source(), '"counsel" "conduct"', 1)
    rows = [dict(r) for r in corpus.execute(sql, params).fetchall()]
    assert len(rows) == 1
    assert rows[0]["_total"] == 2


# ---------------------------------------------------------------------------
# Snippet token clamp + _clean_snippet normalization
# ---------------------------------------------------------------------------


def test_snippet_tokens_clamped_to_fts5_ceiling(
    corpus: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config value beyond the FTS5 ceiling (64) is clamped, keeping the SQL
    valid — snippet() with n > 64 is an FTS5 error."""
    monkeypatch.setattr(config, "SEARCH_SNIPPET_TOKENS", 9999)
    sql, params = build_bm25_sql(
        "zeeker-judgements",
        "judgments",
        "judgments_fts",
        _JUDGMENTS_FTS_COLUMNS,
        dict(_JUDGMENTS_PREVIEW),
        '"negligence"',
        5,
    )
    assert ", 64)" in sql
    assert len(corpus.execute(sql, params).fetchall()) == 1


def test_clean_snippet_collapses_whitespace_and_caps() -> None:
    """_clean_snippet: whitespace collapse, char cap with ellipsis, None on
    empty/non-string input."""
    assert _clean_snippet("a\n\t  b   c") == "a b c"
    assert _clean_snippet("") is None
    assert _clean_snippet(None) is None
    assert _clean_snippet(42) is None

    long = "word " * 200
    cleaned = _clean_snippet(long)
    assert cleaned is not None
    assert len(cleaned) <= config.SEARCH_SNIPPET_MAX_CHARS + 2  # + " …"
    assert cleaned.endswith("…")
