---
phase: 06-envelope-hardening-injection-resistance-labelling
plan: 01
subsystem: testing
tags:
  - config-extensions
  - retrieved-at-middleware
  - metadata-cache-license-for
  - citation-helper
  - frozen-retrieved-at-fixture
  - hostile-inputs-corpus
  - wave-0-stubs

# Dependency graph
requires:
  - phase: 02-discovery-surface-denylists
    provides: MetadataCache with D2-05 normalize-at-ingest invariant + bind/reset contextvar lifecycle
  - phase: 03-structured-retrieval-url-keyed-fetch
    provides: HEAVY_COLUMNS frozenset + tests/test_filter_value_safety.py CANARY_STRINGS source
  - phase: 05-transparent-fragment-parent-joins
    provides: ContextVar bind/reset pattern (bound_parent_pk_cache fixture analog)

provides:
  - LICENSE_DEFAULT_URL constant + LICENSES tuple reshape (dict[str, tuple[str, str]])
  - CONTENT_POLICIES (14 entries) — per-(db, table) content-license posture
  - CITATION_TEMPLATES (13 entries) + DEFAULT_CITATION_TEMPLATE
  - HEAVY_COLUMNS extended with "_policy" (snapshot contract relaxation)
  - Provenance.license_url optional field (back-compat default None)
  - MetadataCache.license_for (async) + license_for_sync (sync) with D6-04 fallback
  - core/middleware/retrieved_at.py module (tool_started_at ContextVar + accessor + middleware)
  - core/citation.py module (_SafeDict + synthesize_citation)
  - tests/conftest.py frozen_retrieved_at fixture (single-plan-touch consolidation)
  - tests/_corpus/ shared canary corpus (CANARY_STRINGS + _surfaces_contain)
  - 4 Wave-0 RED-stub test files for Plan 06-03 GREEN body

affects:
  - 06-02-PLAN (walking slice — wires RetrievedAtMiddleware + envelope factories)
  - 06-03-PLAN (Wave-0 GREEN bodies + parametrized hostile-inputs fan-out)

# Tech tracking
tech-stack:
  added:
    - structlog DEBUG event channel (retrieved_at_fallback safety net)
  patterns:
    - ContextVar set/reset middleware (FastMCP on_call_tool — D6-09)
    - _SafeDict.format_map for None-tolerant template substitution (Pitfall 5)
    - Sync + async accessor pair (license_for / license_for_sync — D6-04)

key-files:
  created:
    - src/mcp_zeeker/core/middleware/retrieved_at.py
    - src/mcp_zeeker/core/citation.py
    - tests/_corpus/__init__.py
    - tests/_corpus/hostile_inputs.py
    - tests/test_retrieved_at_middleware.py
    - tests/test_envelope_snapshot.py
    - tests/test_content_policy_emission.py
    - tests/test_citation_synthesis.py
    - tests/test_hostile_inputs_consolidated.py
  modified:
    - src/mcp_zeeker/config.py
    - src/mcp_zeeker/core/envelope.py
    - src/mcp_zeeker/core/metadata_cache.py
    - tests/test_metadata_cache.py
    - tests/conftest.py

key-decisions:
  - "CITATION_TEMPLATES ships with 13 entries (not 10 as initially documented) — the truths-line itemized count of judgments(1) + enforcement_decisions(1) + 7 sg-gov-newsrooms.*_news + judiciary_news(1) + 3 sglawwatch sums to 13; the plan's `len == 10` was a documented arithmetic typo. 13 is the auditable single source of truth and preserves production data."
  - "pdpc.enforcement_decisions is OMITTED from CONTENT_POLICIES (it has no heavy columns per RESEARCH Probe 3 note; pdpc.enforcement_decisions_fragments is the heavy-bearing twin). Final count: 14 entries (2 zeeker-judgements + 1 pdpc + 8 sg-gov-newsrooms + 3 sglawwatch)."
  - "Envelope factories (for_table_list, for_rows) compatibility-extract tuple[0] from config.LICENSES.get(...) instead of returning the raw string — Rule 3 deviation. Plan 06-02 rewires to MetadataCache.license_for_sync; Plan 06-01 preserves the LICENSES reshape without breaking Phase 1-5 envelope serialization (Pydantic license: str field would reject tuple)."
  - "Used structlog.testing.capture_logs() rather than pytest caplog for the DEBUG retrieved_at_fallback assertion — mirrors the established pattern at tests/test_metadata_cache.py:147 and intercepts the rendered event dict directly (more reliable than the stdlib bridge for structlog BoundLogger)."

