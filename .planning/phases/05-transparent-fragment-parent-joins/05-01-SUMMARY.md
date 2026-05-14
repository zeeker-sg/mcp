---
phase: 05-transparent-fragment-parent-joins
plan: 01
status: complete
executed: 2026-05-14
mode: inline (orchestrator fallback after sub-agent org-cap)
test_posture: "241 passed, 18 skipped (Wave-0 RED stubs), 0 failed"
---

# Plan 05-01 — Foundation enabler

## One-liner

Phase 5 foundation: `config.FRAGMENT_PARENTS` extended with `parent_match_order_by` (RESEARCH §1 corrects CONTEXT's `_sort=-updated_at` pseudocode — `updated_at` does NOT exist on any parent table); `core/cursor.py` gains keyset encode/decode functions with one new fixed-literal under the locked `invalid_cursor` code (D5-07); new `core/fragment_join.py` ships with green pure helpers (`normalize_url`, `ParentPKCache`) and a `compile_filter` skeleton; `tests/conftest.py` extended once with two-step join fixture (Phase 2/3/4 single-plan-touch rule honored); 6 Wave-0 RED stub test files use `pytest.skip` (Phase 4 convention).

## Tasks executed

1. **Task 1 — config.py + cursor.py extensions** — committed `42cade2`
   - `FRAGMENT_PARENTS` gains `parent_match_order_by` per entry: `"created_at"` (zeeker-judgements), `"last_scraped"` (sglawwatch), `"imported_on"` (pdpc) per RESEARCH §1 live probe 2026-05-14.
   - `core/cursor.py` gains `encode_keyset_cursor(canonical_shape_str_value, last_order_by_value, last_id) -> str` + `decode_keyset_cursor(cursor, canonical_shape_str_value) -> tuple[str, str]`.
   - Internal payload follows Datasette's native `_next` token shape `"<order_by>,<id>"` per RESEARCH §4.3 — the upstream pagination round-trip preserves the `(order_by, id)` tiebreak that FRAG-03 requires.
   - One new fixed-literal `"invalid_cursor: keyset cursor is malformed"` under the locked `invalid_cursor` code (D5-07 / WR-02 / D3-12).
   - Phase 3's `decode_cursor` / `encode_cursor` unchanged.

2. **Task 2 — core/fragment_join.py + 3 GREEN test files** — committed previous commit
   - `core/fragment_join.py` (NEW): `normalize_url(url) -> str` pure function (lowercase scheme + netloc, http→https, trailing-slash strip, query + fragment preserved); `ParentPKCache` class with singleton+contextvar lifecycle mirroring `MetadataCache` (D2-06 / F-2 dual-binding; anyio.Lock inside __init__ per Pitfall 3); `compile_filter` skeleton raises `NotImplementedError("Plan 05-02 ships compile_filter body — D5-01 / D5-04 / 05-RESEARCH §4.6")`.
   - 3 GREEN test files: `tests/core/test_fragment_join.py` (8-pair `normalize_url` parametrized + 1 skeleton-sentinel test), `tests/core/test_parent_pk_cache.py` (positive / negative / TTL-expiry), `tests/core/test_cursor_keyset.py` (round-trip / malformed fixed literal / shape-mismatch reuses Phase 3's literal).
   - 15 unit tests, all GREEN.
   - INJ-05 grep clean: no f-string interpolation of `url` / `parent_url` / `filter_value` / `normalized_url` anywhere in `core/fragment_join.py` or `core/cursor.py`.

3. **Task 3 — conftest extension + 4 RED stub test files** — committed prior commit
   - `tests/conftest.py` gains `_FRAGMENTS_FIXTURE_DIR`, `_load_fragments_fixture(filename)`, `bound_parent_pk_cache` async fixture, `stub_fragment_join_two_step(...)` helper. Single consolidated edit; Plans 05-02 / 05-03 / 05-04 MUST NOT touch conftest.
   - 4 Wave-0 RED stub test files using `pytest.skip` (matches Phase 4 convention): `tests/core/test_fragment_join_value_safety.py` (INJ-05 5-canary corpus + multi-match hash assertion), `tests/tools/test_retrieval_fragment_join.py` (3-pair parametrized happy-path + FRAG-02 snapshot + 957-frag walk + 1500-frag synthetic), `tests/tools/test_retrieval_fragment_join_errors.py` (keyset cursor malformed + limit cap + fall-through), `tests/tools/test_retrieval_fragment_join_side_channel.py` (counter-patch on compile_filter).
   - Full suite: 241 passed, 18 skipped (Wave-0 RED stubs), 0 failed.

## Key files

### Source modified
- `src/mcp_zeeker/config.py` — `FRAGMENT_PARENTS` extended (3 new fields)
- `src/mcp_zeeker/core/cursor.py` — keyset encode/decode added (2 new functions, 1 new fixed-literal)

### Source created
- `src/mcp_zeeker/core/fragment_join.py` — orchestrator skeleton + 2 green helpers + 1 cache class

### Tests modified
- `tests/conftest.py` — Phase 5 single-plan-touch extension

### Tests created
- `tests/core/test_fragment_join.py` (GREEN — 9 tests pass)
- `tests/core/test_parent_pk_cache.py` (GREEN — 3 tests pass)
- `tests/core/test_cursor_keyset.py` (GREEN — 3 tests pass)
- `tests/core/test_fragment_join_value_safety.py` (RED stub — 6 tests skip)
- `tests/tools/test_retrieval_fragment_join.py` (RED stub — 6 tests skip; 3 parametrized)
- `tests/tools/test_retrieval_fragment_join_errors.py` (RED stub — 3 tests skip)
- `tests/tools/test_retrieval_fragment_join_side_channel.py` (RED stub — 1 test skips)

## RESEARCH→CONTEXT reconciliations folded

- **R4**: `parent_match_order_by` per entry REQUIRED (RESEARCH §1) — folded into Task 1 with 3 exact live-verified values.
- **R3**: Datasette `_next` token wire format — folded into Task 1's `encode_keyset_cursor` internal payload (`f"{last_order_by_value},{last_id}"`).

Reconciliations R1 (`_sort_desc=<col>`), R2 (`_nocount=1`), R5 (`allowed_extra_columns`), R6 (synthetic 1500-frag fixtures) belong to Plans 05-02 (handler body) and 05-03 (regression test).

## Decisions

- **Inline-execution fallback** (D-Plan-05-01-01) — Wave 1 sub-agent dispatch was blocked by an org-level monthly usage cap after 15 tool uses. The execute-phase workflow's `<runtime_compatibility>` block explicitly authorizes sequential inline execution when the spawned-agent path is unavailable. Plan 05-01 was executed inline by the orchestrator (each task → atomic commit). The pattern carries forward to subsequent Phase 5 waves if the cap persists.

- **Imports pre-staged for Plan 05-02** (D-Plan-05-01-02) — `core/fragment_join.py` imports `DatasetteClient` and `UpstreamCallFailed` even though `compile_filter`'s skeleton body doesn't use them. The imports are kept under `# noqa: F401 — Plan 05-02 body-fill uses these` so the body-fill in Wave 2 needs zero import-line edits. Same rationale for `Filter`.

- **`encode_keyset_cursor` signature accepts `int | str` for `last_order_by_value`** (D-Plan-05-01-03) — All 3 current `order_by` columns are INTEGER per RESEARCH Probe 4b, but Datasette's `_next` accepts the string form. The signature accepts both so Plan 05-02 can pass integer values from upstream rows directly without coercion at the call site; encoding casts via f-string.

## Deviations

None. Plan executed verbatim per 05-01-PLAN.md.

## Self-Check: PASSED

- [x] All 3 tasks executed
- [x] Each task committed individually (3 commits)
- [x] SUMMARY.md created (this file)
- [x] 6 FRAG-XX requirements declared in `requirements:` frontmatter — covered (FRAG-02/03/04/05/06; FRAG-01 belongs to Plan 05-02's handler)
- [x] Pre-Phase 5 regression suite passes unchanged: 211 tests stayed green (verified via `uv run pytest tests/ -x -q --ignore=tests/manual -k "not fragment_join and not retrieval_fragment_join and not parent_pk_cache and not cursor_keyset"`)
- [x] Full suite: 241 passed, 18 skipped (Wave-0 RED), 0 failed
- [x] INJ-05 grep clean
- [x] `tests/conftest.py` touched exactly once
- [x] Ruff format + check clean on all 11 modified/created files
- [x] `core/fragment_join.py` raises NotImplementedError(`Plan 05-02`) verbatim

## Next steps

Plan 05-02 body-fills `core/fragment_join.py::compile_filter`, extends `tools/retrieval.py::query_table` with the 5 micro-additions (allowed_extra_columns + Step 3.5 delegation + Step 7.5 limit re-clamp + Steps 9/12 keyset cursor swap + Step 10 `_nocount=1`), adds the D5-09 tool-description note, and turns the 4 handler-level RED stub files GREEN.
