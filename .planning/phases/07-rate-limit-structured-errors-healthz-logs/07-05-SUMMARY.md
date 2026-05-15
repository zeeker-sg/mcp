---
phase: 07-rate-limit-structured-errors-healthz-logs
plan: 05
subsystem: errors
tags: [retry, query-timeout, upstream-unavailable, err-04, datasette-client, q-open-3]

# Dependency graph
requires:
  - phase: 07
    provides: "Plan 07-04 — core/errors.py CATALOG tuple + raise_query_timeout helper that this plan provides the upstream-layer raise site for"
  - phase: 04
    provides: "DatasetteClient._request_with_retry retry-once-with-jitter on 502/503; UpstreamCallFailed.status field for D4-09 status-class discrimination"
  - phase: 01
    provides: "DatasetteClient typed wrapper (D-13/D-14/D-16) — the host module this plan extends"
provides:
  - "core/datasette_client.QueryTimeoutError(UpstreamCallFailed) — distinct exception type for httpx.TimeoutException so tool handlers can map to query_timeout via isinstance(exc, QueryTimeoutError)"
  - "core/datasette_client._request_with_retry: httpx.TimeoutException catch BEFORE httpx.RequestError (more-specific-first ordering)"
  - "core/datasette_client._request_with_retry: 502/503 second-attempt path now raises UpstreamCallFailed with the 'retry exhausted' marker the planner intended (was previously dead post-loop code; Rule-1 fix)"
  - "tests/test_datasette_client_retry.py: four ERR-04 tests aligned with VALIDATION.md (test_502_twice_raises, test_503_twice_raises, test_504_raises_immediately renamed, test_timeout_raises_query_timeout_error)"
affects: [07-06, 08-validation]

# Tech tracking
tech-stack:
  added: []  # no new dependencies — uses stdlib httpx.TimeoutException and existing test stack
  patterns:
    - "Subclass-discrimination of upstream-layer exceptions: QueryTimeoutError(UpstreamCallFailed) lets handlers branch on isinstance without changing the catch-all UpstreamCallFailed contract"
    - "More-specific-exception-first catch ordering: httpx.TimeoutException is a subclass of httpx.RequestError, so the more-specific catch must be lexically first"
    - "Honest contract repair (Rule 1): when planner-asserted code path is dead but the contract is required, fix the implementation to make the contract reachable rather than weakening the test"

key-files:
  created: []
  modified:
    - "src/mcp_zeeker/core/datasette_client.py — QueryTimeoutError subclass + httpx.TimeoutException catch + retry-exhausted-marker fix on 502/503 second attempt"
    - "tests/test_datasette_client_retry.py — module docstring expanded; test_504 renamed; three new tests (502/503 exhaustion + timeout-error)"

key-decisions:
  - "QueryTimeoutError subclasses UpstreamCallFailed so existing UpstreamCallFailed catch sites in tool handlers continue to work unchanged — ERR-04 distinction surfaces only at handlers that explicitly check isinstance(exc, QueryTimeoutError)"
  - "TimeoutException catch BEFORE RequestError — httpx.TimeoutException is a subclass of httpx.RequestError in httpx 0.28; reversing the order would silently route timeouts to UpstreamCallFailed instead of QueryTimeoutError"
  - "Rule-1 fix: the second-attempt 502/503 path now raises 'retry exhausted on {url}' inline instead of falling through to the generic 'upstream {status} on {url}' raise — matches the planner-asserted truth contract and lets callers disambiguate exhaustion from a fresh failure without recomputing attempt count"
  - "test_504_raises_immediately_no_retry → test_504_raises_immediately rename: VALIDATION.md § Per-Task Verification Map names this test by the shorter form; rename keeps a verifier grep GREEN without changing assertions"
  - "tests/conftest.py left UNCHANGED (single-plan-touch rule per 07-RESEARCH.md Open Question #5; Plan 07-01 owns Phase 7 conftest fixtures)"

patterns-established:
  - "QueryTimeoutError(UpstreamCallFailed) discrimination pattern can be reused for any other upstream-layer exception subtype (e.g., a future PoolTimeoutError) without breaking existing handlers"
  - "ERR-04 retry-exhausted-marker convention — UpstreamCallFailed's str(exc) message contains 'retry exhausted' iff the failure happened after one retry; tool handlers and tests can pattern-match on this substring"

requirements-completed: [ERR-04]

# Metrics
duration: 4min
completed: 2026-05-15
---

# Phase 07 Plan 05: QueryTimeoutError + ERR-04 Retry Tests Summary

