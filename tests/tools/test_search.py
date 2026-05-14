"""
Handler-level tests for the cross-DB search tool — GREEN (Plan 04-02 Task 2).

Tests the `@mcp.tool` `search` handler post body-fill:
- Stubs upstream /{db}.json (auto-discovery shape with `fts_table` populated).
- Stubs per-table /{db}/{table}.json?_search=… responses via regex matcher.
- Asserts envelope preview-row shape (D4-21), heavy-column absence
  (D3-19 / D4-12), multi-DB provenance (D4-16), pagination
  upstream_total_hits population (D4-17), and that no site-wide /-/search.json
  call is dispatched (Pitfall 3).
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for /{database}/{table}.json with any query string."""
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Local DatasetteClient bound to current context (mirror Phase 3 fixture)."""
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _judgments_db_payload() -> dict:
    """Canonical /zeeker-judgements.json — judgments has FTS, judgments_fragments
    has FTS but is denylisted by `_fragments` suffix."""
    return {
        "tables": [
            {
                "name": "judgments",
                "hidden": False,
                "count": 219,
                "columns": [
                    "id",
                    "citation",
                    "case_name",
                    "decision_date",
                    "source_url",
                    "summary",
                    "content_text",
                ],
                "primary_keys": ["id"],
                "fts_table": "judgments_fts",
            },
            {
                "name": "judgments_fragments",
                "hidden": False,
                "count": 5000,
                "columns": [
                    "id",
                    "judgment_id",
                    "ordinal",
                    "paragraph_number",
                ],
                "primary_keys": ["id"],
                # FTS exists upstream but denylisted by _fragments suffix.
                "fts_table": "judgments_fragments_fts",
            },
        ]
    }


def _pdpc_db_payload() -> dict:
    """pdpc has NO FTS — all tables have fts_table=None.

    enforcement_decisions returns rowid-ordered rows if you query it with
    `_search=` (Pitfall 3) — the safety gate must prevent dispatch.
    """
    return {
        "tables": [
            {
                "name": "enforcement_decisions",
                "hidden": False,
                "count": 100,
                "columns": ["title", "organisation", "decision_url", "summary"],
                "primary_keys": [],
                "fts_table": None,
            },
        ]
    }


def _sg_gov_db_payload() -> dict:
    """sg-gov-newsrooms — one table with FTS for simplicity in tests."""
    return {
        "tables": [
            {
                "name": "acra_news",
                "hidden": False,
                "count": 50,
                "columns": [
                    "id",
                    "source_url",
                    "category",
                    "title",
                    "published_date",
                    "content_text",
                    "summary",
                ],
                "primary_keys": [],
                "fts_table": "acra_news_fts",
            },
        ]
    }


def _sglawwatch_db_payload() -> dict:
    """sglawwatch — one table with FTS for simplicity."""
    return {
        "tables": [
            {
                "name": "commentaries",
                "hidden": False,
                "count": 25,
                "columns": [
                    "id",
                    "title",
                    "author",
                    "pub_date",
                    "link",
                    "description",
                    "content_text",
                ],
                "primary_keys": [],
                "fts_table": "commentaries_fts",
            },
        ]
    }


def _stub_four_dbs(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """Stub the four ALLOWED_DATABASES /{db}.json responses."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_db_url("sg-gov-newsrooms"), json=_sg_gov_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_db_url("sglawwatch"), json=_sglawwatch_db_payload(), is_reusable=True
    )


