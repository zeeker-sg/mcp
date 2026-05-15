---
phase: 07-rate-limit-structured-errors-healthz-logs
plan: 04
subsystem: errors
tags: [error-catalog, fastmcp-middleware, request-id, structlog, inj-05, err-02, err-03]

# Dependency graph
requires:
  - phase: 07
    provides: "Plan 07-01 — Phase 7 conftest fixtures (fake_clock, rate_limiter, bucket_store) under single-plan-touch rule; rate-limit middleware skeleton; RATE_* config constants"
  - phase: 06
    provides: "RetrievedAtMiddleware (FastMCP middleware) — D6-09 / D6-10 ordering anchor that ErrorEnrichmentMiddleware MUST register after"
  - phase: 03
    provides: "D3-12 LOCKED 6-code retrieval catalog — phase 7 extends to the full 11"
provides:
  - "core/errors.py: 11-code CATALOG tuple constant + raise_query_timeout + raise_upstream_unavailable helpers (FIXED-literal messages)"
  - "core/middleware/error_enrichment.py: ErrorEnrichmentMiddleware (FastMCP layer) appends [request_id: <hex>] to every ToolError"
  - "tests/test_error_catalog.py: four GREEN tests covering ERR-01 / ERR-02 / ERR-03 / ERR-05"
  - "FastMCP middleware chain wiring: RetrievedAt → ErrorEnrichment → StructuredLog → handler"
affects: [07-05, 07-06, 08-validation]

# Tech tracking
tech-stack:
  added: []  # no new dependencies — uses existing fastmcp.exceptions.ToolError + structlog.contextvars
  patterns:
    - "Catalog-as-tuple-constant: a single literal source of truth for a locked enum-like contract, mechanically asserted by a paired test that fails on any rename or reorder"
    - "FastMCP on_call_tool with try/except (not try/finally): use try/except when the middleware purpose is exception interception rather than always-running cleanup"
    - "Contextvar-passive read: middleware reads structlog.contextvars.get_contextvars() without binding/resetting — the upstream binder (RequestIdMiddleware) owns the lifecycle"

key-files:
  created:
    - "src/mcp_zeeker/core/errors.py — CATALOG tuple constant + 2 new raise helpers"
    - "src/mcp_zeeker/core/middleware/error_enrichment.py — ErrorEnrichmentMiddleware FastMCP middleware"
    - "tests/test_error_catalog.py — 4 GREEN tests for ERR-01 / ERR-02 / ERR-03 / ERR-05"
  modified:
    - "src/mcp_zeeker/server.py — register ErrorEnrichmentMiddleware after RetrievedAtMiddleware"

key-decisions:
  - "CATALOG ordering matches REQUIREMENTS.md ERR-02 exactly — ordering is asserted, not just set membership, so any reorder requires editing both the constant and the test in one commit"
  - "ErrorEnrichmentMiddleware uses FastMCP's str(exc) API for ToolError message extraction — `.message` attribute proposed by PATTERNS.md does not exist on the public API"
  - "raise_query_timeout / raise_upstream_unavailable take ZERO arguments — literally cannot interpolate user input by construction (INJ-05 by design, not by discipline)"
  - "ErrorEnrichmentMiddleware re-raises original exception unchanged when request_id contextvar is empty — preserves catalog-code prefix for tests that bypass RequestIdMiddleware"
  - "Existing visibility.py / filter_compiler.py / tools/retrieval.py raise sites NOT migrated — co-existence model preserves the per-site context (e.g., status-class branching) that lives at those call sites"

patterns-established:
  - "Catalog-as-tuple-constant + paired mechanical test: any rename/reorder/add requires touching both files in one commit (T-07-07 Tampering / Repudiation mitigation)"
  - "ErrorEnrichmentMiddleware ordering: registered AFTER RetrievedAtMiddleware in FastMCP FIFO chain so retrieved_at remains bound during error handling"
  - "FastMCP ToolError API canonical extraction: str(exc) — observed in tests/test_filter_compiler.py:240 and applied uniformly here"

requirements-completed: [ERR-01, ERR-02, ERR-03, ERR-05]

# Metrics
duration: 7min
completed: 2026-05-15
---

# Phase 07 Plan 04: Error Catalog + ErrorEnrichmentMiddleware Summary

**11-code locked CATALOG tuple plus FastMCP middleware that appends `[request_id: <hex>]` to every ToolError so an MCP client and a server-side log line can be correlated by a single hex token (ERR-01 / ERR-02 / ERR-03 / ERR-05).**

## Performance

- **Duration:** 7 min
- **Started:** 2026-05-15T00:42:40Z
- **Completed:** 2026-05-15T00:50:03Z
- **Tasks:** 3 of 3
- **Files modified:** 4 (3 created, 1 modified)

## Accomplishments