**`QueryTimeoutError(UpstreamCallFailed)` subclass and `httpx.TimeoutException` catch path in `_request_with_retry` close ERR-04 by giving the locked `query_timeout` catalog code (07-04) its only Phase-7-owned raise site; four ERR-04 tests in `tests/test_datasette_client_retry.py` are now name-aligned with VALIDATION.md.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-15T00:58:05Z
- **Completed:** 2026-05-15T01:02:33Z
- **Tasks:** 2 of 2
- **Files modified:** 2 (0 created, 2 modified)

## Accomplishments

- Added `QueryTimeoutError(UpstreamCallFailed)` subclass in `core/datasette_client.py` so tool handlers can distinguish upstream timeouts from generic upstream-unavailability via `isinstance(exc, QueryTimeoutError)` — wires the upstream-layer raise site for the `query_timeout` catalog code that 07-04 locked in `core/errors.py`.
- Modified `_request_with_retry` to catch `httpx.TimeoutException` BEFORE `httpx.RequestError` (the more-specific-first ordering required because `TimeoutException` is a subclass of `RequestError` in httpx 0.28).
- Fixed a Rule-1 bug discovered during test writing: the 502/503 second-attempt path was falling through to the generic `upstream {status} on {url}` raise instead of producing the planner-asserted `"retry exhausted"` marker. Added an explicit inline raise for `attempt == 1 and status in (502, 503)` so the marker is now reachable and tests can disambiguate exhaustion from fresh failure.
- Renamed `test_504_raises_immediately_no_retry` → `test_504_raises_immediately` to match VALIDATION.md § Per-Task Verification Map exactly.
- Added three new tests: `test_502_twice_raises`, `test_503_twice_raises`, `test_timeout_raises_query_timeout_error` — all GREEN, all name-aligned with VALIDATION.md. Existing retry-then-succeed tests preserved.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add QueryTimeoutError subclass + httpx.TimeoutException catch in `_request_with_retry`** — `3cebd84` (feat)
2. **Task 2: Add ERR-04 exhaustion + timeout tests; rename test_504; Rule-1 fix to retry-exhausted marker** — `fa4ff20` (test) — also folds in the Rule-1 fix to `_request_with_retry`

## Files Created/Modified

- `src/mcp_zeeker/core/datasette_client.py` — MODIFIED.
  - Added `class QueryTimeoutError(UpstreamCallFailed): pass` immediately after the `UpstreamCallFailed` class definition. Module-level docstring on the new class documents the discrimination pattern (`isinstance(exc, QueryTimeoutError)` → emit `query_timeout` catalog code per `core/errors.raise_query_timeout`) and the Phase-7-vs-Phase-4 scope split (this is the only Phase-7-owned raise site for `query_timeout`; `invalid_query` raise sites in `tools/search.py` remain Phase 4 scope).
  - In `_request_with_retry`: added `except httpx.TimeoutException as exc: raise QueryTimeoutError(str(exc)) from exc` BEFORE the existing `except httpx.RequestError` branch, with an inline comment documenting why the order matters (TimeoutException is a subclass of RequestError).
  - Rule-1 fix: the 502/503 branch now raises `UpstreamCallFailed(f"upstream retry exhausted on {url}", status=resp.status_code)` inline when `attempt == 1`, instead of falling through to the generic `upstream {status} on {url}` raise. The post-loop `raise UpstreamCallFailed(f"upstream retry exhausted on {url}")` remains as defensive belt-and-suspenders (now formally unreachable but kept for clarity).

- `tests/test_datasette_client_retry.py` — MODIFIED.
  - Module docstring expanded to enumerate the four ERR-04 properties under test, with VALIDATION.md cross-reference.
  - Imports add `QueryTimeoutError`.
  - Renamed `test_504_raises_immediately_no_retry` → `test_504_raises_immediately`. Test body unchanged; docstring updated to document the rename.
  - Added `test_502_twice_raises`: registers two 502 responses, asserts `UpstreamCallFailed` with `match="retry exhausted"`, exactly one `asyncio.sleep` call with arg in [0.25, 0.50], exactly two upstream attempts (no third).
  - Added `test_503_twice_raises`: symmetric to 502 case.
  - Added `test_timeout_raises_query_timeout_error`: uses `httpx_mock.add_exception(httpx.ReadTimeout("simulated timeout"))` (httpx.ReadTimeout is a subclass of httpx.TimeoutException), asserts `QueryTimeoutError` raised, no retry sleep (transport-error branch raises immediately per D-16).

## End-to-end Wiring (post-07-05)

