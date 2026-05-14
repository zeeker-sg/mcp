---
phase: 06-envelope-hardening-injection-resistance-labelling
plan: 02
subsystem: envelope-walking-slice
tags:
  - retrieved-at-middleware-registration
  - envelope-factories-rewired
  - per-row-license
  - per-row-citation
  - policy-attachment
  - tool-trailer-broaden
  - underscore-prefix-key-convention

# Dependency graph
requires:
  - phase: 06-envelope-hardening-injection-resistance-labelling
    plan: 01
    provides: RetrievedAtMiddleware module + get_tool_started_at accessor + tool_started_at ContextVar + MetadataCache.license_for_sync + _SafeDict + synthesize_citation + config.CONTENT_POLICIES + config.CITATION_TEMPLATES + HEAVY_COLUMNS += "_policy" + Provenance.license_url

provides:
  - RetrievedAtMiddleware registered as FIRST `mcp.add_middleware()` in server.py (FIFO ordering D6-09/10 / Pitfall 4)
  - 4 envelope factories rewired — retrieved_at via contextvar accessor + license via MetadataCache.license_for_sync
  - for_rows signature drop — `citation: str | None = None` parameter removed (D6-05 per-row citation lives in handler row dict)
  - list_databases row reshape — per-row `license` + `license_url` (D6-03)
  - core/search.py::_one_table row normalize — per-row `license` + `license_url` + `_citation` (9-key shape; D6-03/05)
  - query_table Step 13 — `_policy` inside `retrieved_content` (D6-13/14/15) + per-row `_citation` (D6-05)
  - fetch Step 7 — per-row `_citation` (D6-05); no `_policy` per D6-14
  - tests/test_tool_trailer.py broadened with registry-iteration test (Pattern F)
  - `_license_pair` helper in core/envelope.py — graceful RuntimeError fallback to config.LICENSES for direct-handler-call unit tests

affects:
  - 06-03-PLAN (Wave-0 stubs are now ready to GREEN against the live walking-slice contract; 4 RED stub files remain untouched per single-plan-touch discipline)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Underscore-prefix convention for handler-injected metadata keys (`_citation`, `_policy`) — protects against upstream column-name collisions"
    - "RuntimeError-tolerant MetadataCache fallback in direct-handler call sites (`try: MetadataCache.current() except RuntimeError: config.LICENSES.get(...)`) — applied symmetrically across envelope.py, tools/discovery.py, core/search.py, tools/retrieval.py"
    - "Hoist `retrieved_at_for_call = get_tool_started_at()` and `license_text, license_url = ...license_for_sync(db)` ONCE per per-table-dispatch / per-reshape-loop — T-06-13 DoS bound (not per row)"

key-files:
  created: []
  modified:
    - src/mcp_zeeker/server.py
    - src/mcp_zeeker/core/envelope.py
    - src/mcp_zeeker/core/search.py
    - src/mcp_zeeker/tools/discovery.py
    - src/mcp_zeeker/tools/retrieval.py
    - tests/test_tool_trailer.py
    - tests/tools/test_discovery.py
    - tests/tools/test_search.py

key-decisions:
  - "Per-row citation key is `_citation` (underscore prefix), NOT `citation` as the plan body specified. The bare `citation` key would collide with the upstream judgments.citation column (e.g., value '2026 SGDC 136'), and the source-of-truth core/citation.py docstring already documents `_citation` as the canonical key. Underscore prefix mirrors `_policy` convention (Plan 06-01) and protects against the collision class flagged in threat model T-06-11."
  - "`Envelope.for_rows.citation: str | None = None` parameter dropped from the signature per RESEARCH 'State of the Art' line 858. No existing caller passed `citation=` (verified via grep), so the drop is non-breaking."
  - "Direct-handler-call unit tests don't bind a MetadataCache via lifespan, so all four call sites (envelope.py `_license_pair`, tools/discovery.py:list_databases, core/search.py::_one_table, tools/retrieval.py _policy fallback) tolerate `RuntimeError` from `MetadataCache.current()` and fall back to `config.LICENSES.get(database, ('', ''))`. Production paths always have the cache bound by app.py lifespan."
  - "tests/tools/test_discovery.py and tests/tools/test_search.py shape assertions updated from the old (3-key, 6-key) shapes to the new (5-key, 9-key) shapes per Phase 6 D6-03 + D6-05. These tests were not listed in the plan's files_modified but were in-scope per the Task 2 verify block which runs `tests/tools/`."