- Locked the 11-code error catalog as a single literal tuple `core/errors.CATALOG` in REQUIREMENTS.md ERR-02 order — asserted by `test_all_11_codes_in_catalog` so any future rename or reorder requires editing both files in one commit (T-07-07 mitigation).
- Added two new raise helpers (`raise_query_timeout`, `raise_upstream_unavailable`) with FIXED-literal messages and zero arguments — INJ-05 / T-07-08 enforced by construction, not discipline.
- Wired `ErrorEnrichmentMiddleware` into the FastMCP middleware chain after `RetrievedAtMiddleware` so every ToolError surfaced to a client carries a `[request_id: <hex>]` suffix that an operator can grep against `core/middleware/access_log.py` log lines.
- Four GREEN tests added (no Wave-0 skips) covering ERR-01 (stable-code prefix on every catalog code raise site), ERR-02 (locked set + ordering), ERR-03 (request_id correlation), and ERR-05 (upstream 4xx body never echoed).

## Task Commits

Each task was committed atomically:

1. **Task 1: Create core/errors.py with CATALOG tuple + new raise helpers** — `49937a7` (feat)
2. **Task 2: Implement ErrorEnrichmentMiddleware + register it in server.py** — `da1d833` (feat)
3. **Task 3: GREEN catalog + request_id + upstream-4xx-no-echo tests** — `75cf07d` (test) — also folds in the `str(exc)` Rule-1 fix for `error_enrichment.py`

## Files Created/Modified

- `src/mcp_zeeker/core/errors.py` — NEW. CATALOG tuple constant + raise_query_timeout + raise_upstream_unavailable. Module docstring documents co-existence model with `visibility.py` and lists where each of the 11 codes is raised across the codebase.
- `src/mcp_zeeker/core/middleware/error_enrichment.py` — NEW. `ErrorEnrichmentMiddleware(Middleware)` with one `on_call_tool` method that catches `ToolError`, reads `request_id` from structlog contextvars, and re-raises with `[request_id: <hex>]` appended. Falls back to original re-raise if contextvar is empty (test path).
- `src/mcp_zeeker/server.py` — MODIFIED. Added import + `mcp.add_middleware(ErrorEnrichmentMiddleware())` immediately after `mcp.add_middleware(RetrievedAtMiddleware())`. Comment documents ERR-03 ordering rationale.
- `tests/test_error_catalog.py` — NEW. Four GREEN tests covering the locked catalog contract.

## CATALOG (single source of truth)

`src/mcp_zeeker/core/errors.py` defines the canonical tuple in REQUIREMENTS.md ERR-02 order:

```python
CATALOG: tuple[str, ...] = (
    "unknown_database",
    "unknown_table",
    "unknown_column",
    "invalid_filter_op",
    "invalid_cursor",
    "invalid_query",
    "unsupported_table_for_fetch",
    "not_found",
    "query_timeout",
    "rate_limited",
    "upstream_unavailable",
)
```

The two new helpers emit FIXED literal messages:

```python
def raise_query_timeout() -> NoReturn:
    raise ToolError("query_timeout: Query timed out")

def raise_upstream_unavailable() -> NoReturn:
    raise ToolError("upstream_unavailable: upstream call failed")
```

Both helpers take ZERO arguments — by construction they cannot interpolate user input, upstream URL, or upstream body text. INJ-05 / T-03-01 / T-07-08 enforced at the type level.

## FastMCP Middleware Chain (after this plan)

ASGI layer (outermost first) → FastMCP layer → handler:

```
RequestIdMiddleware (ASGI, outermost)        ← binds request_id + ip_prefix to structlog contextvar
  → OriginAllowlistMiddleware (ASGI)
    → RateLimitMiddleware (ASGI)              ← 429 short-circuits BEFORE JSON-RPC parse (RATE-02)
      → Mount("/mcp", mcp.http_app())
        → RetrievedAtMiddleware (FastMCP)     ← binds tool_started_at contextvar
          → ErrorEnrichmentMiddleware (FastMCP)  ← NEW; catches ToolError, appends [request_id: ...]
            → StructuredLogMiddleware (FastMCP)  ← emits structured tool_call log line
              → tool handler
```

FastMCP middleware is FIFO ("first added is first in, last out"). Registering `ErrorEnrichmentMiddleware` AFTER `RetrievedAtMiddleware` keeps `tool_started_at` bound while the `try/except` in `ErrorEnrichmentMiddleware` runs, so the envelope factory + citation synthesizer paths still see the bound timestamp on the error path.

## Tests Added (all GREEN, no Wave-0 skips)

| Test | Requirement | What it asserts |
| --- | --- | --- |
| `test_all_11_codes_in_catalog` | ERR-02 | `len(CATALOG) == 11` AND set equality AND tuple ordering matches REQUIREMENTS.md (T-07-07 mitigation) |
| `test_all_errors_have_stable_code` | ERR-01 | 8 raise helpers + 2 inline literals each emit a ToolError whose message starts with `"<code>: "` |
| `test_error_includes_request_id` | ERR-03 | `ErrorEnrichmentMiddleware.on_call_tool` appends `[request_id: rid-abc]` to a `ToolError("unknown_database: ...")` raised inside `call_next` |
| `test_upstream_4xx_no_echo` | ERR-05 / INJ-05 | `raise_upstream_unavailable()` and `raise_query_timeout()` produce literal-only messages; an `UpstreamCallFailed` carrying hostile body text + URL is constructed but the canonical helper discards it entirely (no `DROP TABLE`, no `/search.json`, no hostile body in the resulting ToolError) |

