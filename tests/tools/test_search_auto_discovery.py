"""
Auto-discovery semantics tests for cross-DB search — GREEN (Plan 04-03 Task 1).

Tests the FOUR-gate filter in `core.search.searchable_tables_for`:
  1. `fts_table is not None` — LOAD-BEARING safety gate (Pitfall 3 / D4-02).
  2. Table is in `_visible_tables(db)` — Phase 2 hidden-flag + HIDDEN_TABLES.
  3. Table name does NOT end with `_fragments` (SEARCH_DENYLIST_PATTERNS / D4-04).
  4. `resolve_preview_columns` returns non-null title AND url (D4-12).

Plus the load-bearing pdpc-no-dispatch sentinel: pdpc.enforcement_decisions is
NEVER dispatched to because it has NO `fts_table` upstream (04-RESEARCH §3.2 /
Probe 2). The captured `pdpc__enforcement_decisions__search_ignored.json` shows
Datasette silently returning rowid-ordered rows — without the safety gate
these would surface as fake "search results."

Plus the zero-hit invariant (Probe 6 / D4-17): a searchable table that returns
zero rows still appears in envelope.pagination.upstream_total_hits with value 0
so the LLM can see "this table was searched, found nothing."

End-to-end observability: every assertion is on either a handler-emitted
envelope field or the dispatch URL set (httpx_mock.get_requests()), not on
internal call counts — this is the boundary the auto-discovery design must
defend (D4-22).
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from tests.conftest import _db_url, _load_search_fixture, _tables_payload


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for /{database}/{table}.json with any query string.

    Paste-equivalent of tests/tools/test_search.py — keeps Task 1 self-contained.
    """
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Local DatasetteClient bound to current context.

    Does NOT depend on `stub_upstream` — Plan 04-03 auto-discovery tests
    register their own /{db}.json stubs (with non-default `fts_tables` /
    `columns` payloads) and rely on `httpx_mock.reset(...)` is unnecessary
    because this fixture never pre-registers anything.
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _empty_db_payload() -> dict:
    """Minimal /{db}.json payload with zero tables — harmless placeholder."""
    return {"tables": []}