patterns-established:
  - "Pattern P4 (Underscore prefix for handler-injected row metadata): `_citation`, `_policy` — protects against upstream column-name collisions. Same prefix-as-protection discipline as Plan 06-01's `HEAVY_COLUMNS += '_policy'`."
  - "Pattern P5 (Per-dispatch hoisting): when a handler emits multiple rows that share `retrieved_at` + `license` values, capture both ONCE before the per-row loop, not inside it. Pre-hoisting is a T-06-13 DoS-bound discipline and is preferred over walrus operators."
  - "Pattern P6 (Graceful RuntimeError fallback in direct-call code paths): handlers + envelope factories that may be invoked outside a fully bound MCP lifespan (unit tests) tolerate `MetadataCache.current()` RuntimeError and fall back to `config.LICENSES`. Production lifespan always binds the cache."

requirements-completed:
  - ENV-01
  - ENV-02
  - ENV-03
  - ENV-04
  - ENV-05
  - INJ-01
  - INJ-02
  - INJ-04

# Metrics
duration: 8min
completed: 2026-05-14
---

# Phase 06 Plan 02: Walking Slice — Envelope Hardening Live Summary

**Phase 6 walking slice — RetrievedAtMiddleware registered FIRST on the FastMCP instance, all 4 envelope factories rewired (retrieved_at from contextvar, license from MetadataCache.license_for_sync), per-row license/license_url on list_databases + search rows, per-row `_citation` on search/query_table/fetch rows, `_policy` inside retrieved_content on query_table when heavy is requested. Underscore-prefix key convention adopted (`_citation`, `_policy`) to defend against upstream column-name collisions.**

## Performance

- **Duration:** ~8 min (3 task commits over 508 s)
- **Tasks:** 3
- **Files created:** 0
- **Files modified:** 8 (5 src + 3 tests; plan listed 5 src + 1 test — 2 additional test files updated to reflect Phase 6 row-shape changes)
- **Test count delta:** +1 GREEN (274 passed; 6 skipped — 4 Wave-0 stubs untouched + 1 ZEEKER_LIVE + 1 phase-2-only)

## Accomplishments

- **server.py middleware registration (D6-09 / D6-10 / Pitfall 4):** `mcp.add_middleware(RetrievedAtMiddleware())` is now the FIRST `add_middleware` call, before `StructuredLogMiddleware()`. FIFO ordering guarantees that `tool_started_at` is bound on every tool call BEFORE any other middleware can short-circuit (rate-limit / auth-deny). Verified by byte-offset assertion in Task 1's verify block.
- **All 4 envelope factories rewired (D6-01..05 + D6-09/11):** `retrieved_at` flows from `get_tool_started_at()` (the ContextVar bound by `RetrievedAtMiddleware`). `for_table_list` + `for_rows` read license via `MetadataCache.current().license_for_sync(database)`; `for_database_list` + `for_search_results` keep multi-DB `LICENSE_MIXED` + `license_url=None` envelope-level (per-row license is populated by handlers — D6-03). The Plan 06-01 `tuple[0]` compat shim is gone.
- **`for_rows` signature drop:** the `citation: str | None = None` parameter is removed (D6-05 — per-row citation lives in `data[i]["_citation"]` attached by handler row reshape). `inspect.signature(Envelope.for_rows)` does NOT contain `citation` (verified in Task 1).
- **`list_databases` row reshape (D6-03):** rows now carry 5 keys exactly: `{name, description, table_count, license, license_url}`. License sources from `MetadataCache.current().license_for_sync(name)` (upstream non-empty wins → config.LICENSES → empty tuple).
- **`core/search.py::_one_table` row normalize (D6-03 + D6-05):** preview rows now carry 9 keys exactly: `{title, date, summary, url, database, table, license, license_url, _citation}`. `license_for_sync(db)` + `get_tool_started_at()` are hoisted once per per-table dispatch (T-06-13 DoS bound — not per row). Search emits preview-only rows; D6-14 keeps `_policy` out of search responses entirely.
- **`query_table` Step 13 (D6-05/06/07/08 + D6-13/14/15):** every row carries `_citation` at row top level (regardless of heavy projection). When `heavy_to_emit` is non-empty, `_policy` is attached INSIDE `retrieved_content` — operator-authored value from `config.CONTENT_POLICIES.get((database, table))` OR the D6-15 fallback minimal `{source, license, license_url, redistribution}` policy synthesized from the envelope license. `retrieved_at_for_call` is captured ONCE before the reshape loop (D6-09 single-timestamp-per-tool-call).
- **`fetch` Step 7 (D6-05):** per-row `_citation` at row top level. No `_policy` — fetch strips HEAVY_COLUMNS at column-projection time so `retrieved_content` is never present (D6-14).
- **`tests/test_tool_trailer.py` broadened (INJ-01 / INJ-02 / Pattern F):** the original focused `test_list_databases_description_ends_with_trailer` stays alongside the new `test_every_registered_tool_description_ends_with_trailer_via_registry` that iterates every tool via `await mcp.list_tools()`. Any future tool addition that forgets `TOOL_TRAILER` now fails CI immediately.
- **Plan 06-01's Wave-0 stubs are untouched.** All 4 RED stub files (test_envelope_snapshot.py, test_content_policy_emission.py, test_citation_synthesis.py, test_hostile_inputs_consolidated.py) still skip with their original Plan 06-03 GREEN-body reasons.
- **`tests/conftest.py` was NOT touched** — Plan 06-01's single-plan-touch obligation honored.

