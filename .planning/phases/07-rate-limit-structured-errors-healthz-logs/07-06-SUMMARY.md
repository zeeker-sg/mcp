---
phase: 07
plan: 06
subsystem: observability
tags:
  - rate-limit
  - healthz
  - structured-logs
  - operator-docs
  - traceability
dependency_graph:
  requires:
    - 07-01  # rate-limit middleware foundation (synthetic 429 log line emitter)
    - 07-03  # eviction + final ASGI shape — upstream of OBS-04 corpus drive
  provides:
    - obs-01-mechanical-no-upstream-test  # test_healthz_dispatches_no_httpx_request
    - obs-03-log-shape-test               # test_429_log_line_shape
    - obs-04-no-user-input-test           # test_logs_no_user_input
    - rate-06-readme-operator-section
    - obs-02-v2-deferral-traceability
  affects:
    - .planning/REQUIREMENTS.md  # OBS-02 row Pending → Deferred to v2 (D7-05)
    - README.md                  # new operator subsection (RATE-06 / UTC reset / upstream curl)
tech_stack:
  added: []
  patterns:
    - pytest-httpx no-request property assertion (httpx_mock.get_requests() == [])
    - structlog capture_logs + merge_contextvars for synthetic-log-line shape testing
    - parametrized hostile-input no-echo invariant via str(line) substring search
key_files:
  created: []
  modified:
    - tests/test_app.py
    - tests/test_rate_limit.py
    - README.md
    - .planning/REQUIREMENTS.md
decisions:
  - "Existing /healthz test (test_healthz_returns_ok_without_upstream) preserved verbatim; new sibling test_healthz_dispatches_no_httpx_request adds a mechanical pytest-httpx assertion alongside it (per plan 'do NOT modify or rename' the VALIDATION.md-named gate)."
  - "test_429_log_line_shape drives 21 ASGI __call__ invocations (not _drive helper) — the allowed path emits no response messages from dummy_app, so _drive's next() would raise; the inline send-capture pattern from test_rate_limit_fires_before_json_rpc_parse is reused."
  - "test_logs_no_user_input parametrizes over 3 representative hostile strings inline (not the full Phase 6 _corpus/hostile_inputs.py) per plan: keeps the 07-06 test self-contained and Phase 6's corpus as the canonical fan-out site."
  - "OBS-02 deferred row ADDED in REQUIREMENTS.md v2 Observability subsection while the original Phase 1 mapping row remains in the traceability table; D7-05 'honest traceability over false-positive closure' preserved by leaving the v1 §3.7 bullet unchanged and using the table row as source of truth."
  - "README adds a new 'Single-worker requirement (RATE-06)' subsection under Deployment alongside the existing 'Single-worker constraint' block (which predates this plan); duplication is intentional — the new subsection consolidates the three plan-required paragraphs (worker mandate + 00:00 UTC reset + upstream-status curl) in one place per Task 3 wording."
metrics:
  duration_seconds: 280
  duration_human: "~4.5 min"
  completed: 2026-05-15T01:17:40Z
---

# Phase 7 Plan 06: Healthz lock + 429 log shape + operator docs + OBS-02 deferral Summary

**One-liner:** Locks the /healthz no-upstream contract via pytest-httpx, GREENs the last 2 of 15 Wave-0 rate-limit stubs (OBS-03 log shape + OBS-04 no-user-input invariant), documents the single-worker / UTC-midnight-reset / upstream-status-v2 trio in README, and honestly defers OBS-02 to v2 in REQUIREMENTS.md per D7-05.

## Tasks Completed

| Task | Name                                                              | Commit  | Files                                            |
| ---- | ----------------------------------------------------------------- | ------- | ------------------------------------------------ |
| 1    | Add no-upstream-call belt-and-suspenders test for /healthz        | 648d5f6 | tests/test_app.py                                |
| 2    | GREEN the 429 log-line-shape + no-user-input tests                | 08cb186 | tests/test_rate_limit.py                         |
| 3    | README operator section + REQUIREMENTS.md OBS-02 deferral         | 5ac4f71 | README.md, .planning/REQUIREMENTS.md             |

