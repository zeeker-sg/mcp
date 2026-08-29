"""
WR-260829 regression — the ranked-search SQL must EXECUTE, not merely look right.

Production symptom (2026-08-29, mcp.zeeker.sg): `search(query="oppression")`
returned `data: []` with every `upstream_total_hits` entry at 0, while
`curl "https://data.zeeker.sg/zeeker-judgements/judgments.json?_search=oppression"`
returned real rows. `search(query="data", databases=["pdpc"])` returned
`invalid_query: query syntax not supported`.

Root cause: SQLite FTS5 auxiliary functions (`bm25`, `snippet`, `highlight`)
are only callable while the fts5 cursor is positioned on the matched row.
`build_bm25_sql` put `count(*) OVER ()` in the SAME SELECT as `bm25(...)`, and
`build_maxp_sql` wrapped bm25 in a `MIN(...)` aggregate. Both force SQLite to
buffer/sort rows first, which tears down that context and raises
`OperationalError: unable to use function bm25 in the requested context`.
Datasette surfaces it as HTTP 400.

Why it hid for so long — and why THIS file exists rather than more string
assertions: the failure is DATA-DEPENDENT. A query matching nothing never
invokes bm25, so the broken SQL returned HTTP 200 with an empty row set. Every
table with no hits reported an honest "0 hits"; every table that DID have hits
returned a 400. The envelope therefore looked healthy (`failed_tables` counted
the 400s, `upstream_total_hits` showed zeroes) while systematically hiding
every real result. When ALL targets in a scoped search had hits, the handler's
all-400 → `invalid_query` promotion fired, which is why a single common word
like "data" was rejected as bad syntax.

The pre-existing builder tests asserted on SQL SUBSTRINGS and passed
throughout. Only executing the emitted SQL against a genuine FTS5 index — with
a query that MATCHES — can catch this class of bug, so every test here runs
the builder output through `sqlite3` and asserts rows come back.
"""

from __future__ import annotations

import sqlite3

import pytest

from mcp_zeeker import config
from mcp_zeeker.core.citation import placeholder_columns
from mcp_zeeker.core.fts_escape import escape_user_query
from mcp_zeeker.core.search import (
    FragmentSource,
    build_bm25_sql,
    build_maxp_sql,
    resolve_preview_columns,
)

# A token planted in every fixture row, and one guaranteed to match nothing.
_MATCHING_TERM = "oppression"
_ABSENT_TERM = "zzzznosuchtokenzzzz"


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    return con


def _rows(con: sqlite3.Connection, sql: str, params: dict[str, str]) -> list[sqlite3.Row]:
    """Execute builder output the way Datasette does: bound named parameters."""
    return con.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Config-driven schema synthesis
#
# Column sets come from config (LIGHT_COLUMNS + citation placeholders + the
# FTS column order recorded in SEARCH_BM25_WEIGHTS) so that adding a table to
# config automatically extends this suite — the same auto-discovery promise
# D4-22 makes for the runtime path.
# ---------------------------------------------------------------------------


def _table_columns(db: str, table: str, fts_columns: list[str]) -> list[str]:
    """Realistic content-table column set for `<db>.<table>`."""
    template = config.CITATION_TEMPLATES.get((db, table), config.DEFAULT_CITATION_TEMPLATE)
    cols: list[str] = []
    for c in [
        *config.LIGHT_COLUMNS.get(f"{db}.{table}", []),
        *sorted(placeholder_columns(template)),
        *fts_columns,
        "id",
    ]:
        if c not in cols:
            cols.append(c)
    return cols


def _searchable_table_keys() -> list[str]:
    """Every `<db>.<table>` that the runtime would dispatch a BM25 query for.

    Mirrors gate 4 of `searchable_tables_for`: a table whose preview shape
    doesn't resolve is dropped from the fan-out, so it has no SQL to execute.
    """
    keys: list[str] = []
    for key, weights in config.SEARCH_BM25_WEIGHTS.items():
        db, _, table = key.partition(".")
        cols = _table_columns(db, table, list(weights))
        if resolve_preview_columns(db, table, set(cols)) is not None:
            keys.append(key)
    return sorted(keys)


