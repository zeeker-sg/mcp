---
phase: 07-rate-limit-structured-errors-healthz-logs
plan: 01
subsystem: infra
tags: [rate-limit, asgi, middleware, token-bucket, starlette, structlog]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: "OriginAllowlistMiddleware ASGI shape, RequestIdMiddleware contextvar binding, core/ip.py XFF parser, config.TRUSTED_PROXY_DEPTH"
  - phase: 06-envelope-hardening-injection-resistance-labelling
    provides: "single-plan-touch conftest convention (frozen_retrieved_at fixture pattern), structlog contextvars merge_contextvars wiring, D6-10 middleware ordering carry-forward"
provides:
  - "ASGI RateLimitMiddleware short-circuiting at the Starlette layer with HTTP 429 + integer Retry-After header + canonical {error:{code,message,retry_after_seconds,request_id}} body"
  - "Five locked RATE_* config constants (BURST=20, SUSTAINED_PER_SECOND=1.0, DAILY_LIMIT=5000, STORE_CAP=100000, IDLE_TTL_SECONDS=900.0) — single source of truth"
  - "BucketState dataclass with __slots__ keeping the 100k-cap store under 32 MB"
  - "client_ip_from_scope + _normalize_ip_key helpers in core/ip.py for raw-ASGI XFF parsing (no HTTPConnection)"
  - "Phase 7 test fixtures: fake_clock, rate_limiter, bucket_store (single-plan-touch — Plans 07-02..07-06 consume but never modify conftest.py)"
  - "tests/test_rate_limit.py: 15 tests collected, 3 GREEN (RATE-01 burst, RATE-05 body shape, ERR-03 request_id echo), 12 stubs awaiting downstream waves"
