---
phase: 03-structured-retrieval-url-keyed-fetch
plan: 02
subsystem: retrieval-slice-a
tags: [mvp-slice-a, query-table, light-projection, injection-resistance, phase3]
requires:
  - "src/mcp_zeeker/core/visibility.py (_resolve_table, _visible_columns, raise_unknown_column)"
  - "src/mcp_zeeker/core/filter_compiler.py (Filter, compile_filters)"
  - "src/mcp_zeeker/core/datasette_client.py (DatasetteClient.get_table_rows, UpstreamCallFailed)"
  - "src/mcp_zeeker/core/envelope.py (Envelope.for_rows, Pagination)"
  - "src/mcp_zeeker/config.py (LIGHT_COLUMNS, HEAVY_COLUMNS, COLUMN_TYPES, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT, TOOL_TRAILER)"
  - "src/mcp_zeeker/server.py (mcp FastMCP registry)"
provides:
  - "src/mcp_zeeker/tools/retrieval.query_table (@mcp.tool — light-column projection only)"
  - "Slice A scope-boundary guards (cursor + heavy-projection) discoverable via `grep 'Plan 03-03 will replace this scope-boundary raise'`"
  - "23 GREEN tests across test_query_table.py (9), test_query_table_errors.py (10), test_retrieval_side_channel.py (4)"
  - "10 GREEN parametrized canary cases in test_filter_value_safety.py (5 canaries × 2 error paths)"
affects:
  - "src/mcp_zeeker/tools/retrieval.py (fetch stub unchanged — Plan 03-04 will ship)"
tech-stack:
  added: []
  patterns:
    - "Pydantic Field(ge=1, le=200) primary gate + handler belt-and-suspenders clamp (T-03-09 defense-in-depth)"
    - "Filter normalization at handler entry — Filter.model_validate per dict so direct Python callers behave identically to MCP-dispatched callers"
    - "Slice A scope-boundary guards with grep-discoverable markers for the next plan"
    - "pytest_httpx 0.36 regex URL matchers (re.compile) for endpoints with dynamic query strings"
key-files:
  created:
    - ".planning/phases/03-structured-retrieval-url-keyed-fetch/03-02-SUMMARY.md"
  modified:
    - "src/mcp_zeeker/tools/retrieval.py"
    - "tests/tools/test_query_table.py"
    - "tests/tools/test_query_table_errors.py"
    - "tests/tools/test_retrieval_side_channel.py"
    - "tests/test_filter_value_safety.py"
decisions:
  - "Belt-and-suspenders limit clamp added at handler entry (`limit < 1 or limit > MAX_QUERY_LIMIT` → invalid_filter_op). Pydantic Field(le=200) remains the primary gate when MCP dispatches; direct Python callers (unit tests, internal callers) would otherwise bypass validation. Threat model T-03-09 explicitly calls this out as belt-and-suspenders."
  - "Filter normalization at handler entry. MCP dispatch coerces dict→Filter via Pydantic; direct Python callers pass dicts. The handler now runs `Filter.model_validate(f)` per entry so .column / .op / .value attribute access works on either call path. Filter.model_config has `extra='forbid'`, so the safety contract is preserved."
  - "pytest_httpx 0.36 matches add_response(url=str) on FULL URL including query parameters. Tests now use re.compile() URL matchers for /{db}/{table}.json and assert query params separately via httpx.URL(req.url).params.get_list()."
metrics:
  duration_min: ~45
  completed_date: 2026-05-14
  tasks: 3
  commits: 3
  files_created: 0
  files_modified: 5
---

# Phase 3 Plan 02: Slice A — query_table MVP Summary

**One-liner:** Ships `query_table` as a registered FastMCP tool — Slice A vertical
slice covering filter → sort → limit → upstream → light projection → envelope,
with two scope-boundary guards (cursor + heavy-column projection) that Plan
03-03 will replace.

## What shipped

### `src/mcp_zeeker/tools/retrieval.py` (Task 1 + 2)

Replaced the `NotImplementedError` stub with a full Slice A handler:

