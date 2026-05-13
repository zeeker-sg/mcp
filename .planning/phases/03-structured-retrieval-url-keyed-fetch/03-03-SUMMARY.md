---
phase: 03-structured-retrieval-url-keyed-fetch
plan: 03
subsystem: retrieval-slice-b
tags: [mvp-slice-b, cursor, heavy-columns, retrieved-content, qhash, phase3]
requires:
  - "src/mcp_zeeker/core/visibility.py (_resolve_table, _visible_columns, raise_unknown_column — Plan 03-01)"
  - "src/mcp_zeeker/core/filter_compiler.py (Filter, compile_filters — Plan 03-01)"
  - "src/mcp_zeeker/core/datasette_client.py (DatasetteClient.get_table_rows, UpstreamCallFailed — Plan 03-01)"
  - "src/mcp_zeeker/tools/retrieval.query_table Slice A handler (Plan 03-02) — Plan 03-03 replaces its two scope-boundary raises"
  - "src/mcp_zeeker/config.py (HEAVY_COLUMNS, LIGHT_COLUMNS, COLUMN_TYPES, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT)"
provides:
  - "mcp_zeeker.core.cursor (canonical_shape_str, encode_cursor, decode_cursor — D3-03 qhash module)"
  - "core/envelope.Pagination extended with next_cursor: str | None = None and truncated: bool = False (D3-12)"
  - "tools/retrieval.query_table — feature-complete for QUERY-01..10 (Plan 03-02 + 03-03 together)"
  - "13 GREEN parametrized handler-level tests for all FilterOp variants (QUERY-04 reinforced at handler level)"
  - "5 new GREEN tests in test_query_table.py (heavy_columns, default-no-retrieved_content, light-only-no-retrieved_content, cursor_walk_round_trip, truncated_passthrough)"
  - "3 new GREEN tests in test_query_table_errors.py (D3-03 invalid_cursor — shape mismatch, malformed, no-upstream short-circuit)"
  - "2 new GREEN tests in test_cursor.py (padding-safe, tilde-encoded real-cursor)"
affects:
  - "src/mcp_zeeker/tools/retrieval.py (handler body rewritten; module docstring updated; both scope-boundary markers gone)"
  - "tests/tools/test_query_table.py (Slice A tests preserved; 22 new tests added incl. 13-op parametrize)"
  - "tests/tools/test_query_table_errors.py (Slice A cursor scope-boundary test REPLACED by 3 D3-03 tests)"
  - "tests/test_cursor.py (5 RED stubs turned GREEN + 2 new tests added)"
tech-stack:
  added: []
  patterns:
    - "qhash cursor (BLAKE2b digest_size=8 + url-safe base64, '|' separator) — pure stdlib, no new runtime deps (NFR-04)"
    - "Cursor decode short-circuits before upstream table-rows fetch (T-03-11 + T-03-12 — invalid_cursor never burns a Datasette query)"
    - "Heavy/light column partition in handler; reshape step is the load-bearing isolation (T-03-13 — heavy text never leaks at top level)"
    - "Snapshot assertion `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` codified as D3-19 — foundation for Phase 8 TEST-03"
    - "Cursor errors are FIXED LITERALS — `f-string` interpolation of cursor contents is grep-banned (T-03-12 / INJ-05)"
key-files:
  created:
    - "src/mcp_zeeker/core/cursor.py"
  modified:
    - "src/mcp_zeeker/core/envelope.py"
    - "src/mcp_zeeker/tools/retrieval.py"
    - "tests/test_cursor.py"
    - "tests/tools/test_query_table.py"
    - "tests/tools/test_query_table_errors.py"
