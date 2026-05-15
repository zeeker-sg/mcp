---
phase: 07-rate-limit-structured-errors-healthz-logs
plan: 03
subsystem: infra
tags: [rate-limit, eviction, lru, sticky-ttl, xff, asgi, security]

# Dependency graph
requires:
  - phase: 07-rate-limit-structured-errors-healthz-logs
    plan: 01
    provides: "RateLimitMiddleware ASGI shell + BucketState dataclass + _check_bucket entry point + _sweep call site (stub body) + fake_clock/rate_limiter/bucket_store fixtures"
  - phase: 07-rate-limit-structured-errors-healthz-logs
    plan: 02
    provides: "Six GREEN tests covering RATE-01 / RATE-02 / RATE-05 contracts (sustained refill, daily ceiling, UTC midnight reset, integer Retry-After, max-of-waits, RATE-02 placement) — confirmed _check_bucket math is correct so 07-03 changes can rely on it"
provides:
  - "Filled-in _sweep() body — idle-TTL pruning that delegates to _is_expired/_effective_ttl"
  - "New _effective_ttl(bucket, now_utc) method — D7-03 sticky TTL = max(idle_ttl, seconds_to_utc_midnight) when bucket.daily_exceeded, standard idle_ttl otherwise"
  - "New _is_expired(bucket, now_mono, now_utc) method — (now_mono - last_seen_ts) > effective_ttl"
  - "New _enforce_cap(now_mono, now_utc) method — batch LRU at RATE_STORE_CAP, evicts oldest 1% (= max(1, len // 100)) by last_seen_ts ascending"
  - "Cap-enforcement gate wired into _check_bucket's create-new-entry branch (the ONLY path where len(self._store) grows)"
  - "Module docstring documents the >100k-unique-attacker-IP eviction tradeoff per 07-RESEARCH.md § Bucket Store + Eviction 'Critical correctness invariant'"
  - "Four GREEN tests in tests/test_rate_limit.py — XFF depth=1 multi-hop, XFF fewer-hops fallback, store-cap-under-flood at fixture cap=50, sticky-TTL pin + post-midnight release"
