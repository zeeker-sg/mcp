---
phase: 07-rate-limit-structured-errors-healthz-logs
verified: 2026-05-15T12:30:00Z
status: passed
score: 6/6 success-criteria verified
overrides_applied: 0
re_verification:
  previous_status: gaps_found
  previous_score: 5/6
  gaps_closed:
    - "SC-6 / OBS-04 / INJ-05 carry-forward: ip_prefix() now validates via ipaddress.ip_address() and returns the fixed sentinel '_invalid' for non-parseable input; hostile XFF bytes no longer echo into any structured log line."
  gaps_remaining: []
  regressions: []
deferred:
  - truth: "TransformedTool registration would AttributeError at startup because lifespan accesses tool.return_type on the Tool base class which only declares it on FunctionTool subclass (CR-02)"
    addressed_in: "Phase 8"
    evidence: "Phase 8 SC1 covers full test-suite hardening; CR-02 is a future-risk on a Phase 1 lifespan code path (src/mcp_zeeker/app.py:53-67) that today works because all 6 registered tools are FunctionTool instances. Logged in deferred-items.md. Not a Phase 7 deliverable."
human_verification: []
---

# Phase 7: Rate limit + structured errors + healthz + logs Verification Report

**Phase Goal:** The server enforces anonymous-tier rate limits with correct `Retry-After` semantics, returns every error from the locked catalog with stable codes plus request ID, exposes liveness on `/healthz` without leaking upstream status, and emits structured JSON access logs that never echo user input.
**Verified:** 2026-05-15T12:30:00Z
**Status:** passed
**Re-verification:** Yes — after CR-01 gap closure (plan 07-07)

## Re-Verification Summary

The prior report (2026-05-15T11:00:00Z, status: `gaps_found`, score 5/6) found one BLOCKER:

**CR-01:** `ip_prefix()` in `src/mcp_zeeker/core/ip.py` returned raw attacker-controlled XFF content verbatim for non-IPv4-shaped input. The hostile string flowed through `RequestIdMiddleware → structlog contextvar → merge_contextvars → every structured log line including the synthetic 429`.

Plan 07-07 closed CR-01 in four commits (f8da1af, 8f13674, 4c708d1, 05d300e):
- `ip_prefix()` now validates via `ipaddress.ip_address()` and returns `"_invalid"` for non-parseable input.
- WR-01 (IPv6 naive split producing malformed prefixes) was closed incidentally by the same rewrite: IPv6 paths now route through `ipaddress.ip_network(addr.exploded/48).network_address` for canonical output.
- A full end-to-end regression test (`test_hostile_xff_does_not_leak_into_log`, 6 parametrized hostile inputs) drives the full ASGI chain (RequestIdMiddleware → RateLimitMiddleware) via `asgi_client` and asserts zero leakage.
- The false-positive test `test_logs_no_user_input` was renamed to `test_rate_limit_middleware_never_reads_body_bytes` with a docstring clarifying its narrower scope.

OBS-04 traceability row in REQUIREMENTS.md updated to `Satisfied (07-07 gap closure — CR-01)`.

Full test suite: **366 passed, 7 skipped** (was 359 — net +7 from 07-07).

## Goal Achievement