affects: [07-02, 07-03, 07-04, 07-05, 07-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "ASGI middleware time-injection: time_provider: Callable[[], float] constructor kwarg replaces freezegun in tests"
    - "Float-token bucket math: tokens = min(burst, tokens + elapsed * sustained_per_second); allow at >= 1.0"
    - "Sticky-TTL daily lock pattern (D7-03) — call site wired here, sweep body deferred to 07-03"
    - "Synthetic structured log line on ASGI rejection — same LOG_FIELDS shape as StructuredLogMiddleware"

key-files:
  created:
    - "src/mcp_zeeker/core/middleware/rate_limit.py — RateLimitMiddleware ASGI class + BucketState dataclass"
    - "tests/test_rate_limit.py — 15 tests (3 GREEN, 12 Wave-0 stubs)"
    - ".planning/phases/07-rate-limit-structured-errors-healthz-logs/deferred-items.md — out-of-scope discoveries"
  modified:
    - "src/mcp_zeeker/config.py — RATE_BURST/RATE_SUSTAINED_PER_SECOND/RATE_DAILY_LIMIT/RATE_STORE_CAP/RATE_IDLE_TTL_SECONDS"
    - "src/mcp_zeeker/core/ip.py — client_ip_from_scope + _normalize_ip_key helpers"
    - "src/mcp_zeeker/app.py — Middleware(RateLimitMiddleware, ...) inserted between OriginAllowlistMiddleware and Mount('/mcp', ...)"
    - "tests/conftest.py — Phase 7 fixture block: fake_clock / rate_limiter / bucket_store"

key-decisions:
  - "BucketState carries __slots__ (mandatory) — keeps the 100k-cap store at ~30 MB; without slots it would exceed the 32 MB ceiling documented in 07-RESEARCH.md"
  - "Constructor accepts all RATE_* knobs as keyword args; middleware never reads config directly — single source of truth lives in app.py wiring"
  - "Sweep call-site wired in __call__ but the body is `pass` — plan 07-03 fills it without re-touching __call__"
  - "Burst-only retry_after still floors at 1 second (max(1, math.ceil(...))) so a well-behaved client never re-trips immediately even with fractional waits"

patterns-established:
  - "ASGI rejection middleware shape: copy origin.py verbatim; replace JSONResponse with Response (custom Retry-After header) and origin-check with _check_bucket call"
  - "Phase 7 conftest single-plan-touch — only 07-01 modifies conftest.py; downstream plans consume fixtures via dependency"
  - "Test-driving raw ASGI __call__: minimal scope dict + captured-send list pattern recovers status/headers/body without spinning up a Starlette app"

requirements-completed: [RATE-01, RATE-02, RATE-05]

# Metrics
duration: ~30min
completed: 2026-05-15
---

# Phase 7 Plan 01: Rate-limit middleware skeleton + 429 contract + Wave-0 fixtures Summary

**ASGI RateLimitMiddleware with float-token burst bucket and locked 429 contract (integer Retry-After header + canonical JSON body), wired between OriginAllowlistMiddleware and the /mcp mount with three observable-truth tests GREEN and twelve Wave-0 stubs ready for downstream waves.**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-05-15T00:05:00Z
- **Completed:** 2026-05-15T00:36:49Z
- **Tasks:** 3
- **Files modified:** 4 (config.py, ip.py, app.py, conftest.py); 2 created (rate_limit.py, test_rate_limit.py)

## Accomplishments

- **HTTP 429 on the wire.** A 21st request from one IP within the burst window now short-circuits at the ASGI layer with `Retry-After: 1` and `{"error":{"code":"rate_limited","message":"Rate limit exceeded","retry_after_seconds":1,"request_id":"<hex>"}}` — verified by `test_429_body_has_retry_after_seconds` and `test_429_body_has_request_id` against the live `RateLimitMiddleware.__call__`.
- **Single-source-of-truth config.** Five `RATE_*` constants land in `config.py` in one block (RATE-01..06). `app.py` reads them by name; the middleware accepts them as constructor kwargs — zero inline duplication.
- **Wave-0 fixture surface.** `fake_clock` / `rate_limiter` / `bucket_store` live in `tests/conftest.py` and are ready for 07-02 (burst/daily/XFF tests), 07-03 (sticky-TTL + LRU sweep), 07-04 (error-catalog request_id echo) and 07-06 (log-shape tests).
- **Twelve Wave-0 test stubs collected** in `tests/test_rate_limit.py` — every test name in `07-VALIDATION.md § Per-Task Verification Map` now has a `@pytest.mark.skip` placeholder that downstream plans simply replace the body of (no test-name negotiation needed mid-wave).

## Task Commits

Each task was committed atomically:

1. **Task 1: RATE_* config constants + client_ip_from_scope helper** — `8cc26e4` (feat)
2. **Task 2: RateLimitMiddleware ASGI class + BucketState + 429 contract** — `75b3f6a` (feat)
3. **Task 3: app.py registration + conftest fixtures + Wave-0 test stubs** — `69be443` (feat)

## Files Created/Modified

- `src/mcp_zeeker/config.py` (modified) — five RATE_* constants in a labelled block between TRUSTED_PROXY_DEPTH and LOG_FIELDS
- `src/mcp_zeeker/core/ip.py` (modified) — added `client_ip_from_scope(scope, depth)` (raw-ASGI sibling of `client_ip()`) and private `_normalize_ip_key(ip)` for IPv6 bracket stripping
- `src/mcp_zeeker/core/middleware/rate_limit.py` (created, 238 LOC) — `RateLimitMiddleware` ASGI class + `BucketState` dataclass with `__slots__`; `_check_bucket` returns `(allowed, retry_after_seconds)`; `_seconds_to_utc_midnight` for daily Retry-After; `_sweep` is a wired-but-empty placeholder for 07-03
- `src/mcp_zeeker/app.py` (modified) — `Middleware(RateLimitMiddleware, burst=..., ...)` inserted between OriginAllowlistMiddleware and `Mount("/mcp", ...)`
- `tests/conftest.py` (modified) — Phase 7 fixture block: `fake_clock` (mutable list `[0.0]`), `rate_limiter` (real middleware with locked RATE_* + `time_provider=lambda: fake_clock[0]`), `bucket_store` (direct `_store` access)
- `tests/test_rate_limit.py` (created, 184 LOC) — 15 tests: 3 GREEN observable truths, 12 Wave-0 stubs `@pytest.mark.skip`
- `.planning/phases/07-rate-limit-structured-errors-healthz-logs/deferred-items.md` (created) — out-of-scope discoveries

## Decisions Made

- **`__slots__` on BucketState is non-negotiable.** Per 07-RESEARCH.md § Bucket Store, the 100k-entry store is ~30 MB with `__slots__` and ~37 MB without. The 32 MB ceiling is the discriminating constraint; `__slots__` also prevents accidental field addition skewing the token math (T-07-01).
- **Burst-only Retry-After floors at 1 second.** Even when `1.0 - bucket.tokens` is fractionally small, `max(1, math.ceil(...))` returns 1 — a well-behaved client never re-trips immediately on a Retry-After=0. Slight nuance over the literal D7-02 pseudocode but identical to the locked test expectation in `must_haves` (`retry_after == 1`).
- **`_sweep()` body is intentionally empty in this plan.** The call site (`if now_mono - self._last_sweep_ts > self._sweep_interval: self._sweep(...)`) is wired so plan 07-03 only needs to supply the algorithm body without re-touching `__call__`. This keeps 07-03 a single-file change.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Burst-only Retry-After floored at 1 second**
- **Found during:** Task 2 (RateLimitMiddleware implementation)
- **Issue:** The literal D7-02 pseudocode `waits.append(math.ceil((1.0 - bucket.tokens) / sustained_per_second))` returns 0 if `bucket.tokens` is exactly 1.0 - epsilon (the burst-empty edge case immediately after consuming the last token). A 0-second Retry-After would put the client into an immediate re-trip loop and contradicts the must_haves locked expectation that the 21st request returns `retry_after == 1`.
- **Fix:** Wrapped the burst wait in `max(1, math.ceil(...))` — same convention `_seconds_to_utc_midnight` uses. Retry-After is always a positive integer ≥ 1.
- **Files modified:** `src/mcp_zeeker/core/middleware/rate_limit.py` (`_check_bucket` method)
- **Verification:** `tests/test_rate_limit.py::test_burst_allows_20_rejects_21st` asserts `retry_after == 1` after the 21st call — passes.
- **Committed in:** `75b3f6a` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug — invariant safeguard)
**Impact on plan:** Strictly enforces the must_haves invariant; no scope creep, no new files.

