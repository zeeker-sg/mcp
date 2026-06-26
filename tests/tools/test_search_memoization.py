"""
Tests for #6b / #9 — eliminate redundant get_database calls in search discovery.

Asserts that a 4-DB search makes ≤1 get_database call per DB (instead of the
previous ~5× per DB). This is verified by counting upstream /{db}.json
requests dispatched during a single search call.
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
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _judgments_db_payload() -> dict:
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
                ],
                "primary_keys": ["id"],
                "fts_table": "judgments_fts",
            },
            {
                "name": "judgments_fragments",
                "hidden": False,
                "count": 5000,
                "columns": ["id", "judgment_id", "ordinal"],
                "primary_keys": ["id"],
                "fts_table": "judgments_fragments_fts",
            },
        ]
    }


def _sg_gov_db_payload() -> dict:
    """sg-gov-newsrooms with 3 FTS-having tables to exercise per-table memoization."""
    return {
        "tables": [
            {
                "name": "acra_news",
                "hidden": False,
                "count": 50,
                "columns": ["id", "source_url", "category", "title", "published_date", "summary"],
                "primary_keys": [],
                "fts_table": "acra_news_fts",
            },
            {
                "name": "agc_news",
                "hidden": False,
                "count": 30,
                "columns": ["id", "source_url", "category", "title", "published_date", "summary"],
                "primary_keys": [],
                "fts_table": "agc_news_fts",
            },
            {
                "name": "ccs_news",
                "hidden": False,
                "count": 20,
                "columns": ["id", "source_url", "category", "title", "published_date", "summary"],
                "primary_keys": [],
                "fts_table": "ccs_news_fts",
            },
        ]
    }


def _sglawwatch_db_payload() -> dict:
    return {
        "tables": [
            {
                "name": "commentaries",
                "hidden": False,
                "count": 25,
                "columns": ["id", "title", "author", "pub_date", "link", "description"],
                "primary_keys": [],
                "fts_table": "commentaries_fts",
            },
        ]
    }


def _pdpc_db_payload() -> dict:
    """pdpc has no FTS tables — all fts_table=None."""
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


def _stub_all_dbs(httpx_mock: pytest_httpx.HTTPXMock) -> None:
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


def _stub_table_responses(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """Stub per-table FTS responses for all searchable tables."""
    happy = {
        "rows": [{"title": "r", "source_url": "https://r"}],
        "filtered_table_rows_count": 1,
        "next": None,
        "truncated": False,
        "columns": ["title", "source_url"],
    }
    for db, table in [
        ("zeeker-judgements", "judgments"),
        ("sg-gov-newsrooms", "acra_news"),
        ("sg-gov-newsrooms", "agc_news"),
        ("sg-gov-newsrooms", "ccs_news"),
        ("sglawwatch", "commentaries"),
    ]:
        httpx_mock.add_response(url=_table_url_re(db, table), json=happy, is_reusable=True)


def _count_db_fetches(httpx_mock: pytest_httpx.HTTPXMock, db: str) -> int:
    """Count how many times /{db}.json was fetched."""
    url = _db_url(db)
    return sum(1 for r in httpx_mock.get_requests() if str(r.url) == url)


async def test_get_database_called_once_per_db(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """#6b / #9: A 4-DB search makes ≤1 get_database call per DB.

    Before #9, a search made ~20 get_database calls (2 + N per DB, where N is
    the number of searchable tables). With request-scoped memoization + threading
    columns from discovery, this drops to exactly 1 per DB.
    """
    from mcp_zeeker.tools.search import search

    _stub_all_dbs(httpx_mock)
    _stub_table_responses(httpx_mock)

    await search(query="appeal")

    # Each DB should be fetched at most once via /{db}.json.
    # Without the DatabaseSummaryCache (no cache bound in this test), the
    # request-scoped memoization in the handler ensures 1 fetch per DB.
    for db in config.ALLOWED_DATABASES:
        count = _count_db_fetches(httpx_mock, db)
        assert count <= 1, f"expected ≤1 get_database fetch for {db}, got {count}"


async def test_get_database_called_once_per_db_with_cache(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """#6c / #10: With DatabaseSummaryCache bound, steady-state search makes 0
    discovery upstream calls (all served from cache after the first request)."""
    from mcp_zeeker.core.database_summary_cache import DatabaseSummaryCache
    from mcp_zeeker.tools.search import search

    _stub_all_dbs(httpx_mock)
    _stub_table_responses(httpx_mock)

    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        dc_token = DatasetteClient.bind(dc)
        cache = DatabaseSummaryCache(dc, ttl=300)
        cache_token = DatabaseSummaryCache.bind(cache)
        try:
            # First search: populates cache (1 fetch per DB).
            await search(query="appeal")

            first_counts = {
                db: _count_db_fetches(httpx_mock, db) for db in config.ALLOWED_DATABASES
            }
            for db, count in first_counts.items():
                assert count <= 1, f"first search: ≤1 fetch for {db}, got {count}"

            # Second search: all served from cache (0 new fetches).
            await search(query="privacy")

            for db in config.ALLOWED_DATABASES:
                count = _count_db_fetches(httpx_mock, db)
                assert count == first_counts[db], (
                    f"second search: no new fetch for {db}, "
                    f"expected {first_counts[db]}, got {count}"
                )
        finally:
            DatabaseSummaryCache.reset(cache_token)
            DatasetteClient.reset(dc_token)
            DatabaseSummaryCache.clear_singleton()
