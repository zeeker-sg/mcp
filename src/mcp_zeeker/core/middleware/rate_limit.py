# src/mcp_zeeker/core/middleware/rate_limit.py
# Phase 7 Plan 07-01: ASGI rate-limit middleware (RATE-01 + RATE-02 + RATE-05).
#
# Mirrors the OriginAllowlistMiddleware ASGI shape (origin.py). Token-bucket
# math + retry-after semantics per 07-RESEARCH.md § Token Bucket Math /
# Retry-After Arithmetic. Sticky TTL on daily-locked buckets (D7-03) is wired
# in this plan; the LRU sweep BODY is filled in by plan 07-03 — the call site
# already exists here so 07-03 only supplies _sweep().
#
# Threat model:
# - T-07-01 (Tampering / token bucket bypass): float-token refill formula is
#   the canonical one from 07-RESEARCH.md; BucketState carries __slots__ so
#   accidental field addition cannot skew totals.
# - T-07-03 (Spoofing / Elevation — rate-limit AFTER JSON-RPC parse): registered
#   in app.py BEFORE Mount("/mcp", ...) so 429 short-circuits at ASGI; even a
#   malformed JSON-RPC body still triggers 429 (RATE-02).
# - T-07-08 (Information Disclosure — user input in 429 body): body is built
#   from FIXED string literals + integer retry_after_seconds + opaque-hex
#   request_id; scope.body is never read before the 429 fires (INJ-05).
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import structlog
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_zeeker import config
from mcp_zeeker.core.ip import _normalize_ip_key, client_ip_from_scope

logger = structlog.get_logger()


@dataclass
class BucketState:
    """Per-IP token bucket + daily counter state.

    __slots__ is mandatory: at the 100k store cap this saves ~7 MB of
    __dict__ overhead, keeping the bucket store under the 32 MB ceiling
    documented in 07-RESEARCH.md § Bucket Store. It also prevents accidental
    field addition that could skew the token math (T-07-01).
    """

    __slots__ = (
        "tokens",
        "last_refill_ts",
        "daily_count",
        "daily_date",
        "last_seen_ts",
        "daily_exceeded",
    )

    tokens: float
    last_refill_ts: float
    daily_count: int
    daily_date: date
    last_seen_ts: float
    daily_exceeded: bool


