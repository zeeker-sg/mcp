---
phase: 08-full-tests-24h-soak
plan: "02"
subsystem: testing
tags: [pytest, parametrize, filter-ops, hidden-data, rate-limit, error-catalog, cursor, TEST-01]

# Dependency graph
requires:
  - phase: 08-full-tests-24h-soak
    plan: "01"
    provides: conftest.py fixtures (bound_datasette_client, bound_metadata_cache, httpx_mock, rate_limiter, fake_clock, bucket_store, frozen_retrieved_at)
provides:
  - TEST-01 unit-coverage gate: all 13 filter operators, hidden-table/column enforcement, rate-limit windows, error catalog, cursor qhash mismatch
  - tests/test_hidden_data_enforcement.py: parametrized sweep across entire HIDDEN_TABLES + HIDDEN_COLUMNS denylist
  - tests/test_filter_compiler.py: ALL_OPS completeness sweep (13 cases) + numeric×col-type matrix (12 cases)
affects: [08-03, 08-04, 08-05, 08-06, phase-09-registry-submission]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "typing.get_args(FilterOp) for Literal completeness assertion — catches both removal and addition drift"
    - "Parametrize source: _iter_hidden_table_pairs() / _iter_hidden_column_triples() — iterate entire denylist at module load time"
    - "Pitfall 5 pattern: stub INCLUDES hidden item to exercise strip code path (not trivially-pass path)"
    - "D2-10 compliance: hidden_columns_for(db, table) only — zero direct config.HIDDEN_COLUMNS reads"

key-files:
  created:
    - tests/test_hidden_data_enforcement.py
  modified:
    - tests/test_filter_compiler.py
    - tests/test_error_catalog.py

key-decisions:
  - "Used local file-level fixtures in test_hidden_data_enforcement.py rather than conftest.py bound_datasette_client — bound_datasette_client depends on stub_upstream which pre-stubs all 4 databases with fixed payloads; per-test override would conflict with FIFO pytest-httpx consumption order. Local fixtures give full per-test payload control without conftest.py modification."
  - "Ruff-formatted test_error_catalog.py (pre-existing formatting issue) — no behavior change, only whitespace/line-length normalization."

patterns-established:
  - "ALL_OPS tuple at module-level: single source of truth for FilterOp names in tests; any addition/removal to FilterOp Literal triggers both the tuple check AND the len==13 assertion"
  - "Hidden-data parametrize pattern: iterate config denylist at module load → one parametrized case per denylist entry → stub includes the hidden item → assert strip happened"

requirements-completed:
  - TEST-01

# Metrics
duration: 8min
completed: 2026-05-15
---

# Phase 8 Plan 02: TEST-01 Unit-Coverage Gate Summary

**TEST-01 unit-coverage gate locked: 13-op filter completeness + numeric×col-type matrix (tests/test_filter_compiler.py extended) + full hidden-data sweep (tests/test_hidden_data_enforcement.py NEW) + rate-limit keyword-selectability + error catalog + cursor qhash mismatch verified**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-15T06:25:00Z
- **Completed:** 2026-05-15T06:33:48Z
- **Tasks:** 3 (Task 1: filter compiler extension, Task 2: hidden-data enforcement, Task 3: verification)
- **Files modified:** 3 (test_filter_compiler.py, test_hidden_data_enforcement.py NEW, test_error_catalog.py)

## Accomplishments

- **Task 1**: Extended `tests/test_filter_compiler.py` with two parametrized sweeps:
  - `test_op_in_locked_set[<op>]`: 13 cases — asserts each op in ALL_OPS is in `get_args(FilterOp)` AND `len == 13`; closes the "11 vs 13 ops" discrepancy
  - `test_numeric_ops_across_column_types[<op>-<col_type>]`: 12 cases — 4 ops × 3 column types; exercises numeric coercion path deterministically
  - Total: 43 tests (18 existing + 13 + 12)

- **Task 2**: Created `tests/test_hidden_data_enforcement.py` (NEW):
  - `test_list_tables_strips_hidden[<db>-<hidden_table>]`: 10 cases — sweeps all 4 databases × their HIDDEN_TABLES entries; each stub INCLUDES the hidden table (Pitfall 5)
  - `test_describe_table_strips_hidden_columns[<db>-<table>-<hidden_col>]`: 19 cases — sweeps all (db, table) pairs with hidden columns via `hidden_columns_for`; each stub INCLUDES the hidden column (Pitfall 5)
  - D2-10 preserved: zero direct `config.HIDDEN_COLUMNS` reads; `hidden_columns_for` used exclusively

- **Task 3**: Verified keyword-selectability and no renames needed:
  - `pytest -k burst`: selects `test_burst_allows_20_rejects_21st` (already named)
  - `pytest -k sustained`: selects `test_sustained_refill_after_one_second` (already named)
  - `pytest -k daily`: selects 3 tests (`test_daily_limit_5000`, `test_daily_reset_at_utc_midnight`, `test_sticky_ttl_daily_locked_not_expired`) (already named)
  - `tests/test_cursor.py::test_shape_mismatch_raises_invalid_cursor` already exists with exact name
  - `tests/test_error_catalog.py::test_all_11_codes_in_catalog` GREEN (no change needed)
  - Applied `ruff format` to `test_error_catalog.py` (pre-existing formatting issue)