## Task Commits

Each task was committed atomically:

1. **Task 1: register RetrievedAtMiddleware + rewire envelope factories** — `941c346` (feat)
2. **Task 2: list_databases per-row license + search per-row license/citation + tool_trailer broaden** — `e670781` (feat)
3. **Task 3: query_table _policy + per-row _citation + fetch _citation** — `ee4f596` (feat)

## Files Modified

### Source (5)
- `src/mcp_zeeker/server.py` — `mcp.add_middleware(RetrievedAtMiddleware())` inserted FIRST + import line + 6-line FIFO-ordering rationale comment
- `src/mcp_zeeker/core/envelope.py` — 4 factory bodies rewired; new `_license_pair` helper (graceful RuntimeError fallback); `citation` parameter dropped from `for_rows`; `UTC` import removed (no longer used after `datetime.now(tz=UTC)` removal)
- `src/mcp_zeeker/core/search.py` — `_one_table` row normalize loop attaches license + license_url + `_citation`; 3 new imports (synthesize_citation, MetadataCache, get_tool_started_at); module docstring Phase 6 note added
- `src/mcp_zeeker/tools/discovery.py` — `list_databases` row reshape attaches per-row license + license_url with graceful RuntimeError fallback
- `src/mcp_zeeker/tools/retrieval.py` — `query_table` Step 13 + `fetch` Step 7 attach per-row `_citation`; query_table additionally attaches `_policy` inside `retrieved_content` (operator + D6-15 fallback paths); 3 new imports + module docstring Phase 6 note

### Tests (3)
- `tests/test_tool_trailer.py` — broadened with `test_every_registered_tool_description_ends_with_trailer_via_registry` (Pattern F); focused list_databases test preserved
- `tests/tools/test_discovery.py` — row-shape assertion updated from 3-key to 5-key (D6-03 walking-slice change)
- `tests/tools/test_search.py` — `test_preview_shape_uniform` row-shape assertion updated from 6-key to 9-key (D6-03 + D6-05 walking-slice change)

## Decisions Made