## Issues Encountered

- **Pre-existing E501 in `config.py`** (3 instances on `TABLE_DESCRIPTIONS` lines 174-175, 182). Out-of-scope per SCOPE BOUNDARY rule — logged in `deferred-items.md`. Did not block this plan; ruff still passes on the files we created and on the lines we touched.
- **Ruff import-sort fixup on `tests/test_rate_limit.py`.** First emission of the file had an unsorted import block (I001). `uv run ruff check --fix` resolved it cleanly; the change is part of the Task 3 commit.

## User Setup Required

None — no external service configuration required. The single-process in-memory rate limiter is fully self-contained per RATE-06.

## Next Phase Readiness

**Ready for plan 07-02** (sustained refill + daily limit + UTC reset + XFF parse + RATE-02 placement + Retry-After invariants). All required scaffolding is in place:
- `rate_limiter._check_bucket` is the unit-test entry point (no need to drive `__call__`).
- `fake_clock[0] = N` is the time-advance pattern; the limiter's `time_provider` reads it on every call.
- `bucket_store["1.2.3.4"]` exposes `BucketState` directly for assertion.
- Test names are pre-stubbed; 07-02 GREENs them by replacing the function bodies (no new collection — pytest already counts them).

**Ready for plan 07-03** (LRU + sticky-TTL sweep). The `_sweep(now_mono, now_utc)` method exists; 07-03 supplies the body and adds the LRU eviction batch logic per 07-RESEARCH.md § Bucket Store.

**Ready for plan 07-06** (structured 429 log line tests). The synthetic `logger.info("tool_call", ...)` call is already emitted on the deny path with the correct `LOG_FIELDS` shape; 07-06 only needs to assert on the captured log line.

## Known Stubs

The 12 `@pytest.mark.skip` stubs in `tests/test_rate_limit.py` are intentional — Wave-0 placeholders consumed by plans 07-02 / 07-03 / 07-06. They do NOT prevent the plan's goal from being achieved (a working 429 with locked contract); they are the test surface downstream plans GREEN one by one. Plan PRD entry: each stub's `@pytest.mark.skip(reason="Wave 0 stub — plan 07-NN GREENs this (...)")` decorator names the resolving plan.

The `_sweep(self, now_mono, now_utc)` method body is `pass` (intentional stub) — plan 07-03 fills the LRU + TTL eviction algorithm. The call site is already wired in `__call__` so 07-03 is a single-file change.

## Self-Check: PASSED

- File `src/mcp_zeeker/core/middleware/rate_limit.py`: FOUND
- File `tests/test_rate_limit.py`: FOUND
- File `tests/conftest.py` Phase 7 block: FOUND (`grep -c 'Phase 7 — Rate limit fixtures' tests/conftest.py == 1`)
- Commit `8cc26e4` (Task 1): FOUND
- Commit `75b3f6a` (Task 2): FOUND
- Commit `69be443` (Task 3): FOUND
- `uv run pytest -x` exit 0: VERIFIED (337 passed, 19 skipped — 12 of the skips are this plan's intentional Wave-0 stubs)
- `uv run ruff check src/mcp_zeeker/core/middleware/rate_limit.py src/mcp_zeeker/core/ip.py src/mcp_zeeker/app.py tests/test_rate_limit.py`: PASSED
- `python -c "from mcp_zeeker.app import app; assert any('RateLimitMiddleware' in repr(m) for m in app.user_middleware)"`: PASSED

---
*Phase: 07-rate-limit-structured-errors-healthz-logs*
*Completed: 2026-05-15*