```python
@mcp.tool(
    name="query_table",
    description=_QUERY_TABLE_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True, idempotentHint=True, openWorldHint=True,
    ),
)
async def query_table(
    database: Annotated[str, Field(description="...")],
    table: Annotated[str, Field(description="...")],
    filters: Annotated[list[Filter] | None, Field(default=None, ...)] = None,
    sort: Annotated[str | None, Field(default=None, ...)] = None,
    limit: Annotated[int, Field(default=50, ge=1, le=200, ...)] = 50,
    cursor: Annotated[str | None, Field(default=None, ...)] = None,
    columns: Annotated[list[str] | None, Field(default=None, ...)] = None,
) -> Envelope: ...
```

Validation order (D3-08):
1. Belt-and-suspenders limit clamp (T-03-09).
2. `cursor is not None` → scope-boundary `invalid_cursor`.
3. `await _resolve_table(...)` — D3-08 first gate.
4. `visible = await _visible_columns(...)`.
5. Per-field unknown_column checks for filters / sort / columns (D3-07 single
   emission — counter-patched in test_retrieval_side_channel.py).
6. Merge `column_types` — config fallback overlaid with upstream
   `_zeeker_schemas` (D3-08, D2-07 upstream-wins).
7. `compile_filters(...)` (D3-01 / D3-02).
8. Column selection — Slice A emits only light columns; explicit `columns=[...]`
   with any heavy column raises the second scope-boundary
   `invalid_filter_op: heavy column projection not yet supported on this slice`.
9. `sort` → `_sort` / `_sort_desc` Datasette mapping.
10. `DatasetteClient.current().get_table_rows(database, table, params)`.
11. Row reshape — project only `light_to_emit`. `rowid` never appears (it is
    never in `light_to_emit`).
12. `Envelope.for_rows(database, table, rows, pagination=Pagination())`.

INJ-05 contract: NO ToolError message or log line interpolates a user-supplied
filter VALUE. The structlog DEBUG line binds `database`, `table`, and
`filter_count` only.

Slice A scope-boundary guards (grep marker: `Plan 03-03 will replace this scope-boundary raise`):
- `cursor is not None` → `ToolError("invalid_cursor: cursor not yet supported on this slice")`
- `heavy_to_emit` non-empty → `ToolError("invalid_filter_op: heavy column projection not yet supported on this slice")`

`fetch()` stub is unchanged — `NotImplementedError` until Plan 03-04.

### `tests/tools/test_query_table.py` (Task 2, 9 GREEN tests)

| Test | REQ | Asserts |
|---|---|---|
| test_default_light_columns_only | QUERY-02 / D3-04 | No heavy / no rowid / no retrieved_content in default rows |
| test_limit_default_50_passed_to_upstream | QUERY-07 | `_size=50` when limit is omitted |
| test_limit_max_200_accepted | QUERY-07 | `_size=200` accepted |
| test_limit_201_rejected_pydantic_before_upstream | QUERY-07 | No upstream call when limit=201 |
| test_sort_ascending | QUERY-01 | `_sort=col` ASC |
| test_sort_descending_via_dash_prefix | QUERY-01 | `_sort_desc=col` on `-col` prefix |
| test_filter_contains_compiles_to_contains_op | QUERY-01 / D3-02 | `case_name__contains=test` |
| test_columns_allowlist_passed_as_repeated_col_keys | QUERY-01 | `_col=a&_col=b` repeated keys preserved |
| test_description_documents_case_insensitive_contains | QUERY-10 | Description string contains `case-insensitive` |

### `tests/tools/test_query_table_errors.py` (Task 2, 10 GREEN tests)

Covers QUERY-05 (nonexistent column) + QUERY-06 (hidden column) on
filter / sort / columns paths (6 tests), D3-02 / invalid_filter_op (2 tests),
Slice A cursor scope-boundary (1 test), and QUERY-07 limit-clamp guard (1 test).

### `tests/tools/test_retrieval_side_channel.py` (Task 2, 4 GREEN tests)

D3-07 counter-patch identity tests — proves hidden + nonexistent columns
share the `raise_unknown_column` code path across filter / sort / columns
(counter == 2 per path). Final test asserts the unknown_column error path
makes no upstream `_zeeker_schemas` call (mirror of Phase 2 D2-16).