decisions:
  - "Cursor decode placement: AFTER per-field unknown_column checks (so a cursor reused with an arbitrary unknown column name still surfaces unknown_column, not invalid_cursor — better UX) and BEFORE the table-rows upstream call (so invalid_cursor never burns a Datasette query — the T-03-11 / T-03-12 contract). The plan said 'after step 7 (build types_for_table)' which is functionally equivalent for the cursor-decode short-circuit invariant since both `_resolve_table` and `_visible_columns` already issued their upstream calls by then."
  - "Canonical shape uses normalized_filters (post-Pydantic-validation) — model_dump() inside canonical_shape_str ensures dict-vs-Filter callers produce the same hash. Plan 03-02's Filter normalization at handler entry is what makes this work cleanly."
  - "encode_cursor only emits a non-None cursor when upstream `next` is truthy; an empty-string `next` is treated as 'no more pages' (matches Datasette behavior where `next` is null for the last page)."
  - "truncated propagates from upstream verbatim (Phase 3 honest surfacing); Phase 5 FRAG-04 wires the consumer side. The Pagination field default of False keeps backward-compat for tools that don't surface truncation."
  - "13-op end-to-end test uses pdpc.enforcement_decisions with `_zeeker_schemas` typing penalty_amount as INTEGER (no entry in config.COLUMN_TYPES) — exercises the upstream-wins merge path AND the numeric-coercion branch of compile_filters."
metrics:
  duration_min: ~8
  completed_date: 2026-05-14
  tasks: 2
  commits: 4
  files_created: 1
  files_modified: 5
---

# Phase 3 Plan 03: Slice B — heavy_columns + qhash cursor Summary

**One-liner:** Ships Slice B on top of Slice A — heavy-column opt-in via the
`retrieved_content` key (D3-05) + qhash cursor pagination (D3-03). The two
Plan 03-02 scope-boundary raises are removed; `query_table` is now feature-
complete for QUERY-01..10. All 13 filter operators exercise end-to-end through
the handler, and invalid cursors short-circuit before any upstream call.

## What shipped

### `src/mcp_zeeker/core/cursor.py` (D3-03 — new module)

Pure-function qhash cursor module. Public API:

```python
def canonical_shape_str(
    database: str, table: str, sort: str | None,
    filters: list[Any] | None, columns: list[str] | None,
) -> str

def encode_cursor(canonical_shape_str_value: str, datasette_next: str) -> str

def decode_cursor(cursor: str, canonical_shape_str_value: str) -> str
    # → datasette_next (unwrapped) OR raises ToolError(invalid_cursor: ...)
```

Algorithm:
- `canonical_shape_str` → JSON with `sort_keys=True`, filters sorted by
  `(column, op)`, columns sorted or None. Plain dicts and `Filter.model_dump()`
  produce identical output.
- `encode_cursor` → `base64.urlsafe_b64encode(b"{digest}|{next}").rstrip(b"=")`
  where `digest = hashlib.blake2b(..., digest_size=8).hexdigest()` (16 hex chars).
- `decode_cursor` → re-pads `=`, base64-decodes, splits on first `|`, verifies
  the digest. Two fixed-literal failure messages:
    - `"invalid_cursor: cursor is malformed"` (any decode/split failure; uses
      `raise ... from None` to suppress exception chain — T-03-12 / INJ-05)
    - `"invalid_cursor: cursor does not match current request shape"` (digest
      mismatch)

NO IO. NO new runtime deps — stdlib (`base64` / `hashlib` / `json`) +
`fastmcp.exceptions.ToolError`. NFR-04 preserved.

### `src/mcp_zeeker/core/envelope.py` (D3-12 — Pagination extension)

```python
class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int | None = None        # Phase 1 — kept for forward-compat
    next_offset: int | None = None  # Phase 1 — kept for forward-compat
    next_cursor: str | None = None  # D3-12 — produced by encode_cursor
    truncated: bool = False         # D3-12 — Phase 5 FRAG-04 wires consumer
```

`Envelope.for_rows(...)` factory signature unchanged. `extra="forbid"`
preserved — extra fields still rejected.

### `src/mcp_zeeker/tools/retrieval.py` (D3-03 / D3-05 / D3-19)

Both Plan 03-02 scope-boundary raises REMOVED. New handler body (validation
order in module docstring):

