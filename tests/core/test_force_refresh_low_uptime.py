"""Regression tests: force_refresh must expire under a low-uptime monotonic clock.

Root cause under test: monotonic() is seconds-since-boot; a 0.0 sentinel in
force_refresh() read as *fresh* when host uptime < ttl (fresh CI runners,
freshly rebooted servers), making force_refresh a silent no-op. The fix uses
float("-inf") as the expired sentinel.
"""

from __future__ import annotations

import time as _time

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.database_summary_cache import DatabaseSummaryCache
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache


def _base() -> str:
    return config.UPSTREAM_URL.rstrip("/")


def _db_url(name: str) -> str:
    return f"{_base()}/{name}.json"


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


def _fetch_count(httpx_mock, url: str) -> int:
    return len([r for r in httpx_mock.get_requests() if str(r.url) == url])


def _freeze_boot_clock(seconds: float = 10.0):
    """Patch monotonic() to report a low-uptime clock (~seconds since boot)."""
    real = _time.monotonic
    _time.monotonic = lambda: seconds + (real() % 1.0)
    return real


async def test_database_summary_force_refresh_on_low_uptime_clock(
    cache_and_client, httpx_mock
):
    """Simulate a freshly booted host (uptime ~10s < ttl 300).

    With the old 0.0 sentinel, now(~10) - 0.0 < ttl read as fresh and
    force_refresh fetched nothing. With -inf it must fetch again.
    """
    cache, _ = cache_and_client
    url = _db_url("zeeker-judgements")

    real = _freeze_boot_clock(10.0)
    try:
        await cache.get_database("zeeker-judgements")  # fetch #1
        before = _fetch_count(httpx_mock, url)
        assert before == 1, f"expected 1 fetch on first get_database, got {before}"

        await cache.force_refresh("zeeker-judgements")  # must fetch again
        after = _fetch_count(httpx_mock, url)
        assert after > before, (
            f"force_refresh must refetch on a low-uptime clock (had {before}, still {after})"
        )
    finally:
        _time.monotonic = real


async def test_metadata_force_refresh_on_low_uptime_clock(httpx_mock):
    """Same sentinel bug in MetadataCache.force_refresh: 0.0 read 'fresh' on a
    low-uptime clock; -inf must force a real upstream fetch."""
    httpx_mock.add_response(
        url=f"{_base()}/-/metadata.json",
        json={"databases": {"zeeker-judgements": {"tables": {}}}},
        is_reusable=True,
    )
    real = _freeze_boot_clock(10.0)
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        cache = MetadataCache(http, _base(), ttl=300)
        cache_token = MetadataCache.bind(cache)
        try:
            await cache._ensure_fresh()  # fetch #1
            before = len(httpx_mock.get_requests())
            assert before == 1, f"expected 1 metadata fetch, got {before}"

            await cache.force_refresh()  # must fetch again
            after = len(httpx_mock.get_requests())
            assert after > before, (
                f"force_refresh must refetch on a low-uptime clock (had {before}, still {after})"
            )
        finally:
            MetadataCache.reset(cache_token)
            MetadataCache.clear_singleton()
            DatasetteClient.reset(token)
            _time.monotonic = real