---
phase: 07-rate-limit-structured-errors-healthz-logs
plan: 02
subsystem: infra
tags: [rate-limit, token-bucket, retry-after, utc-midnight, asgi, tests]

# Dependency graph
requires:
  - phase: 07-rate-limit-structured-errors-healthz-logs
    plan: 01
    provides: "RateLimitMiddleware ASGI class + _check_bucket entry point + fake_clock/rate_limiter/bucket_store fixtures + 12 Wave-0 test stubs in tests/test_rate_limit.py"
provides:
  - "Six GREEN tests in tests/test_rate_limit.py — sustained 1/s refill, 5,000-daily ceiling, UTC midnight reset, integer-Retry-After invariant on both burst and daily denials, max-of-waits multi-window Retry-After (D7-02), and RATE-02 short-circuit-before-JSON-RPC-parse"
  - "Empirical confirmation that the existing 07-01 _check_bucket implementation already satisfies the full RATE-01 three-window contract + D7-01 calendar-driven daily reset + D7-02 max-of-waits arithmetic — no production-code changes were required"
affects: [07-03, 07-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Lock-step refill drive pattern: drain burst at t=0, then advance fake_clock by 1.0s per call so each subsequent call refills exactly one token — drives 5000 successful calls without bucket starvation"
    - "Partial-refill drive pattern: advance fake_clock by 0.5s before a deny call so tokens land at 0.5 (< 1.0) — forces the burst-wait path to engage simultaneously with daily-exceeded for the max-of-waits assertion"
    - "Calendar-only midnight reset drive: hold fake_clock constant across the UTC date boundary; the daily reset is purely calendar-driven (D7-01), not monotonic-time-driven, and this assertion proves it"
    - "Captured-send 21-call drive pattern for RATE-02: iterate `await rate_limiter(scope, receive, send)` 21 times against a single shared `captured: list[dict]`; first 20 pass to no-op dummy_app silently, 21st emits the 429 — proves the malformed body in receive() is never read"

key-files:
  created: []
  modified:
    - "tests/test_rate_limit.py — 6 stubs replaced with full GREEN bodies (test_sustained_refill_after_one_second, test_daily_limit_5000, test_daily_reset_at_utc_midnight, test_retry_after_is_integer, test_retry_after_max_of_windows, test_rate_limit_fires_before_json_rpc_parse); `date` added to datetime imports"

key-decisions:
  - "Re-used the 1.2.3.4 IP key across day-1 tests and a fresh 5.6.7.8 key for the day-2 segment of test_retry_after_max_of_windows — avoids any chance of cross-day bucket-state pollution while still exercising both UTC date boundaries (23:55 day-1 and 23:59 day-2) that 07-RESEARCH.md § Retry-After Arithmetic worked-example calls out"
  - "test_retry_after_is_integer drives BOTH deny paths (burst-only with key 'burst-test'; daily-exhausted with key 'daily-test') in a single test — keeps the Nyquist invariant #3 assertion in one place rather than splitting the int-type/positive-value contract across two tests"
  - "test_rate_limit_fires_before_json_rpc_parse uses a brand-new IP (9.9.9.9) and an inline scope dict rather than the module-level _build_scope helper — the inline scope's `receive()` returns a malformed body that the helper does not provide, and using a fresh IP guarantees no leftover bucket state from previous fixture-scoped tests"
  - "Single-plan-touch rule preserved — git diff tests/conftest.py is empty; all six tests consume the existing fake_clock / rate_limiter / bucket_store fixtures from 07-01"

patterns-established:
  - "Lock-step 5000-call drive (drain-then-1s-refill) is the canonical pattern for any test that needs the daily counter at the 5000 ceiling without burst starvation"
  - "Partial-refill (0.5s advance) before a deny call is the canonical pattern for forcing simultaneous burst-empty + daily-exceeded states needed for max-of-waits assertions"

requirements-completed: [RATE-01, RATE-02, RATE-05]

# Metrics
duration: ~6min
completed: 2026-05-15
---

# Phase 7 Plan 02: Sustained refill + daily ceiling + UTC midnight reset + max-of-waits Retry-After + RATE-02 placement Summary

**Six GREEN tests lock the full RATE-01 three-window contract (burst + sustained refill + 5,000-daily ceiling), D7-01 calendar-driven UTC midnight reset, D7-02 max-of-waits Retry-After arithmetic, and RATE-02 short-circuit-before-JSON-RPC-parse — confirming the 07-01 `_check_bucket` implementation is already correct on every observable contract surfaced by this plan, with zero production-code changes required.**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-05-15T00:42:19Z
- **Completed:** 2026-05-15T00:48:29Z
- **Tasks:** 2
- **Files modified:** 1 (tests/test_rate_limit.py)
- **Files created:** 0
- **Production files touched:** 0 (zero deviations needed — 07-01's `_check_bucket` body was already correct)

## Accomplishments

- **Sustained 1 token/second refill is empirically observable.** `test_sustained_refill_after_one_second` drives 20 burst calls at `fake_clock=0.0`, denies the 21st, advances to `fake_clock=1.0` and the next call succeeds, advances to `fake_clock=2.0` and another succeeds — proving the float-token refill formula `tokens = min(burst, tokens + elapsed * sustained_per_second)` ticks at exactly the locked 1/s rate.
- **5,000-request daily ceiling enforces deny even with burst available.** `test_daily_limit_5000` drives 5000 successful calls in lock-step refill (advance by 1.0s each call so tokens always refill to 1.0 before consume); the 5001st call has `tokens >= 1.0` after refill yet is denied because `daily_count == self._daily_limit`. Verified `bucket.daily_count == 5000` and `bucket.daily_exceeded is True` after rejection.
- **UTC midnight reset is calendar-driven, not monotonic-time-driven.** `test_daily_reset_at_utc_midnight` exhausts the daily counter on `2026-01-01` and then calls `_check_bucket` with `now_utc = datetime(2026, 1, 2, 0, 0, 1, tzinfo=UTC)` — *without advancing `fake_clock`*. The reset still fires; `bucket.daily_count` rolls to 1, `bucket.daily_date == date(2026, 1, 2)`, and `bucket.daily_exceeded is False`. This proves D7-01: the reset boundary is the UTC date comparison, not elapsed monotonic seconds.
- **Retry-After is always a positive integer ≥ 1 on every deny path.** `test_retry_after_is_integer` exercises both the burst-only deny (21st request immediately) and the daily-exhausted deny (5001st request after lock-step drive) — each path produces `isinstance(retry_after, int) and retry_after >= 1`. Nyquist invariant #3 from 07-RESEARCH.md is locked.
- **D7-02 max-of-waits Retry-After arithmetic verified at two UTC boundaries.** `test_retry_after_max_of_windows` drives the multi-window-exhausted state (tokens < 1.0 AND daily_exceeded == True) at 23:55 UTC: burst_wait = 1, daily_wait = 300, max = **300**. Then re-exhausts on a fresh key at 23:59 UTC on day 2: burst_wait = 1, daily_wait = 60, max = **60**. D7-02 chooses the LARGER wait — a well-behaved client that respects Retry-After will not re-trip immediately on either window.
- **RATE-02 placement proven against a deliberately malformed JSON-RPC body.** `test_rate_limit_fires_before_json_rpc_parse` drives `await rate_limiter(scope, receive, send)` 21 times where `receive()` returns the body `b'\x00\x01NOT VALID JSON-RPC\x02\x03'`. The first 20 calls pass through dummy_app silently (no response messages). The 21st short-circuits with HTTP 429 + canonical body — and crucially, NO exception is raised at any point, because `RateLimitMiddleware.__call__` never invokes `await receive()`. The malformed body is never even read. T-07-03 (Spoofing — rate-limit MUST run before JSON-RPC parse) is empirically mitigated.

## Schedule Tables (the exact (now_mono, now_utc) inputs the plan asked for)

### test_daily_limit_5000 / test_daily_reset_at_utc_midnight (5,000-call drive on 1.2.3.4)

| Step | `fake_clock[0]` | `now_utc` | bucket.tokens (post) | bucket.daily_count (post) | result |
|------|-----------------|-----------|----------------------|---------------------------|--------|
| Init | 0.0 | 2026-01-01 12:00:00Z | 20.0 | 0 | (fixture) |
| Calls 1..20 (burst drain) | 0.0 (constant) | 2026-01-01 12:00:00Z | 0 (after #20) | 20 | allow |
| Calls 21..5000 (lock-step refill) | +1.0 each call → 4980.0 (after #5000) | 2026-01-01 12:00:00Z | 0 (after each) | 5000 (after #5000) | allow |
| Call 5001 (daily-deny on day 1) | 4981.0 | 2026-01-01 12:00:00Z | 1.0 (refilled) | 5000 (no consume) | deny — retry_after = `seconds_to_utc_midnight` |
| Cross UTC midnight (no fake_clock change) | 4981.0 | 2026-01-02 00:00:01Z | 1.0 → 0 (consume) | 1 (reset then +1) | allow |

### test_retry_after_max_of_windows day-1 segment (1.2.3.4)

| Step | `fake_clock[0]` | `now_utc` | bucket.tokens (post) | bucket.daily_count (post) | retry_after |
|------|-----------------|-----------|----------------------|---------------------------|-------------|
| Calls 1..20 (burst drain) | 0.0 | 2026-01-01 12:00:00Z | 0 | 20 | — |
| Calls 21..5000 (lock-step) | +1.0/call → 4980.0 | 2026-01-01 12:00:00Z | 0 | 5000 | — |
| Call 5001 (partial 0.5s refill) | 4980.5 | 2026-01-01 23:55:00Z | 0.5 (no consume) | 5000 | **300** = max(burst_wait=1, daily_wait=300) |

### test_retry_after_max_of_windows day-2 segment (5.6.7.8, fresh key)

| Step | `fake_clock[0]` | `now_utc` | bucket.tokens (post) | bucket.daily_count (post) | retry_after |
|------|-----------------|-----------|----------------------|---------------------------|-------------|
| Reset | 0.0 | (fresh) | 20.0 | 0 | — |
| Calls 1..20 | 0.0 | 2026-01-02 12:00:00Z | 0 | 20 | — |
| Calls 21..5000 (lock-step) | +1.0/call → 4980.0 | 2026-01-02 12:00:00Z | 0 | 5000 | — |
| Call 5001 (partial 0.5s refill) | 4980.5 | 2026-01-02 23:59:00Z | 0.5 (no consume) | 5000 | **60** = max(burst_wait=1, daily_wait=60) |

## Task Commits

Each task was committed atomically:

1. **Task 1: GREEN sustained refill + daily ceiling + UTC midnight reset + Retry-After invariant** — `b1fc2e8` (test)
2. **Task 2: GREEN max-of-waits Retry-After + RATE-02 short-circuit-before-JSON-RPC** — `f316f31` (test)

## Files Created/Modified

- `tests/test_rate_limit.py` (modified) — 6 `@pytest.mark.skip` stubs replaced with full GREEN test bodies; `date` added to the `from datetime import ...` line for the midnight-reset assertion. Net change: +261 / −19. Skip count: 12 (post-07-01) → 6 (post-07-02), exactly the −6 the plan verification asks for.

No production-code changes. The 07-01 `_check_bucket` implementation already correctly handles burst refill, daily ceiling, daily-exceeded flag, max-of-waits Retry-After, and short-circuit before body read. This plan is exclusively a test-driven contract lock.

## Decisions Made

- **No production-code changes.** The 07-01 implementation of `_check_bucket` is already correct on every contract this plan locks. Specifically: the float-token refill formula, the `daily_count >= daily_limit → daily_exceeded = True` transition, the calendar-driven `if bucket.daily_date != today: reset` block, the `max(waits) if waits else 1` deny-path arithmetic, and the `__call__` decision to never read from `receive` before calling `self._check_bucket`. The plan's hypothesis ("any minor bug fixes that the new tests surface") found nothing to fix — the 07-01 author wrote the contract correctly the first time.
- **Use a fresh key per day in test_retry_after_max_of_windows.** Day 1 uses `1.2.3.4` (5000 calls + 1 deny at 23:55 UTC); day 2 uses `5.6.7.8` (a fresh 5000 + 1 deny at 23:59 UTC). This eliminates any concern that day-2 assertions could be polluted by day-1 bucket state — each assertion is reproducible in isolation.
- **`test_retry_after_is_integer` drives BOTH deny paths in one test.** The plan asked for verification of the integer invariant on burst-only AND daily-exhausted denials. Splitting these into two tests would have added another 5000-call drive (slow); using two distinct keys (`burst-test`, `daily-test`) inside one test reuses the same fixture instance and asserts the invariant on both paths in <0.01s.
- **Single-plan-touch rule preserved.** All six new test bodies consume only the 07-01 fixtures (`fake_clock`, `rate_limiter`, `bucket_store`). `git diff tests/conftest.py` is empty after the entire plan.

## Deviations from Plan

None — plan executed exactly as written. No production-code bugs surfaced; no architectural changes needed; no auth gates encountered; no out-of-scope discoveries.

## Issues Encountered

None blocking. Two observations worth recording for downstream waves:

1. The 5000-call drive runs in <50ms per test on the dev workstation — well below any pytest timeout concern. Plans 07-03 / 07-06 can safely use the same lock-step pattern without performance worry.
2. The existing `_drive` helper in `tests/test_rate_limit.py` (used by the 07-01 GREEN tests for the 429 body shape) does NOT generalize to the 21-call full-`__call__` test in this plan, because `_drive` calls `next(m for m in messages if m["type"] == "http.response.start")` which would fail on the silent calls 1..20. `test_rate_limit_fires_before_json_rpc_parse` therefore uses an inline captured-send loop. Future plans needing N-call ASGI drives should follow this inline pattern rather than extending `_drive`.

## User Setup Required

None — pure test additions; no service configuration, no environment variables, no infrastructure.

## Next Phase Readiness

**Ready for plan 07-03** (LRU + sticky-TTL sweep). The remaining 4 skip stubs are already pre-named (`test_xff_parsing_depth_1`, `test_xff_fewer_hops_than_depth`, `test_store_cap_enforced_under_flood`, `test_sticky_ttl_daily_locked_not_expired`) and the `_sweep` call site is already wired in `__call__` from 07-01. 07-03 fills `_sweep`'s body and GREENs the LRU/TTL stubs. The XFF stubs (test_xff_*) are not consumed by this plan or 07-03 — they remain as future-wave stubs (likely 07-02 backfill or 07-06 / 07-04 absorption per the wave plan).

**Ready for plan 07-06** (structured 429 log line tests). The `test_429_log_line_shape` and `test_logs_no_user_input` stubs remain — 07-06 GREENs them by capturing structlog output during a 429-driving call. The synthetic `logger.info("tool_call", ...)` call is already emitted in `__call__` from 07-01 with the correct LOG_FIELDS shape; 07-06 only needs to assert on the captured log.

**Skip ledger after 07-02:**

| Test | Plan that GREENs |
|------|------------------|
| `test_xff_parsing_depth_1` | (un-claimed by 07-02 task list — likely 07-03 or 07-06 backfill) |
| `test_xff_fewer_hops_than_depth` | (un-claimed by 07-02 task list — likely 07-03 or 07-06 backfill) |
| `test_store_cap_enforced_under_flood` | 07-03 |
| `test_sticky_ttl_daily_locked_not_expired` | 07-03 |
| `test_429_log_line_shape` | 07-06 |
| `test_logs_no_user_input` | 07-06 |

## Known Stubs

The 6 remaining `@pytest.mark.skip` stubs in `tests/test_rate_limit.py` are intentional Wave-0 placeholders consumed by plans 07-03 / 07-06 (and possibly 07-04 backfill for the two XFF tests, which are not claimed by this plan's task list despite their `reason=` strings). They do NOT prevent this plan's goal from being achieved (the full RATE-01 / RATE-02 / RATE-05 contract is observably locked); each remaining stub's `@pytest.mark.skip(reason="Wave 0 stub — plan 07-NN GREENs this (...)")` decorator names the resolving plan.

The `_sweep(self, now_mono, now_utc)` method body in `core/middleware/rate_limit.py` remains `pass` (intentional stub from 07-01) — plan 07-03 fills the LRU + TTL eviction algorithm. Not in this plan's scope.

## Self-Check: PASSED

- File `tests/test_rate_limit.py`: FOUND
- Commit `b1fc2e8` (Task 1): FOUND in `git log --oneline 6b7dfde..HEAD`
- Commit `f316f31` (Task 2): FOUND in `git log --oneline 6b7dfde..HEAD`
- `uv run pytest tests/test_rate_limit.py -x` exit 0: VERIFIED (9 passed, 6 skipped)
- `uv run pytest -x` exit 0: VERIFIED (343 passed, 13 skipped)
- `git diff tests/conftest.py` empty: VERIFIED (single-plan-touch rule preserved)
- `grep -c '@pytest.mark.skip' tests/test_rate_limit.py` = 7 (6 decorators + 1 docstring line); was 13 after 07-01 — net decrease of 6 matches plan verification
- `uv run ruff check tests/test_rate_limit.py`: PASSED (All checks passed!)

---
*Phase: 07-rate-limit-structured-errors-healthz-logs*
*Completed: 2026-05-15*