```
upstream Datasette returns ReadTimeout
    → httpx.AsyncClient.request raises httpx.ReadTimeout
        → DatasetteClient._request_with_retry catches httpx.TimeoutException (07-05 NEW)
            → raises QueryTimeoutError(UpstreamCallFailed) (07-05 NEW class)
                → tool handler catches UpstreamCallFailed (existing)
                    → isinstance(exc, QueryTimeoutError) check (Phase 4 / future tool work)
                        → raise_query_timeout() (07-04)
                            → ToolError("query_timeout: Query timed out") (07-04 catalog)
                                → ErrorEnrichmentMiddleware (07-04) appends [request_id: ...]
                                    → MCP client receives stable code + correlation token
```

The Phase 7 contract for `query_timeout` is now end-to-end complete at the upstream-layer; the tool-handler-side `isinstance(exc, QueryTimeoutError)` check is a future Phase 4 / tool-handler refinement — handlers that don't add the check still get correct `upstream_unavailable` mapping (because `QueryTimeoutError` is a subclass of `UpstreamCallFailed`).

## Tests Added (all GREEN, no Wave-0 skips)

| Test | Requirement | What it asserts |
| --- | --- | --- |
| `test_504_raises_immediately` | ERR-04 (renamed) | 504 raises `UpstreamCallFailed(match="upstream 504")` immediately, no sleep, exactly one upstream attempt |
| `test_502_twice_raises` | ERR-04 NEW | Two 502s → `UpstreamCallFailed(match="retry exhausted")`, one sleep in [0.25, 0.50], exactly two attempts |
| `test_503_twice_raises` | ERR-04 NEW | Two 503s → `UpstreamCallFailed(match="retry exhausted")`, one sleep in [0.25, 0.50], exactly two attempts |
| `test_timeout_raises_query_timeout_error` | ERR-04 NEW (07-05) | `httpx.ReadTimeout` → `QueryTimeoutError`, no sleep (transport-error branch fires immediately per D-16) |

Pre-existing tests preserved unchanged: `test_2xx_returns_immediately`, `test_502_retries_once_then_succeeds`, `test_503_retries_once_then_succeeds`.

Test count after this plan: **350 passed, 13 skipped** (was 341+19 after 07-04; the 9-skip / 9-pass swing reflects sibling Wave-2 plans 07-02/07-03 GREENing their Wave-0 stubs in parallel; this plan contributes +3 new ERR-04 tests + 1 rename, no new skips).

## Decisions Made

- **`QueryTimeoutError` subclasses `UpstreamCallFailed`, not a sibling exception.** Existing tool-handler code paths catch `UpstreamCallFailed` and map to `upstream_unavailable`. By making `QueryTimeoutError` a subclass, those handlers continue to work without modification — the ERR-04 distinction only surfaces at handlers that explicitly check `isinstance(exc, QueryTimeoutError)` and call `raise_query_timeout()` instead. This preserves backward compatibility while enabling forward refinement.
- **TimeoutException caught BEFORE RequestError, lexically.** Reversing the order would silently route timeouts to `UpstreamCallFailed` because `httpx.TimeoutException` is a subclass of `httpx.RequestError`. An inline comment in `_request_with_retry` documents the gotcha so a future reorderer cannot silently regress this.
- **Rule-1 fix to make the "retry exhausted" marker reachable.** The plan's must-have truths asserted that consecutive 502/503 raises `UpstreamCallFailed` with a "retry exhausted" marker, but the existing code path actually raised `f"upstream {resp.status_code} on {url}"` instead — the post-loop `f"upstream retry exhausted on {url}"` raise was dead code. Fixed by adding an inline raise for `attempt == 1 and status in (502, 503)`. The dead post-loop raise is kept as belt-and-suspenders. This is the most surgical possible repair: it changes only the message string for one specific code path, doesn't change exception type, and doesn't change `status` semantics (still passes `status=resp.status_code`).
- **conftest.py untouched.** Single-plan-touch rule per 07-RESEARCH.md Open Question #5; Plan 07-01 owns Phase 7 conftest fixtures.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] "retry exhausted" marker on consecutive 502/503 was unreachable**