## Verification

- `uv run pytest tests/test_app.py tests/test_rate_limit.py -x` → **23 passed** (6 + 17).
- `uv run pytest -x` → **359 passed, 7 skipped** (skip set unchanged: 1 carry-forward envelope-snapshot, 5 ZEEKER_LIVE-gated, 1 Phase-2 redundancy marker — none from this plan).
- `grep -c -- '@pytest\.mark\.skip' tests/test_rate_limit.py` → **0 decorators** remain (all 15 originally-stubbed Wave-0 tests across 07-01/02/03/06 are now GREEN).
- README: `--workers 1` × 4, `00:00 UTC` × 1, `upstream-status` × 1 — all plan markers present.
- REQUIREMENTS.md: traceability row `| OBS-02 | Phase 1 | Deferred to v2 (D7-05) |`; v2 Observability subsection adds D7-04/D7-05 rationale with operator curl workaround.

## Detail by Task

### Task 1 — `test_healthz_dispatches_no_httpx_request`

**File:** `tests/test_app.py` (added 20 lines, no deletions).

Added a single new async test placed immediately after `test_healthz_returns_ok_without_upstream`. The new test takes both the `asgi_client` and `httpx_mock` fixtures, registers ZERO upstream responses, issues `GET /healthz`, then asserts:

1. `resp.status_code == 200`
2. `resp.json() == {"status": "ok"}`
3. `httpx_mock.get_requests() == []`

The third assertion is the mechanical no-upstream-call property: `pytest-httpx` records every outgoing httpx call its mock saw — if `/healthz` ever attempted an upstream call (now or after a future regression), this list would be non-empty and the test would fail with a clear message.

