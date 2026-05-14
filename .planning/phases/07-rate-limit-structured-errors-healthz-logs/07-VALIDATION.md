---
phase: 7
slug: rate-limit-structured-errors-healthz-logs
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-15
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Generated from `07-RESEARCH.md` § Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio 1.3.0 (auto mode) |
| **Config file** | `pyproject.toml` (`asyncio_mode = "auto"`) |
| **Quick run command** | `uv run pytest tests/test_rate_limit.py tests/test_error_catalog.py tests/test_datasette_client_retry.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~30 seconds (full suite); ~3 seconds (quick) |

---

## Sampling Rate

- **After every task commit:** Run quick run command (above).
- **After every plan wave:** Run full suite command.
- **Before `/gsd-verify-work`:** Full suite must be green.
- **Max feedback latency:** 30 seconds.

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | 01 | 1 | RATE-01 | T-07-01 (token bucket bypass) | Burst=20 allows 20 reqs, 21st rejected with 429 | unit | `pytest tests/test_rate_limit.py::test_burst_allows_20_rejects_21st -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-01 | — | Sustained 1/s: token refills after 1 second | unit | `pytest tests/test_rate_limit.py::test_sustained_refill_after_one_second -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-01 | T-07-01 | Daily ceiling 5000: 5001st rejected same day | unit | `pytest tests/test_rate_limit.py::test_daily_limit_5000 -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-01 | T-07-02 (early daily reset) | Daily counter resets at UTC midnight, not before | unit | `pytest tests/test_rate_limit.py::test_daily_reset_at_utc_midnight -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-02 | T-07-03 (rate-limit after parse) | Rate-limit fires before JSON-RPC parse | integration | `pytest tests/test_rate_limit.py::test_rate_limit_fires_before_json_rpc_parse -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-03 | T-07-04 (XFF spoof bypass) | XFF right-to-left parsing, depth=1 | unit | `pytest tests/test_rate_limit.py::test_xff_parsing_depth_1 -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-03 | — | XFF fewer hops than depth: fall back to leftmost untrusted hop | unit | `pytest tests/test_rate_limit.py::test_xff_fewer_hops_than_depth -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-04 | T-07-05 (store unbounded) | Single TCP peer cannot expand store beyond 100k via XFF spoofing | unit | `pytest tests/test_rate_limit.py::test_store_cap_enforced_under_flood -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-04 | T-07-06 (daily-lock evasion via idle) | Daily-locked bucket not evicted by 15-min idle TTL | unit | `pytest tests/test_rate_limit.py::test_sticky_ttl_daily_locked_not_expired -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-05 | — | Retry-After header is always integer seconds | unit | `pytest tests/test_rate_limit.py::test_retry_after_is_integer -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-05 | — | Retry-After = max(burst_wait, daily_wait) under multi-window exhaustion | unit | `pytest tests/test_rate_limit.py::test_retry_after_max_of_windows -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-05 | — | 429 body has `retry_after_seconds` field | unit | `pytest tests/test_rate_limit.py::test_429_body_has_retry_after_seconds -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-05 | — | 429 body has `request_id` field | unit | `pytest tests/test_rate_limit.py::test_429_body_has_request_id -x` | ❌ W0 | ⬜ pending |
| TBD | 01 | 1 | RATE-06 | — | README documents single-worker requirement | manual | N/A — doc review | N/A | ⬜ pending |
| TBD | 02 | 1 | ERR-01 | T-07-07 (catalog drift) | All ToolErrors have stable `code: message` prefix from locked 11-code catalog | unit | `pytest tests/test_error_catalog.py::test_all_errors_have_stable_code -x` | ❌ W0 | ⬜ pending |
| TBD | 02 | 1 | ERR-02 | — | All 11 codes exercised in catalog test | unit | `pytest tests/test_error_catalog.py::test_all_11_codes_in_catalog -x` | ❌ W0 | ⬜ pending |
| TBD | 02 | 1 | ERR-03 | — | Error envelope includes `request_id` | unit | `pytest tests/test_error_catalog.py::test_error_includes_request_id -x` | ❌ W0 | ⬜ pending |
| TBD | 02 | 1 | ERR-04 | — | 502 retries once with 250ms + uniform(0,250ms) jitter | unit | `pytest tests/test_datasette_client_retry.py -x` | ✅ exists | ⬜ pending |
| TBD | 02 | 1 | ERR-04 | — | 502 twice → `upstream_unavailable` raised | unit | `pytest tests/test_datasette_client_retry.py::test_502_twice_raises -x` | ❌ W0 | ⬜ pending |
| TBD | 02 | 1 | ERR-04 | — | 504 immediate → `upstream_unavailable` (no retry) | unit | `pytest tests/test_datasette_client_retry.py::test_504_raises_immediately -x` | ✅ exists | ⬜ pending |
| TBD | 02 | 1 | ERR-05 | T-07-08 (upstream body echo) | Upstream 4xx → catalog code, no upstream body echo (INJ-05 preserved) | unit | `pytest tests/test_error_catalog.py::test_upstream_4xx_no_echo -x` | ❌ W0 | ⬜ pending |
| TBD | 03 | 1 | OBS-01 | — | `/healthz` returns 200 + `{"status":"ok"}` without upstream call | unit | `pytest tests/test_app.py::test_healthz_returns_ok_without_upstream -x` | ✅ exists | ⬜ pending |
| TBD | 03 | 1 | OBS-03 | — | 429 log line has all `LOG_FIELDS`, `tool/database/table` are null | unit | `pytest tests/test_rate_limit.py::test_429_log_line_shape -x` | ❌ W0 | ⬜ pending |
| TBD | 03 | 1 | OBS-04 | T-07-09 (input echo in logs) | Logs never contain row contents or filter values regardless of input size | unit | `pytest tests/test_rate_limit.py::test_logs_no_user_input -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs are TBD pending PLAN.md emission — verifier should rewrite the Task ID column after plans are written.*

