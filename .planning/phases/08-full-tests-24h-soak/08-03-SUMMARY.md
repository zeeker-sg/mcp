---
phase: "08"
plan: "03"
plan_id: "08-03"
subsystem: "tests"
tags: ["TEST-03", "TEST-04", "TEST-06", "hostile-inputs", "envelope-snapshot", "HEAVY_COLUMNS"]
dependency_graph:
  requires: ["08-01"]
  provides: ["TEST-03", "TEST-04-marker", "TEST-06"]
  affects: ["tests/test_envelope_snapshot.py", "tests/_corpus/hostile_inputs.py", "tests/tools/test_retrieval_fragment_join.py"]
tech_stack:
  added: []
  patterns: ["per-row partition assertion", "parametrized corpus extension", "docstring-only traceability marker"]
key_files:
  modified:
    - tests/test_envelope_snapshot.py
    - tests/_corpus/hostile_inputs.py
    - tests/tools/test_retrieval_fragment_join.py
decisions:
  - "Rule 1 auto-fix: extended surrogate skip in test_byte_identical_heavy_text_round_trip to include \\udcc0\\udc80 (malformed surrogate pair); same JSON-wire reason as existing \\udc80 carry-forward"
  - "tests/test_hostile_inputs_consolidated.py required NO edits — pytest auto-expansion from CANARY_STRINGS correctly generates 27 cases; all 27 pass GREEN without any carry-forward exceptions"
metrics:
  duration: "~4 minutes"
  completed: "2026-05-15"
  tasks: 4
  files_changed: 3
---

# Phase 8 Plan 03: Snapshot + Hostile-Input + 1500-Fragment Traceability Sweep Summary

**One-liner:** TEST-03 per-tool envelope row-key partition assertions, TEST-06 9-canary corpus expansion to 27 cases GREEN, and TEST-04 docstring traceability marker — data-safety gate complete.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | TEST-03 explicit row-key partition assertion per tool | f6261a2 | tests/test_envelope_snapshot.py |
| 2 | Extend CANARY_STRINGS with 4 new canaries | eccea4c | tests/_corpus/hostile_inputs.py |
| 3 | Run hostile inputs at 9×3 = 27 cases | (no file change needed) | — |
| 4 | Append TEST-04 traceability marker to fragment join docstring | 85923ef | tests/tools/test_retrieval_fragment_join.py |
| fix | Rule 1: extend surrogate skip for \\udcc0\\udc80 | 9a419a6 | tests/test_envelope_snapshot.py |

## TEST-06: Hostile-Input Case Count

pytest collected **27 cases** (9 canaries × 3 tools):

```
uv run pytest tests/test_hostile_inputs_consolidated.py --collect-only -q
27 tests collected
```

All 27 cases pass GREEN. No carry-forward exceptions required in `test_hostile_inputs_consolidated.py` for the 4 new canaries.

## CANARY_STRINGS First-5-Entries Invariant (Phase 6 stability)

Verified via Python introspection:
```python
CANARY_STRINGS[:5] == [
    "</system>",                      # index 0 — HTML/system tag injection
    "NEAR('data' 'protection') AND NOT",  # index 1 — FTS5 operators
    "x" * 5001,                       # index 2 — 5 KB oversized string
    "ZEEKER_CANARY_42",               # index 3 — plain round-trip detector
    "\udc80",                         # index 4 — lone surrogate
]
```
Indices 0-4 preserved exactly. New entries at indices 5-8.

## Per-Canary Exceptions in Task 3

**No exceptions needed in `tests/test_hostile_inputs_consolidated.py`.** All 4 new canaries (BOM, RTL, malformed surrogate pair, FTS5 op string) pass the 9×3 matrix without any carry-forward exception.

However, a Rule 1 auto-fix was required for `tests/test_envelope_snapshot.py::test_byte_identical_heavy_text_round_trip`: the `\udcc0\udc80` surrogate pair canary is unrepresentable on the JSON wire (same mechanism as the existing `\udc80` lone-surrogate carry-forward). The existing skip was extended to cover both surrogates.