### Success Criteria (from ROADMAP.md)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Token bucket (burst 20, sustained 1 tok/s, daily 5,000/IP/24h) implemented as ASGI middleware so 429s short-circuit before JSON-RPC parsing; HTTP 429 with integer `Retry-After` header AND `retry_after_seconds` in body | ✓ VERIFIED | `RateLimitMiddleware` registered at ASGI layer in `app.py:107-121`; `_check_bucket` implements all three windows; `__call__` builds 429 with `Retry-After` header; 13/13 RATE-01..05 tests GREEN. |
| 2 | Client IP from XFF parsed right-to-left with TRUSTED_PROXY_DEPTH; flood of 10k spoofed XFF leaves bucket store ≤ 100k with LRU + TTL eviction | ✓ VERIFIED | `client_ip_from_scope` implements right-to-left; `_enforce_cap` batch-LRU evicts oldest 1% at cap; `_sweep` idle-evicts via `_effective_ttl`; all XFF/eviction tests GREEN. |
| 3 | Every error has stable `code` from locked 11-code catalog, request ID for correlation, never echoes upstream Datasette message bodies | ✓ VERIFIED | `core/errors.CATALOG` is the literal 11-tuple; `ErrorEnrichmentMiddleware` appends `[request_id: ...]`; `raise_query_timeout`/`raise_upstream_unavailable` take ZERO arguments so user input cannot be interpolated. All 4 ERR tests GREEN. |
| 4 | Upstream 502/503 (not 504) retried exactly once with 250ms + uniform(0,250ms) jitter and surfaced as `upstream_unavailable` if retry fails; 504 surfaces immediately | ✓ VERIFIED | `_request_with_retry` for-loop (0,1); 502/503 attempt 0 sleeps `0.25 + random.random()*0.25`; 504 raises immediately; 7 retry tests GREEN. |
| 5 | `GET /healthz` returns 200 minimal payload without consulting `data.zeeker.sg`; upstream-health diagnostics on separate operator-only path | ✓ VERIFIED | Handler returns `JSONResponse({"status": "ok"})` — no upstream call; `test_healthz_dispatches_no_httpx_request` asserts `httpx_mock.get_requests() == []`. OBS-02 (`/internal/upstream-status`) explicitly deferred to v2. |
| 6 | Structured JSON access logs emit only the locked field set; no row contents and no filter values appear in any log line regardless of input size | ✓ VERIFIED | CR-01 CLOSED. `ip_prefix()` validates via `ipaddress.ip_address()`; non-parseable input returns `"_invalid"`. End-to-end regression: `test_hostile_xff_does_not_leak_into_log` drives full ASGI chain with 6 hostile XFF payloads (including `</system><admin>SECRET`) — ALL 6 parametrized cases PASS. `ip_prefix` field in 429 log line matches `^([0-9a-fA-F:.]+|_invalid)$` for every case. |

