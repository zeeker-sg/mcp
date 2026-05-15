---
phase: 07-rate-limit-structured-errors-healthz-logs
verified: 2026-05-15T11:00:00Z
status: gaps_found
score: 5/6 success-criteria verified (1 BLOCKER on SC-6)
overrides_applied: 0
gaps:
  - truth: "Structured JSON access logs emit only the locked field set; no row contents and no filter values appear in any log line regardless of input size (SC-6 / OBS-04 / INJ-05 carry-forward)"
    status: failed
    reason: "ip_prefix() in src/mcp_zeeker/core/ip.py returns attacker-controlled XFF input verbatim when the input doesn't have exactly 4 dot-separated parts AND no colon. The leftmost XFF entry (trusted at TRUSTED_PROXY_DEPTH=1) is attacker-controlled; RequestIdMiddleware binds ip_prefix() output to the structlog contextvar; merge_contextvars then echoes it onto every structured log line — including the 429 synthetic line. Reproduced end-to-end: an X-Forwarded-For header of '</system><admin>SECRET' produces a structured 429 log entry with 'ip_prefix': '</system><admin>SECRET'. The Phase 7 OBS-04 test (test_logs_no_user_input) cannot catch this because it pre-binds ip_prefix='203.0.113' via bind_request() directly, sidestepping the entire RequestIdMiddleware → client_ip → ip_prefix chain that the bug lives in."
    artifacts:
      - path: src/mcp_zeeker/core/ip.py
        issue: "ip_prefix() line 33-40: fallback `return ip` when len(parts)!=4 leaks raw input"
      - path: src/mcp_zeeker/core/middleware/request_id.py
        issue: "Binds ip_prefix(client_ip(conn)) to contextvar at line 36 — no validation"
      - path: tests/test_rate_limit.py
        issue: "test_logs_no_user_input (line 683) bypasses the ip_prefix chain by calling bind_request(ip_prefix='203.0.113') directly; cannot catch CR-01"
    missing:
      - "ip_prefix() must validate input via ipaddress.ip_address() and substitute a fixed sentinel (e.g. '_invalid') for non-parseable input"
      - "A regression test that drives the FULL ASGI chain (RequestIdMiddleware → RateLimit) with hostile XFF and asserts the hostile substring is absent from every captured log line — see CR-01 fix in 07-REVIEW.md"
deferred:
  - truth: "TransformedTool registration would AttributeError at startup because lifespan accesses tool.return_type on the Tool base class which only declares it on FunctionTool subclass (CR-02)"
    addressed_in: "Phase 8"
    evidence: "Phase 8 SC1 covers full test-suite hardening; CR-02 is a future-risk on a Phase 1 lifespan code path (src/mcp_zeeker/app.py:53-67) that today happens to work because all 6 registered tools are FunctionTool instances. Not a Phase 7 deliverable; the lifespan was authored in Phase 1 and Phase 7 did not touch it. Logged for follow-up but does not block the Phase 7 goal today."
human_verification: []
---

# Phase 7: Rate limit + structured errors + healthz + logs Verification Report

**Phase Goal:** The server enforces anonymous-tier rate limits with correct `Retry-After` semantics, returns every error from the locked catalog with stable codes plus request ID, exposes liveness on `/healthz` without leaking upstream status, and emits structured JSON access logs that never echo user input.
**Verified:** 2026-05-15T11:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification

## Goal Achievement

### Success Criteria (from ROADMAP.md)

