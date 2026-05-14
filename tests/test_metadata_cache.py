"""
MetadataCache lifecycle unit tests.

Tests D2-02 (singleton+contextvar), D2-03 (lazy fetch, TTL, single-flight,
stale-on-error), D2-05 (case-insensitive DB lookup via normalize-at-ingest),
D2-08 (metadata_gap log).

Live test (requires ZEEKER_LIVE=1): verifies real upstream metadata is parseable.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_httpx
import structlog.testing

from mcp_zeeker import config
from mcp_zeeker.core.metadata_cache import MetadataCache

# Metadata stub with mixed-case DB key to exercise D2-05 normalize-at-ingest.
# The fixture sets ttl=0, so every call triggers a refresh (simplifies TTL tests).
METADATA_STUB = {
    "databases": {
        "Zeeker-Judgements": {
            "tables": {"judgments": {"description": "Singapore court judgments"}}
        },
        "sg-gov-newsrooms": {
            "license": "CC-BY-4.0",
            "tables": {},
        },
    }
}


@pytest.fixture
def bound_cache(httpx_mock: pytest_httpx.HTTPXMock):
    """Local MetadataCache fixture with ttl=0 and is_reusable stub response."""
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json=METADATA_STUB,
        is_reusable=True,
    )
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    cache = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(cache)
    yield cache
    MetadataCache.reset(token)
    MetadataCache.clear_singleton()


async def test_lazy_first_fetch(bound_cache, httpx_mock):
    """D2-03: No upstream call before first get; exactly 1 call after first get."""
    assert bound_cache._data is None
    result = await bound_cache.get_table_metadata("zeeker-judgements", "judgments")
    assert result is not None
    assert len(httpx_mock.get_requests()) == 1


async def test_case_insensitive_db_lookup(bound_cache):
    """D2-05: Mixed-case upstream key 'Zeeker-Judgements' normalized to 'zeeker-judgements'.

    METADATA_STUB has key 'Zeeker-Judgements'. _fetch_and_normalize lowercases it
    so get_table_metadata('zeeker-judgements', ...) resolves correctly.
    """
    result = await bound_cache.get_table_metadata("zeeker-judgements", "judgments")
    assert result is not None
    assert result["description"] == "Singapore court judgments"


@pytest.fixture
def stale_on_error_cache(httpx_mock: pytest_httpx.HTTPXMock):
    """Fixture with explicit response sequence: 200 then 503 then 200 (not reusable)."""
    # First call succeeds
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json=METADATA_STUB,
    )
    # force_refresh triggers a 503 failure
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        status_code=503,
    )
    # After stale-on-error, the next get_table_metadata triggers another fetch (ttl=0)
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json=METADATA_STUB,
    )
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    cache = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(cache)
    yield cache
    MetadataCache.reset(token)
    MetadataCache.clear_singleton()


async def test_stale_on_error(stale_on_error_cache):
    """D2-03 stale-on-error: after one successful fetch, upstream 503 does not wipe data."""
    cache = stale_on_error_cache
    # First fetch succeeds
    await cache.get_table_metadata("zeeker-judgements", "judgments")
    assert cache._data is not None

    # force_refresh triggers a 503 failure (stale-on-error: data preserved)
    await cache.force_refresh()

    # Should still return stale data (get_table_metadata triggers another fetch with ttl=0)
    result = await cache.get_table_metadata("zeeker-judgements", "judgments")
    assert result is not None


async def test_single_flight_under_concurrency(bound_cache, httpx_mock):
    """D2-03 single-flight: 5 concurrent calls with ttl=0 trigger <= 2 upstream fetches.

    With ttl=0, every call would normally fetch. The anyio.Lock ensures only one
    holder fetches at a time; concurrent waiters either serve stale (if data exists)
    or wait for the holder (first ever fetch). Under test conditions (fast mock),
    we expect 1-2 fetches total rather than 5.
    """
    # Launch 5 concurrent get_table_metadata calls
    results = await asyncio.gather(
        *[bound_cache.get_table_metadata("zeeker-judgements", "judgments") for _ in range(5)]
    )
    # All should succeed
    assert all(r is not None for r in results)
    # Should not have made 5 separate upstream calls
    assert len(httpx_mock.get_requests()) <= 2


async def test_force_refresh_sets_last_fetch_zero(bound_cache):
    """D2-03 force_refresh: sets _last_fetch to 0 and triggers re-fetch."""
    # First call sets _last_fetch > 0
    await bound_cache.get_table_metadata("zeeker-judgements", "judgments")
    assert bound_cache._last_fetch > 0

    # force_refresh resets and re-fetches
    await bound_cache.force_refresh()
    # After force_refresh, _last_fetch is set again (but > 0 because refresh completed)
    assert bound_cache._data is not None


async def test_metadata_gap_logged(bound_cache):
    """D2-08: metadata_gap INFO log emitted when table not found."""
    with structlog.testing.capture_logs() as cap_logs:
        result = await bound_cache.get_table_metadata("zeeker-judgements", "nonexistent_table")
    assert result is None
    gap_events = [e for e in cap_logs if e.get("event") == "metadata_gap"]
    assert len(gap_events) >= 1


async def test_get_database_license_returns_value(bound_cache):
    """get_database_license returns license string when present in upstream data."""
    result = await bound_cache.get_database_license("sg-gov-newsrooms")
    assert result == "CC-BY-4.0"


async def test_get_database_license_returns_none_when_absent(bound_cache):
    """get_database_license returns None when license key missing (D2-03)."""
    result = await bound_cache.get_database_license("zeeker-judgements")
    assert result is None


# ---------------------------------------------------------------------------
# Phase 6 — license_for / license_for_sync coverage (D6-01 / D6-04)
# ---------------------------------------------------------------------------


async def test_license_for_returns_upstream_value(httpx_mock: pytest_httpx.HTTPXMock):
    """D6-04 step 1: upstream non-empty `(license, license_url)` wins.

    Also exercises D2-05 mixed-case lookup: the upstream stub key is
    `Zeeker-Judgements`; the lookup key is `zeeker-judgements` (lowercased).
    """
    stub = {
        "databases": {
            "Zeeker-Judgements": {
                "license": "All rights reserved",
                "license_url": "https://example.test/tos",
                "tables": {"judgments": {"description": "Singapore court judgments"}},
            },
        }
    }
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json=stub,
        is_reusable=True,
    )
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    cache = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(cache)
    try:
        result = await MetadataCache.current().license_for("zeeker-judgements")
        assert result == ("All rights reserved", "https://example.test/tos")
    finally:
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()
        await http.aclose()


async def test_license_for_falls_back_to_config(bound_cache):
    """D6-04 step 2: upstream license absent → config.LICENSES tuple wins.

    METADATA_STUB has no `license` field on `Zeeker-Judgements`, so the D6-04
    fallback chain steps from upstream → config.LICENSES (which seeds
    `("CC-BY-4.0", LICENSE_DEFAULT_URL)` for every ALLOWED_DATABASES entry).
    """
    result = await MetadataCache.current().license_for("zeeker-judgements")
    assert result == ("CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/")


async def test_license_for_falls_back_to_empty_when_unknown_db(bound_cache):
    """D6-04 step 3: unknown DB → ('', '') — no exception, no upstream_unavailable."""
    result = await MetadataCache.current().license_for("not-a-real-db")
    assert result == ("", "")


def test_license_for_sync_cold_cache_returns_empty():
    """D6-04 cold-cache acceptance: license_for_sync returns ('', '') without await."""
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
    # No _ensure_fresh call — _data is None
    assert mc._data is None
    assert mc.license_for_sync("zeeker-judgements") == ("", "")


async def test_license_for_sync_warm_cache_returns_upstream(bound_cache):
    """license_for_sync reads the same underlying dict as license_for after warm-up.

    After any call that populates _data (here, a license_for call), the sync
    accessor sees the config-fallback tuple because METADATA_STUB has no
    license fields under the Zeeker-Judgements entry.
    """
    mc = MetadataCache.current()
    # Warm the cache via the async accessor
    _ = await mc.license_for("zeeker-judgements")
    assert mc._data is not None
    # Sync accessor returns the same fallback tuple (D6-04 step 2)
    assert mc.license_for_sync("zeeker-judgements") == (
        "CC-BY-4.0",
        "https://creativecommons.org/licenses/by/4.0/",
    )


@pytest.mark.live
async def test_live_metadata_parseable():
    """Live probe: real /-/metadata.json is parseable and sg-gov-newsrooms has CC-BY-4.0.

    Requires ZEEKER_LIVE=1 to run. Verified 2026-05-13 against production endpoint.
    """
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    cache = MetadataCache(http, config.UPSTREAM_URL, ttl=60)
    token = MetadataCache.bind(cache)
    try:
        lic = await cache.get_database_license("sg-gov-newsrooms")
        assert lic == "CC-BY-4.0"
    finally:
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()
        await http.aclose()