**Score:** 6/6 success criteria verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/mcp_zeeker/core/middleware/rate_limit.py` | RateLimitMiddleware ASGI class + BucketState | ✓ VERIFIED | 332 LOC; all 4 eviction methods implemented; unchanged by 07-07 |
| `src/mcp_zeeker/core/ip.py` | client_ip_from_scope + validated ip_prefix() | ✓ VERIFIED | `ip_prefix()` rewritten in 07-07: validates via `ipaddress.ip_address()`, returns `"_invalid"` sentinel for hostile input; IPv6 uses canonical /48 form (closes WR-01) |
| `src/mcp_zeeker/config.py` | RATE_BURST/SUSTAINED_PER_SECOND/DAILY_LIMIT/STORE_CAP/IDLE_TTL_SECONDS | ✓ VERIFIED | All 5 constants present; unchanged |
| `src/mcp_zeeker/app.py` | RateLimitMiddleware registered between OriginAllowlist and Mount('/mcp') | ✓ VERIFIED | app.py:107-121 — unchanged by 07-07 |
| `src/mcp_zeeker/core/errors.py` | CATALOG tuple + raise_query_timeout + raise_upstream_unavailable | ✓ VERIFIED | 11 codes; FIXED-literal helpers; unchanged |
| `src/mcp_zeeker/core/middleware/error_enrichment.py` | ErrorEnrichmentMiddleware appending [request_id: ...] | ✓ VERIFIED | 75 LOC; unchanged |
| `src/mcp_zeeker/server.py` | ErrorEnrichmentMiddleware registered AFTER RetrievedAt | ✓ VERIFIED | server.py:18-22; unchanged |
| `src/mcp_zeeker/core/datasette_client.py` | QueryTimeoutError subclass + httpx.TimeoutException catch | ✓ VERIFIED | Unchanged |
| `tests/conftest.py` | Phase 7 fixtures fake_clock/rate_limiter/bucket_store | ✓ VERIFIED | Unchanged |
| `tests/test_rate_limit.py` | RATE-01..05 + OBS-03/04 tests GREEN; renamed false-positive; new CR-01 full-chain test | ✓ VERIFIED | `test_rate_limit_middleware_never_reads_body_bytes` renamed + scoped (WR-07 closure); `test_hostile_xff_does_not_leak_into_log` (6 hostile inputs) added — ALL GREEN |
| `tests/test_logging.py` | IPv6 expectations updated; test_ip_prefix_rejects_non_ip added | ✓ VERIFIED | `test_ip_prefix_truncates_ipv6_to_48` updated for canonical /48 form; `test_ip_prefix_rejects_non_ip` asserts 6 hostile inputs return `"_invalid"` — ALL GREEN |
| `tests/test_app.py::test_healthz_dispatches_no_httpx_request` | OBS-01 belt-and-suspenders test | ✓ VERIFIED | GREEN; unchanged |
| `tests/test_error_catalog.py` | 4 tests for ERR-01/02/03/05 | ✓ VERIFIED | All 4 GREEN; unchanged |
| `tests/test_datasette_client_retry.py` | ERR-04 tests (502/503 exhaustion, 504, timeout) | ✓ VERIFIED | 7 tests GREEN; unchanged |
| `README.md` | --workers 1 mandate + 00:00 UTC reset + upstream-status v2 deferral | ✓ VERIFIED | Unchanged |
| `.planning/REQUIREMENTS.md` | OBS-04 row updated to Satisfied with 07-07 reference | ✓ VERIFIED | Row 267: `OBS-04 \| Phase 1 \| Satisfied (07-07 gap closure — CR-01)` |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/mcp_zeeker/app.py` | `core/middleware/rate_limit.py` | `Middleware(RateLimitMiddleware, burst=...)` in middleware list | ✓ WIRED | app.py:114-121 |
| `core/middleware/rate_limit.py` | `core/ip.py` | `client_ip_from_scope(scope, depth)` import + call | ✓ WIRED | rate_limit.py:59 import; rate_limit.py:147 call |
| `core/middleware/rate_limit.py` | `core/middleware/request_id.py` | `structlog.contextvars.get_contextvars()['request_id']` | ✓ WIRED | rate_limit.py:159 |
| `core/middleware/request_id.py` | `core/ip.py` | `bind_request(ip_prefix=ip_prefix(client_ip(conn)))` | ✓ WIRED + SAFE | ip_prefix() now validates input; non-parseable XFF → "_invalid" sentinel; hostile bytes cannot enter the structlog contextvar chain |
| `core/middleware/error_enrichment.py` | `structlog.contextvars` | `get_contextvars().get('request_id', '')` | ✓ WIRED | error_enrichment.py:65 |
| `src/mcp_zeeker/server.py` | `core/middleware/error_enrichment.py` | `mcp.add_middleware(ErrorEnrichmentMiddleware())` after RetrievedAt | ✓ WIRED | server.py:21 |
| `core/datasette_client.py` | `core/errors.py` | `QueryTimeoutError` discriminated; `raise_query_timeout` translates | ✓ WIRED | End-to-end smoke tested in 07-05 SUMMARY |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|--------------|--------|--------------------|--------|
| `RateLimitMiddleware` (429 path) | `request_id` | structlog contextvars (bound by RequestIdMiddleware) | Yes — uuid4 hex or sanitized client header | ✓ FLOWING |
| `RateLimitMiddleware` (429 path) | `retry_after` | `_check_bucket` deny path | Yes — integer ≥ 1 | ✓ FLOWING |
| `RateLimitMiddleware` (synthetic log line) | `ip_prefix` (via contextvar) | `ip_prefix(client_ip(conn))` in request_id.py:36 | Yes — /24 prefix string or "_invalid" sentinel; never raw hostile XFF (CR-01 closed) | ✓ FLOWING |
| `ErrorEnrichmentMiddleware` (`on_call_tool`) | `request_id` | structlog contextvars | Yes | ✓ FLOWING |
| `_request_with_retry` (datasette_client.py) | `resp.status_code` | `httpx.AsyncClient.request` | Yes — real HTTP status | ✓ FLOWING |
| `healthz` handler | `{"status": "ok"}` | Static literal | Static — by design (OBS-01) | ✓ FLOWING (intentional static) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite passes | `uv run pytest -x` | 366 passed, 7 skipped | ✓ PASS |
| CR-01 canonical reproduction — 6 hostile XFF inputs through full ASGI chain | `uv run pytest tests/test_rate_limit.py::test_hostile_xff_does_not_leak_into_log -v` | 6/6 PASSED | ✓ PASS |
| ip_prefix() rejects hostile strings with "_invalid" sentinel | `uv run pytest tests/test_logging.py::test_ip_prefix_rejects_non_ip -v` | PASSED | ✓ PASS |
| ip_prefix() emits canonical /48 form for IPv6 (WR-01 closure) | `uv run pytest tests/test_logging.py::test_ip_prefix_truncates_ipv6_to_48 -v` | PASSED | ✓ PASS |
| Phase 7 test surface (all 5 modules) | `uv run pytest tests/test_rate_limit.py tests/test_logging.py tests/test_app.py tests/test_error_catalog.py tests/test_datasette_client_retry.py` | 46 passed | ✓ PASS |
| RateLimit middleware registered in production app | `python -c "from mcp_zeeker.app import app; assert any('RateLimitMiddleware' in repr(m) for m in app.user_middleware)"` | exit 0 | ✓ PASS |
| CATALOG has the locked 11 codes | `python -c "from mcp_zeeker.core.errors import CATALOG; assert len(CATALOG) == 11"` | exit 0 | ✓ PASS |