## _DISPATCH_ARGS Stub Analysis (Pitfall 5 — TEST-03)

All 5 tools with dispatch entries (`list_databases`, `list_tables`, `describe_table`, `query_table`, `search`) exercise the new TEST-03 assertion. However, all tools use empty-row stubs (`_empty_table_payload()` with `rows: []`), meaning the per-row partition assertion passes **trivially** for list tools that return zero rows.

The exception is `test_heavy_namespace_contract_per_tool` which uses `_judgments_row_with_canary("heavy body text fixture")` and exercises a row with `retrieved_content`. The TEST-03 assertion in `test_every_registered_tool_returns_envelope_with_correct_provenance` itself passes trivially for query_table because its stub returns empty rows. This is noted for follow-up in a future plan if stub quality needs improvement; it is NOT fixed in this plan per the plan's instruction ("note this in SUMMARY rather than fix here").

## Confirmation: No conftest.py or REQUIREMENTS.md Modification

- `git diff --name-only tests/conftest.py` — empty (not touched)
- `git diff --name-only .planning/REQUIREMENTS.md` — empty (not touched; REQUIREMENTS.md traceability table update forward-pointed to Plan 08-06)

## Verification Commands (all exit 0)

```bash
uv run pytest tests/test_envelope_snapshot.py -x              # 10 passed, 2 skipped
uv run pytest tests/test_envelope_snapshot.py -k retrieved_content -x  # 0 deselected (no rows)
uv run pytest tests/test_hostile_inputs_consolidated.py -x    # 27 passed
uv run pytest tests/tools/test_retrieval_fragment_join.py::test_1500_fragment_walk_synthetic -x  # 1 passed
uv run pytest -x -q                                            # 385 passed, 8 skipped
uv run ruff check tests/test_envelope_snapshot.py tests/_corpus/hostile_inputs.py tests/test_hostile_inputs_consolidated.py tests/tools/test_retrieval_fragment_join.py  # OK
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Extended surrogate skip to cover \\udcc0\\udc80 in test_byte_identical_heavy_text_round_trip**
- **Found during:** Task 3 final verification (full suite run)
- **Issue:** New `\udcc0\udc80` malformed surrogate pair canary added in Task 2 caused `test_byte_identical_heavy_text_round_trip` to fail with `UnicodeEncodeError` when httpx_mock tried to encode the upstream stub response — same mechanism as the existing `\udc80` lone-surrogate carry-forward
- **Fix:** Extended `if canary == "\udc80":` skip to `if canary in ("\udc80", "\udcc0\udc80"):` with updated inline documentation
- **Files modified:** `tests/test_envelope_snapshot.py`
- **Commit:** 9a419a6

**2. Task 3 required NO file edit**
- The plan correctly anticipated auto-expansion: `@pytest.mark.parametrize("canary", CANARY_STRINGS)` auto-generates 27 cases as CANARY_STRINGS grows. No code change needed.
- All 4 new canaries passed the 9×3 matrix without carry-forward exceptions.

## Forward Pointer

TEST-03/04/06 closed; 08-04/05/06 may proceed (08-04 / 08-05 in parallel within Wave 3; 08-06 in Wave 4).

## Self-Check: PASSED

| Item | Status |
|------|--------|
| tests/test_envelope_snapshot.py exists | FOUND |
| tests/_corpus/hostile_inputs.py exists | FOUND |
| tests/tools/test_retrieval_fragment_join.py exists | FOUND |
| Commit f6261a2 (Task 1) | FOUND |
| Commit eccea4c (Task 2) | FOUND |
| Commit 85923ef (Task 4) | FOUND |
| Commit 9a419a6 (Rule 1 fix) | FOUND |
| Full suite 385 passed, 0 failures | PASSED |