Test count after this plan: **341 passed, 19 skipped** (337 baseline + 4 new; Wave-0 skips unchanged — owned by plans 07-02 / 07-03 / 07-06).

## Decisions Made

- **CATALOG ordering is part of the contract.** `test_all_11_codes_in_catalog` asserts both set equality AND tuple ordering. A reorder is not a no-op.
- **Co-existence model, not migration.** `visibility.py` raise helpers and the inline `ToolError("invalid_filter_op: ...")` / `ToolError("invalid_cursor: ...")` sites are NOT moved. They predate this module and carry per-site context (e.g., the four-tuple `failure_statuses` discrimination in search). `core/errors.py` adds only the catalog constant and the two new helpers.
- **`rate_limited` is in CATALOG but has no raise helper.** It is emitted only by `core/middleware/rate_limit.py` as the ASGI 429 body; it is never a ToolError. Including it in CATALOG keeps the catalog a single source of truth across both error paths.
- **ErrorEnrichmentMiddleware is FastMCP layer, not ASGI.** `request_id` is bound to the structlog contextvar by ASGI `RequestIdMiddleware`; by the time `on_call_tool` runs the contextvar is already populated, so this middleware reads it passively.
- **Falls back to unmodified re-raise when contextvar is empty.** Tests that bypass `RequestIdMiddleware` (e.g., direct unit tests of error helpers without the ASGI stack) get the original ToolError prefix unchanged — no `[request_id: ]` empty suffix.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FastMCP `ToolError` has no `.message` attribute**

- **Found during:** Task 3 (running `test_all_errors_have_stable_code`)
- **Issue:** Both `07-PATTERNS.md` § core/middleware/error_enrichment.py and the plan's Task 2 + Task 3 actions referenced `exc.message` to extract the human-readable message from a `ToolError`. The `fastmcp.exceptions.ToolError` public API exposes the message via `str(exc)` / `exc.args[0]` only — no `.message` attribute exists. Probing in the worktree's installed `fastmcp` confirmed: `dir(ToolError("foo"))` excluding dunders is `['add_note', 'args', 'with_traceback']`. The canonical extraction pattern in this codebase is `str(exc_info.value)`, observed at `tests/test_filter_compiler.py:240`.
- **Fix:** Replaced `.message` with `str(...)` in two places: (a) `error_enrichment.py:on_call_tool` now builds the new message as `f"{exc!s} [request_id: {request_id}]"`; (b) `tests/test_error_catalog.py` asserts on `str(exc_info.value)` and `str(err)` throughout. Added an inline comment in both files documenting the API gotcha so future maintainers do not re-introduce the pattern.
- **Files modified:** `src/mcp_zeeker/core/middleware/error_enrichment.py`, `tests/test_error_catalog.py`
- **Verification:** All 4 target tests GREEN; full suite 341 passed (337 + 4) with 19 unchanged Wave-0 skips.
- **Committed in:** `75cf07d` (folded into the Task 3 commit since the test-write surfaced the bug — separating into its own commit would have left an intermediate state with broken middleware-test integration)

## Authentication Gates

None encountered — plan was fully autonomous.

## Threat Flags

None — no new security-relevant surface introduced beyond what the plan's `<threat_model>` already enumerated (T-07-07 mitigated by CATALOG-tuple-constant + paired test; T-07-08 mitigated by FIXED-literal messages in the two new helpers).

## Self-Check: PASSED

Mechanical verification:

- `src/mcp_zeeker/core/errors.py` — FOUND
- `src/mcp_zeeker/core/middleware/error_enrichment.py` — FOUND
- `tests/test_error_catalog.py` — FOUND
- `src/mcp_zeeker/server.py` — MODIFIED (grep `ErrorEnrichmentMiddleware` returns 2)
- Commit `49937a7` (Task 1 — feat) — FOUND
- Commit `da1d833` (Task 2 — feat) — FOUND
- Commit `75cf07d` (Task 3 — test) — FOUND
- `tests/conftest.py` — UNCHANGED (single-plan-touch rule preserved; `git diff tests/conftest.py | wc -l` returns 0)
- All 4 target tests GREEN (`test_all_11_codes_in_catalog`, `test_all_errors_have_stable_code`, `test_error_includes_request_id`, `test_upstream_4xx_no_echo`)
- Full suite: 341 passed, 19 skipped (Wave-0 unchanged)
- `python -c "from mcp_zeeker.core.errors import CATALOG; assert len(CATALOG) == 11"` exits 0
- `grep -c 'CATALOG' src/mcp_zeeker/core/errors.py` returns 2 (constant declaration + docstring reference)
- `grep -c 'ErrorEnrichmentMiddleware' src/mcp_zeeker/server.py` returns 2 (import + add_middleware)