async def test_fts_gate_drops_non_fts_table(
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D4-02 gate 1 (fts_table) + gate 2 (visibility) end-to-end.

    Synthetic DB with THREE tables:
      - t_fts        — fts_table set, preview-shape-resolvable (PASSES all 4 gates)
      - t_nofts      — fts_table=None, preview-shape-resolvable (FAILS gate 1)
      - t_hidden     — fts_table set, preview-shape-resolvable BUT added to
                       config.HIDDEN_TABLES["zeeker-judgements"] via monkeypatch
                       (FAILS gate 2)

    Asserts exactly ONE per-table /search dispatch — against `t_fts`.
    The non-FTS table is the load-bearing gate (Pitfall 3); the hidden table
    is the Phase 2 visibility gate (DISC-05 inherited).
    """
    from mcp_zeeker.tools.search import search

    # Hide t_hidden via monkeypatch on config.HIDDEN_TABLES — preserves Plan
    # 04-01's consolidation discipline (NO conftest edits in Plan 04-03).
    monkeypatch.setitem(
        config.HIDDEN_TABLES,
        "zeeker-judgements",
        config.HIDDEN_TABLES.get("zeeker-judgements", set()) | {"t_hidden"},
    )

    # /{db}.json stubs — zeeker-judgements gets the 3-table synthetic payload,
    # others empty. is_reusable=True because the handler reads each /{db}.json
    # multiple times in one request (searchable_tables_for → get_database, then
    # _visible_tables inside the FOUR-gate filter, plus _visible_columns at
    # handler step 5, plus the step-10 post-filter _visible_tables cache when
    # rows come back).
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            ["t_fts", "t_nofts", "t_hidden"],
            fts_tables={"t_fts": "t_fts_fts", "t_hidden": "t_hidden_fts"},
            columns={
                "t_fts": ["title", "source_url"],
                "t_nofts": ["title", "source_url"],
                "t_hidden": ["title", "source_url"],
            },
        ),
        is_reusable=True,
    )
    # When databases=None (default), handler iterates all four — stub the others.
    for db in ("pdpc", "sg-gov-newsrooms", "sglawwatch"):
        httpx_mock.add_response(url=_db_url(db), json=_empty_db_payload(), is_reusable=True)

    # Only t_fts is expected to be dispatched. Register exactly one per-table
    # response — preview-resolvable shape via `title` + `source_url`.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "t_fts"),
        json={
            "rows": [{"title": "r1", "source_url": "https://r1"}],
            "filtered_table_rows_count": 1,
            "next": None,
            "truncated": False,
            "columns": ["title", "source_url"],
        },
        is_reusable=True,
    )

    envelope = await search(query="x", limit=10)

    # Exactly one /zeeker-judgements/t_fts dispatch.
    t_fts_reqs = [
        r
        for r in httpx_mock.get_requests()
        if _table_url_re("zeeker-judgements", "t_fts").match(str(r.url))
    ]
    assert len(t_fts_reqs) == 1, f"expected 1 dispatch to t_fts, got {len(t_fts_reqs)}"

    # ZERO dispatches to t_nofts (fts_table gate) and t_hidden (visibility gate).
    t_nofts_reqs = [
        r
        for r in httpx_mock.get_requests()
        if _table_url_re("zeeker-judgements", "t_nofts").match(str(r.url))
    ]
    assert t_nofts_reqs == [], "t_nofts should be dropped by fts_table gate (Pitfall 3)"
    t_hidden_reqs = [
        r
        for r in httpx_mock.get_requests()
        if _table_url_re("zeeker-judgements", "t_hidden").match(str(r.url))
    ]
    assert t_hidden_reqs == [], "t_hidden should be dropped by visibility gate"

    # Envelope reflects the gates: only t_fts in upstream_total_hits.
    totals = envelope.pagination.upstream_total_hits
    assert "zeeker-judgements.t_fts" in totals
    assert "zeeker-judgements.t_nofts" not in totals
    assert "zeeker-judgements.t_hidden" not in totals


async def test_fragments_excluded_via_denylist(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-04 gate 3 (denylist suffix) end-to-end.

    Synthetic DB with TWO tables that BOTH pass the fts_table + visibility +
    preview gates:
      - t1            — passes all 4 gates → DISPATCHED.
      - t1_fragments  — fts_table set, preview-resolvable, but suffix matches
                        SEARCH_DENYLIST_PATTERNS=("_fragments",) → DROPPED.

    Direct test for the load-bearing endswith("_fragments") logic — any
    weakening (case-fold, glob, substring match) of the suffix check would
    break this test.
    """
    from mcp_zeeker.tools.search import search

    # Test scopes to databases=["zeeker-judgements"] — other DBs not consulted.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            ["t1", "t1_fragments"],
            fts_tables={"t1": "t1_fts", "t1_fragments": "t1_fragments_fts"},
            columns={
                "t1": ["title", "source_url"],
                "t1_fragments": ["title", "source_url"],
            },
        ),
        is_reusable=True,
    )

    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "t1"),
        json={
            "rows": [{"title": "r1", "source_url": "https://r1"}],
            "filtered_table_rows_count": 1,
            "next": None,
            "truncated": False,
            "columns": ["title", "source_url"],
        },
        is_reusable=True,
    )

    envelope = await search(query="x", databases=["zeeker-judgements"], limit=10)

    # Exactly one dispatch — to t1.
    t1_reqs = [
        r
        for r in httpx_mock.get_requests()
        if _table_url_re("zeeker-judgements", "t1").match(str(r.url))
    ]
    assert len(t1_reqs) == 1, f"expected 1 dispatch to t1, got {len(t1_reqs)}"

    # ZERO dispatches to t1_fragments (SEARCH_DENYLIST_PATTERNS suffix gate).
    fragments_reqs = [
        r
        for r in httpx_mock.get_requests()
        if _table_url_re("zeeker-judgements", "t1_fragments").match(str(r.url))
    ]
    assert fragments_reqs == [], (
        "t1_fragments must be excluded by SEARCH_DENYLIST_PATTERNS suffix (D4-04)"
    )
    assert "zeeker-judgements.t1_fragments" not in envelope.pagination.upstream_total_hits