def _judgments_search_rows(n: int = 2, filtered_count: int = 219) -> dict:
    """Per-table FTS response — judgments shape with heavy content_text on rows."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "rowid": i + 1,
                "id": str(i + 1),
                "citation": f"2026 SGDC {i + 1}",
                "case_name": f"Title row {i + 1}",
                "decision_date": "2026-01-01",
                "source_url": f"https://example.com/j/{i + 1}",
                "summary": f"summary {i + 1}",
                # Heavy column — must NOT surface in the preview envelope.
                "content_text": "this is very large body text",
            }
        )
    return {
        "rows": rows,
        "columns": [
            "rowid",
            "id",
            "citation",
            "case_name",
            "decision_date",
            "source_url",
            "summary",
            "content_text",
        ],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": filtered_count,
    }


def _acra_search_rows(n: int = 2, filtered_count: int = 32) -> dict:
    """Per-table FTS response — acra_news shape."""
    rows = []
    for i in range(n):
        rows.append(
            {
                "rowid": i + 1,
                "id": str(i + 1),
                "source_url": f"https://example.com/a/{i + 1}",
                "category": "news",
                "title": f"acra row {i + 1}",
                "published_date": "2026-01-15",
                "content_text": "heavy body",
                "summary": f"sgov summary {i + 1}",
            }
        )
    return {
        "rows": rows,
        "columns": [
            "rowid",
            "id",
            "source_url",
            "category",
            "title",
            "published_date",
            "content_text",
            "summary",
        ],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": filtered_count,
    }


def _commentaries_search_rows(n: int = 1, filtered_count: int = 5) -> dict:
    rows = []
    for i in range(n):
        rows.append(
            {
                "rowid": i + 1,
                "id": str(i + 1),
                "title": f"commentary {i + 1}",
                "author": "Doe",
                "pub_date": "2026-01-20",
                "link": f"https://example.com/c/{i + 1}",
                "description": f"desc {i + 1}",
                "content_text": "heavy body",
            }
        )
    return {
        "rows": rows,
        "columns": [
            "rowid",
            "id",
            "title",
            "author",
            "pub_date",
            "link",
            "description",
            "content_text",
        ],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": filtered_count,
    }


def _stub_per_table_responses(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """Stub /{db}/{table}.json for each searchable table."""
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_search_rows(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("sg-gov-newsrooms", "acra_news"),
        json=_acra_search_rows(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("sglawwatch", "commentaries"),
        json=_commentaries_search_rows(),
        is_reusable=True,
    )


async def test_default_databases_searches_all_four(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """SEARCH-02 / D4-10: search(query='x') with no databases= dispatches per-table
    FTS for all 4 ALLOWED_DATABASES (pdpc returns 0 naturally because no FTS)."""
    from mcp_zeeker.tools.search import search

    _stub_four_dbs(httpx_mock)
    _stub_per_table_responses(httpx_mock)

    envelope = await search(query="appeal")

    # Rows came in.
    assert len(envelope.data) >= 1
    # pdpc has no FTS — must NOT appear in upstream_total_hits keys.
    pdpc_keys = [k for k in envelope.pagination.upstream_total_hits if k.startswith("pdpc.")]
    assert pdpc_keys == [], f"pdpc must not appear in upstream_total_hits: {pdpc_keys}"


async def test_preview_shape_uniform(datasette_client, httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """SEARCH-04 / D4-21 + Phase 6 D6-03 / D6-05: every row in envelope.data has
    exactly the 9 preview keys — the original 6 preview keys plus the Phase 6
    additions (per-row license, license_url, citation)."""
    from mcp_zeeker.tools.search import search

    _stub_four_dbs(httpx_mock)
    _stub_per_table_responses(httpx_mock)

    envelope = await search(query="appeal")

    expected_keys = {
        "title",
        "date",
        "summary",
        "url",
        "database",
        "table",
        # Phase 6 / D6-03 + D6-05 additions. `_citation` (underscore prefix)
        # avoids collision with upstream columns literally named `citation`
        # (e.g., judgments.citation). Matches the canonical convention in
        # core/citation.py.
        "license",
        "license_url",
        "_citation",
    }
    for row in envelope.data:
        assert set(row.keys()) == expected_keys, (
            f"row keys mismatch: {set(row.keys())} vs {expected_keys}"
        )


async def test_no_heavy_columns_in_preview(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D3-19 / D4-12: heavy columns NEVER appear in preview rows.

    Stubs include `content_text` on every upstream row; the handler must strip.
    """
    from mcp_zeeker.tools.search import search

    _stub_four_dbs(httpx_mock)
    _stub_per_table_responses(httpx_mock)

    envelope = await search(query="appeal")
    assert envelope.data, "expected at least one row"
    for row in envelope.data:
        assert set(row.keys()) & config.HEAVY_COLUMNS == set(), (
            f"heavy column leaked: {set(row.keys()) & config.HEAVY_COLUMNS}"
        )


async def test_envelope_provenance_for_search(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-16: envelope.provenance.database is None, .table is None,
    .license == LICENSE_MIXED (multi-DB scope)."""
    from mcp_zeeker.tools.search import search

    _stub_four_dbs(httpx_mock)
    _stub_per_table_responses(httpx_mock)

    envelope = await search(query="appeal")
    assert envelope.provenance.database is None
    assert envelope.provenance.table is None
    assert envelope.provenance.license == config.LICENSE_MIXED


async def test_upstream_total_hits_populated(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-17: envelope.pagination.upstream_total_hits keyed `<db>.<table>` and
    populated from each per-table `filtered_table_rows_count`."""
    from mcp_zeeker.tools.search import search

    _stub_four_dbs(httpx_mock)
    # Specific counts to assert against.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_search_rows(filtered_count=219),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("sg-gov-newsrooms", "acra_news"),
        json=_acra_search_rows(filtered_count=32),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("sglawwatch", "commentaries"),
        json=_commentaries_search_rows(filtered_count=5),
        is_reusable=True,
    )

    envelope = await search(query="appeal")
    totals = envelope.pagination.upstream_total_hits
    assert totals.get("zeeker-judgements.judgments") == 219
    assert totals.get("sg-gov-newsrooms.acra_news") == 32
    assert totals.get("sglawwatch.commentaries") == 5


async def test_no_site_wide_search_dispatched(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-18 / Pitfall 3: handler NEVER hits /-/search.json — only per-table FTS."""
    from mcp_zeeker.tools.search import search

    _stub_four_dbs(httpx_mock)
    _stub_per_table_responses(httpx_mock)

    await search(query="appeal")

    site_wide_calls = [r for r in httpx_mock.get_requests() if "/-/search.json" in str(r.url)]
    assert site_wide_calls == [], (
        f"handler must not dispatch /-/search.json: {[str(r.url) for r in site_wide_calls]}"
    )


async def test_limit_one_returns_exactly_one(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """SEARCH-02 / D4-11: limit=1 returns exactly one row even if many matched."""
    from mcp_zeeker.tools.search import search

    # Only zeeker-judgements is queried; the other DB stubs go unused so flag
    # them optional. This test scopes via `databases=["zeeker-judgements"]`.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_search_rows(n=3),
        is_reusable=True,
    )

    envelope = await search(query="appeal", databases=["zeeker-judgements"], limit=1)
    assert len(envelope.data) == 1