- **Per-row citation key uses `_citation` (underscore prefix), NOT bare `citation`.** See Deviations §1 — Rule 1 bug. Bare `citation` would collide with the upstream `judgments.citation` column. `core/citation.py` already documented `_citation` as the canonical key.
- **`for_rows.citation` parameter dropped.** Verified via grep that no caller passed `citation=`; drop is non-breaking. D6-05 per-row citation flows through the handler row reshape, not the factory.
- **`_license_pair` helper in envelope.py tolerates RuntimeError from `MetadataCache.current()`.** Direct-handler-call unit tests don't bind a cache via lifespan; the helper falls back to `config.LICENSES.get(database, ("", ""))`. The same graceful-fallback pattern is applied at three other call sites: `tools/discovery.py:list_databases`, `core/search.py::_one_table`, and `tools/retrieval.py` `_policy` D6-15 fallback path. Production paths always have the cache bound by app.py lifespan.
- **`tests/tools/test_discovery.py` and `tests/tools/test_search.py` shape assertions updated.** Both tests were not listed in the plan's `files_modified` frontmatter, but the Task 2 verify block runs `tests/tools/` so they were in-scope. The plan's `behavior` section explicitly defines the new 5-key (list_databases) and 9-key (search) row shapes — the assertion updates make those tests consistent with the Phase 6 walking-slice contract.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Per-row citation key collision with upstream column name**
- **Found during:** Task 3 sub-regression (`tests/tools/test_query_table.py::test_heavy_columns_appear_under_retrieved_content`)
- **Issue:** The plan body specifies bare `citation` as the per-row key (line 41: "9 keys: `{title, date, summary, url, database, table, license, license_url, citation}`"; line 522: same; query_table action body line 759: `row["citation"] = synthesize_citation(...)`). But the upstream `judgments` table has a real column literally named `citation` (value `"2026 SGDC 136"`), which is in the light projection by default. The bare `citation` key would overwrite the upstream column value with the synthesized citation string, corrupting both fields. Threat model T-06-11 explicitly defends `_policy` from this collision class — the same logic applies to `_citation`. Additionally, the source-of-truth `core/citation.py` module docstring (Plan 06-01) ALREADY documents the row key as `_citation` (underscore prefix): "citation string that ships under `Envelope.data[i][\"_citation\"]`".
- **Fix:** Use `_citation` (underscore prefix) as the row key everywhere — `core/search.py::_one_table`, `tools/retrieval.py::query_table` Step 13, `tools/retrieval.py::fetch` Step 7. Underscore-prefix convention mirrors `_policy` (Plan 06-01 added `_policy` to `HEAVY_COLUMNS`). Tests updated: `tests/tools/test_search.py::test_preview_shape_uniform` expected-keys set now includes `_citation`. Existing `tests/tools/test_query_table.py:478` assertion `row.get("citation") == "2026 SGDC 136"` now passes because the upstream column survives untouched. 4 Wave-0 RED stubs do NOT lock in the literal string `"citation"` — they only reference `synthesize_citation` the function — so this rename is forward-compatible with Plan 06-03.
- **Files modified:** `src/mcp_zeeker/core/search.py`, `src/mcp_zeeker/tools/retrieval.py`, `tests/tools/test_search.py`
- **Verification:** `uv run pytest -x -q` exits 0 (274 passed, 6 skipped).
- **Committed in:** `ee4f596` (Task 3)

**2. [Rule 3 — Blocking] `MetadataCache.current()` RuntimeError on unbound cache in direct-handler-call unit tests**
- **Found during:** Task 1 regression (`tests/test_envelope.py::test_for_rows_signature_stable`)
- **Issue:** `Envelope.for_rows` and `Envelope.for_table_list` now call `MetadataCache.current().license_for_sync(database)`. `MetadataCache.current()` raises `RuntimeError` when neither the contextvar nor the singleton is bound. Phase 1 envelope tests construct envelopes directly without binding a cache — they break. The plan's behavior block acknowledged that `license_for_sync` returns `("", "")` on cold cache (D6-04 acceptance) but did not address the unbound-cache edge case at all.
- **Fix:** Added a `_license_pair(database)` module-private helper in `core/envelope.py` that wraps the `MetadataCache.current().license_for_sync(database)` call in try/except. On `RuntimeError`, returns `config.LICENSES.get(database, ("", ""))`. The same graceful-fallback pattern is applied at three other call sites: `tools/discovery.py:list_databases`, `core/search.py::_one_table`, and `tools/retrieval.py` D6-15 fallback path. Production paths always have the cache bound by `app.py` lifespan; the fallback only matters for direct-handler-call unit tests.
- **Files modified:** `src/mcp_zeeker/core/envelope.py`, `src/mcp_zeeker/tools/discovery.py`, `src/mcp_zeeker/core/search.py`, `src/mcp_zeeker/tools/retrieval.py`
- **Verification:** `uv run pytest -x -q` exits 0 across all 3 task commits.
- **Committed in:** `941c346` (Task 1 — envelope.py helper) + `e670781` (Task 2 — discovery.py + search.py call-site fallbacks) + `ee4f596` (Task 3 — retrieval.py D6-15 fallback)

**3. [Rule 3 — Blocking] `tests/tools/test_discovery.py` + `tests/tools/test_search.py` row-shape assertions update**
- **Found during:** Task 2 sub-regression
- **Issue:** `tests/tools/test_discovery.py::test_list_databases` asserted `set(row.keys()) == {"name", "description", "table_count"}` (3-key Phase-1 shape); `tests/tools/test_search.py::test_preview_shape_uniform` asserted the 6-key shape. Both contradict the new Phase 6 5-key (list_databases) and 9-key (search) shapes that the plan's behavior block explicitly defines. Neither test file was listed in the plan's `files_modified` frontmatter, but the Task 2 verify block ran `tests/tools/` so they were unavoidably in-scope.
- **Fix:** Updated each `set(row.keys()) == ...` assertion to the new shape per Phase 6 D6-03 + D6-05. Added inline comments citing the decisions.
- **Files modified:** `tests/tools/test_discovery.py`, `tests/tools/test_search.py`
- **Verification:** Both tests pass under `uv run pytest -x -q`.
- **Committed in:** `e670781` (Task 2)