async def test_pdpc_no_dispatch(datasette_client, httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """04-RESEARCH §3.2 / Pitfall 3 / T-04-04: pdpc never gets `_search=` calls.

    Two assertions:
      (a) With `databases=["pdpc"]` (explicit scope) → empty envelope and ZERO
          /pdpc/<table>?_search= URLs in the dispatch set (handler step 6
          short-circuit on empty target_tables).
      (b) With the DEFAULT databases (all four) → still ZERO /pdpc/...?_search=
          URLs (pdpc's tables all have fts_table=None per 04-RESEARCH Probe 2,
          so searchable_tables_for("pdpc") returns ()).

    Captured fixture `pdpc__enforcement_decisions__search_ignored.json` proves
    that if dispatch DID happen, Datasette would silently return rowid-ordered
    rows. This test makes the absence of that dispatch observable end-to-end.
    """
    from mcp_zeeker.tools.search import search

    # pdpc — 4 tables, ALL fts_table=None (matches 04-RESEARCH Probe 2 reality).
    # The default `_tables_payload(names)` emits fts_table=None per Plan 04-01.
    pdpc_payload = _tables_payload(
        [
            "_zeeker_schemas",
            "_zeeker_updates",
            "enforcement_decisions",
            "enforcement_decisions_fragments",
        ],
        columns={
            "enforcement_decisions": ["title", "decision_url", "summary"],
            "enforcement_decisions_fragments": ["title", "decision_url"],
        },
    )
    httpx_mock.add_response(url=_db_url("pdpc"), json=pdpc_payload, is_reusable=True)

    # The other 3 DBs get realistic FTS-having payloads so the handler still
    # has dispatch work (otherwise the empty-target short-circuit fires for the
    # all-four case too — different code path).
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            ["judgments"],
            fts_tables={"judgments": "judgments_fts"},
            columns={"judgments": ["title", "source_url"]},
        ),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_db_url("sg-gov-newsrooms"),
        json=_tables_payload(
            ["acra_news"],
            fts_tables={"acra_news": "acra_news_fts"},
            columns={"acra_news": ["title", "source_url"]},
        ),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_db_url("sglawwatch"),
        json=_tables_payload(
            ["commentaries"],
            fts_tables={"commentaries": "commentaries_fts"},
            columns={"commentaries": ["title", "link"]},
        ),
        is_reusable=True,
    )

    # Per-table FTS stubs for the three FTS-having DBs so the all-DBs call
    # doesn't hit unmatched-response errors.
    happy_row = {
        "rows": [{"title": "r", "source_url": "https://r", "link": "https://r"}],
        "filtered_table_rows_count": 1,
        "next": None,
        "truncated": False,
        "columns": ["title", "source_url", "link"],
    }
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=happy_row, is_reusable=True
    )
    httpx_mock.add_response(
        url=_table_url_re("sg-gov-newsrooms", "acra_news"), json=happy_row, is_reusable=True
    )
    httpx_mock.add_response(
        url=_table_url_re("sglawwatch", "commentaries"), json=happy_row, is_reusable=True
    )

    # Path (a): explicit databases=["pdpc"] → empty envelope, zero pdpc dispatches.
    envelope_pdpc_only = await search(query="privacy", databases=["pdpc"])
    pdpc_search_reqs = [
        r for r in httpx_mock.get_requests() if "/pdpc/" in str(r.url) and "_search=" in str(r.url)
    ]
    assert pdpc_search_reqs == [], (
        f"pdpc must never be dispatched (Pitfall 3): {[str(r.url) for r in pdpc_search_reqs]}"
    )
    assert envelope_pdpc_only.data == []
    assert envelope_pdpc_only.pagination.upstream_total_hits == {}
    assert envelope_pdpc_only.pagination.failed_tables == 0

    # Path (b): default databases (all four) → still zero pdpc dispatches.
    envelope_all = await search(query="privacy")
    pdpc_search_reqs_all = [
        r for r in httpx_mock.get_requests() if "/pdpc/" in str(r.url) and "_search=" in str(r.url)
    ]
    assert pdpc_search_reqs_all == [], (
        "pdpc must never be dispatched even when default databases includes it: "
        f"{[str(r.url) for r in pdpc_search_reqs_all]}"
    )
    # And pdpc must not appear in upstream_total_hits.
    pdpc_keys = [k for k in envelope_all.pagination.upstream_total_hits if k.startswith("pdpc.")]
    assert pdpc_keys == [], f"pdpc must not appear in upstream_total_hits: {pdpc_keys}"


