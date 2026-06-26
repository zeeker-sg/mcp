"""
Tests for #6c / #10 — DatabaseSummaryCache: TTL caching + single-flight.

Asserts:
- Cache hit: second get_database call for the same DB within TTL makes no
  additional upstream HTTP request.
- TTL expiry: after TTL, a new upstream fetch occurs.
- Single-flight: concurrent get_database calls for the same DB share one fetch.
- Stale-on-error: if a refresh fails, the cached value is served.
- Singleton + contextvar lifecycle (bind/reset/clear_singleton).
- force_refresh bypasses TTL.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.database_summary_cache import DatabaseSummaryCache
from mcp_zeeker.core.datasette_client import DatasetteClient


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _sample_payload() -> dict:
    return {
        "tables": [
            {
                "name": "judgments",
                "hidden": False,
                "count": 219,
                "columns": ["id", "citation", "case_name", "source_url"],
                "primary_keys": ["id"],
                "fts_table": "judgments_fts",
            },
        ]
    }


@pytest.fixture
async def cache_and_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Bind a DatabaseSummaryCache wrapping a DatasetteClient."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_sample_payload(), is_reusable=True
    )
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        cache = DatabaseSummaryCache(dc, ttl=300)
        cache_token = DatabaseSummaryCache.bind(cache)
        yield cache, dc
        DatabaseSummaryCache.reset(cache_token)
        DatasetteClient.reset(token)
        DatabaseSummaryCache.clear_singleton()


async def test_cache_hit_no_second_fetch(cache_and_client, httpx_mock: pytest_httpx.HTTPXMock):
    """Second get_database within TTL makes no additional upstream request."""
    cache, _ = cache_and_client

    result1 = await cache.get_database("zeeker-judgements")
    assert result1.tables[0].name == "judgments"

    result2 = await cache.get_database("zeeker-judgements")
    assert result2.tables[0].name == "judgments"

    # Only one upstream request should have been made.
    db_requests = [
        r for r in httpx_mock.get_requests() if str(r.url) == _db_url("zeeker-judgements")
    ]
    assert len(db_requests) == 1, f"expected 1 upstream fetch, got {len(db_requests)}"


async def test_ttl_expiry_triggers_refetch(cache_and_client, httpx_mock: pytest_httpx.HTTPXMock):
    """After TTL expires, a new upstream fetch occurs."""
    cache, _ = cache_and_client
    cache._ttl = 0  # expire immediately

    await cache.get_database("zeeker-judgements")
    await cache.get_database("zeeker-judgements")

    db_requests = [
        r for r in httpx_mock.get_requests() if str(r.url) == _db_url("zeeker-judgements")
    ]
    assert len(db_requests) == 2, f"expected 2 fetches with TTL=0, got {len(db_requests)}"


async def test_single_flight_concurrent_misses(
    cache_and_client, httpx_mock: pytest_httpx.HTTPXMock
):
    """Concurrent get_database calls for the same DB share one fetch (single-flight)."""
    cache, _ = cache_and_client

    # Fire 8 concurrent calls — the 8-parallel-search burst pattern from #6.
    results = await asyncio.gather(*[cache.get_database("zeeker-judgements") for _ in range(8)])

    # All results are the same object (or equivalent).
    assert all(r.tables[0].name == "judgments" for r in results)

    # Only one upstream fetch.
    db_requests = [
        r for r in httpx_mock.get_requests() if str(r.url) == _db_url("zeeker-judgements")
    ]
    assert len(db_requests) == 1, (
        f"single-flight should share 1 fetch for 8 concurrent calls, got {len(db_requests)}"
    )


async def test_stale_on_error_serves_cached(httpx_mock: pytest_httpx.HTTPXMock):
    """If a refresh fails after a successful fetch, the cached value is served."""
    # First call succeeds.
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_sample_payload())

    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        cache = DatabaseSummaryCache(dc, ttl=0)  # always stale → always refresh
        cache_token = DatabaseSummaryCache.bind(cache)
        try:
            # First call: fetches successfully.
            result1 = await cache.get_database("zeeker-judgements")
            assert result1.tables[0].name == "judgments"

            # Second call: upstream fails with 502 (retry-once means 2x 502 responses).
            httpx_mock.add_response(url=_db_url("zeeker-judgements"), status_code=502)
            httpx_mock.add_response(url=_db_url("zeeker-judgements"), status_code=502)
            result2 = await cache.get_database("zeeker-judgements")
            assert result2.tables[0].name == "judgments"
        finally:
            DatabaseSummaryCache.reset(cache_token)
            DatasetteClient.reset(token)
            DatabaseSummaryCache.clear_singleton()


async def test_force_refresh_bypasses_ttl(cache_and_client, httpx_mock: pytest_httpx.HTTPXMock):
    """force_refresh triggers a new upstream fetch even within TTL."""
    cache, _ = cache_and_client

    await cache.get_database("zeeker-judgements")

    # Add a second response for the forced refresh.
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_sample_payload())

    await cache.force_refresh("zeeker-judgements")

    db_requests = [
        r for r in httpx_mock.get_requests() if str(r.url) == _db_url("zeeker-judgements")
    ]
    assert len(db_requests) == 2, f"expected 2 fetches after force_refresh, got {len(db_requests)}"


async def test_cache_isolated_per_context(httpx_mock: pytest_httpx.HTTPXMock):
    """Each contextvar-bound cache is independent (test isolation)."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_sample_payload(), is_reusable=True
    )

    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)

        cache1 = DatabaseSummaryCache(dc, ttl=300)
        token1 = DatabaseSummaryCache.bind(cache1)
        assert DatabaseSummaryCache.current() is cache1
        # Actually fetch to consume the mocked response.
        await cache1.get_database("zeeker-judgements")

        cache2 = DatabaseSummaryCache(dc, ttl=300)
        token2 = DatabaseSummaryCache.bind(cache2)
        assert DatabaseSummaryCache.current() is cache2

        DatabaseSummaryCache.reset(token2)
        assert DatabaseSummaryCache.current() is cache1

        DatabaseSummaryCache.reset(token1)
        DatasetteClient.reset(token)
        DatabaseSummaryCache.clear_singleton()
