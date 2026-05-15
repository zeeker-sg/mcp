"""
Tests for RateLimitMiddleware (Phase 7 — RATE-01..05 + OBS-03/04).

Wave 0 (plan 07-01) GREENed three tests covering the observable truths locked
in 07-01-PLAN.md must_haves:
  - test_burst_allows_20_rejects_21st (RATE-01 burst)
  - test_429_body_has_retry_after_seconds (RATE-05 body shape)
  - test_429_body_has_request_id (RATE-05 body shape)

Plans 07-02 / 07-03 / 07-04 GREENed the daily / refill / eviction / XFF /
Retry-After tests. Plan 07-06 GREENs the final two:
  - test_429_log_line_shape (OBS-03 log shape)
  - test_logs_no_user_input (OBS-04 / INJ-05 no-echo invariant)
All 15 originally-stubbed tests are now GREEN; no `@pytest.mark.skip`
decorators remain in this file at the end of Phase 7.

Test driving the ASGI __call__ directly (without a full Starlette app):
build a minimal `scope` dict + a captured-`send` pattern; concatenate
`messages[1:].body` to recover the response body bytes.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
import structlog

from mcp_zeeker.core.middleware.rate_limit import BucketState

# ---------------------------------------------------------------------------
# Helpers — minimal ASGI scope + captured send for 429 response inspection
# ---------------------------------------------------------------------------


def _build_scope(client_ip: str = "1.2.3.4") -> dict:
    """A minimal HTTP POST /mcp/ scope; the rate limiter never reads the body."""
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "headers": [(b"content-type", b"application/json")],
        "client": (client_ip, 443),
    }


async def _drive(rate_limiter, scope: dict) -> tuple[dict, bytes]:
    """Await rate_limiter(scope, receive, send); return (start_msg, body_bytes)."""
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict) -> None:
        messages.append(msg)

    await rate_limiter(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start, body


# ---------------------------------------------------------------------------
# GREEN — three observable truths from 07-01-PLAN.md must_haves
# ---------------------------------------------------------------------------


def test_burst_allows_20_rejects_21st(rate_limiter, fake_clock):
    """RATE-01: 20 burst tokens permit 20 requests; the 21st is denied with retry_after=1."""
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for i in range(20):
        allowed, retry_after = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
        assert allowed is True, f"request {i + 1} should be allowed"
        assert retry_after == 0
    allowed, retry_after = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
    assert allowed is False, "21st request should be denied"
    assert retry_after == 1, f"expected retry_after=1, got {retry_after}"


async def test_429_body_has_retry_after_seconds(rate_limiter, fake_clock):
    """RATE-05: 429 body includes integer retry_after_seconds + canonical code."""
    # Drain the bucket via _check_bucket (20 allowed) so the 21st full __call__
    # path returns the 429 response we want to inspect.
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for _ in range(20):
        allowed, _ = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
        assert allowed is True

    start, body_bytes = await _drive(rate_limiter, _build_scope("1.2.3.4"))
    assert start["status"] == 429
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}
    # Retry-After header is a base-10 integer string (RATE-05 / D7-02).
    assert headers.get("retry-after", "").isdigit(), (
        f"Retry-After must be integer string, got {headers.get('retry-after')!r}"
    )

    body = json.loads(body_bytes.decode("utf-8"))
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["message"] == "Rate limit exceeded"
    assert isinstance(body["error"]["retry_after_seconds"], int)
    assert body["error"]["retry_after_seconds"] >= 1


async def test_429_body_has_request_id(rate_limiter, fake_clock):
    """RATE-05 / ERR-03: 429 body echoes the request_id bound by RequestIdMiddleware."""
    # Drain the bucket so the next request triggers the 429 path.
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for _ in range(20):
        rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)

    # Bind the request_id contextvar — production binding happens upstream
    # in RequestIdMiddleware before RateLimitMiddleware runs. Use the
    # structlog contextvars module directly to simulate that binding.
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="rid-xyz")
    try:
        _, body_bytes = await _drive(rate_limiter, _build_scope("1.2.3.4"))
    finally:
        structlog.contextvars.clear_contextvars()

    body = json.loads(body_bytes.decode("utf-8"))
    assert body["error"]["request_id"] == "rid-xyz"


# ---------------------------------------------------------------------------
# Wave-0 stubs — plans 07-02 / 07-03 / 07-04 / 07-06 GREEN these
# Test names match 07-VALIDATION.md § Per-Task Verification Map exactly.
# ---------------------------------------------------------------------------


def test_sustained_refill_after_one_second(rate_limiter, fake_clock):
    """RATE-01: token refills after 1 second of idle (sustained 1 tok/s).

    Drain the burst (20 tokens) at fake_clock=0.0; verify the 21st call at the
    same instant denies. Advance fake_clock to 1.0 and assert the next call
    succeeds (one token has refilled). Advance to 2.0 and assert another
    success — sustained 1/s holds indefinitely.
    """
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # Drain 20 tokens at t=0.
    for i in range(20):
        allowed, _ = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
        assert allowed is True, f"burst-drain request {i + 1} should be allowed"

    # 21st at t=0 denies — burst is empty.
    allowed, retry_after = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
    assert allowed is False
    assert retry_after == 1

    # Advance to t=1.0 — exactly one token has refilled at 1 tok/s.
    fake_clock[0] = 1.0
    allowed, retry_after = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
    assert allowed is True, "request at t=1.0 should be allowed (token refilled)"
    assert retry_after == 0

    # Advance to t=2.0 — another token has refilled.
    fake_clock[0] = 2.0
    allowed, retry_after = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
    assert allowed is True, "request at t=2.0 should be allowed (sustained 1 tok/s)"
    assert retry_after == 0


def test_daily_limit_5000(rate_limiter, fake_clock, bucket_store):
    """RATE-01: 5001st request in same UTC day is rejected.

    Drive 5000 successful calls — drain the 20-token burst at t=0, then advance
    fake_clock by 1.0 each call so the bucket refills exactly one token in
    lock-step. After call 5000 the daily counter is exhausted; call 5001
    (still 2026-01-01) returns (False, retry_after) with retry_after >= 1.
    """
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # First 20 calls at t=0 drain the burst (no refill needed).
    for i in range(20):
        allowed, _ = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
        assert allowed is True, f"burst-drain request {i + 1} should be allowed"

    # Calls 21..5000: advance by 1.0 each call so exactly one token refills.
    for i in range(20, 5000):
        fake_clock[0] += 1.0
        allowed, _ = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
        assert allowed is True, f"sustained request {i + 1} should be allowed"

    # 5001st call (still on 2026-01-01) — daily ceiling enforces deny even
    # though the burst bucket would otherwise have a token.
    fake_clock[0] += 1.0
    allowed, retry_after = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc)
    assert allowed is False, "5001st request must be denied by daily ceiling"
    assert retry_after >= 1, f"daily-deny retry_after must be >= 1, got {retry_after}"

    # Verify bucket state after rejection.
    bucket = bucket_store["1.2.3.4"]
    assert bucket.daily_count == 5000
    assert bucket.daily_exceeded is True


def test_daily_reset_at_utc_midnight(rate_limiter, fake_clock, bucket_store):
    """RATE-01 / D7-01: daily counter resets exactly at 00:00 UTC.

    Exhaust the daily ceiling on 2026-01-01 (5000 successful calls + 1 deny);
    then call _check_bucket with now_utc on 2026-01-02 and assert the daily
    counter has reset to 1, daily_date has advanced, and daily_exceeded is
    False. The fake_clock is NOT advanced between the deny and the
    post-midnight call — proving the reset is driven solely by the UTC date
    boundary, not by elapsed monotonic time.
    """
    now_utc_day1 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # Drain burst at t=0.
    for _ in range(20):
        rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc_day1)

    # Calls 21..5000 in lock-step refill.
    for _ in range(20, 5000):
        fake_clock[0] += 1.0
        rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc_day1)

    # 5001st on day 1 — denied by daily ceiling.
    fake_clock[0] += 1.0
    allowed, _ = rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc_day1)
    assert allowed is False

    # Cross UTC midnight (now 2026-01-02). Do NOT advance fake_clock — the
    # reset is purely calendar-driven (D7-01).
    now_utc_day2 = datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC)
    allowed, retry_after = rate_limiter._check_bucket(
        "1.2.3.4", fake_clock[0], now_utc_day2
    )
    assert allowed is True, "first request after UTC midnight must be allowed"
    assert retry_after == 0

    bucket = bucket_store["1.2.3.4"]
    assert bucket.daily_count == 1
    assert bucket.daily_date == date(2026, 1, 2)
    assert bucket.daily_exceeded is False


async def test_rate_limit_fires_before_json_rpc_parse(rate_limiter):
    """RATE-02: malformed JSON-RPC body still returns 429, never JSON-RPC parse error.

    Drive the FULL `__call__` ASGI path 21 times in a single test with a
    deliberately malformed JSON-RPC body. The first 20 calls pass through to
    the no-op dummy_app (no response messages emitted). The 21st short-circuits
    at the rate-limit middleware with HTTP 429 — proving the malformed body
    is never parsed (T-07-03 mitigation: rate-limit fires BEFORE JSON-RPC
    parsing). No exception is raised at any point because the middleware
    never reads from `receive`.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "headers": [(b"content-type", b"application/json")],
        "client": ("9.9.9.9", 443),
    }
    malformed_body = b"\x00\x01NOT VALID JSON-RPC\x02\x03"

    async def receive() -> dict:
        return {"type": "http.request", "body": malformed_body, "more_body": False}

    captured: list[dict] = []

    async def send(msg: dict) -> None:
        captured.append(msg)

    # Calls 1..20 — allowed; dummy_app is a no-op so no response messages
    # are sent. Each call must NOT raise even though the body is malformed.
    for i in range(20):
        await rate_limiter(scope, receive, send)
        assert captured == [], (
            f"call {i + 1}: dummy_app should emit no response messages, got {captured}"
        )

    # Call 21 — rate-limit middleware short-circuits with 429 BEFORE any
    # JSON-RPC parsing would have happened. The malformed body is never read.
    await rate_limiter(scope, receive, send)

    start = next(m for m in captured if m["type"] == "http.response.start")
    assert start["status"] == 429, (
        f"21st request must short-circuit with 429, got status={start['status']}"
    )

    body_bytes = b"".join(
        m.get("body", b"") for m in captured if m["type"] == "http.response.body"
    )
    body = json.loads(body_bytes.decode("utf-8"))
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["message"] == "Rate limit exceeded"
    assert isinstance(body["error"]["retry_after_seconds"], int)
    assert body["error"]["retry_after_seconds"] >= 1