patterns-established:
  - "Pattern P1 (D6-09 middleware lifecycle): ContextVar.set on entry → try/await/finally reset(token). Mirrors metadata_cache.py:30-32 contextvar lifecycle; future per-call context (request_id, ip_prefix, etc.) follows the same template."
  - "Pattern P2 (Pitfall 5 None pre-process): subclass defaultdict(str); pre-process the source dict's None values to '' BEFORE storage in __init__; inject synthetic placeholders last-write-wins. Future template-substitution helpers (e.g., a fragment-citation variant) follow the same shape."
  - "Pattern P3 (sync + async accessor pair): when an envelope factory cannot await but needs the same data the async path serves, ship BOTH variants reading the SAME backing dict, with the sync variant returning a degraded ('','') value on cold cache instead of raising or awaiting."

requirements-completed:
  - ENV-02
  - ENV-03
  - ENV-04
  - ENV-05
  - INJ-04
  - INJ-05

# Metrics
duration: 25min
completed: 2026-05-14
---

# Phase 06 Plan 01: Foundation — Envelope-Hardening Helpers Summary

**Phase 6 foundation — config extensions (LICENSES tuple reshape, CONTENT_POLICIES, CITATION_TEMPLATES, HEAVY_COLUMNS + `_policy`), Provenance.license_url field, MetadataCache.license_for async+sync accessors, RetrievedAtMiddleware (ContextVar capture, NOT yet registered), _SafeDict-backed citation helper, frozen_retrieved_at fixture, shared hostile-input corpus, and 4 Wave-0 RED stubs.**

## Performance

- **Duration:** ~25 min
- **Tasks:** 3
- **Files created:** 9
- **Files modified:** 5
- **Test count delta:** +8 GREEN (273 passed; 6 skipped — 4 Wave-0 stubs, 1 ZEEKER_LIVE-gated, 1 phase-2-only)

## Accomplishments

- **Config single source of truth (D-21 / CFG-01):** LICENSES reshaped to `dict[str, tuple[str, str]]` with `LICENSE_DEFAULT_URL`. CONTENT_POLICIES (14 tuple-keyed entries) and CITATION_TEMPLATES (13 entries) + DEFAULT_CITATION_TEMPLATE land as operator-authored constants. HEAVY_COLUMNS extended with `_policy` for snapshot-contract relaxation.
- **Pydantic Provenance.license_url field add (D6-02):** optional, defaults None, ConfigDict(extra="forbid") preserved. Back-compat with every Phase 1-5 construction site.
- **MetadataCache.license_for (async) + license_for_sync (sync) with D6-04 fallback:** upstream-non-empty → config.LICENSES → empty tuple. Sibling `get_database_license` preserved verbatim for back-compat. Five new GREEN tests cover the fallback chain (3 async + 2 sync).
- **RetrievedAtMiddleware module ships (D6-09/D6-10/D6-11) but is NOT yet registered.** `tool_started_at` ContextVar, `get_tool_started_at()` accessor with DEBUG safety-net log, `RetrievedAtMiddleware.on_call_tool` with try/finally reset on both success AND exception paths. 3 new GREEN unit tests cover entry/exit binding, exception cleanup, and the DEBUG fallback log.
- **_SafeDict + synthesize_citation citation helper:** defaultdict(str) subclass with None→"" pre-processing (Pitfall 5) and synthetic `{retrieved_at}` placeholder (last-write-wins). DEFAULT_CITATION_TEMPLATE fallback proven.
- **tests/conftest.py single-plan-touch consolidation:** Phase 6 marker block + `frozen_retrieved_at` fixture (yields the bound datetime per Open Question 3 recommendation). Plans 06-02 and 06-03 MUST NOT modify tests/conftest.py.
- **tests/_corpus/ shared canary corpus** with verbatim 5-entry `CANARY_STRINGS` + `_surfaces_contain` helper. Phase 3/4/5 per-test corpora deliberately preserved as regression coverage.
- **4 Wave-0 RED-stub test files** that collect cleanly, skip with informative reasons, and structurally validate symbol resolution before skipping. Plan 06-03 fills the GREEN bodies.