### Probe Execution

No probes documented for this phase (no `scripts/*/tests/probe-*.sh` discovered). Section: SKIPPED (no probes).

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| RATE-01 | 07-01, 07-02 | Token bucket burst 20 / 1 tok/s / daily 5k | ✓ SATISFIED | `_check_bucket` math + 6 GREEN tests |
| RATE-02 | 07-01, 07-02 | ASGI middleware before JSON-RPC parse | ✓ SATISFIED | Registered between OriginAllowlist and Mount('/mcp'); `test_rate_limit_fires_before_json_rpc_parse` GREEN |
| RATE-03 | 07-03 | XFF right-to-left with TRUSTED_PROXY_DEPTH | ✓ SATISFIED | `client_ip_from_scope`; XFF parsing tests GREEN |
| RATE-04 | 07-03 | LRU cap 100k + TTL evict idle | ✓ SATISFIED | `_enforce_cap` + `_sweep`; eviction tests GREEN |
| RATE-05 | 07-01, 07-02 | 429 + Retry-After (integer) + retry_after_seconds in body | ✓ SATISFIED | `__call__` 429 path; body/header shape tests GREEN |
| RATE-06 | 07-06 | Single Uvicorn worker (documented) | ✓ SATISFIED | README contains `--workers 1` (4 occurrences) + rationale |
| ERR-01 | 07-04 | Stable codes on every error | ✓ SATISFIED | `test_all_errors_have_stable_code` (10 raise sites) GREEN |
| ERR-02 | 07-04 | Locked 11-code catalog | ✓ SATISFIED | `core/errors.CATALOG` tuple + `test_all_11_codes_in_catalog` GREEN |
| ERR-03 | 07-04 | request_id echo for correlation | ✓ SATISFIED | `ErrorEnrichmentMiddleware` + `test_error_includes_request_id` GREEN |
| ERR-04 | 07-05 | 502/503 retry-once-with-jitter; 504 immediate | ✓ SATISFIED | `_request_with_retry` + 7 GREEN tests |
| ERR-05 | 07-04 | 4xx mapped to catalog; no upstream body echo | ✓ SATISFIED | `test_upstream_4xx_no_echo` GREEN; zero-arg constructors cannot interpolate user input |

OBS-* requirement status (formally Phase 1 requirements per REQUIREMENTS.md, but Phase 7 ROADMAP goal explicitly covers them):