def _build_table_fixture(con: sqlite3.Connection, table: str, columns: list[str], fts: list[str]):
    """Create `<table>` + `<table>_fts` and insert one matching + one non-matching row."""
    fts_table = f"{table}_fts"
    con.execute(
        f"CREATE TABLE {_qident(table)} ({', '.join(_qident(c) + ' TEXT' for c in columns)})"
    )
    con.execute(
        f"CREATE VIRTUAL TABLE {_qident(fts_table)} USING "
        f"fts5({', '.join(_qident(c) for c in fts)})"
    )
    for rowid, body in ((1, f"claim of {_MATCHING_TERM} by the majority"), (2, "unrelated text")):
        con.execute(
            f"INSERT INTO {_qident(table)}(rowid, {', '.join(_qident(c) for c in columns)}) "
            f"VALUES ({rowid}, {', '.join(['?'] * len(columns))})",
            [f"{c}-{rowid}" for c in columns],
        )
        con.execute(
            f"INSERT INTO {_qident(fts_table)}(rowid, {', '.join(_qident(c) for c in fts)}) "
            f"VALUES ({rowid}, {', '.join(['?'] * len(fts))})",
            [body] * len(fts),
        )
    con.commit()
    return fts_table


# ---------------------------------------------------------------------------
# 1. build_bm25_sql — executes against a real FTS5 index
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", _searchable_table_keys())
def test_bm25_sql_returns_rows_for_a_matching_query(key: str) -> None:
    """THE regression: a term present in the FTS index must produce rows.

    Before the fix this raised `unable to use function bm25 in the requested
    context` (HTTP 400 upstream) for every table that actually had a hit.
    """
    db, _, table = key.partition(".")
    fts_columns = list(config.SEARCH_BM25_WEIGHTS[key])
    columns = _table_columns(db, table, fts_columns)
    preview = resolve_preview_columns(db, table, set(columns))
    assert preview is not None  # guarded by _searchable_table_keys

    con = _connect()
    fts_table = _build_table_fixture(con, table, columns, fts_columns)
    sql, params = build_bm25_sql(
        db, table, fts_table, fts_columns, preview, escape_user_query(_MATCHING_TERM), 20
    )

    rows = _rows(con, sql, params)

    assert rows, f"{key}: matching query returned no rows — bm25 SQL is broken"
    assert rows[0]["_score"] < 0, "bm25() scores are negative (best = most negative)"
    assert rows[0]["_total"] == 1, "count(*) OVER () must see the full match set"
    # The preview columns the normalizer reads are present in the row —
    # except any mapped to a heavy column, which `_search_select_columns`
    # strips by design (D3-04: heavy text never enters a search row).
    for col in (c for c in preview.values() if c is not None):
        if col in config.HEAVY_COLUMNS:
            continue
        assert col in rows[0].keys()


@pytest.mark.parametrize("key", _searchable_table_keys())
def test_bm25_sql_zero_match_is_empty_not_an_error(key: str) -> None:
    """The masking half of the bug: a non-matching query must ALSO succeed.

    The broken SQL passed this case (bm25 was never invoked), which is exactly
    how a total outage read as "0 hits everywhere". Keeping both halves
    asserted means a future regression can't hide behind the empty case again.
    """
    db, _, table = key.partition(".")
    fts_columns = list(config.SEARCH_BM25_WEIGHTS[key])
    columns = _table_columns(db, table, fts_columns)
    preview = resolve_preview_columns(db, table, set(columns))
    assert preview is not None

    con = _connect()
    fts_table = _build_table_fixture(con, table, columns, fts_columns)
    sql, params = build_bm25_sql(
        db, table, fts_table, fts_columns, preview, escape_user_query(_ABSENT_TERM), 20
    )

    assert _rows(con, sql, params) == []