## Task Commits

Each task was committed atomically:

1. **Task 1: config.py extensions + Provenance.license_url field** — `f27fc8e` (feat)
2. **Task 2: MetadataCache.license_for + RetrievedAtMiddleware + _SafeDict** — `114c04f` (feat)
3. **Task 3: tests/conftest.py frozen_retrieved_at + tests/_corpus + 4 Wave-0 stubs** — `4b120d3` (test)

## Files Created/Modified

### Created
- `src/mcp_zeeker/core/middleware/retrieved_at.py` — `tool_started_at` ContextVar + `get_tool_started_at()` accessor + `RetrievedAtMiddleware` (NOT yet registered)
- `src/mcp_zeeker/core/citation.py` — `_SafeDict(defaultdict)` + `synthesize_citation(database, table, row, retrieved_at)`
- `tests/_corpus/__init__.py` — empty package marker
- `tests/_corpus/hostile_inputs.py` — `CANARY_STRINGS` (5 entries verbatim) + `_surfaces_contain` helper
- `tests/test_retrieved_at_middleware.py` — 3 GREEN unit tests (entry/exit, exception cleanup, fallback DEBUG log)
- `tests/test_envelope_snapshot.py` — Wave-0 RED stub (Plan 06-03 GREEN body)
- `tests/test_content_policy_emission.py` — Wave-0 RED stub (Plan 06-03 GREEN body)
- `tests/test_citation_synthesis.py` — Wave-0 RED stub (Plan 06-03 GREEN body)
- `tests/test_hostile_inputs_consolidated.py` — Wave-0 RED stub (Plan 06-03 GREEN body)

### Modified
- `src/mcp_zeeker/config.py` — LICENSES tuple reshape, LICENSE_DEFAULT_URL, CONTENT_POLICIES (14), CITATION_TEMPLATES (13), DEFAULT_CITATION_TEMPLATE, HEAVY_COLUMNS += `_policy`
- `src/mcp_zeeker/core/envelope.py` — Provenance.license_url optional field + factory bodies compatibility-extract tuple[0] from LICENSES
- `src/mcp_zeeker/core/metadata_cache.py` — append `license_for` (async) + `license_for_sync` (sync); `get_database_license` preserved unchanged
- `tests/test_metadata_cache.py` — +5 GREEN license_for/license_for_sync tests
- `tests/conftest.py` — Phase 6 marker block + `frozen_retrieved_at` fixture

## Decisions Made

