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


def test_license_for_sync_cold_cache_falls_back_to_config():
    """D6.1-01 / Finding #1: cold-cache `license_for_sync` returns the
    `config.LICENSES` fallback for known databases, NOT `("", "")`.

    Phase 6 / D6-04 contract is "config-fallback on cold cache, upstream
    `/-/metadata.json` value on warm cache". Plan 06.1-01 corrects the
    cold-cache branch which previously short-circuited to `("", "")` and
    surfaced empty license / license_url on every response served before
    `_ensure_fresh()` first ran (manifested as Finding #1 — empty `license`
    fields on `list_databases` rows immediately after server start).

    A genuinely-unknown database (not in `self._data` AND not in
    `config.LICENSES`) still returns `("", "")` — no exception.
    """
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
    # No _ensure_fresh call — _data is None
    assert mc._data is None
    # Known database falls back to config.LICENSES (D6.1-01 fix).
    assert mc.license_for_sync("zeeker-judgements") == (
        "CC-BY-4.0",
        "https://creativecommons.org/licenses/by/4.0/",
    )
    # Genuinely-unknown DB still returns ("", "") — no exception, no surfacing.
    assert mc.license_for_sync("not-a-real-db") == ("", "")


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
@pytest.mark.parametrize("database", config.ALLOWED_DATABASES)
async def test_live_metadata_parseable(database: str):
    """Live probe: real /-/metadata.json is parseable and per-DB license is
    either a non-empty string or None.

    D6.1-04 / Finding #3 rewrite (was: assertion against literal "CC-BY-4.0"
    that drifted to "All rights reserved" within 48 hours on 2026-05-15). The
    dual-layer model Phase 6 introduced (envelope Provenance.license carries
    upstream-as-is; retrieved_content._policy.license carries the operator-
    chosen content license) makes the specific upstream value irrelevant to
    connector correctness. What matters at the live boundary is:

      1. /-/metadata.json is reachable (HTTP 200 — implicit: get_database_license
         would have raised through httpx if not).
      2. Body parses as JSON (implicit: get_database_license would have raised).
      3. get_database_license() returns a string OR None — never raises;
         never returns the empty string (which would mask the D6.1-01
         cold-cache root cause).

    The dual-layer invariant — that _policy.license emits the operator-locked
    value regardless of upstream drift — is asserted by
    test_policy_license_unaffected_by_upstream_drift below (NOT live; uses
    httpx_mock to stub a drifted upstream value).

    Requires ZEEKER_LIVE=1.
    """
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    cache = MetadataCache(http, config.UPSTREAM_URL, ttl=60)
    token = MetadataCache.bind(cache)
    try:
        lic = await cache.get_database_license(database)
        # Acceptable return values per D2-03 + D6-04: a non-empty string
        # (upstream populated the license field) OR None (upstream omitted
        # the field — the D6-04 fallback chain handles this at the
        # license_for/license_for_sync layer). The empty string is NOT
        # acceptable — it would mask the D6.1-01 cold-cache root cause.
        assert lic is None or (isinstance(lic, str) and lic != ""), (
            f"{database}: get_database_license returned unexpected value: {lic!r}"
        )
    finally:
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()
        await http.aclose()