def test_bm25_sql_respects_limit_and_ranks_by_relevance() -> None:
    """Rows come back relevance-ordered (best first) and capped at the limit."""
    con = _connect()
    con.execute("CREATE TABLE t (title TEXT, source_url TEXT, url TEXT, date TEXT)")
    con.execute("CREATE VIRTUAL TABLE t_fts USING fts5(title, summary)")
    bodies = {
        1: ("strong", f"{_MATCHING_TERM} {_MATCHING_TERM} {_MATCHING_TERM}"),
        2: ("weak", f"a long passage of filler text mentioning {_MATCHING_TERM} once " * 20),
        3: ("also", f"{_MATCHING_TERM} appears here too"),
    }
    for rowid, (title, body) in bodies.items():
        con.execute("INSERT INTO t(rowid, title, source_url) VALUES (?,?,?)", (rowid, title, "u"))
        con.execute("INSERT INTO t_fts(rowid, title, summary) VALUES (?,?,?)", (rowid, title, body))
    con.commit()

    preview = {"title": "title", "date": None, "summary": None, "url": "source_url"}
    sql, params = build_bm25_sql(
        "dbA", "t", "t_fts", ["title", "summary"], preview, escape_user_query(_MATCHING_TERM), 2
    )
    rows = _rows(con, sql, params)

    assert len(rows) == 2, "outer LIMIT must cap the returned rows"
    assert [r["_score"] for r in rows] == sorted(r["_score"] for r in rows), "ORDER BY _score ASC"
    # `_total` is the full match count, NOT the truncated row count — that is
    # what `pagination.upstream_total_hits` reports to the agent.
    assert rows[0]["_total"] == 3


def test_bm25_sql_multi_term_query_requires_all_terms() -> None:
    """Unquoted multi-term queries AND their terms (adjacency not required)."""
    con = _connect()
    con.execute("CREATE TABLE t (title TEXT, source_url TEXT, url TEXT, date TEXT)")
    con.execute("CREATE VIRTUAL TABLE t_fts USING fts5(title)")
    for rowid, title in ((1, "minority oppression in a company"), (2, "oppression alone")):
        con.execute("INSERT INTO t(rowid, title, source_url) VALUES (?,?,?)", (rowid, title, "u"))
        con.execute("INSERT INTO t_fts(rowid, title) VALUES (?,?)", (rowid, title))
    con.commit()

    preview = {"title": "title", "date": None, "summary": None, "url": "source_url"}

    def _run(query: str) -> list[sqlite3.Row]:
        sql, params = build_bm25_sql(
            "dbA", "t", "t_fts", ["title"], preview, escape_user_query(query), 20
        )
        return _rows(con, sql, params)

    # Both terms present, order/adjacency irrelevant → the one row that has both.
    assert [r["title"] for r in _run("company oppression")] == ["minority oppression in a company"]
    # Explicit double-quoted phrase → adjacency required, so neither row matches.
    assert _run('"company oppression"') == []
    # FTS5 operators are literal, not operators — no syntax error, no match.
    assert _run("oppression OR nothingmatchesthis") == []


# ---------------------------------------------------------------------------
# 2. build_maxp_sql — fragment passage rollup, executes against real FTS5
# ---------------------------------------------------------------------------