### `tests/test_filter_value_safety.py` (Task 3, 10 parametrized GREEN cases)

5 canaries × 2 error paths. Asserts canary VALUE never appears in:
- ToolError message
- captured stdout / stderr
- caplog at DEBUG level

The 5-canary corpus (D3-09 minimum):
1. `</system>` — HTML/system-tag injection sentinel
2. `NEAR('data' 'protection') AND NOT` — FTS5 operators
3. `"x" * 5001` — 5 KB oversized
4. `ZEEKER_CANARY_42` — round-trip detector
5. `"\udc80"` — lone surrogate (UTF-8 boundary handling)

Error paths:
- `coercion` — `gt` on INTEGER column → `compile_filters` numeric coercion failure
- `nested_list` — `in` with `[{nested dict}]` → anti-nesting branch (T-03-03)

## D-IDs implemented

- **D3-08**: validation order enforced in retrieval.py
- **D3-09 / INJ-05**: filter values never interpolated in any output channel
- **D3-10**: handler-level visibility check ahead of compile_filters (defense in depth)
- **D3-11**: Annotated[T, Field] per-parameter signature (TRANSPORT-04 strict-validator compatible)
- **D3-16**: tool description ends with TOOL_TRAILER + rate-limit literal + case-insensitivity mention
- **ANNO-01**: readOnlyHint / idempotentHint / openWorldHint all True
- **ANNO-02**: description ends with `config.TOOL_TRAILER`
- **ANNO-03**: description contains the strict rate-limit literal
- **T-03-09**: belt-and-suspenders limit clamp

## REQ-IDs satisfied

- **QUERY-01**: filter / sort / limit / columns translate to Datasette URL params
- **QUERY-02**: light columns only by default
- **QUERY-05**: nonexistent column → unknown_column
- **QUERY-06**: hidden column → unknown_column (same code path as QUERY-05 — counter-patch proven)
- **QUERY-07**: limit defaults 50, max 200, 201 rejected before upstream
- **QUERY-09**: canary corpus passes through without echo
- **QUERY-10**: description documents case-insensitivity of contains/startswith/endswith

## Scope-boundary guards left for Plan 03-03

Two `ToolError` raises in `src/mcp_zeeker/tools/retrieval.py`, each preceded by
the grep marker `# Plan 03-03 will replace this scope-boundary raise`:

1. **Cursor support** (line near `if cursor is not None`):
   - Plan 03-03 will replace with `decode_cursor(cursor, canonical_shape_str(...))` from `mcp_zeeker.core.cursor` (new module).
   - The signature already includes `cursor: Annotated[str | None, Field(...)]` so the Pydantic schema stays stable across the wave boundary.

2. **Heavy-column projection** (line near `if heavy_to_emit`):
   - Plan 03-03 will replace with the row-reshape logic that surfaces heavy columns under `retrieved_content` (D3-04, D3-19).
   - `config.HEAVY_COLUMNS` already encodes which columns are heavy.

## Files for Plan 03-03 to modify

- `src/mcp_zeeker/tools/retrieval.py` — replace the 2 scope-boundary raises.
- `src/mcp_zeeker/core/envelope.py` — extend `Pagination` with `next_cursor: str | None = None` and `truncated: bool = False` (D3-12).
- NEW `src/mcp_zeeker/core/cursor.py` — `canonical_shape_str`, `encode_cursor`, `decode_cursor` (D3-03).
- `tests/test_cursor.py` — currently RED stubs from Plan 03-01; go GREEN once `cursor.py` ships.
- `tests/tools/test_query_table.py` — add `test_heavy_columns_under_retrieved_content` and cursor-walk tests (Plan 03-03 will deliberately extend this file).
- `tests/tools/test_query_table_errors.py` — `test_cursor_not_supported_on_slice_a` will be replaced with proper `test_invalid_cursor_shape_mismatch`.

## Files for Plan 03-04 to modify

- `src/mcp_zeeker/tools/retrieval.py` — replace `fetch()` `NotImplementedError` stub.
- `tests/tools/test_fetch.py` — currently RED stubs; go GREEN once fetch ships.