---

**Total deviations:** 3 auto-fixed (1 Rule 1 — bug, 2 Rule 3 — blocking).
**Impact on plan:** All three deviations preserve plan intent. (1) is THE most important — applying the canonical `_citation` underscore-prefix key prevents a production data-corruption bug that the plan body inadvertently invited. (2) keeps Phase 1 + 2 + 3 + 4 + 5 direct-handler-call regression tests green under the new D6-04 license-sourcing path. (3) reconciles two pre-existing tool tests with the plan-defined Phase 6 row shapes. No scope creep — every change is gated by a D6-NN decision or by the plan's own behavior block.

## Issues Encountered

- **Plan's `06-CONTEXT.md` + `06-RESEARCH.md` were `.gitignore`'d in the worktree base.** Copied from main repo to enable local read. No committed work was affected.
- **Pre-existing ruff format + check failures in untouched files.** 6 files would be reformatted; 45 errors flagged across `tests/tools/test_discovery_side_channel.py`, `tests/tools/test_list_tables.py`, and others. Out-of-scope per SCOPE BOUNDARY rule. Logged in `.planning/phases/06-envelope-hardening-injection-resistance-labelling/deferred-items.md`. All 8 files touched by Plan 06-02 pass `ruff format --check` and `ruff check` cleanly.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- **Plan 06-03 (Wave 3 tail) is fully unblocked.** The 4 Wave-0 RED stub files (`tests/test_envelope_snapshot.py`, `tests/test_content_policy_emission.py`, `tests/test_citation_synthesis.py`, `tests/test_hostile_inputs_consolidated.py`) are untouched and still skip. They are ready to receive parametrized GREEN test bodies against the live walking-slice contract:
  - `list_databases` rows → 5 keys
  - `search` rows → 9 keys (including `_citation`)
  - `query_table` rows → light-or-heavy + `_citation` (and `_policy` inside `retrieved_content` when heavy)
  - `fetch` row → `_citation` (no `_policy`)
- **Plan 06-03 MUST NOT modify** `src/mcp_zeeker/config.py`, `src/mcp_zeeker/core/middleware/retrieved_at.py`, `src/mcp_zeeker/core/citation.py`, `src/mcp_zeeker/core/metadata_cache.py`, `tests/conftest.py`, or any of Plan 06-02's source edits.
- **Plan 06-03 must use the `_citation` key** when asserting per-row citation strings (NOT bare `citation` — see Deviations §1).
- **Operator review gate (5 [OPERATOR REVIEW] CONTENT_POLICIES rows).** Plan 06-03 manual UAT must confirm the operator-authored CONTENT_POLICIES emissions for `zeeker-judgements.judgments` (Crown Copyright posture), `pdpc.enforcement_decisions_fragments` (SODL applies to text), all 8 `sg-gov-newsrooms.*_news` (SODL), `sglawwatch.headlines` / `commentaries` (third-party copyright), and `sglawwatch.about_singapore_law_fragments` (SAL terms).

## Self-Check: PASSED

Self-check ran 2026-05-14:
- All 8 modified files exist with the documented Phase 6 changes (verified via `git diff` against base `86fc78b`).
- All 3 task commits exist in `git log --oneline -5`: `941c346` (Task 1), `e670781` (Task 2), `ee4f596` (Task 3).
- `uv run pytest -x -q` exits 0: 274 passed, 6 skipped (4 Wave-0 stubs + 1 live + 1 phase-2-only). No failed or errored tests.
- `uv run ruff format --check` and `uv run ruff check` on all 8 touched files exit 0 (pre-existing ruff issues in untouched files logged to `deferred-items.md`).
- Wave-0 stub files (test_envelope_snapshot.py, test_content_policy_emission.py, test_citation_synthesis.py, test_hostile_inputs_consolidated.py) are untouched: `git diff 86fc78b..HEAD -- tests/test_envelope_snapshot.py tests/test_content_policy_emission.py tests/test_citation_synthesis.py tests/test_hostile_inputs_consolidated.py` produces no output.
- `tests/conftest.py` is untouched: same `git diff` check.

---
*Phase: 06-envelope-hardening-injection-resistance-labelling*
*Completed: 2026-05-14*