class RateLimitMiddleware:
    """ASGI middleware enforcing the anonymous-tier rate limit (RATE-01..05).

    - Burst: BURST tokens, refill at SUSTAINED_PER_SECOND tok/s (RATE-01).
    - Daily: DAILY_LIMIT requests per IP per UTC day (D7-01).
    - 429 response: Retry-After integer seconds + canonical JSON body
      `{"error": {"code": "rate_limited", "message": ..., "retry_after_seconds": int,
      "request_id": str}}` (RATE-05).
    - Short-circuits BEFORE Mount("/mcp", ...) so JSON-RPC parsing never
      runs on a rate-limited request (RATE-02).

    Time injection: `time_provider` lets tests drive a fake clock.
    Production passes `time.monotonic` (the default) which is jump-resistant.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        burst: int,
        sustained_per_second: float,
        daily_limit: int,
        store_cap: int,
        idle_ttl_seconds: float,
        time_provider: Callable[[], float] = time.monotonic,
        sweep_interval_seconds: float = 30.0,
    ) -> None:
        self.app = app
        self._burst = burst
        self._sustained_per_second = sustained_per_second
        self._daily_limit = daily_limit
        self._store_cap = store_cap
        self._idle_ttl_seconds = idle_ttl_seconds
        self._time_provider = time_provider
        self._sweep_interval = sweep_interval_seconds
        self._depth = getattr(config, "TRUSTED_PROXY_DEPTH", 1)
        self._store: dict[str, BucketState] = {}
        self._last_sweep_ts: float = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Non-HTTP scopes (lifespan, websocket) pass through unchanged — verbatim
        # from origin.py line 29-31.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        perf_start = time.perf_counter()
        now_mono = self._time_provider()
        now_utc = datetime.now(tz=UTC)

        # Time-gated TTL/LRU sweep — algorithm body lands in plan 07-03; the
        # call site is wired here so 07-03 is a single-file change.
        if now_mono - self._last_sweep_ts > self._sweep_interval:
            self._sweep(now_mono, now_utc)
            self._last_sweep_ts = now_mono

        ip = client_ip_from_scope(scope, self._depth)
        key = _normalize_ip_key(ip) or "_unknown"

        allowed, retry_after = self._check_bucket(key, now_mono, now_utc)

        if allowed:
            await self.app(scope, receive, send)
            return

        # Rejected: emit synthetic structured log line + 429 response.
        # request_id and ip_prefix are already bound to the structlog
        # contextvar by RequestIdMiddleware (outer layer).
        request_id = structlog.contextvars.get_contextvars().get("request_id", "")
        duration_ms = int((time.perf_counter() - perf_start) * 1000)
        logger.info(
            "tool_call",
            tool=None,
            database=None,
            table=None,
            duration_ms=duration_ms,
            status="rejected",
            error_code="rate_limited",
        )

        body = json.dumps(
            {
                "error": {
                    "code": "rate_limited",
                    "message": "Rate limit exceeded",
                    "retry_after_seconds": retry_after,
                    "request_id": request_id,
                }
            }
        ).encode("utf-8")
        response = Response(
            content=body,
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)

    def _check_bucket(
        self, key: str, now_mono: float, now_utc: datetime
    ) -> tuple[bool, int]:
        """Token-bucket + daily-counter decision.

        Returns (allowed, retry_after_seconds). retry_after_seconds is 0 on
        the allow path (caller ignores it) and a positive integer on the deny
        path (D7-02: max(burst_wait, daily_wait)).
        """
        bucket = self._store.get(key)
        today = now_utc.date()
        if bucket is None:
            bucket = BucketState(
                tokens=float(self._burst),
                last_refill_ts=now_mono,
                daily_count=0,
                daily_date=today,
                last_seen_ts=now_mono,
                daily_exceeded=False,
            )
            self._store[key] = bucket

        # D7-01: daily counter resets at 00:00 UTC. Reset the daily_exceeded
        # flag too so a previously-locked IP can resume on the new day.
        if bucket.daily_date != today:
            bucket.daily_count = 0
            bucket.daily_date = today
            bucket.daily_exceeded = False

        # 07-RESEARCH.md § Token Bucket Math — refill formula.
        elapsed = now_mono - bucket.last_refill_ts
        if elapsed > 0:
            bucket.tokens = min(
                float(self._burst),
                bucket.tokens + elapsed * self._sustained_per_second,
            )
            bucket.last_refill_ts = now_mono

        bucket.last_seen_ts = now_mono

        if bucket.tokens >= 1.0 and bucket.daily_count < self._daily_limit:
            bucket.tokens -= 1.0
            bucket.daily_count += 1
            if bucket.daily_count >= self._daily_limit:
                bucket.daily_exceeded = True
            return True, 0

        # Deny path: D7-02 Retry-After = max of all active window waits.
        waits: list[int] = []
        if bucket.tokens < 1.0:
            tokens_needed = 1.0 - bucket.tokens
            waits.append(max(1, math.ceil(tokens_needed / self._sustained_per_second)))
        if bucket.daily_exceeded:
            waits.append(self._seconds_to_utc_midnight(now_utc))
        retry_after = max(waits) if waits else 1
        return False, retry_after

    @staticmethod
    def _seconds_to_utc_midnight(now_utc: datetime) -> int:
        """Integer seconds until next 00:00:00 UTC. Minimum 1 (never 0).

        07-RESEARCH.md § Retry-After Arithmetic — UTC has no DST and leap
        seconds are absorbed by datetime.now(tz=UTC) at the OS level.
        """
        tomorrow = (now_utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(1, math.ceil((tomorrow - now_utc).total_seconds()))

    def _sweep(self, now_mono: float, now_utc: datetime) -> None:
        """Time-gated TTL + LRU eviction sweep — body filled in by plan 07-03.

        The call site already exists in __call__ above so 07-03 only needs to
        supply this body (D7-03 sticky-TTL semantics + 100k cap LRU backstop).
        """