## Task Commits

1. **Task 1: filter compiler extension** — `69df686`
2. **Task 2: hidden-data enforcement** — `79dfc68`
3. **Task 3: verification + ruff cleanup** — `cd7dbda`

## ALL_OPS Tuple (verbatim as added to tests/test_filter_compiler.py)

```python
ALL_OPS = (
    "exact",
    "not",
    "contains",
    "startswith",
    "endswith",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "notin",
    "isnull",
    "notnull",
)
```

## Parametrize Fan-Out Counts

| Test | Cases | Description |
|------|-------|-------------|
| `test_op_in_locked_set` | 13 | One per op name in ALL_OPS |
| `test_numeric_ops_across_column_types` | 12 | 4 ops × 3 col types (INTEGER, REAL, TEXT) |
| `test_list_tables_strips_hidden` | 10 | All (database, hidden_table) pairs from HIDDEN_TABLES |
| `test_describe_table_strips_hidden_columns` | 19 | All (db, table, hidden_col) triples via hidden_columns_for |

## Task 3 Renames

No renames needed. All three keyword-selectable windows (`burst`, `sustained`, `daily`) already appear verbatim in test function names in `tests/test_rate_limit.py`. The cursor qhash-mismatch test is already named `test_shape_mismatch_raises_invalid_cursor`.

## conftest.py Unchanged

`git diff --name-only tests/conftest.py` — empty (no output). The per-file local fixtures in `test_hidden_data_enforcement.py` satisfy the single-plan-touch rule.

## D2-10 Single-Source-of-Truth Preserved

`uv run pytest tests/test_config_lookup_single_source.py -x` — 2 passed. The new test file uses `hidden_columns_for` exclusively; zero `config.HIDDEN_COLUMNS` attribute accesses in the file (verified via grep).

## VALIDATION.md Per-Task Commands — All GREEN

| Command | Result |
|---------|--------|
| `pytest tests/test_filter_compiler.py -x` | 43 passed |
| `pytest tests/test_hidden_data_enforcement.py::test_list_tables_strips_hidden -x` | 10 passed |
| `pytest tests/test_hidden_data_enforcement.py::test_describe_table_strips_hidden_columns -x` | 19 passed |
| `pytest tests/test_rate_limit.py -k burst -x` | 1 passed |
| `pytest tests/test_rate_limit.py -k sustained -x` | 1 passed |
| `pytest tests/test_rate_limit.py -k daily -x` | 3 passed |
| `pytest tests/test_error_catalog.py -x` | 4 passed |
| `pytest tests/test_cursor.py::test_shape_mismatch_raises_invalid_cursor -x` | 1 passed |

## Full Suite

`uv run pytest -x -q` — 424 passed, 7 skipped (no regressions).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Auto-fix] Used local file-level fixtures instead of conftest.py bound_datasette_client**
- **Found during:** Task 2 implementation
- **Issue:** `bound_datasette_client` depends on `stub_upstream` which pre-stubs all `/{db}.json` endpoints with fixed payloads. The parametrized hidden-table test needs per-test payload control (to include the specific hidden table). Using FIFO-ordered pytest-httpx with the pre-stubbed fixture would require complex workarounds.
- **Fix:** Created local `datasette_client` and `metadata_cache` fixtures at the top of `test_hidden_data_enforcement.py` — same pattern as `tests/tools/test_list_tables.py` lines 55-78. The plan's instruction "DO NOT add new fixtures in tests/conftest.py" was honored (local fixtures are not conftest.py additions).
- **Files modified:** tests/test_hidden_data_enforcement.py only
- **Commit:** 79dfc68

**2. [Rule 1 - Formatting] Applied ruff format to test_error_catalog.py**
- **Found during:** Task 3 ruff check
- **Issue:** Pre-existing whitespace/line-length issues in test_error_catalog.py failed `ruff format --check`. The plan requires ruff-clean verification.
- **Fix:** Applied `ruff format tests/test_error_catalog.py`. No logic changes.
- **Files modified:** tests/test_error_catalog.py
- **Commit:** cd7dbda

## Known Stubs

None — all test logic is fully wired to production code.

## Threat Flags

None — no new network endpoints, auth paths, file access patterns, or schema changes introduced. This plan adds test files only.

## Self-Check

Files exist:
- tests/test_filter_compiler.py — FOUND
- tests/test_hidden_data_enforcement.py — FOUND
- tests/test_error_catalog.py — FOUND

Commits exist:
- 69df686 (Task 1) — FOUND
- 79dfc68 (Task 2) — FOUND
- cd7dbda (Task 3) — FOUND

## Self-Check: PASSED

## Forward Pointer

TEST-01 unit-coverage gate locked; 08-03 (TEST-03/04/06) and 08-04/05/06 (TEST-02 + TEST-05 + NFR-05) may proceed.