| #   | Truth                                                                                                                                                                                                                                                                | Status      | Evidence                                                                                                                                                                                                                                                                                                                                  |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Token bucket (burst 20, sustained 1 tok/s, daily 5,000/IP/24h) implemented as ASGI middleware so 429s short-circuit before JSON-RPC parsing; HTTP 429 with integer `Retry-After` header AND `retry_after_seconds` in body                                            | ✓ VERIFIED  | `RateLimitMiddleware` registered at ASGI layer in `src/mcp_zeeker/app.py:107-121` between OriginAllowlist and Mount('/mcp'); `_check_bucket` (rate_limit.py:189-249) implements all three windows; `_seconds_to_utc_midnight` ensures integer; `__call__` builds 429 with `Retry-After` header (line 185); 13/13 RATE-01..05 tests GREEN. |
| 2   | Client IP from XFF parsed right-to-left with TRUSTED_PROXY_DEPTH; flood of 10k spoofed XFF leaves bucket store ≤ 100k with LRU + TTL eviction                                                                                                                        | ✓ VERIFIED  | `client_ip_from_scope` (ip.py:43-77) implements right-to-left parsing identical to `client_ip`; `_enforce_cap` (rate_limit.py:310-331) batch-LRU evicts oldest 1% at cap; `_sweep` (rate_limit.py:290-306) idle-evicts via `_effective_ttl`. Tests: `test_xff_parsing_depth_1`, `test_xff_fewer_hops_than_depth`, `test_store_cap_enforced_under_flood`, `test_sticky_ttl_daily_locked_not_expired` all GREEN. |
| 3   | Every error has stable `code` from locked 11-code catalog, request ID for correlation, never echoes upstream Datasette message bodies                                                                                                                                | ✓ VERIFIED  | `core/errors.CATALOG` (errors.py:74-86) is the literal 11-tuple; `ErrorEnrichmentMiddleware` (error_enrichment.py:54-75) appends `[request_id: <hex>]` after RetrievedAt in FastMCP chain (server.py:21); `raise_query_timeout`/`raise_upstream_unavailable` use FIXED literals (errors.py:89-123) — the constructor takes ZERO arguments so user input cannot be echoed by construction. Tests: `test_all_11_codes_in_catalog`, `test_all_errors_have_stable_code`, `test_error_includes_request_id`, `test_upstream_4xx_no_echo` all GREEN. |
| 4   | Upstream 502/503 (not 504) retried exactly once with 250ms + uniform(0,250ms) jitter and surfaced as `upstream_unavailable` if retry fails; 504 surfaces immediately                                                                                                 | ✓ VERIFIED  | `_request_with_retry` (datasette_client.py:141-192): `for attempt in (0, 1)` loop; 502/503 attempt 0 sleeps `0.25 + random.random() * 0.25` then continues; attempt 1 raises `UpstreamCallFailed("upstream retry exhausted on {url}")`; 504 raises immediately at line 178-183; new `QueryTimeoutError(UpstreamCallFailed)` subclass for `httpx.TimeoutException` (caught BEFORE RequestError per subclass-precedence). Tests: `test_502_twice_raises`, `test_503_twice_raises`, `test_504_raises_immediately`, `test_502_retries_once_then_succeeds`, `test_503_retries_once_then_succeeds`, `test_timeout_raises_query_timeout_error` all GREEN. |
| 5   | `GET /healthz` returns 200 minimal payload without consulting `data.zeeker.sg`; upstream-health diagnostics on separate operator-only path                                                                                                                          | ✓ VERIFIED  | `healthz` handler (app.py:93-95) returns `JSONResponse({"status": "ok"})` — no upstream import or call; `test_healthz_dispatches_no_httpx_request` (test_app.py) asserts `httpx_mock.get_requests() == []` after `/healthz` request. OBS-02 (`/internal/upstream-status`) explicitly deferred to v2 per D7-04/D7-05; REQUIREMENTS.md row 265 marks it `Deferred to v2 (D7-05)`; v1 ships only `/healthz` and operators inspect via external curl per README. |
| 6   | Structured JSON access logs emit only the locked field set; no row contents and no filter values appear in any log line regardless of input size                                                                                                                     | ✗ FAILED    | **CR-01 BLOCKER.** `ip_prefix()` (ip.py:33-40) returns `ip` verbatim when `len(parts) != 4` and no colon. Hostile XFF (e.g. `</system><admin>SECRET`) flows: client_ip()→leftmost XFF entry → ip_prefix()→returns hostile string verbatim → RequestIdMiddleware binds to structlog contextvar → merge_contextvars puts it on every log line including the 429 synthetic line. Reproduced end-to-end (see Behavioral Spot-Checks below). Test `test_logs_no_user_input` cannot catch this because it pre-binds `ip_prefix='203.0.113'` via `bind_request()` directly (test_rate_limit.py:704), sidestepping the actual chain. SC-6 explicitly requires "regardless of input size"; this is a log-injection vulnerability that contradicts INJ-05 carry-forward (07-CONTEXT.md line 75: "user-supplied URL / filter values never echo into error bodies or logs. Phase 7 MUST preserve."). |