affects: [07-04, 07-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Inline send-capture drive (no _drive helper) for tests that exercise full __call__ on the ALLOWED path — dummy_app emits no response messages, and _drive's `next(...)` would raise StopIteration in a coroutine surfacing as RuntimeError. Pattern matches test_rate_limit_fires_before_json_rpc_parse from 07-02."
    - "Direct BucketState seeding for test efficiency: skip the 5000-call lock-step drive when the test only needs to verify post-lock eviction behavior, not the math that led there. Plan explicitly authorized this shortcut as 'acceptable'."
    - "Per-test middleware instantiation with a small store_cap (50) when the test needs to observe cap-bound LRU behavior — production cap is 100k, but the algorithm is identical and a small cap keeps the test fast (200 inserts vs 100,001)."
    - "Cap-enforcement at the create-new-entry branch (not in _sweep) — separation of concerns: _sweep is idle-based pruning, _enforce_cap is cap-based pruning. Co-locating them would obscure the invariant that cap-enforcement is gated on store growth, not on time."

key-files:
  created: []
  modified:
    - "src/mcp_zeeker/core/middleware/rate_limit.py — added 4 methods (_effective_ttl, _is_expired, _enforce_cap, filled-in _sweep body); wired _enforce_cap call into _check_bucket's create-new-entry branch; expanded module docstring with the >100k-IP tradeoff and T-07-04/05/06 mitigation summaries. Net: +93 lines."
    - "tests/test_rate_limit.py — replaced 4 @pytest.mark.skip stubs with full GREEN test bodies (test_xff_parsing_depth_1, test_xff_fewer_hops_than_depth, test_store_cap_enforced_under_flood, test_sticky_ttl_daily_locked_not_expired); added `from mcp_zeeker.core.middleware.rate_limit import BucketState`. Net: +173 lines."

key-decisions:
  - "Cap-enforcement gates on store growth, not on the time-gated sweep cycle. _sweep handles idleness; _enforce_cap handles cap. Wiring _enforce_cap inside _check_bucket's create-new-entry branch (the only growth path) makes the invariant `len(self._store) <= self._store_cap after every insert` directly observable in the test (`assert len(middleware._store) <= 50` inside the per-iteration loop)."
  - "Sticky TTL via _effective_ttl as a method, not as inline arithmetic in _is_expired. The plan's must_have invariant is testable in isolation via `rate_limiter._effective_ttl(bucket, now_utc) > 900.0` — and the test does exactly that in the (a) sub-assertion of test_sticky_ttl_daily_locked_not_expired. Folding the formula into _is_expired would have lost that observability."
  - "Direct BucketState seeding for the sticky-TTL test. The plan explicitly OKed either approach (5000-call lock-step OR direct mutation). Direct seeding runs in microseconds and keeps the test focused on the single observable: 'a daily-locked bucket survives the 15-min standard TTL on the same UTC day, and is released on the next UTC day.' The 5000-call drive is already exhaustively covered by 07-02's test_daily_limit_5000."
  - "Inline send-capture drive for the XFF tests instead of extending _drive. _drive uses `next(m for m in messages if m['type'] == 'http.response.start')` which raises StopIteration when the request is allowed (dummy_app emits nothing). In an async coroutine, StopIteration propagates as RuntimeError. Inline send-capture mirrors test_rate_limit_fires_before_json_rpc_parse and is the established 07-02 pattern for full-__call__ allowed-path drives."
  - "Test fixture cap (50) is 1/2000 of the production cap (100,000). The plan called this out explicitly. The eviction algorithm is byte-identical at any cap size; the small fixture cap keeps the flood test under 0.01s instead of needing 100,001 inserts at 100k entries (which would still complete in <1s but adds noise to the test feedback loop)."
  - "Single-plan-touch rule preserved — git diff against the wave base (e89c4d0) on tests/conftest.py is empty. All Phase 7 conftest additions remain consolidated in 07-01 per the cross-plan merge-conflict learning."

patterns-established:
  - "Cap-enforcement gates on store-growth paths, not on time. Future eviction work (Redis-backed v2, multi-tenant tiers) should preserve this invariant: any code path that grows the store calls _enforce_cap before returning."
  - "Direct dataclass seeding is the canonical fast-path for any rate-limit test that needs a specific bucket state without exercising the math that produces that state. The 5000-call lock-step drive is reserved for tests of the math itself (07-02 pattern)."

requirements-completed: [RATE-03, RATE-04]

# Metrics
duration: ~3min
completed: 2026-05-15
---

# Phase 7 Plan 03: Bucket store eviction — sticky-TTL sweep + batch-LRU cap + XFF depth tests

**Filled in `_sweep` + added `_effective_ttl` + `_is_expired` + `_enforce_cap` to close the RATE-04 LRU + sticky-TTL story; GREENed four tests proving XFF depth=1 parsing keys correctly on the leftmost (client) entry, the fewer-hops-than-depth fallback never raises IndexError, the bucket store stays bounded under a 200-IP spoof flood at fixture cap=50, and a daily-locked bucket survives the 15-min standard TTL on its locked day but releases after the UTC midnight rollover.**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-05-15T09:00:54+0800 (commit c19e839)
- **Completed:** 2026-05-15T09:03:44+0800 (commit c63fbf8)
- **Tasks:** 2
- **Files modified:** 2 (1 production, 1 test)
- **Files created:** 0
- **Test runtime:** rate_limit module 0.03s (13 passed, 2 skipped); full suite 4.88s (351 passed, 9 skipped)

## Method Signatures Added

All four are methods on `RateLimitMiddleware`:

| Signature | Purpose |
|-----------|---------|
| `_effective_ttl(self, bucket: BucketState, now_utc: datetime) -> float` | D7-03 sticky TTL: `max(idle_ttl_seconds, seconds_to_utc_midnight)` when `bucket.daily_exceeded` is True; otherwise `idle_ttl_seconds` |
| `_is_expired(self, bucket: BucketState, now_mono: float, now_utc: datetime) -> bool` | True iff `(now_mono - bucket.last_seen_ts) > self._effective_ttl(bucket, now_utc)` |
| `_sweep(self, now_mono: float, now_utc: datetime) -> None` | Idle-TTL eviction pass — collects expired keys via `_is_expired` and deletes them. Time-gated by `__call__`'s `_last_sweep_ts` check (already wired in 07-01) |
| `_enforce_cap(self, now_mono: float, now_utc: datetime) -> None` | Batch LRU backstop — when `len(self._store) >= self._store_cap`, sorts keys by `last_seen_ts` ascending and deletes `max(1, len // 100)` (= 1,000 at the production 100k cap, 1 at fixture cap=50). Called from the create-new-entry branch of `_check_bucket` |

## Cap Sizing Decision (test fixture vs production)

| Context | `store_cap` | Eviction batch size (`max(1, cap // 100)`) | Test runtime |
|---------|-------------|--------------------------------------------|--------------|
| Production (`config.RATE_STORE_CAP`) | **100,000** | 1,000 | n/a (never observed in tests; fires only under flood) |
| `test_store_cap_enforced_under_flood` fixture | **50** | 1 | <0.01s for 200 inserts (multiple cap-firings observed) |

The fixture cap is 1/2000 of production. The plan explicitly called this out — the algorithm is byte-identical at any cap size, so a small cap proves the invariant `len(self._store) <= self._store_cap` while keeping the test fast and the assertion failure (if it ever regresses) easy to diagnose.

## Sticky-TTL Edge Cases Surfaced During Testing

The plan asked for a "release after midnight" sub-assertion. Discovering the exact mechanics surfaced one non-obvious detail worth recording for future maintenance:

**`_sweep` does NOT clear `daily_exceeded` on its own.** The flag is reset to `False` only inside `_check_bucket`'s `if bucket.daily_date != today:` block — i.e., on the FIRST request of the new UTC day. So a bucket that has been silent across midnight remains `daily_exceeded=True` from `_sweep`'s point of view, which means its effective TTL is STILL `max(15min, seconds_to_midnight)` even on the new day. The plan's sub-assertion explicitly tests this by simulating the date-rollover branch directly (mutating `daily_exceeded=False`, `daily_date=date(2026,1,2)`, `daily_count=0` in the test) before calling `_sweep`. Without that simulation, the bucket would NOT be evicted on the next-day sweep — which is correct behavior (it preserves the lock until a real request triggers the date-rollover reset), but easy to misread as a bug.

**Practical consequence:** A daily-locked bucket whose owner never returns will remain in the store across midnights, sticky on each subsequent day until either (a) a new request from that IP triggers the date-rollover reset and lets the standard 15-min TTL apply, or (b) the LRU backstop evicts it under cap pressure. Memory-bounded by the 100k cap, so this is acceptable; doc-noted in the module docstring.

**Effective TTL math at the test boundary** (12:00:00 UTC, ~12 hours to midnight):
- `seconds_to_utc_midnight(12:15:01 UTC)` ≈ 42,299 seconds (11h 44m 59s)
- `max(900, 42_299)` = **42,299 seconds** = ~11h 45min sticky window
- A request idle for 901 seconds (just over the standard 15-min) is therefore NOT expired (`901 < 42_299`)

The test asserts both the survival (`key in bucket_store`) AND the sanity check that `_effective_ttl > 900.0`, so a regression that broke the sticky logic would surface either way.

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement _sweep + _enforce_cap + sticky-TTL eviction** — `c19e839` (feat)
2. **Task 2: GREEN XFF depth + store cap + sticky TTL tests** — `c63fbf8` (test)

## Files Created/Modified

- `src/mcp_zeeker/core/middleware/rate_limit.py` (modified) — +93 / −7. Added 4 methods (`_effective_ttl`, `_is_expired`, `_enforce_cap`, filled-in `_sweep` body); wired `_enforce_cap` into `_check_bucket`'s create-new-entry branch; expanded module docstring with the >100k-IP tradeoff documentation and T-07-04/05/06 mitigation summaries.
- `tests/test_rate_limit.py` (modified) — +173 / −12. Replaced 4 `@pytest.mark.skip` stubs with full GREEN bodies; added `BucketState` import. Skip count: 6 (post-07-02) → 2 (post-07-03), exactly the −4 the plan verification asks for.
- `tests/conftest.py` — UNTOUCHED. Single-plan-touch rule preserved (git diff against wave base `e89c4d0` returns empty for this file).

## Acceptance Criteria — Verification

### Task 1
- [x] `grep -c 'def _sweep' …rate_limit.py` = 1
- [x] `grep -c 'def _enforce_cap' …rate_limit.py` = 1
- [x] `grep -c 'def _effective_ttl' …rate_limit.py` = 1
- [x] `grep -c 'def _is_expired' …rate_limit.py` = 1
- [x] `grep -v '^#' …rate_limit.py | grep -c 'last_seen_ts'` = 8 (≥ 3)
- [x] Smoke import + callable check — exit 0
- [x] `uv run pytest tests/test_rate_limit.py -x` — 9 passed, 6 skipped (no regression on existing tests)
- [x] `uv run ruff check …rate_limit.py` — All checks passed

### Task 2
- [x] `test_xff_parsing_depth_1` — exit 0
- [x] `test_xff_fewer_hops_than_depth` — exit 0
- [x] `test_store_cap_enforced_under_flood` — exit 0
- [x] `test_sticky_ttl_daily_locked_not_expired` — exit 0
- [x] Skip count decrease = 4 (post-07-02 was 7 by `grep -c '@pytest.mark.skip'`; post-07-03 is 3 — the docstring mention on line 10 accounts for the +1 in both counts; decorator count went 6→2)
- [x] `uv run pytest tests/test_rate_limit.py -x` — 13 passed, 2 skipped
- [x] `uv run pytest -x` — 351 passed, 9 skipped (full suite GREEN)
- [x] `git diff tests/conftest.py` (vs wave base e89c4d0) — empty

## Decisions Made

- **Module docstring carries the >100k-attacker-IP eviction tradeoff prominently.** The plan asked for this and 07-RESEARCH.md § Bucket Store + Eviction calls it out as the "Critical correctness invariant." The docstring lists: (a) the conditions under which it triggers (>100k simultaneous unique attacker IPs), (b) why it's accepted (memory-bounded; tradeoff between memory ceiling and lock-survival), (c) the v2 mitigation paths (Redis-backed shared store; raise the cap if memory permits).
- **`_enforce_cap` uses sorted() not heap.** O(n log n) vs O(n). At the production 100k cap, sorted() runs in ~10ms in Python (measured ballpark from 07-RESEARCH.md). The simpler API is worth the constant factor; if profiling later shows this firing under sustained pressure, swap to `heapq.nsmallest(evict_count, ...)` which is O(n log k) and would drop to ~1ms at k=1000.
- **Cap-enforcement is at the create-new-entry branch, not in `_sweep`.** Separating concerns keeps the invariant testable: `assert len(middleware._store) <= cap` after every `_check_bucket` call. If both lived in `_sweep`, the test would have to call `_sweep` after every insert to verify the bound — defeating the time-gated sweep optimization.
- **`_effective_ttl` is a method, not a free function.** It accesses `self._idle_ttl_seconds` and calls `self._seconds_to_utc_midnight` (already a `@staticmethod` on the class). Making it a method also keeps it directly testable from the rate_limiter fixture (`rate_limiter._effective_ttl(bucket, now_utc)`) — which the sticky-TTL test exercises in its sanity-check assertion.
- **Single-plan-touch rule preserved.** `tests/conftest.py` was not modified. All four new test bodies consume only the existing 07-01 fixtures. `BucketState` import is added to the test file directly (not exported from conftest).

## Threat Model — Mitigations Locked

| Threat ID | Category | Mitigation Locked By This Plan |
|-----------|----------|--------------------------------|
| T-07-04 | Spoofing (XFF parsing depth) | `test_xff_parsing_depth_1` + `test_xff_fewer_hops_than_depth` lock the depth=1 algorithm shared with `core/ip.py:client_ip`. Production code path was already correct from 07-01; this plan ADDED the proof, not the implementation. |
| T-07-05 | Denial of Service (bucket store size) | `_enforce_cap` batch-LRU evicts oldest 1% when `len(store) >= self._store_cap`; `test_store_cap_enforced_under_flood` proves the cap holds under a 200-IP flood against a `store_cap=50` test fixture. |
| T-07-06 | Elevation of Privilege (daily-lock evasion via 15-min idle) | `_effective_ttl` returns `max(15 min, seconds_to_next_utc_midnight)` when `bucket.daily_exceeded` is True; `test_sticky_ttl_daily_locked_not_expired` proves a daily-locked bucket survives 15-min idle on the same UTC day; documented tradeoff for >100k-IP flood is in module docstring. |

## Deviations from Plan

None. The plan executed exactly as written. No production-code bugs surfaced beyond the deliberate stub fill-in; no architectural changes needed; no auth gates encountered; no out-of-scope discoveries.

Two minor in-line corrections during execution (not deviations from plan intent):

1. **Ruff E501 on the `_enforce_cap` docstring inline comment** (line was 117 chars, limit 100). Wrapped the comment to two lines. Trivial.
2. **Ruff F841 on `now_utc_day1_noon` unused variable** in `test_sticky_ttl_daily_locked_not_expired`. The variable was a leftover from an earlier draft that planned to call `_sweep` at noon before the 15-min advance; the final implementation goes directly to t=901 so the noon variable was unreferenced. Removed.
3. **`_drive` helper does not work for ALLOWED-path tests.** Discovered when the first XFF test failed with `RuntimeError: coroutine raised StopIteration` — `_drive`'s `next(m for m in messages if m['type'] == 'http.response.start')` raises StopIteration on the allowed path (dummy_app emits no messages), and StopIteration in an async coroutine becomes RuntimeError. Switched both XFF tests to inline send-capture (mirroring `test_rate_limit_fires_before_json_rpc_parse` from 07-02). This is the established Phase 7 pattern, not a deviation.

## Issues Encountered

None blocking. One observation worth recording for downstream waves:

1. **`_sweep` does not auto-clear `daily_exceeded`.** This is correct behavior — the flag clearance lives in `_check_bucket`'s date-rollover branch, which only runs on a real request. But it means a daily-locked bucket whose owner never returns stays sticky across midnights (until LRU evicts it under cap pressure). Memory-bounded by the 100k cap, so safe. Documented in the SUMMARY's "Sticky-TTL Edge Cases Surfaced" section above so future maintainers don't try to "fix" what looks like a missed reset.

## User Setup Required

None — pure code + test additions; no service configuration, no environment variables, no infrastructure.

## Next Phase Readiness

**Ready for plan 07-04** (whatever it covers — likely structured-error catalog and HTTP→envelope-error mapping per the wave plan). The rate-limit middleware is now fully production-correct on all RATE-* contracts (RATE-01 burst+sustained+daily, RATE-02 placement, RATE-03 XFF, RATE-04 LRU + sticky TTL, RATE-05 429 envelope shape). No remaining stubs in `core/middleware/rate_limit.py`.

**Ready for plan 07-06** (structured 429 log line tests). Two skip stubs remain in `tests/test_rate_limit.py` (`test_429_log_line_shape`, `test_logs_no_user_input`) — both are 07-06's responsibility per the skip-decorator `reason=` strings. The synthetic `logger.info("tool_call", ...)` call in `RateLimitMiddleware.__call__`'s deny path is already wired from 07-01; 07-06 only needs to capture and assert on its shape.

**Skip ledger after 07-03:**

| Test | Plan that GREENs |
|------|------------------|
| `test_429_log_line_shape` | 07-06 |
| `test_logs_no_user_input` | 07-06 |

## Known Stubs

The 2 remaining `@pytest.mark.skip` stubs in `tests/test_rate_limit.py` are intentional Wave-0 placeholders consumed by plan 07-06. They do NOT prevent this plan's goal from being achieved (the full RATE-03 + RATE-04 + D7-03 contract is observably locked); each remaining stub's `reason=` string names the resolving plan.

No production-code stubs remain in `src/mcp_zeeker/core/middleware/rate_limit.py` after this plan — all four eviction-related methods have full bodies, and `_sweep`'s call site in `__call__` is wired and exercised by the sticky-TTL test.

## Self-Check: PASSED

- File `src/mcp_zeeker/core/middleware/rate_limit.py`: FOUND
- File `tests/test_rate_limit.py`: FOUND
- Commit `c19e839` (Task 1): FOUND in `git log --oneline e89c4d0..HEAD`
- Commit `c63fbf8` (Task 2): FOUND in `git log --oneline e89c4d0..HEAD`
- `uv run pytest tests/test_rate_limit.py -x` exit 0: VERIFIED (13 passed, 2 skipped)
- `uv run pytest -x` exit 0: VERIFIED (351 passed, 9 skipped, 5 warnings)
- `git diff e89c4d0 -- tests/conftest.py` empty: VERIFIED (single-plan-touch rule preserved)
- `grep -c '@pytest.mark.skip' tests/test_rate_limit.py` = 3 (was 7 post-07-02 = 6 decorators + 1 docstring line; now 2 decorators + 1 docstring line); decorator-decrease of 4 matches plan verification
- `uv run ruff check src/mcp_zeeker/core/middleware/rate_limit.py tests/test_rate_limit.py`: PASSED (All checks passed!)

---
*Phase: 07-rate-limit-structured-errors-healthz-logs*
*Completed: 2026-05-15*