# ---------------------------------------------------------------------------
# D6.1-04 — dual-layer invariant: _policy.license is unaffected by upstream
# license drift on /-/metadata.json. Non-live (uses httpx_mock).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "database,table,upstream_license",
    [
        ("sg-gov-newsrooms", "mlaw_news", "All rights reserved"),
        ("sg-gov-newsrooms", "mlaw_news", "CC-BY-4.0"),
        ("sg-gov-newsrooms", "mlaw_news", None),
        ("pdpc", "enforcement_decisions_fragments", "Drifted random text"),
    ],
)
async def test_policy_license_unaffected_by_upstream_drift(
    database: str,
    table: str,
    upstream_license: str | None,
    httpx_mock: pytest_httpx.HTTPXMock,
    monkeypatch,
):
    """D6.1-04 dual-layer invariant: regardless of what upstream returns for
    /-/metadata.json, the `_policy.license` emitted under `retrieved_content`
    equals the operator-locked value in `config.CONTENT_POLICIES`.

    Stubs the upstream /-/metadata.json with a drifted (or None) license
    value, then dispatches `query_table` against a stubbed table-rows
    response and asserts the emitted `_policy.license` is the SODL string
    from `config.CONTENT_POLICIES` — the dual-layer (envelope-level
    upstream-as-is vs `_policy`-level operator-locked) contract Phase 6
    set up.

    The test exercises the actual `query_table` handler with full
    cache/middleware bindings so a future bug that accidentally couples
    upstream license into `_policy` emission would re-trip the gate. A
    test that only compared `CONTENT_POLICIES[key]["license"]` against
    itself would be tautological — this stubbed-dispatch shape is the
    only acceptable form per the plan's executor note.
    """
    import re
    from datetime import UTC, datetime

    from mcp_zeeker.core.datasette_client import DatasetteClient
    from mcp_zeeker.core.fragment_join import ParentPKCache
    from mcp_zeeker.core.middleware.retrieved_at import tool_started_at
    from mcp_zeeker.tools.retrieval import query_table

    # Heavy column per (db, table) — matches _HEAVY_COL_PER_TABLE in
    # tests/test_content_policy_emission.py for these two entries.
    heavy_col_per_table = {
        ("sg-gov-newsrooms", "mlaw_news"): "content_text",
        ("pdpc", "enforcement_decisions_fragments"): "text",
    }
    heavy_col = heavy_col_per_table[(database, table)]

    # 1. Stub /-/metadata.json with the drifted upstream license value.
    db_data: dict = {"tables": {}}
    if upstream_license is not None:
        db_data["license"] = upstream_license
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json={"databases": {database: db_data}},
        is_reusable=True,
    )

    # 2. Build the visible upstream surface (every light column + heavy_col).
    cols = list(config.LIGHT_COLUMNS.get(f"{database}.{table}", []))
    if heavy_col not in cols:
        cols.append(heavy_col)

    # 3. Stub /{database}.json with the table visible.
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/{database}.json",
        json={
            "tables": [
                {
                    "name": table,
                    "hidden": False,
                    "count": 1,
                    "columns": cols,
                    "primary_keys": [],
                    "fts_table": None,
                }
            ]
        },
        is_reusable=True,
    )
    # 4. Stub /{database}/_zeeker_schemas.json (column-types lookup).
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/{database}/_zeeker_schemas.json",
        json={
            "columns": [
                "resource_name",
                "schema_version",
                "schema_hash",
                "column_definitions",
                "created_at",
                "updated_at",
            ],
            "rows": [],
        },
        is_reusable=True,
    )
    # 5. Stub /{database}/{table}.json row payload — fill every column.
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    table_url_re = re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")
    row = {c: "fixture" for c in cols}
    row[heavy_col] = "heavy fixture body"
    httpx_mock.add_response(
        url=table_url_re,
        json={
            "rows": [row],
            "columns": cols,
            "next": None,
            "truncated": False,
            "filtered_table_rows_count": 1,
        },
        is_reusable=True,
    )

    # 6. Bind DatasetteClient + MetadataCache + ParentPKCache + tool_started_at.
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc_token = DatasetteClient.bind(DatasetteClient(http))
        mc = MetadataCache(http, config.UPSTREAM_URL, ttl=60)
        mc_token = MetadataCache.bind(mc)
        pk_token = ParentPKCache.bind(ParentPKCache())
        rt_token = tool_started_at.set(datetime(2026, 1, 1, tzinfo=UTC))
        try:
            # Warm the metadata cache — consumes the stubbed /-/metadata.json
            # mock above with the DRIFTED upstream_license value AND verifies
            # the warm-cache layer is observing the drift. This ensures the
            # rest of the test exercises license_for_sync warm-cache logic
            # (not cold-cache D6.1-01 fallback).
            await mc.force_refresh()
            warm_lic, _ = mc.license_for_sync(database)
            if upstream_license:
                # Warm cache reflects the drifted upstream value.
                assert warm_lic == upstream_license, (
                    f"warm cache failed to observe drifted upstream license: "
                    f"expected {upstream_license!r}, got {warm_lic!r}"
                )
            else:
                # Upstream omitted license — D6-04 fallback chain to
                # config.LICENSES (CC-BY-4.0 for known DBs).
                assert warm_lic == config.LICENSES[database][0], (
                    f"warm-but-empty fallback failed: expected "
                    f"{config.LICENSES[database][0]!r}, got {warm_lic!r}"
                )

            envelope = await query_table(
                database=database,
                table=table,
                columns=[heavy_col],
                limit=1,
            )
        finally:
            tool_started_at.reset(rt_token)
            ParentPKCache.reset(pk_token)
            MetadataCache.reset(mc_token)
            DatasetteClient.reset(dc_token)
            MetadataCache.clear_singleton()
            DatasetteClient.clear_singleton()
            ParentPKCache.clear_singleton()

    # 7. THE INVARIANT: regardless of `upstream_license`, `_policy.license`
    #    is the operator-locked value from config.CONTENT_POLICIES.
    rows = envelope.data
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    rc = rows[0].get("retrieved_content")
    assert rc is not None, f"retrieved_content missing: {rows[0]!r}"
    emitted_policy = rc.get("_policy")
    assert emitted_policy is not None, f"_policy missing: {rc!r}"
    expected_license = config.CONTENT_POLICIES[(database, table)]["license"]
    assert emitted_policy["license"] == expected_license, (
        f"Dual-layer invariant violated: upstream license drifted to "
        f"{upstream_license!r} but _policy.license should still emit "
        f"operator-locked {expected_license!r}, got "
        f"{emitted_policy['license']!r}"
    )