**Score:** 5/6 success criteria verified

### Required Artifacts

| Artifact                                              | Expected                                                  | Status      | Details                                                                                                          |
| ----------------------------------------------------- | --------------------------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------- |
| `src/mcp_zeeker/core/middleware/rate_limit.py`        | RateLimitMiddleware ASGI class + BucketState              | ✓ VERIFIED  | 332 LOC; class+dataclass present; `__slots__` correct; all 4 eviction methods implemented                        |
| `src/mcp_zeeker/core/ip.py`                           | client_ip_from_scope helper for raw ASGI scope            | ⚠️ FUNCTIONAL but `ip_prefix()` BROKEN | `client_ip_from_scope` works; `_normalize_ip_key` works (modulo WR-02); BUT `ip_prefix()` returns hostile input verbatim (CR-01 BLOCKER) |
| `src/mcp_zeeker/config.py`                            | RATE_BURST/SUSTAINED_PER_SECOND/DAILY_LIMIT/STORE_CAP/IDLE_TTL_SECONDS | ✓ VERIFIED  | All 5 constants present (verified by import smoke test)                                                          |
| `src/mcp_zeeker/app.py`                               | RateLimitMiddleware registered between OriginAllowlist and Mount('/mcp') | ✓ VERIFIED  | app.py:107-121 — middleware list correct                                                                          |
| `src/mcp_zeeker/core/errors.py`                       | CATALOG tuple + raise_query_timeout + raise_upstream_unavailable | ✓ VERIFIED  | 11 codes in REQUIREMENTS.md order; both helpers FIXED-literal                                                    |
| `src/mcp_zeeker/core/middleware/error_enrichment.py`  | ErrorEnrichmentMiddleware appending [request_id: ...]     | ✓ VERIFIED  | 75 LOC; `on_call_tool` try/except ToolError; uses `str(exc)` (not `.message`); falls back unchanged when contextvar empty |
| `src/mcp_zeeker/server.py`                            | ErrorEnrichmentMiddleware registered AFTER RetrievedAt    | ✓ VERIFIED  | server.py:18-22 — RetrievedAt → ErrorEnrichment → StructuredLog                                                  |
| `src/mcp_zeeker/core/datasette_client.py`             | QueryTimeoutError subclass + httpx.TimeoutException catch | ✓ VERIFIED  | `QueryTimeoutError(UpstreamCallFailed)` at line 44; TimeoutException catch at line 157 BEFORE RequestError       |
| `tests/conftest.py`                                   | Phase 7 fixtures fake_clock/rate_limiter/bucket_store     | ✓ VERIFIED  | "Phase 7 — Rate limit fixtures" block exists; 17 tests in test_rate_limit.py consume them                        |
| `tests/test_rate_limit.py`                            | All RATE-01..05 + OBS-03/04 tests GREEN                   | ⚠️ ALL TESTS PASS but `test_logs_no_user_input` is a FALSE-POSITIVE for SC-6 (sidesteps the ip_prefix chain) |
| `tests/test_app.py::test_healthz_dispatches_no_httpx_request` | OBS-01 belt-and-suspenders test                           | ✓ VERIFIED  | Test exists and passes                                                                                            |
| `tests/test_error_catalog.py`                         | 4 tests for ERR-01/02/03/05                               | ✓ VERIFIED  | All 4 GREEN                                                                                                       |
| `tests/test_datasette_client_retry.py`                | ERR-04 tests (502/503 exhaustion, 504, timeout)           | ✓ VERIFIED  | 7 tests GREEN (4 new + 3 pre-existing)                                                                            |
| `README.md`                                           | --workers 1 mandate + 00:00 UTC reset + upstream-status v2 deferral | ✓ VERIFIED  | grep counts: `--workers 1`=4, `00:00 UTC`=1, `upstream-status`=1                                                  |
| `.planning/REQUIREMENTS.md`                           | OBS-02 row marked Deferred to v2 with D7-05 reference     | ✓ VERIFIED  | Row 265: `\| OBS-02 \| Phase 1 \| Deferred to v2 (D7-05) \|`; v2 Observability subsection added                    |