def _build_fragment_fixture(key: str) -> tuple[sqlite3.Connection, FragmentSource]:
    """Realistic parent + fragment + fragment-fts fixture for a config source.

    One parent (`p1`) with TWO matching passages — so the MaxP rollup has
    something to collapse — plus one parent (`p2`) with none.
    """
    db, _, frag_table = key.partition(".")
    spec = config.SEARCH_FRAGMENT_SOURCES[key]
    parent_table = spec["parent_table"]
    parent_key, parent_link = spec["parent_key"], spec["parent_link"]

    parent_cols = _table_columns(
        db, parent_table, list(config.SEARCH_BM25_WEIGHTS.get(f"{db}.{parent_table}", {}))
    )
    if parent_key not in parent_cols:
        parent_cols.append(parent_key)
    preview = resolve_preview_columns(db, parent_table, set(parent_cols))
    assert preview is not None, f"{key}: parent preview must resolve for a fragment source"

    con = _connect()
    con.execute(
        f"CREATE TABLE {_qident(parent_table)} "
        f"({', '.join(_qident(c) + ' TEXT' for c in parent_cols)})"
    )
    con.execute(f"CREATE TABLE {_qident(frag_table)} ({_qident(parent_link)} TEXT, body TEXT)")
    frag_fts = f"{frag_table}_fts"
    con.execute(f"CREATE VIRTUAL TABLE {_qident(frag_fts)} USING fts5(text)")

    for pid in ("p1", "p2"):
        con.execute(
            f"INSERT INTO {_qident(parent_table)}"
            f"({', '.join(_qident(c) for c in parent_cols)}) "
            f"VALUES ({', '.join(['?'] * len(parent_cols))})",
            [pid if c == parent_key else f"{c}-{pid}" for c in parent_cols],
        )
    passages = [
        ("p1", f"the {_MATCHING_TERM} remedy was sought"),
        ("p1", f"further discussion of {_MATCHING_TERM} follows"),
        ("p2", "nothing relevant in this paragraph"),
    ]
    for rowid, (pid, body) in enumerate(passages, start=1):
        con.execute(
            f"INSERT INTO {_qident(frag_table)}"
            f"(rowid, {_qident(parent_link)}, body) VALUES (?,?,?)",
            (rowid, pid, body),
        )
        con.execute(f"INSERT INTO {_qident(frag_fts)}(rowid, text) VALUES (?,?)", (rowid, body))
    con.commit()

    source = FragmentSource(
        fragment_table=frag_table,
        fragment_fts=frag_fts,
        fragment_fts_columns=["text"],
        parent_table=parent_table,
        parent_link=parent_link,
        parent_key=parent_key,
        preview=preview,
    )
    return con, source


@pytest.mark.parametrize("key", sorted(config.SEARCH_FRAGMENT_SOURCES))
def test_maxp_sql_returns_parent_rows_for_a_matching_query(key: str) -> None:
    """Passage search must surface parent rows, not raise the bm25 context error.

    `MIN(bm25(...))` — an aggregate directly over the auxiliary function — was
    the fragment-path form of the same bug.
    """
    db = key.partition(".")[0]
    con, source = _build_fragment_fixture(key)
    sql, params = build_maxp_sql(db, source, escape_user_query(_MATCHING_TERM), 20)

    rows = _rows(con, sql, params)

    assert rows, f"{key}: matching passage query returned no parent rows"
    assert len(rows) == 1, "MaxP rolls both matching passages up to ONE parent row"
    assert rows[0]["_score"] < 0
    assert rows[0]["_total"] == 1, "_total counts DISTINCT matched parents"
    # Fragment body columns are matched via the fts index only — the passage
    # text itself never enters the projected row.
    assert "body" not in rows[0].keys() and "text" not in rows[0].keys()


@pytest.mark.parametrize("key", sorted(config.SEARCH_FRAGMENT_SOURCES))
def test_maxp_sql_zero_match_is_empty_not_an_error(key: str) -> None:
    """Fragment path: the zero-match case must stay a clean empty result."""
    db = key.partition(".")[0]
    con, source = _build_fragment_fixture(key)
    sql, params = build_maxp_sql(db, source, escape_user_query(_ABSENT_TERM), 20)

    assert _rows(con, sql, params) == []


# ---------------------------------------------------------------------------
# 3. High-fidelity zeeker-judgements fixture — the table that regressed
# ---------------------------------------------------------------------------