- **CITATION_TEMPLATES count is 13, not 10** (see Deviations §1). 13 is the auditable single source of truth — drops nothing the action enumerates and preserves production-relevant data.
- **pdpc.enforcement_decisions omitted from CONTENT_POLICIES** — the table has no columns in HEAVY_COLUMNS per RESEARCH Probe 3; the heavy-bearing twin `pdpc.enforcement_decisions_fragments` is included. Final count: 14, matching the plan's `len(CONTENT_POLICIES) == 14` assertion.
- **Envelope factories compatibility-extract** — `for_table_list` and `for_rows` now read `config.LICENSES.get(db, ("", ""))[0]` rather than the raw string (Deviations §2). Plan 06-02 rewires to MetadataCache.license_for_sync; Plan 06-01 preserves serializable Phase 1-5 envelopes despite the LICENSES tuple reshape.
- **structlog.testing.capture_logs() over caplog** — for the `retrieved_at_fallback` DEBUG assertion, capture_logs intercepts the rendered event dict directly (mirrors `test_metadata_gap_logged` at `tests/test_metadata_cache.py:147`).
- **Plain `async def` middleware tests, not `asyncio.run()` inside.** pytest-asyncio is in `auto` mode (`pyproject.toml`); calling `asyncio.run()` inside an already-running event loop would raise. Tests run as natural async functions; the third (sync) test uses `contextvars.copy_context().run(...)` for isolation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] CITATION_TEMPLATES count: plan-internal inconsistency between 10 (verify block + truths headline + Task 3 stub) and 13 (truths itemized breakdown + action's explicit enumeration)**
- **Found during:** Task 1 (config.py extensions)
- **Issue:** The plan's truths line states "10 entries" but the itemized breakdown sums to 13 (`judgments(1) + enforcement_decisions(1) + 7 sg-gov-newsrooms.*_news + judiciary_news(1) + 3 sglawwatch`). The `<action>` block enumerates 13 specific (db, table) → template pairs by name, including all 8 sg-gov-newsrooms tables and 3 sglawwatch tables. The verify block hard-codes `len == 10`.
- **Fix:** Shipped 13 entries (the auditable itemized list from `<action>`). Updated the Wave-0 stub's structural assertion in `tests/test_citation_synthesis.py` to `len == 13` with an in-file comment explaining the planner's arithmetic typo. The Task 1 verify Python block's `len == 10` assertion was NOT re-run as written (replaced with a print of the actual count); ALL OTHER assertions in that block pass green.
- **Files modified:** `src/mcp_zeeker/config.py`, `tests/test_citation_synthesis.py`
- **Verification:** `uv run pytest -x -q` → 273 passed, 6 skipped. Wave-0 stub's structural validation passes.
- **Committed in:** `f27fc8e` (Task 1) + `4b120d3` (Task 3 stub assertion)

**2. [Rule 3 — Blocking] Envelope factory tuple-shape compat for LICENSES reshape**
- **Found during:** Task 1 (Provenance.license_url field add + LICENSES reshape)
- **Issue:** `config.LICENSES.get(database, "")` previously returned a `str` (or `""` default). After Task 1 reshape it returns `tuple[str, str]` for known DBs. `Envelope.for_table_list` and `Envelope.for_rows` factories pass this value to `Provenance(license=...)` where the field is typed `license: str` — Pydantic 2 with `ConfigDict(extra="forbid")` would reject a tuple. Phase 1-5 regression suite would break (test_envelope.py, test_envelope_contract.py, every `for_rows` consumer test).
- **Fix:** Extract `tuple[0]` (license text) in both factory bodies; default tuple `("", "")` for unknown DBs. The plan acceptance criteria predicted "still tolerate `(text, url)` tuples because Plan 06-01 leaves the factory body unchanged in Wave 1" — strict literal reading would require zero edits, but Pydantic's runtime rejection of `tuple → str` made that impossible. The minimal patch (one-liner `_license_tuple = ...; license=_license_tuple[0]`) preserves all existing license values and behavior for Phase 1-5.
- **Files modified:** `src/mcp_zeeker/core/envelope.py`
- **Verification:** `uv run pytest tests/test_envelope.py tests/test_envelope_contract.py` → all passing.
- **Committed in:** `f27fc8e` (Task 1)

**3. [Rule 3 — Blocking] sync `license_for_sync` accessor returns `("", "")` on cold cache without raising**
- **Found during:** Task 2 (MetadataCache.license_for + license_for_sync)
- **Issue:** The plan's D6-04 cold-cache acceptance criterion was clearly specified, but the planner's behavior block did not include a dedicated GREEN test for the cold path. I added `test_license_for_sync_cold_cache_returns_empty` (and a warm-path companion) so the sync accessor's contract is regression-proofed at the foundation level.
- **Fix:** Two extra GREEN tests in `tests/test_metadata_cache.py` (5 new total instead of 3). Functionality matches the plan's behavior spec.
- **Files modified:** `tests/test_metadata_cache.py`
- **Verification:** Both new tests pass; pre-existing `test_get_database_license_*` tests unchanged.
- **Committed in:** `114c04f` (Task 2)

---

**Total deviations:** 3 auto-fixed (3 Rule 3 — blocking)
**Impact on plan:** All three deviations preserve plan intent. (1) ships the production-relevant data per the action's enumeration. (2) keeps Phase 1-5 regression suite green under the LICENSES tuple reshape. (3) adds defensive test coverage for an explicitly stated D6-04 acceptance criterion that lacked a dedicated test. No scope creep — every change is gated by D6-NN decisions or RESEARCH probes.

## Issues Encountered

- **Initial worktree base reset wiped phase 06 planning artifacts.** The `git reset --hard ${EXPECTED_BASE}` step in the worktree branch check rewound the worktree to commit `e3a8bb8`, which predates the (locally-only, `.gitignore`'d) phase-06 planning files. Recovered by copying `.planning/phases/06-envelope-hardening-injection-resistance-labelling/*.md` from the main repo (`/Users/houfu/Projects/zeeker-mcp/`). No committed work was affected; the recovery only restored ignored local files.
- **pre-existing E501 lint failures** in `src/mcp_zeeker/config.py` lines 174/175/182 (TABLE_DESCRIPTIONS) were NOT introduced by this plan — confirmed via `git stash && ruff check` before changes. Pre-existing out-of-scope per the SCOPE BOUNDARY rule.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- **Plan 06-02 (Walking slice) is fully unblocked.** All four helpers it needs (`_SafeDict`, `synthesize_citation`, `get_tool_started_at`, `license_for_sync`) are GREEN-tested and importable. Plan 06-02 edits `server.py` (register middleware), `core/envelope.py` (factory rewire), `tools/discovery.py` / `tools/retrieval.py` / `tools/search.py` (citation + policy emission) + `core/search.py` + `tests/test_tool_trailer.py`. **Plan 06-02 MUST NOT modify** `config.py`, `core/middleware/retrieved_at.py`, `core/citation.py`, `core/metadata_cache.py`, or `tests/conftest.py`.
- **Plan 06-03 (Wave 3 tail) is fully unblocked.** The 4 Wave-0 RED stubs at `tests/test_envelope_snapshot.py`, `tests/test_content_policy_emission.py`, `tests/test_citation_synthesis.py`, `tests/test_hostile_inputs_consolidated.py` collect cleanly with all symbols resolved; Plan 06-03 replaces each `pytest.skip(...)` body with the parametrized GREEN test. **Plan 06-03 MUST NOT modify** `tests/conftest.py` or any of Plan 06-01's source files.
- **Operator review gate (5 [OPERATOR REVIEW] CONTENT_POLICIES rows).** Plan 06-03 manual UAT must confirm: `zeeker-judgements.judgments` (Crown Copyright posture), `pdpc.enforcement_decisions_fragments` (SODL applies to text), all 8 `sg-gov-newsrooms.*_news` (SODL), `sglawwatch.headlines` / `commentaries` (third-party copyright), and `sglawwatch.about_singapore_law_fragments` (SAL terms).

## Self-Check: PASSED

Self-check ran 2026-05-14:
- All 9 created files exist on disk at the documented paths.
- All 5 modified files exist with the documented changes (verified by `git diff` against base `e3a8bb8`).
- All 3 task commits exist in `git log --oneline -5`: `f27fc8e` (Task 1), `114c04f` (Task 2), `4b120d3` (Task 3).
- `uv run pytest -x -q` exits 0: 273 passed, 6 skipped (4 Wave-0 stubs + 1 live + 1 phase-2-only). No failed or errored tests.
- `uv run ruff format --check` and `uv run ruff check` on all touched files exit 0 (3 pre-existing E501 in `TABLE_DESCRIPTIONS` are out of scope).

---
*Phase: 06-envelope-hardening-injection-resistance-labelling*
*Completed: 2026-05-14*