### Key Link Verification

| From                                              | To                                              | Via                                                       | Status      | Details                                                  |
| ------------------------------------------------- | ----------------------------------------------- | --------------------------------------------------------- | ----------- | -------------------------------------------------------- |
| `src/mcp_zeeker/app.py`                           | `core/middleware/rate_limit.py`                 | `Middleware(RateLimitMiddleware, burst=...)` in middleware list | ✓ WIRED     | app.py:114-121                                            |
| `core/middleware/rate_limit.py`                   | `core/ip.py`                                    | `client_ip_from_scope(scope, depth)` import + call       | ✓ WIRED     | rate_limit.py:59 import; rate_limit.py:147 call           |
| `core/middleware/rate_limit.py`                   | `core/middleware/request_id.py`                 | `structlog.contextvars.get_contextvars()['request_id']`  | ✓ WIRED     | rate_limit.py:159 — reads contextvar bound by RequestId   |
| `core/middleware/request_id.py`                   | `core/ip.py`                                    | `bind_request(ip_prefix=ip_prefix(client_ip(conn)))`     | ⚠️ WIRED but UNSAFE | request_id.py:11 import; request_id.py:36 call — but ip_prefix() leaks hostile XFF (CR-01) |
| `core/middleware/error_enrichment.py`             | `structlog.contextvars`                         | `get_contextvars().get('request_id', '')`                | ✓ WIRED     | error_enrichment.py:65                                    |
| `src/mcp_zeeker/server.py`                        | `core/middleware/error_enrichment.py`           | `mcp.add_middleware(ErrorEnrichmentMiddleware())` after RetrievedAt | ✓ WIRED     | server.py:21                                              |
| `core/datasette_client.py`                        | `core/errors.py`                                | `QueryTimeoutError` discriminated by handlers; `raise_query_timeout` translates | ✓ WIRED     | end-to-end smoke test in 07-05 SUMMARY                    |

### Data-Flow Trace (Level 4)

| Artifact                                              | Data Variable        | Source                          | Produces Real Data                  | Status            |
| ----------------------------------------------------- | -------------------- | ------------------------------- | ----------------------------------- | ----------------- |
| `RateLimitMiddleware` (`__call__` 429 path)           | `request_id`         | structlog contextvars (bound by RequestIdMiddleware) | Yes — uuid4 hex or sanitized client header | ✓ FLOWING         |
| `RateLimitMiddleware` (`__call__` 429 path)           | `retry_after`        | `_check_bucket` deny path       | Yes — integer ≥ 1 from D7-02 max(waits) arithmetic | ✓ FLOWING         |
| `RateLimitMiddleware` (synthetic log line)            | `ip_prefix` (via contextvar from RequestIdMiddleware) | `ip_prefix(client_ip(conn))` in request_id.py:36 | **NO — leaks raw hostile XFF (CR-01)** | ⚠️ HOLLOW for the security invariant — STATIC string flows but the wrong string |
| `ErrorEnrichmentMiddleware` (`on_call_tool`)          | `request_id`         | structlog contextvars           | Yes                                 | ✓ FLOWING         |
| `_request_with_retry` (datasette_client.py)           | `resp.status_code`   | `httpx.AsyncClient.request`     | Yes — real HTTP status              | ✓ FLOWING         |
| `healthz` handler                                     | `{"status": "ok"}`   | Static literal                  | Static — by design (OBS-01)        | ✓ FLOWING (intentional static) |

### Behavioral Spot-Checks