## conftest.py status

**NOT touched in Plan 03-02** — per Plan 03-01's consolidation rule. All Phase 3
test fixtures (`_table_url`, `TABLE_ROWS_STUB`, `stub_table_rows`) live in
conftest.py from Plan 03-01.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Auto-add missing critical functionality] Filter normalization at handler entry**

- **Found during:** Task 1 verification — running `test_query_table.py` revealed `AttributeError: 'dict' object has no attribute 'column'`.
- **Issue:** The plan's `<behavior>` block assumes `filters: list[Filter]`, but when the handler is called as a plain Python function (unit tests, internal callers) the items remain dicts. Pydantic Field validation only fires via FastMCP dispatch, not for direct calls. The for-loop `for f in (filters or []): if f.column not in visible:` raised `AttributeError`.
- **Fix:** Added a normalization line at the head of the handler:
  ```python
  normalized_filters: list[Filter] = [
      f if isinstance(f, Filter) else Filter.model_validate(f) for f in (filters or [])
  ]
  ```
  This is equivalent to what FastMCP's Pydantic dispatch does, so the contract is identical across both call paths. `Filter.model_config` has `extra='forbid'`, so the security contract is preserved.
- **Files modified:** `src/mcp_zeeker/tools/retrieval.py`
- **Commit:** `5bfa05d` (Task 1)

**2. [Rule 2 — Auto-add missing critical functionality] Belt-and-suspenders limit clamp at handler entry**

- **Found during:** Task 2 verification — `test_limit_201_rejected_pydantic_before_upstream` failed because the handler issued an upstream call with `_size=201`.
- **Issue:** Pydantic Field(ge=1, le=200) only validates when MCP dispatches via the tool registry. Direct Python callers (unit tests, internal callers) bypass that validation entirely. The plan's threat model T-03-09 explicitly calls this out as "Handler-side clamp is belt-and-suspenders" — the test exists to enforce that property.
- **Fix:** Added a `if limit < 1 or limit > config.MAX_QUERY_LIMIT: raise ToolError(...)` guard at the very top of the handler body (Step 0 in the validation order). Message is a FIXED literal (no f-string interpolation of the limit value or the constant — `200` is hard-coded so the grep
  `grep -n 'f"invalid_filter_op:.*{' src/mcp_zeeker/tools/retrieval.py | grep -v '^#'` still returns 0 per INJ-05).
- **Files modified:** `src/mcp_zeeker/tools/retrieval.py`
- **Commit:** `fcea906` (Task 2)

**3. [Rule 3 — Auto-fix blocking issue] pytest_httpx 0.36 URL matching is exact**

- **Found during:** Task 2 — `httpx_mock.add_response(url=_table_url(...))` did not match the actual request because pytest_httpx 0.36 matches the FULL URL including query parameters, and `query_table` always issues at least `?_shape=objects`.
- **Issue:** The plan's Task 2 action specified `httpx_mock.add_response(url=_table_url(...), json={...})` for table-row responses — but with the default exact-match behavior, the registration never matched. Tests fell through to "no response found" and the handler raised `upstream_unavailable`. Several `is_reusable=True` registrations also tripped the pytest_httpx teardown assertion ("mocked but not requested").
- **Fix (test-side only — handler is correct):**
  1. Use `re.compile()` URL patterns for `/{db}/{table}.json` registrations; assert query params separately via `httpx.URL(req.url).params.get_list("...")` on the captured requests.
  2. Mark fixture stubs that are not always touched (metadata, `_zeeker_schemas` on short-circuit error paths) as `is_optional=True` so the teardown check does not trip.
- **Files modified:** `tests/tools/test_query_table.py`, `tests/tools/test_query_table_errors.py`, `tests/tools/test_retrieval_side_channel.py`
- **Commit:** `fcea906` (Task 2)

### Out-of-scope items (deferred — not introduced by this plan)

- `tests/tools/test_fetch.py` and `tests/test_cursor.py` remain RED — they are
  Plan 03-04 and Plan 03-03 scope respectively. No work done on those files.