- **Found during:** Task 2 (running `test_502_twice_raises`)
- **Issue:** The plan's `<must_haves><truths>` block (and 07-RESEARCH.md § 502/503 Retry Verification by extension) asserted that two consecutive 502/503 responses raise `UpstreamCallFailed` with a "retry exhausted" marker, with no third attempt. The existing `_request_with_retry` code path actually raised `f"upstream {resp.status_code} on {url}"` (with `status=resp.status_code`) on the second-attempt 502/503, because the `if resp.status_code in (502, 503) and attempt == 0:` branch only triggered on the first attempt — the second-attempt 502/503 fell through to the generic `raise UpstreamCallFailed(f"upstream {resp.status_code} on {url}", status=resp.status_code)` block. The post-loop `f"upstream retry exhausted on {url}"` raise was dead code (the loop body always raises or returns). The test failed with `Regex pattern did not match. Regex: 'retry exhausted'  Input: 'upstream 502 on /test.json'`.
- **Why this is Rule 1, not Rule 4:** The contract (`UpstreamCallFailed("retry exhausted")` on consecutive 502/503) is a planner-asserted must-have truth. The fix is purely behavioral — change the message string on one specific code path. It does not change exception type, does not change `status` semantics, does not introduce a new layer, and does not affect any other call site. Architectural-decision territory (Rule 4) would be e.g. switching from "one retry" to "two retries" or introducing a new error class hierarchy.
- **Fix:** In `_request_with_retry`, restructured the 502/503 handling. Was:
  ```python
  if resp.status_code in (502, 503) and attempt == 0:
      await asyncio.sleep(0.25 + random.random() * 0.25)
      continue
  ```
  Now:
  ```python
  if resp.status_code in (502, 503):
      if attempt == 0:
          await asyncio.sleep(0.25 + random.random() * 0.25)
          continue
      # ERR-04 / 07-05: second-attempt 502/503 — retry exhausted.
      raise UpstreamCallFailed(
          f"upstream retry exhausted on {url}",
          status=resp.status_code,
      )
  ```
- **Files modified:** `src/mcp_zeeker/core/datasette_client.py`
- **Verification:** All 7 tests in `test_datasette_client_retry.py` GREEN (4 pre-existing + 3 new); full suite GREEN at 350 passed / 13 skipped (no regressions in any tool that consumes `UpstreamCallFailed`).
- **Committed in:** `fa4ff20` (folded into the Task 2 commit because the test that surfaced the bug and the implementation fix must land together to keep the suite GREEN at every commit boundary)

## Authentication Gates

None encountered — plan was fully autonomous.

## Threat Flags

None — no new security-relevant surface introduced beyond what the plan's `<threat_model>` already enumerated:
- T-07-08 (Information Disclosure: upstream body echo in error path) is preserved by the existing `raise_upstream_unavailable()` helper from 07-04 which discards the `UpstreamCallFailed` constructor argument entirely. The new "retry exhausted" message in `UpstreamCallFailed` only includes the upstream URL (server-constructed from validated path components), never user-supplied filter values. INJ-05 chain preserved end-to-end.
- The new `QueryTimeoutError` constructor takes only `str(exc)` from the `httpx.TimeoutException` — httpx timeout exception messages are library-generated and never include the request URL or any user-supplied data (verified by reading httpx 0.28 source).

## Self-Check: PASSED

Mechanical verification:

- `src/mcp_zeeker/core/datasette_client.py` — MODIFIED (`grep -c 'class QueryTimeoutError' src/mcp_zeeker/core/datasette_client.py` returns 1)
- `tests/test_datasette_client_retry.py` — MODIFIED (`grep -c 'def test_' tests/test_datasette_client_retry.py` returns 7)
- Commit `3cebd84` (Task 1 — feat) — FOUND in git log
- Commit `fa4ff20` (Task 2 — test + Rule-1 fix) — FOUND in git log
- `tests/conftest.py` — UNCHANGED (single-plan-touch rule preserved; `git diff tests/conftest.py | wc -l` returns 0)
- All 4 target tests GREEN (`test_502_twice_raises`, `test_503_twice_raises`, `test_504_raises_immediately`, `test_timeout_raises_query_timeout_error`)
- Full suite: 350 passed, 13 skipped (no regressions)
- `python -c "from mcp_zeeker.core.datasette_client import QueryTimeoutError, UpstreamCallFailed; assert issubclass(QueryTimeoutError, UpstreamCallFailed)"` exits 0
- `grep -c 'except httpx.TimeoutException' src/mcp_zeeker/core/datasette_client.py` returns 1 (exactly one catch clause)
- `grep -n 'except httpx' src/mcp_zeeker/core/datasette_client.py` confirms TimeoutException line is BEFORE RequestError line
- End-to-end wiring smoke test: `QueryTimeoutError → raise_query_timeout → query_timeout (CATALOG)` round-trip exits 0
- `grep -c 'def test_504_raises_immediately' tests/test_datasette_client_retry.py` returns 1
- `grep -c 'def test_504_raises_immediately_no_retry' tests/test_datasette_client_retry.py` returns 0
- `uv run ruff check src/mcp_zeeker/core/datasette_client.py tests/test_datasette_client_retry.py` exits 0