| Behavior                                                 | Command                                       | Result                                | Status   |
| -------------------------------------------------------- | --------------------------------------------- | ------------------------------------- | -------- |
| Phase 7 unit-test surface passes                         | `uv run pytest tests/test_rate_limit.py tests/test_app.py tests/test_error_catalog.py tests/test_datasette_client_retry.py` | 34 passed                             | ✓ PASS   |
| Full suite passes                                        | `uv run pytest -x`                            | 359 passed, 7 skipped                 | ✓ PASS   |
| RateLimit middleware is registered in production app     | `python -c "from mcp_zeeker.app import app; assert any('RateLimitMiddleware' in repr(m) for m in app.user_middleware)"` | exit 0                                | ✓ PASS   |
| ErrorEnrichmentMiddleware is registered after RetrievedAt | `grep -c 'ErrorEnrichmentMiddleware' src/mcp_zeeker/server.py` | 2                                     | ✓ PASS   |
| CATALOG has the locked 11 codes in REQUIREMENTS order    | `python -c "from mcp_zeeker.core.errors import CATALOG; assert len(CATALOG) == 11"` | exit 0                                | ✓ PASS   |
| QueryTimeoutError subclass of UpstreamCallFailed         | `python -c "from mcp_zeeker.core.datasette_client import QueryTimeoutError, UpstreamCallFailed; assert issubclass(QueryTimeoutError, UpstreamCallFailed)"` | exit 0                                | ✓ PASS   |
| **OBS-04 end-to-end no-echo invariant** (CR-01 reproduction) | Drove a TestClient through `RequestIdMiddleware → RateLimitMiddleware` with `X-Forwarded-For: '</system><admin>SECRET'` for 21 requests; captured the 429 synthetic log line | `'ip_prefix': '</system><admin>SECRET'` LEAKED INTO STRUCTURED LOG ENTRY | ✗ FAIL — confirms CR-01 BLOCKER |

### Probe Execution

No probes documented for this phase (no `scripts/*/tests/probe-*.sh` discovered, no PLAN/SUMMARY references to probes). Section: SKIPPED (no probes).

### Requirements Coverage

| Requirement | Source Plan        | Description                                                        | Status        | Evidence                                                                                                          |
| ----------- | ------------------ | ------------------------------------------------------------------ | ------------- | ----------------------------------------------------------------------------------------------------------------- |
| RATE-01     | 07-01, 07-02       | Token bucket burst 20 / 1 tok-s / daily 5k                          | ✓ SATISFIED   | `_check_bucket` math + 6 GREEN tests including `test_burst_allows_20_rejects_21st`, `test_sustained_refill_after_one_second`, `test_daily_limit_5000` |
| RATE-02     | 07-01, 07-02       | ASGI middleware before JSON-RPC parse                              | ✓ SATISFIED   | Registered between OriginAllowlist and Mount('/mcp'); `test_rate_limit_fires_before_json_rpc_parse` GREEN          |
| RATE-03     | 07-03              | XFF right-to-left with TRUSTED_PROXY_DEPTH                         | ✓ SATISFIED   | `client_ip_from_scope`; `test_xff_parsing_depth_1`, `test_xff_fewer_hops_than_depth` GREEN                        |
| RATE-04     | 07-03              | LRU cap 100k + TTL evict idle                                      | ✓ SATISFIED   | `_enforce_cap` + `_sweep`; `test_store_cap_enforced_under_flood`, `test_sticky_ttl_daily_locked_not_expired` GREEN |
| RATE-05     | 07-01, 07-02       | 429 + Retry-After (integer) + retry_after_seconds in body          | ✓ SATISFIED   | `__call__` 429 path; tests `test_429_body_has_retry_after_seconds`, `test_retry_after_is_integer`, `test_retry_after_max_of_windows` GREEN |
| RATE-06     | 07-06              | Single Uvicorn worker (documented)                                 | ✓ SATISFIED   | README contains `--workers 1` (4 occurrences) + bug-class explanation                                              |
| ERR-01      | 07-04              | Stable codes on every error                                        | ✓ SATISFIED   | `test_all_errors_have_stable_code` (10 raise sites) GREEN                                                         |
| ERR-02      | 07-04              | Locked 11-code catalog                                             | ✓ SATISFIED   | `core/errors.CATALOG` tuple + `test_all_11_codes_in_catalog` (asserts ordering AND set) GREEN                     |
| ERR-03      | 07-04              | request_id echo for correlation                                    | ✓ SATISFIED   | `ErrorEnrichmentMiddleware` + `test_error_includes_request_id` GREEN                                              |
| ERR-04      | 07-05              | 502/503 retry-once-with-jitter; 504 immediate                      | ✓ SATISFIED   | `_request_with_retry` + 7 GREEN tests in `test_datasette_client_retry.py`                                         |
| ERR-05      | 07-04              | 4xx mapped to catalog; no upstream body echo                       | ✓ SATISFIED   | `test_upstream_4xx_no_echo` GREEN; `raise_upstream_unavailable` takes ZERO args (cannot interpolate user input)   |

