# src/mcp_zeeker/core/middleware/rate_limit.py
# Phase 7 Plan 07-01: ASGI rate-limit middleware (RATE-01 + RATE-02 + RATE-05).
# Phase 7 Plan 07-03: filled-in eviction methods (_sweep + _enforce_cap +
# _effective_ttl + _is_expired) closing RATE-03/RATE-04 + D7-03 contract.
#
# Mirrors the OriginAllowlistMiddleware ASGI shape (origin.py). Token-bucket
# math + retry-after semantics per 07-RESEARCH.md § Token Bucket Math /
# Retry-After Arithmetic. Sticky TTL on daily-locked buckets (D7-03) lives in
# `_effective_ttl`; the LRU 100k backstop lives in `_enforce_cap` (called from
# the create-new-entry branch of `_check_bucket` — the only path where store
# size grows).
#
# Eviction tradeoff (RATE-04 / D7-03 — accepted, documented per 07-RESEARCH.md
# § Bucket Store + Eviction "Critical correctness invariant"):
#   Under a simultaneous flood of >100,000 unique attacker IPs, the batch LRU
#   in `_enforce_cap` may evict a legitimate daily-locked bucket. When that IP
#   re-enters, it gets a fresh BucketState with daily_count=0 — effectively
#   bypassing its daily ceiling for the rest of the UTC day. This requires
#   sustained pressure from >100k distinct IPs and is bounded by the 100k cap
#   itself (the legitimate bucket's sticky TTL = max(15min, time-to-midnight)
#   keeps it preferentially retained until the store is genuinely full of
#   newer entries). Mitigation if observed in production: raise RATE_STORE_CAP
#   (memory permitting) or move bucket state to a shared store (Redis) — both
#   are v2 territory.
#
# Threat model:
# - T-07-01 (Tampering / token bucket bypass): float-token refill formula is
#   the canonical one from 07-RESEARCH.md; BucketState carries __slots__ so
#   accidental field addition cannot skew totals.
# - T-07-03 (Spoofing / Elevation — rate-limit AFTER JSON-RPC parse): registered
#   in app.py BEFORE Mount("/mcp", ...) so 429 short-circuits at ASGI; even a
#   malformed JSON-RPC body still triggers 429 (RATE-02).
# - T-07-04 (Spoofing / XFF parsing depth): client_ip_from_scope reads XFF
#   right-to-left at TRUSTED_PROXY_DEPTH=1; deviation requires explicit
#   config change (RATE-03).
# - T-07-05 (DoS / unbounded bucket store): _enforce_cap batch-LRU evicts
#   oldest 1% (= 1,000 entries at the 100k cap) when len(store) >= store_cap;
#   guarantees len(store) <= store_cap at all times (RATE-04).
# - T-07-06 (Elevation / daily-lock evasion via 15-min idle): _effective_ttl
#   returns max(15min, seconds_to_utc_midnight) when bucket.daily_exceeded —
#   pinning the bucket until the next UTC day rolls over (D7-03).
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
            # RATE-04: this is the only path where len(self._store) grows, so
            # cap-enforcement is gated here rather than in __call__. Under a
            # >100k unique-attacker-IP flood, the batch LRU in _enforce_cap
            # backstops the store size at RATE_STORE_CAP.
            self._enforce_cap(now_mono, now_utc)

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

    def _effective_ttl(self, bucket: BucketState, now_utc: datetime) -> float:
        """D7-03 sticky-TTL: daily-locked buckets stay pinned until UTC midnight.

        Standard idle TTL is `self._idle_ttl_seconds` (15 min). For a bucket
        whose `daily_exceeded` flag is True, return the LARGER of (15 min,
        seconds-to-next-utc-midnight) so the bucket cannot be evicted by the
        idle sweep before its daily counter resets at the UTC date roll. Once
        the daily reset happens (in `_check_bucket`'s date-comparison block),
        `daily_exceeded` flips back to False and the effective TTL collapses
        to the standard 15 min — so an idle bucket that crosses midnight is
        eligible for normal sweeping again on the next pass.
        """
        if bucket.daily_exceeded:
            return max(
                self._idle_ttl_seconds,
                float(self._seconds_to_utc_midnight(now_utc)),
            )
        return self._idle_ttl_seconds

    def _is_expired(
        self, bucket: BucketState, now_mono: float, now_utc: datetime
    ) -> bool:
        """True iff bucket has been idle longer than its effective TTL."""
        return (now_mono - bucket.last_seen_ts) > self._effective_ttl(
            bucket, now_utc
        )

    def _sweep(self, now_mono: float, now_utc: datetime) -> None:
        """Idle-TTL eviction pass — drop buckets idle past their effective TTL.

        D7-03 sticky-TTL semantics live in `_effective_ttl`: daily-locked
        buckets are protected until the next UTC midnight. Cap-based eviction
        is a SEPARATE concern handled by `_enforce_cap` (called from the
        bucket-creation path in `_check_bucket`); `_sweep` only handles
        idleness, not store size. Time-gated by `__call__`'s `_last_sweep_ts`
        check so this runs at most once per `_sweep_interval` seconds.
        """
        expired = [
            key
            for key, bucket in self._store.items()
            if self._is_expired(bucket, now_mono, now_utc)
        ]
        for key in expired:
            del self._store[key]

    # RATE-04: batch LRU backstop fires only when the store crosses
    # RATE_STORE_CAP — under normal load it never runs.
    def _enforce_cap(self, now_mono: float, now_utc: datetime) -> None:
        """Batch LRU eviction at the 100k cap — DoS backstop (T-07-05).

        Per 07-RESEARCH.md § Bucket Store + Eviction "Recommended LRU
        eviction batch": when the store reaches `self._store_cap`, evict the
        oldest 1 % of entries (= 1,000 at the production 100k cap, scales
        linearly for smaller test fixtures) in a single pass. Sorting by
        `last_seen_ts` ascending puts the longest-idle buckets at the front
        of the eviction list. Daily-locked legitimate buckets are still
        keyed on `last_seen_ts` here — the sticky TTL in `_effective_ttl`
        does NOT influence cap-based eviction. Under a sustained flood of
        >100,000 unique attacker IPs, this means a daily-locked legitimate
        bucket CAN be evicted and re-enter with a fresh daily counter; that
        tradeoff is documented in the module docstring.
        """
        if len(self._store) >= self._store_cap:
            evict_count = max(1, len(self._store) // 100)
            by_age = sorted(
                self._store, key=lambda k: self._store[k].last_seen_ts
            )
            for key in by_age[:evict_count]:
                del self._store[key]