async def test_no_preview_columns_drops_table(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-12 gate 4 (preview-shape resolvable) end-to-end.

    Synthetic DB with ONE table that PASSES gates 1-3 but FAILS gate 4:
      - weird_table — fts_table set, visible, no `_fragments` suffix, but
                      columns=["odd_column_a","odd_column_b","odd_column_c"]
                      which match NEITHER any SEARCH_PREVIEW_DEFAULTS["title"]
                      candidate NOR any SEARCH_PREVIEW_DEFAULTS["url"] candidate.

    resolve_preview_columns returns None → searchable_tables_for emits
    `search_table_no_preview_columns` warning and drops the table.

    Asserts envelope.data == [] (no rows merged), no dispatch, no failures
    (drop happened pre-dispatch, not at fan-out).
    """
    from mcp_zeeker.tools.search import search

    # Test scopes to databases=["zeeker-judgements"] — other DBs not consulted.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            ["weird_table"],
            fts_tables={"weird_table": "weird_table_fts"},
            columns={"weird_table": ["odd_column_a", "odd_column_b", "odd_column_c"]},
        ),
        is_reusable=True,
    )

    # Deliberately register NO per-table response — if the test passes, no
    # request to /zeeker-judgements/weird_table.json is dispatched anyway.
    envelope = await search(query="x", databases=["zeeker-judgements"], limit=10)

    # No dispatch happened — the table was dropped at discovery.
    weird_reqs = [
        r
        for r in httpx_mock.get_requests()
        if _table_url_re("zeeker-judgements", "weird_table").match(str(r.url))
    ]
    assert weird_reqs == [], "weird_table must not be dispatched — dropped at discovery"

    assert envelope.pagination.upstream_total_hits == {}, (
        "upstream_total_hits must be empty — no table dispatched"
    )
    assert envelope.pagination.failed_tables == 0, (
        "failed_tables must be 0 — drop happened pre-dispatch, not at fan-out"
    )
    assert envelope.data == [], "no rows expected when the only table is dropped"


async def test_zero_total_hits_table_still_in_upstream_total_hits(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """04-RESEARCH Probe 6 / D4-17 invariant.

    Searchable table that legitimately matches zero rows for the query STILL
    appears in envelope.pagination.upstream_total_hits with value 0 — so the
    LLM sees "this table was searched, found nothing." Drill-down hint relies
    on this.

    Uses the captured fixture `sg_gov_newsrooms__acra_news__zero_hits.json`
    (rows=[], filtered_table_rows_count=0).
    """
    from mcp_zeeker.tools.search import search

    # Test scopes to databases=["sg-gov-newsrooms"] — other DBs not consulted.
    httpx_mock.add_response(
        url=_db_url("sg-gov-newsrooms"),
        json=_tables_payload(
            ["acra_news"],
            fts_tables={"acra_news": "acra_news_fts"},
            columns={
                "acra_news": [
                    "title",
                    "published_date",
                    "summary",
                    "source_url",
                ],
            },
        ),
        is_reusable=True,
    )

    httpx_mock.add_response(
        url=_table_url_re("sg-gov-newsrooms", "acra_news"),
        json=_load_search_fixture("sg_gov_newsrooms__acra_news__zero_hits.json"),
        is_reusable=True,
    )

    envelope = await search(query="ZZZNOMATCHCANARY", databases=["sg-gov-newsrooms"], limit=10)

    assert envelope.data == [], "zero-hit table → no rows merged"
    totals = envelope.pagination.upstream_total_hits
    assert "sg-gov-newsrooms.acra_news" in totals, (
        "zero-hit table must still appear in upstream_total_hits (Probe 6 / D4-17)"
    )
    assert totals["sg-gov-newsrooms.acra_news"] == 0