**Note on OBS-* requirements:** The phase plan frontmatter lists only RATE-01..06 and ERR-01..05 (11 IDs). OBS-01/03/04 are formally Phase 1 requirements per REQUIREMENTS.md row 264-268, but the Phase 7 ROADMAP goal explicitly covers `/healthz` (OBS-01) and structured-log no-input-echo (OBS-04). 07-06 explicitly GREENs OBS-01/03/04 tests. **OBS-04 is FAILED in production code (CR-01) despite the test passing**, because the test bypasses the actual production chain.

| OBS Requirement | Status                  | Evidence                                                                                                                                |
| --------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| OBS-01          | ✓ SATISFIED             | `/healthz` handler + `test_healthz_dispatches_no_httpx_request` (mechanical no-upstream assertion)                                       |
| OBS-02          | DEFERRED to v2 (D7-05)  | REQUIREMENTS.md row 265 marks deferred; honest traceability                                                                              |
| OBS-03          | ✓ SATISFIED             | `test_429_log_line_shape` asserts the LOG_FIELDS-locked key set and the synthetic 429 emits exactly the locked fields                    |
| OBS-04          | ✗ NOT SATISFIED         | CR-01: `ip_prefix()` leaks raw hostile XFF; the OBS-04 test pre-binds a clean prefix and cannot catch this. Production end-to-end exploit reproduced. |
| OBS-05          | ✓ SATISFIED (carry-forward) | RequestIdMiddleware uses contextvars; structlog merge_contextvars wired; verified by inspection                                          |

### Anti-Patterns Found

