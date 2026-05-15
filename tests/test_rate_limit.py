"""
Tests for RateLimitMiddleware (Phase 7 — RATE-01..05 + OBS-03/04).

Wave 0 (plan 07-01) GREENs three tests covering the observable truths locked
in 07-01-PLAN.md must_haves:
  - test_burst_allows_20_rejects_21st (RATE-01 burst)
  - test_429_body_has_retry_after_seconds (RATE-05 body shape)
  - test_429_body_has_request_id (RATE-05 body shape)

The remaining 12 tests are stubbed `@pytest.mark.skip` — plans 07-02 / 07-03 /
07-04 / 07-06 GREEN them per 07-VALIDATION.md § Per-Task Verification Map.

Test driving the ASGI __call__ directly (without a full Starlette app):
build a minimal `scope` dict + a captured-`send` pattern; concatenate
`messages[1:].body` to recover the response body bytes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import structlog

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


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (sustained refill)")
def test_sustained_refill_after_one_second():
    """RATE-01: token refills after 1 second of idle (sustained 1 tok/s)."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (daily limit)")
def test_daily_limit_5000():
    """RATE-01: 5001st request in same UTC day is rejected."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (daily reset)")
def test_daily_reset_at_utc_midnight():
    """RATE-01 / D7-01: daily counter resets exactly at 00:00 UTC."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (RATE-02 placement)")
def test_rate_limit_fires_before_json_rpc_parse():
    """RATE-02: malformed JSON-RPC body still returns 429, never JSON-RPC parse error."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (XFF parsing)")
def test_xff_parsing_depth_1():
    """RATE-03: depth=1 selects parts[-(depth+1)] from XFF."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (XFF fallback)")
def test_xff_fewer_hops_than_depth():
    """RATE-03: when len(parts) <= depth, return parts[0]."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-03 GREENs this (LRU cap)")
def test_store_cap_enforced_under_flood():
    """RATE-04: bucket store len() never exceeds RATE_STORE_CAP under XFF spoof flood."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-03 GREENs this (sticky TTL)")
def test_sticky_ttl_daily_locked_not_expired():
    """RATE-04 / D7-03: daily-locked buckets sticky beyond standard 15-min idle TTL."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (Retry-After invariant)")
def test_retry_after_is_integer():
    """RATE-05: Retry-After header is always a positive integer string."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-02 GREENs this (Retry-After max)")
def test_retry_after_max_of_windows():
    """RATE-05 / D7-02: Retry-After = max(burst_wait, daily_wait) when both exhausted."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-06 GREENs this (log shape)")
def test_429_log_line_shape():
    """OBS-03: 429 synthetic log line has only LOG_FIELDS keys; tool/db/table are null."""


@pytest.mark.skip(reason="Wave 0 stub — plan 07-06 GREENs this (no user input)")
def test_logs_no_user_input():
    """OBS-04 / INJ-05: rate-limit log line never contains body / filter values."""