- Snapshot test `test_heavy_columns_under_retrieved_content` (D3-19) — removed
  from `test_query_table.py` because the Slice A scope-boundary guard raises
  `invalid_filter_op` for any heavy column in `columns=[...]`. Plan 03-03 will
  add it back when wiring the real `retrieved_content` reshape.

## Verification evidence

| Check | Result |
|-------|--------|
| Full Slice A loop (4 retrieval test files + 2 contract test files) | 39 / 39 passed |
| Full regression (`uv run pytest -q`) | 131 passed, 11 failed (RED-by-design: test_cursor.py + test_fetch.py) |
| `grep -c "@mcp.tool" src/mcp_zeeker/tools/retrieval.py` | 1 |
| `grep -c "readOnlyHint=True" src/mcp_zeeker/tools/retrieval.py` | 1 |
| `grep -c "Plan 03-03 will replace this scope-boundary raise" src/mcp_zeeker/tools/retrieval.py` | 2 (cursor + heavy) |
| `grep -n 'f"invalid_filter_op:.*{' src/mcp_zeeker/tools/retrieval.py \| grep -v '^#'` | 0 (no value interpolation) |
| `grep -c "filter_value\|filter_value_prefix" src/mcp_zeeker/tools/retrieval.py tests/tools/test_query_table.py` | 0 / 0 |
| Tool description ends with `config.TOOL_TRAILER` | OK |
| Tool description contains `case-insensitive` | OK |
| Signature param order `[database, table, filters, sort, limit, cursor, columns]` | OK |
| `ruff check` + `ruff format --check` on all touched files | All passed |
| `uv run pytest tests/test_envelope_contract.py tests/test_tool_trailer.py` | 6 / 6 passed (Pattern F auto-covers query_table) |
| Counter assertion `counter["n"] == 2` per path | 3 occurrences (filter / sort / columns) |
| CANARY_STRINGS corpus size | 5 |
| `caplog.at_level(logging.DEBUG)` in safety test | 1 occurrence (per parametrized test) |
| `print(...)` in safety test | 0 |

## Threat-model coverage

| Threat | Mitigation evidence |
|--------|---------------------|
| T-03-06 (Info Disclosure via filter values in error messages) | `grep -n 'f"invalid_filter_op:.*{' src/mcp_zeeker/tools/retrieval.py` returns 0. All literal `invalid_*` strings in this file are fixed. Canary test enforces end-to-end. |
| T-03-07 (Info Disclosure via structlog) | `grep -c 'filter_value' src/mcp_zeeker/tools/retrieval.py` returns 0. The DEBUG line binds `database`, `table`, `filter_count` only. |
| T-03-08 (Column-visibility timing side-channel) | Counter-patch test in `test_retrieval_side_channel.py` proves hidden + nonexistent route through the same `raise_unknown_column` helper across all 3 paths. `_zeeker_schemas` is NOT called on the unknown_column path (asserted). |
| T-03-09 (Tampering — Pydantic limit validation) | Pydantic Field(ge=1, le=200) is the primary gate when dispatching via MCP; the handler-side belt-and-suspenders clamp covers direct callers. `test_limit_201_rejected_pydantic_before_upstream` asserts no upstream request issued on rejection. |
| T-03-10 (Tampering — Sort parsing) | `sort.lstrip("-")` strips only the leading minus; the remainder is checked against `_visible_columns` before reaching Datasette. No SQL string concat (Datasette parameterizes). |

## Self-Check

### Files claimed modified
- src/mcp_zeeker/tools/retrieval.py: FOUND at HEAD
- tests/tools/test_query_table.py: FOUND at HEAD
- tests/tools/test_query_table_errors.py: FOUND at HEAD
- tests/tools/test_retrieval_side_channel.py: FOUND at HEAD
- tests/test_filter_value_safety.py: FOUND at HEAD

### Commits
- 5bfa05d: feat(03-02): implement query_table handler (Slice A — light-column projection) — FOUND
- fcea906: test(03-02): fill query_table happy / error / side-channel test bodies — FOUND
- ed4d874: test(03-02): fill canary corpus for filter-value safety (QUERY-09 / INJ-05) — FOUND

## Self-Check: PASSED