```python
# Step 9 — canonical shape + cursor decode (before upstream table-rows call)
canonical_shape = canonical_shape_str(database, table, sort, normalized_filters, columns)
datasette_next: str | None = None
if cursor is not None:
    datasette_next = decode_cursor(cursor, canonical_shape)
# ...
# Step 10 — upstream params include heavy column names
upstream_cols = [*light_to_emit, *heavy_to_emit]
if datasette_next is not None:
    params.append(("_next", datasette_next))
# ...
# Step 13 — reshape: heavy values re-keyed under retrieved_content
for upstream_row in result.get("rows", []) or []:
    row = {c: upstream_row[c] for c in light_to_emit if c in upstream_row}
    if heavy_to_emit:
        row["retrieved_content"] = {
            c: upstream_row[c] for c in heavy_to_emit if c in upstream_row
        }
    reshaped.append(row)
# Step 14 — encode next cursor; surface truncation honestly
next_cursor = encode_cursor(canonical_shape, result["next"]) if result.get("next") else None
truncated = bool(result.get("truncated", False))
```

D3-05 contract: `retrieved_content` key appears ONLY when `heavy_to_emit` is
non-empty. Default-light responses (or explicit columns with no heavy member)
MUST NOT carry the key — codified by snapshot tests.

### Tests