---

## Wave 0 Requirements

- [ ] `tests/test_rate_limit.py` — new file, stubs for RATE-01..05 + OBS-03/04 (skipped tests)
- [ ] `tests/test_error_catalog.py` — new file, stubs for ERR-01..03 + ERR-05 (skipped tests)
- [ ] `tests/test_datasette_client_retry.py` — extend with `test_502_twice_raises` stub
- [ ] `tests/conftest.py` — add `fake_clock`, `rate_limiter`, `bucket_store` fixtures (single-plan-touch rule: lands in Plan 07-01 only)
- [ ] `src/mcp_zeeker/core/middleware/rate_limit.py` — new ASGI middleware module (target file)
- [ ] `src/mcp_zeeker/config.py` — additions: `RATE_BURST`, `RATE_SUSTAINED_PER_SECOND`, `RATE_DAILY_LIMIT`, `RATE_STORE_CAP`, `RATE_IDLE_TTL_SECONDS`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| README documents single-worker (`workers=1`) requirement | RATE-06 | Documentation review — no automated check for prose | Open `README.md`; confirm operator section explicitly states `uvicorn ... --workers 1` and explains why (in-memory limiter would silently multiply with multiple workers) |
| OBS-02 (in-process upstream-status) explicitly deferred to v2 in REQUIREMENTS.md traceability | D7-05 | Single-source-of-truth update; no behavioral test possible | Open `.planning/REQUIREMENTS.md`; confirm OBS-02 row marked "deferred to v2" with reference to D7-05 |

---

## Nyquist Invariant Properties

The following invariants must hold under adversarial conditions and are the **properties** (not just specific cases) the test suite must enforce:

1. **Bounded store under hostile flood.** Single TCP peer cannot expand bucket store beyond `RATE_STORE_CAP` (100k) via XFF spoofing. `_enforce_cap()` is called on every request; `len(store) <= RATE_STORE_CAP` is a hard invariant.

2. **No early daily reset via eviction.** A daily-locked bucket is never reset by going idle; sticky TTL = `max(15 min, time-to-next-utc-midnight)`. Re-creation after LRU eviction (only possible under 100k-IP flood) starts a fresh counter — documented accepted tradeoff, requires > 100k unique attacker IPs.

3. **Integer-seconds Retry-After.** `math.ceil()` on `max(burst_wait, daily_wait)` where all waits are positive. `seconds_to_utc_midnight()` returns `max(1, ...)`. The header value is always a positive integer.

4. **Log emission contains no user input.** The rate-limit middleware log line contains only fields from `LOG_FIELDS`; `tool/database/table` are hardcoded `None`; the request body is never parsed or logged.

5. **429 body contains no user input.** Body is constructed from fixed strings + `retry_after_seconds` (int) + `request_id` (opaque UUID hex). No URL, filter value, query string, or user-supplied parameter is included.

6. **Daily reset fires exactly at UTC midnight.** `datetime.now(tz=timezone.utc).date()` comparison ensures reset only on UTC date change. DST and leap seconds do not affect UTC midnight.

7. **Single log line per request.** `StructuredLogMiddleware` (FastMCP layer) does NOT emit a second log line for 429s — the ASGI middleware short-circuits before FastMCP processes the request.

8. **/healthz dispatches no upstream HTTP request.** Asserted via `httpx_mock` recording: zero outgoing requests during a `/healthz` GET.

---

## Validation Sign-Off

- [ ] All tasks have `<acceptance_criteria>` referencing an automated command from this map OR a Wave 0 stub
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all `❌ W0` references in the verification map
- [ ] No watch-mode flags (`pytest -f`, `vitest --watch`) anywhere
- [ ] Feedback latency < 30s for quick-run suite
- [ ] `nyquist_compliant: true` set in frontmatter after Wave 0 lands

**Approval:** pending