Also added `import pytest_httpx` at the top of the file (the original test took no httpx fixture so didn't need the import). The existing `test_healthz_returns_ok_without_upstream` is preserved verbatim per plan: it remains the VALIDATION.md-named gate.

**Acceptance criteria — all green:**
- Both `test_healthz_*` tests pass individually and together.
- `grep -c 'def test_healthz' tests/test_app.py` = **2** (≥ 2).
- `git diff tests/conftest.py` = empty.

### Task 2 — GREEN OBS-03 log shape + OBS-04 no-user-input

**File:** `tests/test_rate_limit.py` (170 insertions, 9 deletions — removed 2 stubs + module docstring update).

#### `test_429_log_line_shape`

Replaces the `@pytest.mark.skip` stub with a full async test. The test:

1. Calls `bind_request("rid-log", "203.0.113")` to simulate the contextvar binding done by `RequestIdMiddleware` upstream.
2. Enters `capture_logs(processors=[structlog.contextvars.merge_contextvars])` — same processor list used by `test_logging.py::test_log_fields_locked_to_config`, so contextvars are merged but no other production processors run.
3. Drives 21 full `await rate_limiter(scope, receive, send)` calls (not via the `_drive` helper, which assumes an emitting app). The 21st short-circuits with the 429 path in `RateLimitMiddleware.__call__` and emits exactly one synthetic structured log line.
4. Filters captured entries to those with `event="tool_call"` and `error_code="rate_limited"` and asserts exactly one match.
5. Asserts the synthetic line carries `tool=None`, `database=None`, `table=None`, `status="rejected"`, `error_code="rate_limited"`, `request_id="rid-log"`, `ip_prefix="203.0.113"`.
6. Bounds the key set: `set(line.keys()) - (set(config.LOG_FIELDS) | {"event", "log_level", "level", "timestamp"}) == set()`.
7. Calls `clear_request()` in `finally` so the next test starts with a clean contextvar.

#### `test_logs_no_user_input`

Replaces the second `@pytest.mark.skip` stub with a parametrized test. The test takes one of three representative hostile inputs as a parameter (chosen per plan to be self-contained — not the full Phase 6 corpus): `"DROP TABLE users; --"`, `"</system><admin>"`, `'" OR 1=1 --'`.

For each hostile string:

1. Binds `request_id="rid-no-echo"` and `ip_prefix="203.0.113"` (a fixed /24-prefix that does NOT contain the hostile substring — proves the contextvar is the only IP-influenced field on the log line and it's already truncated).
2. Builds an ASGI scope with `x-forwarded-for: <hostile>` (latin-1 encoded with replace-on-error) — the rate-limit middleware uses XFF for IP keying. Because the same XFF is reused for all 21 requests, a single bucket accumulates 20 tokens and the 21st triggers the rate-limit branch.
3. The `receive` callable returns a body containing the hostile string — the test thereby proves the middleware never reads the body. (RateLimitMiddleware never awaits `receive`.)
4. Drives 21 requests; captures the synthetic 429 log entry (filter on event="tool_call" + error_code="rate_limited").
5. Asserts `hostile not in str(line)` — repr of the captured log dict (which includes both keys and values) contains zero substrings of the hostile input.

The contextvar `ip_prefix` is bound to a clean /24 string ("203.0.113") rather than being derived from the hostile XFF, because in production `RequestIdMiddleware` runs upstream of `RateLimitMiddleware` and binds the /24-truncated prefix; this test fixture mirrors that ordering by binding directly. The point of the test is the no-leak invariant — it would fail loudly if the rate-limit middleware ever started parsing the body or echoed raw XFF into the log.

#### Module docstring update

The opening docstring previously claimed "12 tests are stubbed `@pytest.mark.skip`" — that count was for the post-07-01 state. Updated to reflect that all 15 originally-stubbed Wave-0 tests are now GREEN by the end of Phase 7 (07-02 / 07-03 / 07-04 GREENed the daily / refill / eviction / XFF / Retry-After tests; 07-06 GREENs the final two). No `@pytest.mark.skip` decorators remain in the file.

**Acceptance criteria — all green:**
- Both new tests pass (4 invocations total: 1 + 3 parametrized).
- `grep -c '@pytest\.mark\.skip' tests/test_rate_limit.py` decorator count is now **0** (was 2 before this plan).
- Full `uv run pytest -x` is GREEN; no regressions across 359 tests.
- `git diff tests/conftest.py` = empty.

### Task 3 — README operator section + OBS-02 deferral

**Files:** `README.md` (+22 lines), `.planning/REQUIREMENTS.md` (modified 1 row + added 1 subsection).

#### README — chosen subsection title

Per plan, the chosen subsection title is **`### Single-worker requirement (RATE-06)`** placed immediately after the existing `### Single-worker constraint` block under the "Deployment" section. Both blocks coexist intentionally: the existing block predates Phase 7 and was added during initial Caddy/topology docs; the new block consolidates the three plan-required paragraphs in one place under an explicit RATE-06 callout. Three paragraphs as the plan specifies:

1. **Single-worker mandate.** Explicit `uvicorn ... --workers 1` command + the bug-class explanation ("each worker keeps its own bucket store — a class of bug that only shows up under load") + RATE-06 traceability anchor.
2. **Daily reset at 00:00 UTC.** Explains correlated burst behavior at midnight and notes the burst+sustained windows still cap it.
3. **Upstream health via curl.** Documents the `curl https://data.zeeker.sg/-/metadata.json` workaround for v1, references D7-04 for the v2 deferral, and reaffirms `/healthz` is liveness-only per OBS-01.

#### REQUIREMENTS.md — before/after

**Traceability row (line 254 before, 265 after the v2 insertion):**

```diff
-| OBS-02 | Phase 1 | Pending |
+| OBS-02 | Phase 1 | Deferred to v2 (D7-05) |
```

**v2 Requirements section — new Observability subsection added between "Discoverability" and "Out of Scope":**

```markdown
### Observability (deferred from v1)

- **OBS-02**: `/internal/upstream-status` operator-only endpoint — deferred to v2 per Phase 7
  decision D7-04/D7-05. Operators inspect upstream health via
  `curl https://data.zeeker.sg/-/metadata.json` from outside the container in v1. The v1
  surface ships `/healthz` (OBS-01) for process liveness only; an in-process upstream-health
  endpoint is intentionally out of scope until the API-keyed-tier work in v2 introduces an
  ops-token / loopback-listener model. Honest traceability over false-positive closure
  (D7-05): the requirement bullet remains in §3.7 with the original Phase 1 mapping; the
  traceability row above is the source of truth for status.
```

The original `## v1 Requirements > Observability > OBS-02` bullet at line ~109 is **preserved unchanged** per plan and per D7-05. The `OBS-02` token now appears 3 times in the file: original v1 bullet, new v2-deferred bullet, traceability row. The traceability table is the canonical source of truth for status; the v1 bullet remains for historical / requirements-coverage continuity.

**Acceptance criteria — all green:**
- `grep -c -- '--workers 1' README.md` = **4** (≥ 1).
- `grep -c '00:00 UTC' README.md` = **1** (≥ 1).
- `grep -c 'upstream-status' README.md` = **1** (≥ 1).
- `grep -c 'OBS-02' .planning/REQUIREMENTS.md` = **3** (≥ 2).
- `grep -c 'Deferred to v2' .planning/REQUIREMENTS.md` = **1** (≥ 1).
- `grep -n 'OBS-02' .planning/REQUIREMENTS.md | grep -i 'defer'` returns 2 lines (v2 subsection bullet + traceability row).
- `uv run pytest -x` exits 0 (no test depends on README/REQUIREMENTS content).

## Deviations from Plan

None — plan executed exactly as written. The plan's three tasks each landed in a single atomic commit; the must_haves truths and artifacts blocks are all satisfied; no Rule 1/2/3/4 deviations were triggered during execution.

## Threat Model — T-07-09 disposition

The plan registered exactly one threat — `T-07-09 (Information Disclosure / 429 synthetic log line contents)` with disposition `mitigate`. The mitigation is now proven by `test_logs_no_user_input`: three hostile-input strings (SQL injection, prompt-injection token, FTS5-style operator) are placed simultaneously into the request body AND into the X-Forwarded-For header; after driving the rate-limit branch, the captured 429 log line repr contains zero substrings of any hostile input. This carries forward Phase 3's INJ-05 invariant to the rate-limit layer:

- The middleware never reads `receive()` before emitting 429, so request-body content cannot leak.
- `ip_prefix` is bound to the `/24`-truncated value by `RequestIdMiddleware` upstream — the rate-limit middleware reads only the contextvar, not the raw XFF.
- The synthetic log line uses a fixed key set (`tool=None, database=None, table=None, duration_ms, status, error_code`) — every value is either fixed or derived from a non-user input.

T-07-09 mitigation status: **closed** by `test_logs_no_user_input` parametrized over the 3 representative hostile strings.

## Threat Flags

None — no new security-relevant surface introduced. All changes in this plan are additive (one new test in test_app.py, two GREEN-ed tests in test_rate_limit.py, README docs, REQUIREMENTS.md row update).

## Self-Check: PASSED

- `tests/test_app.py` exists and contains `test_healthz_dispatches_no_httpx_request` (verified via prior grep `def test_healthz` count = 2).
- `tests/test_rate_limit.py` exists and contains both `test_429_log_line_shape` and `test_logs_no_user_input` (verified via the targeted pytest run that collected 4 items: 1 + 3 parametrized).
- `README.md` operator subsection present (verified via `grep -c -- '--workers 1' README.md` = 4 and `grep -c '00:00 UTC' README.md` = 1).
- `.planning/REQUIREMENTS.md` OBS-02 row marked `Deferred to v2 (D7-05)` (verified via grep).
- All three commits present in `git log --oneline -5`:
  - `648d5f6 test(07-06): lock /healthz no-upstream-call contract via httpx_mock`
  - `08cb186 test(07-06): GREEN OBS-03 log shape + OBS-04 no-user-input invariants`
  - `5ac4f71 docs(07-06): operator notes (RATE-06, UTC reset) + OBS-02 v2 deferral`
- Full test suite GREEN: 359 passed, 7 skipped (no plan-induced skips).