def _judgments_fixture() -> sqlite3.Connection:
    """Mirror of the live zeeker-judgements schema (data.zeeker.sg, 2026-08-29).

    `judgments` has NO declared primary key upstream and `judgments_fts`
    indexes exactly [case_name, summary, court_summary] — the shape the
    production SQL runs against.
    """
    con = _connect()
    con.executescript(
        """
        CREATE TABLE judgments (
            id TEXT, citation TEXT, case_name TEXT, case_numbers TEXT,
            decision_date TEXT, court TEXT, subject_tags TEXT, source_url TEXT,
            pdf_url TEXT, content_text TEXT, court_summary TEXT, summary TEXT,
            created_at TEXT, has_content INTEGER, has_court_summary INTEGER,
            fragment_count INTEGER, extracted_at TEXT, summary_generated_at TEXT
        );
        CREATE VIRTUAL TABLE judgments_fts USING fts5(case_name, summary, court_summary);
        CREATE TABLE judgments_fragments (
            id TEXT, judgment_id TEXT, ordinal INTEGER, paragraph_number TEXT,
            class_name TEXT, section_heading TEXT, content_text TEXT
        );
        CREATE VIRTUAL TABLE judgments_fragments_fts USING fts5(content_text);
        """
    )
    con.execute(
        "INSERT INTO judgments(rowid, id, citation, case_name, decision_date, court, "
        "source_url, content_text, court_summary, summary) "
        "VALUES (1, 'j1', '[2026] SGHC 157', 'GOH BIN SENG v YEO NENG JIAN STEPHEN', "
        "'2026-01-01', 'SGHC', 'https://example.test/j1', ?, 'court summary text', ?)",
        (f"body mentioning {_MATCHING_TERM}", f"minority {_MATCHING_TERM} claim"),
    )
    con.execute(
        "INSERT INTO judgments_fts(rowid, case_name, summary, court_summary) VALUES "
        "(1, 'GOH BIN SENG v YEO NENG JIAN STEPHEN', ?, 'court summary text')",
        (f"minority {_MATCHING_TERM} claim",),
    )
    con.execute(
        "INSERT INTO judgments_fragments(rowid, id, judgment_id, ordinal, content_text) "
        "VALUES (1, 'f1', 'j1', 1, ?)",
        (f"paragraph discussing {_MATCHING_TERM} at length",),
    )
    con.execute(
        "INSERT INTO judgments_fragments_fts(rowid, content_text) VALUES (1, ?)",
        (f"paragraph discussing {_MATCHING_TERM} at length",),
    )
    con.commit()
    return con


def test_judgments_bm25_sql_finds_the_production_case() -> None:
    """The exact query that returned `data: []` in production now returns rows."""
    con = _judgments_fixture()
    preview = resolve_preview_columns(
        "zeeker-judgements",
        "judgments",
        {r[1] for r in con.execute("PRAGMA table_info(judgments)")},
    )
    assert preview == {
        "title": "case_name",
        "date": "decision_date",
        "summary": "summary",
        "url": "source_url",
    }

    sql, params = build_bm25_sql(
        "zeeker-judgements",
        "judgments",
        "judgments_fts",
        ["case_name", "summary", "court_summary"],
        preview,
        escape_user_query(_MATCHING_TERM),
        20,
    )
    rows = _rows(con, sql, params)

    assert len(rows) == 1
    assert rows[0]["case_name"] == "GOH BIN SENG v YEO NENG JIAN STEPHEN"
    assert rows[0]["source_url"] == "https://example.test/j1"
    # Citation-template columns are projected alongside the preview columns so
    # `synthesize_citation` has real values to substitute.
    assert rows[0]["citation"] == "[2026] SGHC 157" and rows[0]["court"] == "SGHC"
    # `content_text` is heavy — it must never reach the SELECT list.
    assert "content_text" not in rows[0].keys()


def test_judgments_maxp_sql_finds_the_body_only_match() -> None:
    """Body text is only FTS-indexed on the fragments table; MaxP must roll it
    up to the parent judgment row."""
    con = _judgments_fixture()
    source = FragmentSource(
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
    sql, params = build_maxp_sql("zeeker-judgements", source, escape_user_query(_MATCHING_TERM), 20)
    rows = _rows(con, sql, params)

    assert len(rows) == 1
    assert rows[0]["case_name"] == "GOH BIN SENG v YEO NENG JIAN STEPHEN"
    assert rows[0]["_score"] < 0
    assert "content_text" not in rows[0].keys()