async def test_xff_parsing_depth_1(rate_limiter, bucket_store):
    """RATE-03 / T-07-04: depth=1 selects parts[-(depth+1)] from XFF.

    Caddy in front of the MCP server appends the TCP peer to XFF so the
    header arrives as `X-Forwarded-For: <client>, <caddy_peer>`. With
    TRUSTED_PROXY_DEPTH=1 the parser drops the rightmost (Caddy) hop and
    keys the bucket on the client (leftmost) entry. This is the canonical
    multi-hop case from 07-RESEARCH.md § XFF Parsing.

    Drive uses an inline send-capture (no _drive helper) because dummy_app
    emits no response messages on the allowed path — _drive's StopIteration
    on `next(...)` would surface as a coroutine RuntimeError. Mirrors the
    inline drive pattern established in test_rate_limit_fires_before_json_rpc_parse.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-forwarded-for", b"203.0.113.5, 10.0.0.1"),
        ],
        "client": ("10.0.0.1", 443),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    captured: list[dict] = []

    async def send(msg: dict) -> None:
        captured.append(msg)

    await rate_limiter(scope, receive, send)

    # Exactly one bucket created, keyed on the leftmost (client) entry.
    assert list(bucket_store.keys()) == ["203.0.113.5"], (
        f"expected single bucket keyed '203.0.113.5', got {list(bucket_store.keys())!r}"
    )
    # Allowed path: dummy_app is a no-op so no response messages are emitted.
    assert captured == [], f"allowed request should emit no response messages, got {captured}"


async def test_xff_fewer_hops_than_depth(rate_limiter, bucket_store):
    """RATE-03 / T-07-04: when len(parts) <= depth, return parts[0].

    Edge case from 07-RESEARCH.md § XFF Parsing § Edge Cases Handled — the
    XFF header carries fewer hops than TRUSTED_PROXY_DEPTH (1). The
    `client_ip_from_scope` helper falls back to the leftmost present entry
    rather than indexing out-of-range. With one entry, that entry IS the
    client. This guards against an IndexError in production when a misbehaving
    upstream sends only the client IP without appending its own.
    """
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-forwarded-for", b"203.0.113.5"),
        ],
        "client": ("10.0.0.1", 443),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    captured: list[dict] = []

    async def send(msg: dict) -> None:
        captured.append(msg)

    await rate_limiter(scope, receive, send)

    assert list(bucket_store.keys()) == ["203.0.113.5"], (
        f"expected single bucket keyed '203.0.113.5', got {list(bucket_store.keys())!r}"
    )
    assert captured == [], f"allowed request should emit no response messages, got {captured}"


def test_store_cap_enforced_under_flood(fake_clock):
    """RATE-04 / T-07-05: bucket store len() never exceeds store_cap under XFF spoof flood.

    Drives 200 unique spoofed-XFF entries against a fixture-sized store_cap=50
    middleware (production cap is 100,000 — the test uses 50 for speed; the
    algorithm is identical). After every batch LRU eviction len(store) drops
    to ~99% of cap; the loop's invariant is the strict upper bound at the
    moment we observe it (always at the end of `_check_bucket` after an
    insert). Over 200 inserts the cap fires multiple times — the assertion is
    that len(store) <= 50 at every observation point.
    """
    from mcp_zeeker import config
    from mcp_zeeker.core.middleware.rate_limit import RateLimitMiddleware

    async def dummy_app(scope, receive, send):
        return None

    middleware = RateLimitMiddleware(
        dummy_app,
        burst=config.RATE_BURST,
        sustained_per_second=config.RATE_SUSTAINED_PER_SECOND,
        daily_limit=config.RATE_DAILY_LIMIT,
        store_cap=50,
        idle_ttl_seconds=config.RATE_IDLE_TTL_SECONDS,
        time_provider=lambda: fake_clock[0],
    )

    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    for i in range(200):
        # Advance the clock so every insert has a strictly increasing
        # last_seen_ts — gives the LRU sort a deterministic eviction order.
        fake_clock[0] += 0.01
        middleware._check_bucket(f"198.51.100.{i}", fake_clock[0], now_utc)
        # Cap-bound invariant must hold AFTER every insert (post _enforce_cap).
        assert len(middleware._store) <= 50, (
            f"store size exploded to {len(middleware._store)} after insert #{i + 1} (cap=50)"
        )

    # Final state: store is at-or-just-under cap, never above.
    assert len(middleware._store) <= 50
    # And the survivors must be the most-recently-seen IPs (LRU eviction
    # behavior). The last insert was 198.51.100.199 — that one MUST still
    # be present.
    assert "198.51.100.199" in middleware._store


def test_sticky_ttl_daily_locked_not_expired(rate_limiter, fake_clock, bucket_store):
    """RATE-04 / D7-03 / T-07-06: daily-locked buckets sticky beyond standard 15-min idle TTL.

    Two assertions in one test:
      (a) On the SAME UTC day, a daily-locked bucket survives _sweep() even
          when it has been idle longer than the standard 15-min TTL — the
          sticky TTL = max(15 min, seconds_to_next_utc_midnight) keeps it
          pinned. This proves a daily-locked legitimate IP cannot bypass its
          ceiling by going silent for 15 minutes.
      (b) After the daily counter would naturally reset (fast-forward both
          fake_clock and now_utc past midnight, AND clear daily_exceeded so
          the effective TTL collapses back to 15 min), the bucket IS evicted
          on the next _sweep — proving sticky-TTL is bounded to the locked
          day, not perpetual.
    """
    fake_clock[0] = 0.0
    key = "198.51.100.7"

    # Seed a daily-locked bucket directly — fast-path equivalent to the
    # 5000-call lock-step drive used by 07-02. Both are documented in the
    # plan as acceptable.
    bucket = BucketState(
        tokens=0.0,
        last_refill_ts=0.0,
        daily_count=5000,
        daily_date=date(2026, 1, 1),
        last_seen_ts=0.0,
        daily_exceeded=True,
    )
    bucket_store[key] = bucket

    # (a) Advance to t=901 (just past 15-min standard TTL) and now_utc to
    # 12:15:01 UTC on the SAME day. Effective TTL = max(900,
    # seconds_to_next_utc_midnight ≈ 42_299) = 42_299 — bucket survives.
    fake_clock[0] = 901.0
    now_utc_day1_after_ttl = datetime(2026, 1, 1, 12, 15, 1, tzinfo=UTC)
    rate_limiter._sweep(901.0, now_utc_day1_after_ttl)
    assert key in bucket_store, (
        "daily-locked bucket evicted by 15-min idle sweep — sticky TTL is broken"
    )
    # Sanity-check that the effective TTL is materially larger than 900.
    eff = rate_limiter._effective_ttl(bucket_store[key], now_utc_day1_after_ttl)
    assert eff > 900.0, (
        f"effective TTL must be > 900s while daily_exceeded; got {eff}"
    )

    # (b) Cross UTC midnight. Advance fake_clock far past the now-much-smaller
    # effective TTL, AND flip daily_exceeded off (this is what _check_bucket's
    # date-rollover block does on the first request of the new UTC day; here
    # we simulate it directly because _sweep does not run that branch). With
    # daily_exceeded=False, the effective TTL collapses to the standard 15
    # min, and the bucket's idle time (now_mono - last_seen_ts = huge) makes
    # it eligible for eviction.
    bucket_store[key].daily_exceeded = False
    bucket_store[key].daily_date = date(2026, 1, 2)
    bucket_store[key].daily_count = 0
    # last_seen_ts stays at 0.0 — the bucket has been idle the whole time.
    fake_clock[0] = 100_000.0  # well past 900s of idleness
    now_utc_day2 = datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC)
    rate_limiter._sweep(100_000.0, now_utc_day2)
    assert key not in bucket_store, (
        "bucket should be evicted after midnight rollover when no longer daily-locked"
    )


def test_retry_after_is_integer(rate_limiter, fake_clock):
    """RATE-05: Retry-After is always a positive integer (>= 1) on every deny.

    Exercises both deny paths:
      (a) burst-only deny: 21st request after 20 immediate calls.
      (b) daily-exhausted deny: 5001st request in the same UTC day.
    Both must yield `isinstance(retry_after, int) and retry_after >= 1` —
    never zero, never a float (Nyquist invariant #3 from 07-RESEARCH.md).
    """
    now_utc = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # (a) Burst-only deny — drain 20 tokens at t=0, 21st fails.
    for _ in range(20):
        rate_limiter._check_bucket("burst-test", fake_clock[0], now_utc)
    allowed, retry_after = rate_limiter._check_bucket(
        "burst-test", fake_clock[0], now_utc
    )
    assert allowed is False
    assert isinstance(retry_after, int), (
        f"burst retry_after must be int, got {type(retry_after).__name__}"
    )
    assert retry_after >= 1, f"burst retry_after must be >= 1, got {retry_after}"

    # (b) Daily-exhausted deny — drain 5000 in lock-step on a different key.
    daily_key = "daily-test"
    for _ in range(20):
        rate_limiter._check_bucket(daily_key, fake_clock[0], now_utc)
    for _ in range(20, 5000):
        fake_clock[0] += 1.0
        rate_limiter._check_bucket(daily_key, fake_clock[0], now_utc)
    fake_clock[0] += 1.0
    allowed, retry_after = rate_limiter._check_bucket(
        daily_key, fake_clock[0], now_utc
    )
    assert allowed is False
    assert isinstance(retry_after, int), (
        f"daily retry_after must be int, got {type(retry_after).__name__}"
    )
    assert retry_after >= 1, f"daily retry_after must be >= 1, got {retry_after}"


def test_retry_after_max_of_windows(rate_limiter, fake_clock, bucket_store):
    """RATE-05 / D7-02: Retry-After = max(burst_wait, daily_wait) when both exhausted.

    Maps directly to D7-02 worked example in 07-RESEARCH.md § Retry-After
    Arithmetic. Drive 5000 successful calls so the daily counter is at the
    ceiling and the bucket is daily_exceeded=True; for the 5001st call,
    advance fake_clock by ONLY 0.5s instead of 1.0s so the burst refill
    leaves tokens at 0.5 (< 1.0). At now_utc = 23:55:00 UTC, burst_wait=1
    and daily_wait=300; max(1, 300) = 300 — D7-02 chooses the LARGER wait.
    Then re-exhaust on 2026-01-02 23:59:00 UTC and confirm the value
    matches max(1, seconds_to_utc_midnight) for that boundary as well.
    """
    now_utc_day1 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    # Drain burst at t=0.
    for _ in range(20):
        rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc_day1)

    # Calls 21..5000 in lock-step refill — tokens always == 0 after consume.
    for _ in range(20, 5000):
        fake_clock[0] += 1.0
        rate_limiter._check_bucket("1.2.3.4", fake_clock[0], now_utc_day1)

    bucket = bucket_store["1.2.3.4"]
    assert bucket.daily_count == 5000
    assert bucket.daily_exceeded is True
    assert bucket.tokens < 1.0  # last consume left tokens at 0

    # 5001st call at 23:55 UTC. Advance fake_clock by ONLY 0.5 so the partial
    # refill leaves tokens at 0.5 (< 1.0) — both windows now active.
    fake_clock[0] += 0.5
    now_utc_2355 = datetime(2026, 1, 1, 23, 55, 0, tzinfo=UTC)
    allowed, retry_after = rate_limiter._check_bucket(
        "1.2.3.4", fake_clock[0], now_utc_2355
    )
    assert allowed is False
    # burst_wait = ceil((1.0 - 0.5) / 1.0) = 1; daily_wait = 5*60 = 300.
    # D7-02: Retry-After = max(1, 300) = 300.
    assert retry_after == 300, (
        f"D7-02 max-of-waits at 23:55 UTC: expected 300, got {retry_after}"
    )

    # Re-exhaust on a fresh UTC day (2026-01-02) at 23:59 UTC. Use a new key
    # so we drive a clean burst-then-daily exhaustion without interfering
    # with the 1.2.3.4 bucket's history.
    key2 = "5.6.7.8"
    now_utc_day2_noon = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    fake_clock[0] = 0.0  # fresh monotonic timeline for this key

    for _ in range(20):
        rate_limiter._check_bucket(key2, fake_clock[0], now_utc_day2_noon)
    for _ in range(20, 5000):
        fake_clock[0] += 1.0
        rate_limiter._check_bucket(key2, fake_clock[0], now_utc_day2_noon)

    fake_clock[0] += 0.5
    now_utc_day2_2359 = datetime(2026, 1, 2, 23, 59, 0, tzinfo=UTC)
    allowed, retry_after = rate_limiter._check_bucket(
        key2, fake_clock[0], now_utc_day2_2359
    )
    assert allowed is False
    # burst_wait = 1; daily_wait = 60 (one minute to midnight).
    # D7-02: Retry-After = max(1, 60) = 60.
    assert retry_after == 60, (
        f"D7-02 max-of-waits at 23:59 UTC: expected 60, got {retry_after}"
    )


async def test_429_log_line_shape(rate_limiter, fake_clock):
    """OBS-03: 429 synthetic log line has only LOG_FIELDS keys; tool/db/table are null.

    Drives 21 full ASGI __call__ invocations. The 21st short-circuits with a
    429 and emits exactly one synthetic structured log line. Asserts:
      - tool / database / table are None (the rate-limit middleware never
        reaches a tool, so no tool name is bound).
      - status == "rejected", error_code == "rate_limited" (the rate-limit
        middleware's canonical synthetic-log shape).
      - request_id and ip_prefix are picked up from the structlog contextvar
        bound by RequestIdMiddleware upstream (here simulated via bind_request).
      - The set of keys on the log line is bounded by config.LOG_FIELDS plus
        structlog's own meta (event, log_level, level, timestamp). No extras.
    """
    from mcp_zeeker import config
    from mcp_zeeker.core.logging import bind_request, clear_request
    from structlog.testing import capture_logs

    bind_request(request_id="rid-log", ip_prefix="203.0.113")
    try:
        with capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as cap:
            scope = _build_scope("1.2.3.4")

            async def receive() -> dict:
                return {"type": "http.request", "body": b"", "more_body": False}

            captured: list[dict] = []

            async def send(msg: dict) -> None:
                captured.append(msg)

            # 20 allowed calls — dummy_app emits no response messages, but
            # the rate limiter does NOT log on the allowed path either.
            for i in range(20):
                await rate_limiter(scope, receive, send)
            # 21st call — short-circuits with 429 and emits the synthetic
            # log line we want to inspect.
            await rate_limiter(scope, receive, send)
    finally:
        clear_request()

    rate_limited_lines = [
        line
        for line in cap
        if line.get("event") == "tool_call"
        and line.get("error_code") == "rate_limited"
    ]
    assert len(rate_limited_lines) == 1, (
        f"expected exactly one rate_limited log line, got {len(rate_limited_lines)}: "
        f"{rate_limited_lines!r}"
    )
    line = rate_limited_lines[0]

    # tool / database / table are None on the rate-limit synthetic line —
    # the middleware short-circuits BEFORE any tool dispatch.
    assert line["tool"] is None
    assert line["database"] is None
    assert line["table"] is None
    # Status / error_code are the canonical rate-limit values.
    assert line["status"] == "rejected"
    assert line["error_code"] == "rate_limited"
    # Contextvar fields merged in via merge_contextvars.
    assert line["request_id"] == "rid-log"
    assert line["ip_prefix"] == "203.0.113"

    # Key-set bound: only LOG_FIELDS + structlog meta is allowed.
    allowed_keys = set(config.LOG_FIELDS) | {
        "event",
        "log_level",
        "level",
        "timestamp",
    }
    extra = set(line.keys()) - allowed_keys
    assert extra == set(), (
        f"unexpected extra keys in 429 log line: {extra!r}. Full line: {line!r}"
    )


@pytest.mark.parametrize(
    "hostile",
    [
        "DROP TABLE users; --",
        "</system><admin>",
        '" OR 1=1 --',
    ],
)
async def test_logs_no_user_input(rate_limiter, fake_clock, hostile):
    """OBS-04 / INJ-05: rate-limit log line never contains body / filter values.

    The rate-limit middleware never parses the request body and only reads
    headers for XFF-based IP keying. The /24-truncated `ip_prefix` (set by
    RequestIdMiddleware upstream) is the ONLY user-influenced value that can
    reach the log line. Hostile content (canary tokens, FTS5 operators,
    `</system>` tokens) injected into the request body OR into headers must
    NOT appear in the captured log line.

    Drives 21 requests with the SAME hostile XFF value so a single bucket
    accumulates 20 tokens and the 21st triggers the synthetic 429 + log
    line. The hostile string is also placed in the request body (which the
    middleware never reads).
    """
    from mcp_zeeker.core.logging import bind_request, clear_request
    from structlog.testing import capture_logs

    # IP-prefix bound by RequestIdMiddleware in production. Use a fixed
    # /24-prefix string so the contextvar value is deterministic and does
    # NOT contain the hostile substring.
    bind_request(request_id="rid-no-echo", ip_prefix="203.0.113")
    try:
        with capture_logs(
            processors=[structlog.contextvars.merge_contextvars]
        ) as cap:
            # Use the SAME spoofed XFF for all 21 requests — same bucket key,
            # so the 21st request actually hits the rate limit.
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/mcp/",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-forwarded-for", hostile.encode("latin-1", errors="replace")),
                ],
                "client": ("10.0.0.1", 443),
            }

            async def receive() -> dict:
                # The rate-limit middleware never reads from receive — the
                # hostile body is here purely to prove it cannot leak.
                return {
                    "type": "http.request",
                    "body": hostile.encode("utf-8"),
                    "more_body": False,
                }

            captured: list[dict] = []

            async def send(msg: dict) -> None:
                captured.append(msg)

            # Drive 21 requests; the 21st emits the synthetic 429 log line.
            for _ in range(21):
                await rate_limiter(scope, receive, send)
    finally:
        clear_request()

    rate_limited_lines = [
        line
        for line in cap
        if line.get("event") == "tool_call"
        and line.get("error_code") == "rate_limited"
    ]
    assert len(rate_limited_lines) >= 1, (
        f"expected at least one rate_limited log line, got 0: {cap!r}"
    )
    line = rate_limited_lines[0]
    # The hostile string must NOT appear anywhere in the log line repr —
    # neither in any value nor any key. The middleware never parses the
    # body, and ip_prefix is /24-truncated (carried by the contextvar from
    # RequestIdMiddleware), so user input cannot leak.
    line_repr = str(line)
    assert hostile not in line_repr, (
        f"hostile input leaked into 429 log line: {hostile!r} found in {line_repr!r}"
    )