| File                                                  | Line       | Pattern                                                  | Severity      | Impact                                                                                                                                                                                                                                |
| ----------------------------------------------------- | ---------- | -------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/mcp_zeeker/core/ip.py`                           | 33-40      | Unvalidated input echoed verbatim (`return ip` fallback) | 🛑 BLOCKER    | CR-01: log-injection vulnerability; violates SC-6 / OBS-04 / INJ-05 carry-forward; phase goal "logs that never echo user input" not actually achieved.                                                                                |
| `src/mcp_zeeker/app.py`                               | 56-67      | Attribute access on base class without `getattr` default (`tool.return_type`) | ⚠️ WARNING    | CR-02: Phase 1 lifespan code (not Phase 7 surface); works today because all registered tools are FunctionTool instances; would AttributeError if a TransformedTool is registered later. Surfaced by code review; not a Phase 7 deliverable. |
| `src/mcp_zeeker/core/datasette_client.py`             | 192        | Unreachable defensive raise                              | ℹ️ INFO       | IN-01 (review): post-loop raise on retry-exhaustion is unreachable but kept; could use `raise AssertionError("unreachable")` for clarity. Non-blocking.                                                                                |
| `src/mcp_zeeker/core/middleware/rate_limit.py`        | 137, 160   | Wall-clock `time.perf_counter` mixed with injected `time_provider`               | ⚠️ WARNING    | WR-03: `duration_ms` always uses real wall clock even when fake clock injected; not currently asserted on in tests; latent test-flakiness risk only.                                                                                  |
| `src/mcp_zeeker/core/datasette_client.py`             | 221-239    | Over-broad `except (..., TypeError)` catch              | ⚠️ WARNING    | WR-04: TypeError-swallow over an `await` block masks real bugs. Pre-existing (Phase 2/4 code), surfaced in this phase's review. Non-blocking for Phase 7 goal.                                                                         |
| `src/mcp_zeeker/core/ip.py`                           | 37-38      | IPv6 prefix logic uses naive split                      | ⚠️ WARNING    | WR-01: IPv6 zero-compression produces malformed prefix strings (e.g. `2001:db8::1` → `2001:db8:`); related to CR-01 fix scope.                                                                                                       |
| `src/mcp_zeeker/core/ip.py`                           | 80-91      | `_normalize_ip_key` doesn't strip `[::1]:8080` port form | ⚠️ WARNING    | WR-02: IPv4-with-port and bracketed-IPv6-with-port forms produce duplicate buckets — token-bucket bypass via port rotation; minor in current XFF model since Caddy normalizes, but documented as gap.                                |
| `tests/test_rate_limit.py`                            | 683-759    | Test bypasses the chain it claims to test                | 🛑 BLOCKER (test) | WR-07: `test_logs_no_user_input` calls `bind_request(ip_prefix='203.0.113')` directly, sidestepping `RequestIdMiddleware → client_ip → ip_prefix`. The test cannot catch CR-01. False-positive coverage of OBS-04. |

### Human Verification Required

None. The CR-01 BLOCKER is mechanically verifiable (an end-to-end TestClient drive reproduces the leak directly), so no human-only judgment is needed.

### Deferred Items

| # | Item                                                                                                                                                                                                                          | Addressed In | Evidence                                                                                                                                                                                                                                                                       |
| - | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1 | CR-02 — `lifespan` accesses `tool.return_type` on `Tool` base; would AttributeError on TransformedTool registration. Currently safe because all 6 registered tools are FunctionTool instances and the access succeeds today. | Phase 8      | Phase 8 goal "complete test suite covers every contract surface" includes hardening of cross-cutting infrastructure. CR-02 is a Phase 1 lifespan concern (app.py:53-67) that Phase 7 did not modify; the bug is latent (no failure today). The fix is small (use `getattr(tool, "return_type", None)`) but is not part of the Phase 7 success-criteria contract. |

**Pre-existing E501 in `config.py`** (logged in `deferred-items.md` by 07-01) is also out-of-scope formatting — does not affect the Phase 7 goal.

### Gaps Summary

**One BLOCKER prevents Phase 7's goal from being achieved end-to-end:**

The phase goal says **"emits structured JSON access logs that never echo user input"**. SC-6 explicitly requires this "regardless of input size". The 07-CONTEXT.md (line 75) names INJ-05 carry-forward as a MUST-PRESERVE invariant for Phase 7.

**The bug:** `src/mcp_zeeker/core/ip.py:ip_prefix()` returns its input verbatim when the input has neither a colon nor exactly four dot-separated parts. An attacker-controlled `X-Forwarded-For` value (the leftmost entry, which `client_ip()` returns at `TRUSTED_PROXY_DEPTH=1`) flows: `client_ip → ip_prefix → bind_request → structlog contextvar → merge_contextvars → every structured log line including the 429 synthetic line`.

**End-to-end reproduction:** Driving a real Starlette TestClient with `X-Forwarded-For: '</system><admin>SECRET'` for 21 requests produces a synthetic 429 log entry with `'ip_prefix': '</system><admin>SECRET'` — the hostile string echoed verbatim into the structured access log.

**Why the OBS-04 test misses it:** `tests/test_rate_limit.py::test_logs_no_user_input` calls `bind_request(ip_prefix='203.0.113')` directly (line 704), pre-binding a clean value. The test then drives `RateLimitMiddleware` in isolation — never invoking `RequestIdMiddleware` and never calling `ip_prefix()` with hostile input. The test passes, but it asserts the WRONG layer of the contract; the production chain is not exercised. The code review surfaced this as WR-07 in addition to CR-01.

**Fix sketch (from 07-REVIEW.md CR-01):**
```python
# src/mcp_zeeker/core/ip.py
import ipaddress

def ip_prefix(ip: str) -> str:
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "_invalid"  # do not echo attacker bytes
    ...
```
Plus a regression test that drives the FULL `RequestIdMiddleware → ...` ASGI chain with hostile XFF and asserts the hostile substring appears in NO captured log line.

**Scope and scale:** Single-file fix (~10 LOC). Should be GREEN-able in one plan (07-07 gap closure). The change does not affect the rate-limit math, the catalog, the healthz contract, or ERR-04 retry — those are all VERIFIED.

**Recommendation:** Do NOT proceed to Phase 8 until CR-01 is closed. The Phase 7 goal "logs never echo user input" is not actually achieved, and Phase 8's hostile-input corpus tests would surface this same bug if they exercise the full chain. Closing it inside Phase 7 keeps the phase contract honest with the test surface.

CR-02 should also be closed (defensively `getattr` with a clear error message) but is independently scheduled as part of Phase 8 hardening per the deferred section above.

---

_Verified: 2026-05-15T11:00:00Z_
_Verifier: Claude (gsd-verifier)_