| OBS Requirement | Status | Evidence |
|----------------|--------|----------|
| OBS-01 | ✓ SATISFIED | `/healthz` handler + `test_healthz_dispatches_no_httpx_request` GREEN |
| OBS-02 | DEFERRED to v2 (D7-05) | REQUIREMENTS.md row 265 marks deferred |
| OBS-03 | ✓ SATISFIED | `test_429_log_line_shape` asserts LOG_FIELDS-locked key set |
| OBS-04 | ✓ SATISFIED | CR-01 closed in 07-07: `ip_prefix()` validates via `ipaddress.ip_address()`; REQUIREMENTS.md row 267 updated to `Satisfied (07-07 gap closure — CR-01)` |
| OBS-05 | ✓ SATISFIED (carry-forward) | RequestIdMiddleware uses contextvars; structlog merge_contextvars wired |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/mcp_zeeker/app.py` | 56-67 | `tool.return_type` on Tool base class (CR-02) | ⚠️ WARNING | Deferred to Phase 8; works today because all registered tools are FunctionTool instances. Not a Phase 7 deliverable. |
| `src/mcp_zeeker/core/datasette_client.py` | 192 | Unreachable defensive raise (IN-01) | INFO | Post-loop raise is unreachable but kept; non-blocking. |
| `src/mcp_zeeker/core/middleware/rate_limit.py` | 137, 160 | `time.perf_counter` mixed with injected `time_provider` for `duration_ms` (WR-03) | ⚠️ WARNING | Latent test-flakiness risk only; no current assertion on `duration_ms` value; non-blocking. |
| `src/mcp_zeeker/core/datasette_client.py` | 221-239 | Over-broad `except (..., TypeError)` catch (WR-04) | ⚠️ WARNING | Pre-existing Phase 2/4 code; non-blocking for Phase 7 goal. |
| `src/mcp_zeeker/core/ip.py` | 80-91 | `_normalize_ip_key` does not strip `[::1]:8080` port form (WR-02) | ⚠️ WARNING | Minor token-bucket bypass via port rotation; Caddy normalizes in production; non-blocking. |

No `TBD`, `FIXME`, or `XXX` markers found in any file modified by phase 07-07 (confirmed by scan).

### Human Verification Required

None. All success criteria are mechanically verifiable. The CR-01 reproduction ran end-to-end in a test process. No human judgment required.

### Deferred Items

Items not yet met but explicitly addressed in later milestone phases.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | CR-02 — `lifespan` accesses `tool.return_type` on `Tool` base; would AttributeError on TransformedTool registration. Currently safe because all 6 registered tools are FunctionTool instances. | Phase 8 | Phase 8 goal "complete test suite covers every contract surface" includes hardening of cross-cutting infrastructure. Fix is `getattr(tool, "return_type", None)` — small but not a Phase 7 success-criteria item. |

WR-03, WR-04, WR-02, and IN-01 are tracked in `deferred-items.md` per the prior report. WR-01 (IPv6 naive split) was closed by the CR-01 fix in 07-07. WR-07 (false-positive test) was addressed by the rename + scope-narrow in 07-07 commit 4c708d1.

### Gaps Summary

No blocking gaps remain. The single BLOCKER from the prior report (CR-01 / SC-6 / OBS-04) is verified closed at HEAD.

**Specific verification of the prior BLOCKER closure:**

The CR-01 fix in `src/mcp_zeeker/core/ip.py:35-63` replaces the dangerous `return ip` fallback with:
1. Empty string → return `""` (preserves "no IP" semantics)
2. Valid IPv4 → return `"a.b.c"` (/24 prefix per OBS-04)
3. Valid IPv6 → return canonical /48 network base address string (closes WR-01 incidentally)
4. Everything else → return `"_invalid"` (forecloses the hostile-XFF injection chain)

The regression test `test_hostile_xff_does_not_leak_into_log` drives the FULL production ASGI chain (not the isolated rate-limit fixture) with 6 distinct hostile input classes including the verifier's canonical reproduction (`</system><admin>SECRET`). All 6 parametrized cases PASS, confirming that hostile XFF content cannot reach any structured log line through the `RequestIdMiddleware → ip_prefix → structlog contextvar → merge_contextvars` chain.

---

_Verified: 2026-05-15T12:30:00Z_
_Verifier: Claude (gsd-verifier)_
_Re-verification after: 07-07 CR-01 gap closure_