**`tests/test_cursor.py`** (Plan 03-01 RED stubs → 7 GREEN tests):
- `test_round_trip` — encode/decode round-trips correctly
- `test_shape_mismatch_raises_invalid_cursor` — different shape → invalid_cursor
- `test_malformed_cursor_raises` — non-base64 token → invalid_cursor (malformed)
- `test_empty_datasette_next_round_trips` — last-page sentinel (`""`) survives
- `test_filters_sorted_canonically` — filter list order doesn't affect digest
- **NEW** `test_cursor_padding_safe` — encode strips `=` padding; decode re-pads
- **NEW** `test_tilde_encoded_datasette_cursor` — real upstream-style
  `2026-05-13T00~3A01~3A00,46f0249efaf2efa64b334177d1285849` round-trips
  intact (`|` separator never collides with Datasette's tilde-encoded chars)

**`tests/tools/test_query_table.py`** (Slice A preserved; 22 new tests added):
- `test_heavy_columns_appear_under_retrieved_content` (QUERY-03 / D3-05 / D3-19)
  — snapshot assertion `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` and
  `set(row['retrieved_content'].keys()) ⊆ HEAVY_COLUMNS`.
- `test_default_response_has_no_retrieved_content_key` (D3-05)
- `test_light_only_columns_omit_retrieved_content` (D3-05)
- `test_cursor_walk_round_trip` (QUERY-08) — page-1 returns next_cursor;
  page-2 call carries `_next=PAGE2_TOKEN` to upstream; page-2 terminates with
  `next_cursor=None`
- `test_truncated_passed_through` (D3-12)
- `test_thirteen_ops_end_to_end` — parametrized over all 13 FilterOp variants
  (QUERY-04 handler-level coverage); uses `_zeeker_schemas` to type
  `penalty_amount` as INTEGER for numeric ops, and the `in/notin` cases
  assert the comma-joined upstream form `title__in=A,B`.

**`tests/tools/test_query_table_errors.py`** (Slice A cursor scope-boundary
test REPLACED):
- ~~`test_cursor_not_supported_on_slice_a`~~ → REMOVED
- **NEW** `test_invalid_cursor_on_shape_mismatch` — encode under sort=None,
  decode under sort='decision_date' → invalid_cursor
- **NEW** `test_invalid_cursor_on_malformed` — non-base64 token → invalid_cursor
- **NEW** `test_invalid_cursor_short_circuits_before_upstream` — asserts ZERO
  requests to `/enforcement_decisions.json` after invalid_cursor failure
  (proves cursor decode runs before the upstream table-rows fetch)

## D-IDs implemented

- **D3-03**: qhash cursor module — canonical_shape_str / encode_cursor / decode_cursor; BLAKE2b 8-byte digest + url-safe base64 + `|` separator
- **D3-05**: retrieved_content layout — heavy columns nest under a single key,
  absent when no heavy column requested
- **D3-12**: Pagination extended with `next_cursor` and `truncated`
- **D3-19**: inline snapshot tests — `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` and
  `set(row['retrieved_content'].keys()) ⊆ HEAVY_COLUMNS` (foundation for
  Phase 8 TEST-03)

## REQ-IDs satisfied

- **QUERY-03**: heavy-column opt-in via `retrieved_content` (D3-05)
- **QUERY-04**: 13 ops end-to-end at handler level (compiler unit tests from
  Plan 03-01 complemented by the handler-level parametrize)
- **QUERY-08**: qhash cursor — produces next_cursor on multi-page boundary,
  consumes it correctly on follow-up call, rejects with invalid_cursor on
  shape change BEFORE upstream call

## query_table Slice B status

`query_table` is now feature-complete for **QUERY-01..10** (Plan 03-02 + 03-03
together). The handler shape is locked for Phase 3 — Plan 03-04 ships `fetch`
independently and does not modify `query_table`.

| REQ | Plan 03-02 | Plan 03-03 |
|------|-----------|-----------|
| QUERY-01 (filter/sort/limit/columns) | ✓ | — |
| QUERY-02 (default light) | ✓ | — |
| QUERY-03 (heavy via retrieved_content) | scope-boundary | ✓ |
| QUERY-04 (13 ops compiler) | ✓ at compiler | ✓ at handler |
| QUERY-05 (unknown_column nonexistent) | ✓ | — |
| QUERY-06 (unknown_column hidden) | ✓ | — |
| QUERY-07 (limit default 50, max 200) | ✓ | — |
| QUERY-08 (qhash cursor) | scope-boundary | ✓ |
| QUERY-09 (canary corpus) | ✓ | — |
| QUERY-10 (case-insensitive LIKE) | ✓ | — |

## Scope-boundary cleanup — grep-clean

```
$ grep -c "Plan 03-03 will replace" src/mcp_zeeker/tools/retrieval.py
0
$ grep -c "retrieved_content" src/mcp_zeeker/tools/retrieval.py
10
$ grep -c "from mcp_zeeker.core.cursor import" src/mcp_zeeker/tools/retrieval.py
1
```

Both Plan 03-02 scope-boundary raises (cursor + heavy-projection) and their
`# Plan 03-03 will replace` markers are gone.

## Files for Plan 03-04 to modify

`query_table` is DONE — Plan 03-04 must NOT touch the handler. Plan 03-04 scope:

- `src/mcp_zeeker/tools/retrieval.py` — replace ONLY the `fetch()`
  `NotImplementedError` stub (lines near end of file). Plan 03-04 may import
  the same `canonical_shape_str` / `encode_cursor` if it adopts cursor
  pagination for fetch (not currently planned — fetch is URL-keyed single-row).
- `tests/tools/test_fetch.py` — currently RED-by-design; will go GREEN once
  fetch ships.
- NEW `tests/manual/PHASE3-CLIENT-VERIFY.md` — client-verify checklist for the
  Phase 3 retrieval suite (per the plan output spec).

## conftest.py status

**NOT touched in Plan 03-03** — per Plan 03-01's consolidation rule. All
Phase 3 test fixtures (`_table_url`, `TABLE_ROWS_STUB`, `stub_table_rows`)
live in conftest.py from Plan 03-01.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Auto-fix blocking issue] Ruff E501 long lines in 13-op parametrize cases**

- **Found during:** Task 2 verification — `ruff check` flagged 8 long-line
  errors in the `_THIRTEEN_OPS_CASES` tuple at the end of test_query_table.py,
  plus one long line in the retrieved_content snapshot extraction.
- **Issue:** Each parametrize tuple `(name, filter_dict, expected_key,
  expected_value)` ran past 100 chars because the filter dict contains
  `{"column": "...", "op": "...", "value": ...}`. The plan-level verification
  step `ruff format --check` enforces line length on this repo.
- **Fix:** Ran `ruff format` on the touched files. The formatter wrapped each
  tuple across multiple lines automatically. For the snapshot extraction, I
  hoisted `set(rc.keys()) - config.HEAVY_COLUMNS` into a `leaked` local before
  the f-string. No behavior change.
- **Files modified:** `tests/tools/test_query_table.py`
- **Commit:** `b3d5492` (Task 2)

**2. [Rule 2 — Auto-add missing critical functionality] `from None` exception suppression in decode_cursor**

- **Found during:** Implementing `decode_cursor` while reviewing the T-03-12
  threat-model row.
- **Issue:** The plan's `<behavior>` showed `except Exception: raise
  ToolError("invalid_cursor: cursor is malformed")` — a bare `raise` would
  leave `__context__` set to the original `ValueError`/`UnicodeDecodeError`/
  whatever, which often echoes the offending bytes (e.g. "invalid padding"
  with the raw input). The T-03-12 row says "Neither echoes cursor contents",
  but a `caplog` listener or default Python traceback formatter would still
  surface the chain.
- **Fix:** Added `from None` to the `raise ToolError(...)` inside the except
  block — mirror of the same discipline used in filter_compiler.py's numeric-
  coercion failure path (Plan 03-01 Deviation #2).
- **Files modified:** `src/mcp_zeeker/core/cursor.py`
- **Commit:** `7c641bc` (Task 1)

**3. [Rule 3 — Auto-fix blocking issue] Pre-existing I001 ruff import-order errors in Plan 03-01 RED stub**

- **Found during:** Task 1 ruff verification — `test_cursor.py` had 2 I001
  errors on the function-body imports.
- **Issue:** Plan 03-01 introduced function-body imports in `test_cursor.py`
  (the Wave 0 stub idiom) without `ruff` ordering them. Plan 03-03's
  `ruff check` step on the same file surfaced them. The Wave 0 stub left
  these as a known quirk because they collected fine before the cursor
  module existed.
- **Fix:** Ran `ruff check --fix` — auto-reorganized the imports inside the
  two affected test bodies. No behavior change (the functions still import
  `fastmcp.exceptions.ToolError` + `mcp_zeeker.core.cursor.*` lazily).
- **Files modified:** `tests/test_cursor.py`
- **Commit:** `7c641bc` (Task 1)

### Out-of-scope items (deferred — not introduced by this plan)

- `tests/tools/test_fetch.py` remains RED — Plan 03-04 scope. 6 failed, 3
  errors in the full regression.
- `tests/tools/test_discovery_side_channel.py` carries 7 pre-existing E501
  warnings from Plan 03-01's note — not touched here.
- `src/mcp_zeeker/config.py` has 12 pre-existing E501 warnings in
  `LIGHT_COLUMNS` / `TABLE_DESCRIPTIONS` (pre-existing).

### cwd-drift recovery during early Task 1

- **Symptom:** First two Edit/Write calls against `tests/test_cursor.py`
  using the conventional relative-style absolute path under
  `/Users/houfu/Projects/zeeker-mcp/tests/...` were silently no-ops (Read
  reported updated content, but `git status` reported clean tree and `wc`
  confirmed the old byte count).
- **Cause:** The agent's CWD-tied path resolution was pointing at the
  parent-repo path rather than the worktree, even though Bash's `pwd`
  reported the worktree root.
- **Recovery:** Switched all subsequent Edit/Write calls to use the
  fully-qualified worktree path
  (`/Users/houfu/Projects/zeeker-mcp/.claude/worktrees/agent-aaec935b351ca278e/...`).
  All edits succeeded once the prefix was explicit.
- **Outcome:** No data lost; first incorrect edit attempts produced no
  artifacts. All commits live in the agent's branch.

## Verification evidence

| Check | Result |
|-------|--------|
| `uv run pytest tests/test_cursor.py tests/tools/test_query_table.py tests/tools/test_query_table_errors.py tests/tools/test_retrieval_side_channel.py tests/test_filter_value_safety.py tests/test_envelope_contract.py tests/test_tool_trailer.py -x -q` | 66 / 66 passed |
| `uv run pytest --ignore=tests/tools/test_fetch.py -q` | 158 passed, 2 skipped |
| `uv run pytest -q` | 158 passed, 6 failed (`test_fetch.py` RED-by-design — Plan 03-04 scope), 6 errors (also `test_fetch.py`), 2 skipped |
| `grep -c "Plan 03-03 will replace" src/mcp_zeeker/tools/retrieval.py` | 0 |
| `grep -c "retrieved_content" src/mcp_zeeker/tools/retrieval.py` | 10 |
| `grep -c "from mcp_zeeker.core.cursor import" src/mcp_zeeker/tools/retrieval.py` | 1 |
| `grep -n 'f"invalid_cursor:' src/mcp_zeeker/core/cursor.py \| grep -v '^#'` | 0 (no value interpolation — T-03-12 / INJ-05) |
| `pytest tests/tools/test_query_table.py::test_thirteen_ops_end_to_end --collect-only -q \| grep -c "test_thirteen_ops_end_to_end"` | 13 |
| `grep -c "HEAVY_COLUMNS == set" tests/tools/test_query_table.py` | 2 (D3-19 snapshot — default-light + heavy-projection paths) |
| ruff check on all 6 touched files | All checks passed |
| ruff format --check on all 6 touched files | 6 files already formatted |
| Pagination `extra="forbid"` preserved | OK (asserted in inline check + `tests/test_envelope_contract.py`) |
| No new runtime deps (NFR-04) | OK — stdlib only in cursor.py |

## Threat-model coverage

| Threat | Mitigation evidence |
|--------|---------------------|
| T-03-11 (Tampering — qhash cursor) | `decode_cursor` raises before any upstream call on digest mismatch. The qhash is NOT a security primitive — Datasette's parameterized SQL still bounds attacker reach. `test_invalid_cursor_short_circuits_before_upstream` asserts no `/enforcement_decisions.json` request. |
| T-03-12 (Info Disclosure — cursor error messages) | `grep -n 'f"invalid_cursor:' src/mcp_zeeker/core/cursor.py` returns 0. Two FIXED literal messages only. `from None` suppresses `__cause__` chain so the raw base64 input cannot leak via traceback formatters. |
| T-03-13 (Info Disclosure — heavy column leakage) | Row-reshape partitions on `config.HEAVY_COLUMNS` before emission. D3-19 snapshot test enforces `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` (top-level) and `set(retrieved_content.keys()) ⊆ HEAVY_COLUMNS` (nested). |
| T-03-14 (DoS — cursor walk page chains) | Accepted per threat register; Phase 7 rate limiter is the primary defense. Plan 03-03 does not bound page-walk count per request. |

## Self-Check

### Files claimed created
- `src/mcp_zeeker/core/cursor.py` — FOUND at HEAD

### Files claimed modified
- `src/mcp_zeeker/core/envelope.py` — FOUND at HEAD (Pagination extension)
- `src/mcp_zeeker/tools/retrieval.py` — FOUND at HEAD (handler body rewritten)
- `tests/test_cursor.py` — FOUND at HEAD (7 tests, all green)
- `tests/tools/test_query_table.py` — FOUND at HEAD (22 new tests added)
- `tests/tools/test_query_table_errors.py` — FOUND at HEAD (Slice A cursor test replaced)

### Commits
- `064dd20` — test(03-03): extend test_cursor.py with padding-safe + tilde-encoded round-trip tests — FOUND
- `7c641bc` — feat(03-03): ship core/cursor.py + extend Pagination — FOUND
- `2a16f60` — test(03-03): add RED tests for retrieved_content + cursor walk + 13 ops — FOUND
- `b3d5492` — feat(03-03): wire retrieved_content + qhash cursor — query_table feature-complete — FOUND

## Self-Check: PASSED
