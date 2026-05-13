---
phase: 03-structured-retrieval-url-keyed-fetch
plan: 01
subsystem: retrieval-foundation
tags: [foundation, filter-compiler, visibility, wave-0-stubs, phase3]
requires:
  - "src/mcp_zeeker/core/datasette_client.py (existing _request_with_retry, get_database, TableSummary)"
  - "src/mcp_zeeker/core/config_lookup.py (hidden_columns_for)"
  - "src/mcp_zeeker/config.py (ALLOWED_DATABASES, HIDDEN_COLUMNS, HIDDEN_TABLES, URL_COLUMNS, COLUMN_TYPES)"
provides:
  - "mcp_zeeker.core.visibility (5 raise_unknown_* helpers + _visible_tables/_visible_columns/_resolve_table)"
  - "mcp_zeeker.core.filter_compiler (Filter pydantic model + 13-op FilterOp + compile_filters)"
  - "mcp_zeeker.core.datasette_client.DatasetteClient.get_table_rows (table-row fetch with _shape=objects)"
  - "config.DEFAULT_QUERY_LIMIT (50), config.MAX_QUERY_LIMIT (200), config.HEAVY_COLUMNS (frozenset of 6)"
  - "tests/conftest.py: _table_url helper + TABLE_ROWS_STUB constant + stub_table_rows fixture"
  - "tests/test_filter_compiler.py: 16 GREEN unit tests covering all 13 FilterOp variants"
  - "6 RED-by-design Wave 0 stub test files (collect-only OK; will go green as Plans 03-02/03/04 ship)"
affects:
  - "src/mcp_zeeker/tools/discovery.py (re-exports + removed local raise_unknown_* helpers)"
  - "tests/tools/test_discovery_side_channel.py (patch target updated to core.visibility — Rule 1 deviation)"
tech-stack:
  added: []
  patterns:
    - "Single-call-site security boundary (config_lookup.py mirror): core/filter_compiler.py owns all filter→URL translation"
    - "Sole-emission raise_unknown_* helpers: one function per error code, no ad-hoc raises in handlers"
    - "Counter-patch identity for column visibility (mirror of DISC-05 table-level pattern)"
    - "Function-body imports in Wave 0 stub tests so collection succeeds before the module-under-test exists"
key-files:
  created:
    - "src/mcp_zeeker/core/visibility.py"
    - "src/mcp_zeeker/core/filter_compiler.py"
    - "tests/test_filter_compiler.py"
    - "tests/test_cursor.py"
    - "tests/test_filter_value_safety.py"
    - "tests/tools/test_query_table.py"
    - "tests/tools/test_query_table_errors.py"
    - "tests/tools/test_retrieval_side_channel.py"
    - "tests/tools/test_fetch.py"
  modified:
    - "src/mcp_zeeker/config.py"
    - "src/mcp_zeeker/core/datasette_client.py"
    - "src/mcp_zeeker/tools/discovery.py"
    - "tests/conftest.py"
    - "tests/tools/test_discovery_side_channel.py"
decisions:
  - "Phase 2 side-channel test patch target relocated from mcp_zeeker.tools.discovery.raise_unknown_table to mcp_zeeker.core.visibility.raise_unknown_table — the call-site moved with _resolve_table; the re-export is a name binding that does NOT redirect call-site lookups (Python attribute resolution semantics)."
  - "Numeric coercion failures suppress the original ValueError chain via `from None` to prevent value text leaking into the exception __cause__ (T-03-01 / INJ-05)."
  - "All conftest.py Phase 3 extensions consolidated into this plan — Plans 03-02 / 03-03 / 03-04 MUST NOT edit tests/conftest.py (Pitfall 4)."
metrics:
  duration_min: ~30
  completed_date: 2026-05-13
  tasks: 3
  commits: 3
  files_created: 9
  files_modified: 5
---

# Phase 3 Plan 1: Foundation — Visibility, Filter Compiler, Wave 0 Stubs Summary

**One-liner:** Lays the Phase 3 retrieval foundation — moves visibility helpers to `core/visibility.py`, adds the pure-function 13-op `compile_filters`, extends `DatasetteClient` with `get_table_rows`, and ships all Wave 0 test stubs plus the consolidated `conftest.py` extension in a single plan to avoid the Phase 2 conftest merge pitfall.

## What shipped

### `src/mcp_zeeker/config.py` (D3-04, D3-17)

Three new constants appended after `LOG_FIELDS`:

```python
DEFAULT_QUERY_LIMIT: int = 50
MAX_QUERY_LIMIT: int = 200
HEAVY_COLUMNS: frozenset[str] = frozenset(
    {"content_text", "full_text", "html_raw",
     "footnote_text", "figure_descriptions", "text"}
)
```

### `src/mcp_zeeker/core/visibility.py` (D3-06, D3-07 — new module)

Hosts the table-level helpers extracted from `tools/discovery.py` plus the new
column-level mirror. Public API:

```python
# Sole-emission error helpers
def raise_unknown_database(database: str) -> None
def raise_unknown_table(database: str, table: str) -> None
def raise_unknown_column(database: str, table: str, column: str) -> None
def raise_unsupported_table_for_fetch(database: str, table: str) -> None
def raise_not_found(database: str, table: str) -> None

# Visibility gates (single source of truth)
async def _visible_tables(database: str) -> set[str]
async def _resolve_table(database: str, table: str) -> None
async def _visible_columns(database: str, table: str) -> set[str]
```

`_visible_columns` reads via `DatasetteClient.current().get_database(database)`
and subtracts `hidden_columns_for(database, table)` — mirrors the
`_visible_tables` shape, no separate `HIDDEN_COLUMNS` pre-check (Pitfall 1).

### `src/mcp_zeeker/core/filter_compiler.py` (D3-01, D3-02, D3-10 — new module)

Pure-function security boundary. Public API:

```python
FilterOp = Literal[
    "exact", "not", "contains", "startswith", "endswith",
    "gt", "gte", "lt", "lte",
    "in", "notin",
    "isnull", "notnull",
]  # exactly 13 strings

class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    op: FilterOp
    value: Any = None

def compile_filters(
    filters: list[Filter],
    *,
    visible_columns: set[str],
    column_types: dict[str, str],
) -> list[tuple[str, str]]
```

Op-dispatch (verified against RESEARCH.md live probes):

- `exact`/`not`/`contains`/`startswith`/`endswith` → `("{col}__{op}", str(value))`
- `gt`/`gte`/`lt`/`lte` → numeric coerce per `column_types`; failure → generic `invalid_filter_op`
- `in`/`notin` → comma-joined: `("{col}__in", "a,b,c")` (verified URL form)
- `isnull`/`notnull` → `("{col}__isnull", "1")` (value ignored)

Every `invalid_filter_op` message is a FIXED LITERAL — no f-string interpolation
of user-supplied values (T-03-01 / D3-09 / INJ-05). Numeric coercion failures
use `raise ... from None` to drop the original ValueError text from
`__cause__`.

### `src/mcp_zeeker/core/datasette_client.py` (D3-14)

New async method `get_table_rows(database, table, params) -> dict` that prepends
`("_shape", "objects")` to the params list and routes through the existing
`_request_with_retry` (retry-once-with-jitter on 502/503, immediate
`UpstreamCallFailed` on 504/transport error).

### `src/mcp_zeeker/tools/discovery.py`

Removed local definitions of `raise_unknown_database`, `raise_unknown_table`,
`_visible_tables`, `_resolve_table` (they live in `core/visibility.py` now).
Re-exports at the top of the module preserve the existing
`from mcp_zeeker.tools.discovery import raise_unknown_table` imports — the
re-export is a name BINDING (same function object), but call-site name lookup
inside `_resolve_table` happens in `core.visibility` (see Deviations below).

### `tests/conftest.py` (D3-06 conftest consolidation — Pitfall 4 prevention)

- `_table_url(database, table) -> str` helper (mirrors `_db_url`)
- `TABLE_ROWS_STUB` constant — well-formed `_shape=objects` payload
- `stub_table_rows(httpx_mock)` fixture — thin facade for retrieval tests

**Plans 03-02 / 03-03 / 03-04 MUST NOT edit `tests/conftest.py`.**

### Test files

- `tests/test_filter_compiler.py` — **16 GREEN tests** exercising every one of
  the 13 FilterOp variants, numeric coercion happy + failure paths, comma-join
  in/notin, anti-nesting rejection (D3-10 / T-03-03), unknown_column gate,
  canary non-echo assertion (INJ-05).
- `tests/test_cursor.py` — D3-03 stubs (RED until Plan 03-03)
- `tests/test_filter_value_safety.py` — D3-09 5-canary corpus (RED until 03-02)
- `tests/tools/test_query_table.py` — QUERY-01/02/03/07 stubs (RED until 03-02)
- `tests/tools/test_query_table_errors.py` — QUERY-05/06 stubs (RED until 03-02)
- `tests/tools/test_retrieval_side_channel.py` — D3-07 counter-patch identity
  tests (RED until 03-02)
- `tests/tools/test_fetch.py` — FETCH-01/02/03/04/05 stubs (RED until 03-04)

All stub test files use **function-body imports** of their target module so
`pytest --collect-only` succeeds before the implementation ships.

## D-IDs implemented

- **D3-01**: `Filter` pydantic model + `compile_filters` pure function
- **D3-02**: 13-op `FilterOp` Literal + verified URL forms (RESEARCH.md __in
  comma-join decision honored)
- **D3-04**: `config.HEAVY_COLUMNS` frozenset (6 column names)
- **D3-06**: visibility helpers moved to `core/visibility.py`; column-level
  `_visible_columns` mirrors the `_visible_tables` shape
- **D3-07**: `raise_unknown_column` single emission point — counter-patch test
  hook ready for Plan 03-02 to consume
- **D3-10**: numeric coercion + anti-nesting in `compile_filters` (T-03-03)
- **D3-14**: `DatasetteClient.get_table_rows` method shape — handler in Plan
  03-04 will consume
- **D3-17**: `DEFAULT_QUERY_LIMIT` (50), `MAX_QUERY_LIMIT` (200)
- **D3-18 stubs**: all 7 Wave 0 test stub files plus conftest extension

## Public API for downstream plans

**Plan 03-02 (query_table)** consumes:
```python
from mcp_zeeker.core.visibility import (
    _resolve_table, _visible_columns, raise_unknown_column,
)
from mcp_zeeker.core.filter_compiler import Filter, compile_filters
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker import config
# Reads: config.HEAVY_COLUMNS, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT
```

**Plan 03-03 (cursor + envelope.Pagination)** consumes:
- nothing new from this plan (cursor module is a pure addition)

**Plan 03-04 (fetch)** consumes:
```python
from mcp_zeeker.core.visibility import (
    _resolve_table, _visible_columns,
    raise_unsupported_table_for_fetch, raise_not_found,
)
from mcp_zeeker.core.datasette_client import DatasetteClient
# Calls DatasetteClient.current().get_table_rows(db, table, [("source_url__exact", url)])
```

## Tests RED by design (will go GREEN as downstream plans ship)

| File | Goes GREEN with |
|------|-----------------|
| `tests/test_cursor.py` | Plan 03-03 ships `mcp_zeeker.core.cursor` |
| `tests/test_filter_value_safety.py` | Plan 03-02 ships `query_table` |
| `tests/tools/test_query_table.py` | Plan 03-02 ships `query_table` |
| `tests/tools/test_query_table_errors.py` | Plan 03-02 ships `query_table` + Plan 03-03 ships cursor |
| `tests/tools/test_retrieval_side_channel.py` | Plan 03-02 ships `query_table` |
| `tests/tools/test_fetch.py` | Plan 03-04 ships `fetch` |

All RED stubs collect successfully (`--collect-only` exit 0) — verified.

## conftest.py status

**TOUCHED IN PLAN 03-01 ONLY — Plans 03-02 / 03-03 / 03-04 MUST NOT EDIT.**

Phase 2 LEARNINGS confirmed that two plans editing `tests/conftest.py` in the
same wave produces a merge conflict. The Phase 3 retrieval-fixture surface
(`_table_url`, `TABLE_ROWS_STUB`, `stub_table_rows`) is wholly contained in
this commit; downstream plans only consume it.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Phase 2 side-channel test patch target relocated**

- **Found during:** Task 1 (running `uv run pytest tests/tools/test_discovery_side_channel.py` after the move)
- **Issue:** The plan's `<behavior>` block stipulated "the existing
  `tests/tools/test_discovery_side_channel.py` continues to pass UNCHANGED"
  AND the `<acceptance_criteria>` asserted the counter-patch identity holds.
  Both held in isolation, but the Phase 2 test patches
  `mcp_zeeker.tools.discovery.raise_unknown_table` and expects that to
  intercept calls from `_resolve_table`. After moving `_resolve_table` to
  `core.visibility`, the function's call-site name lookup happens against
  `core.visibility.raise_unknown_table`, not the re-exported name. Python's
  attribute resolution semantics mean the re-export is a binding — patching
  the *name* in `tools.discovery` does not redirect the call-site lookup in
  `core.visibility`. The plan's two claims are not simultaneously achievable
  given Python's mock-patching semantics.
- **Fix:** Updated the patch target in `tests/tools/test_discovery_side_channel.py`
  from `"mcp_zeeker.tools.discovery.raise_unknown_table"` to
  `"mcp_zeeker.core.visibility.raise_unknown_table"`. The test still asserts
  code-path identity (counter == 2) and identical message format — only the
  patch site changed. The acceptance-criterion identity assertion
  (`v is raise_unknown_table`) still holds.
- **Files modified:** `tests/tools/test_discovery_side_channel.py`
- **Commit:** `ee42d84` (Task 1)

**2. [Rule 2 — Auto-add missing critical functionality] Suppress exception chain in numeric coercion failure**

- **Found during:** Task 2 ruff `B904` complaint
- **Issue:** A bare `raise ToolError(...)` inside an `except (TypeError,
  ValueError):` block implicitly sets `__context__` to the original
  `ValueError`. The `ValueError` message often echoes the offending value
  (e.g. `"invalid literal for int(): 'abc'"`). The plan's T-03-01 guarantee
  is "no f-string interpolation of user values in ToolError messages" — but
  the original exception leaks through the `__cause__`/`__context__` chain
  if propagated by an outer logger or error formatter (D3-09 / INJ-05).
- **Fix:** Use `raise ToolError(...) from None` to explicitly suppress the
  chain. Verified manually that `e.__cause__` is `None` and `str(e)` does
  not contain the offending value.
- **Files modified:** `src/mcp_zeeker/core/filter_compiler.py`
- **Commit:** `20b324c` (Task 2)

### Out-of-scope items (deferred — not introduced by this plan)

- `tests/tools/test_discovery_side_channel.py` carries 7 pre-existing E501
  "line too long" warnings and a pre-existing format-check failure. My touch
  (the patch-target update) is a single-line semantic change; running
  `ruff format` on the file produces a large cosmetic diff for unrelated
  long-line dict literals. Per the SCOPE BOUNDARY rule, I did not include
  those reformat changes. Suggested separate cleanup pass.
- `src/mcp_zeeker/config.py` has 12 pre-existing E501 warnings in
  `LIGHT_COLUMNS` and `TABLE_DESCRIPTIONS`. My Phase 3 additions (`HEAVY_COLUMNS`,
  `DEFAULT_QUERY_LIMIT`, `MAX_QUERY_LIMIT`) introduce no new E501.

## Verification evidence

| Check | Result |
|-------|--------|
| `uv run python -c "from mcp_zeeker.core.visibility import _resolve_table, _visible_tables, _visible_columns, raise_unknown_database, raise_unknown_table, raise_unknown_column, raise_unsupported_table_for_fetch, raise_not_found"` | ✅ OK |
| `assert config.HEAVY_COLUMNS == frozenset({...6 names...})` | ✅ OK |
| `assert config.DEFAULT_QUERY_LIMIT == 50 and config.MAX_QUERY_LIMIT == 200` | ✅ OK |
| `from mcp_zeeker.tools.discovery import raise_unknown_table as rt2; assert rt2 is raise_unknown_table` (identity) | ✅ OK |
| `uv run pytest tests/test_filter_compiler.py tests/tools/test_discovery_side_channel.py tests/tools/test_describe_table.py tests/tools/test_list_tables.py -x -q` | ✅ 36 passed |
| `uv run pytest tests/test_cursor.py tests/test_filter_value_safety.py tests/tools/test_query_table.py tests/tools/test_query_table_errors.py tests/tools/test_retrieval_side_channel.py tests/tools/test_fetch.py --collect-only -q` | ✅ 33 tests collected |
| `uv run pytest --ignore=tests/test_cursor.py --ignore=tests/test_filter_value_safety.py --ignore=tests/tools/test_query_table.py --ignore=tests/tools/test_query_table_errors.py --ignore=tests/tools/test_retrieval_side_channel.py --ignore=tests/tools/test_fetch.py -q` | ✅ 98 passed, 2 skipped |
| `grep -c "CANARY_STRINGS\s*=" tests/test_filter_value_safety.py` | 1 (corpus size 5) |
| Distinct ops in test_filter_compiler.py | 13 (all FilterOp variants) |
| f-string interpolation in `invalid_filter_op:` messages | 0 (verified via grep) |
| Ruff lint of NEW Phase 3 files | ✅ All checks passed |
| Ruff format of NEW Phase 3 files | ✅ 11 files already formatted |

## Threat-model coverage

| Threat | Mitigation evidence |
|--------|---------------------|
| T-03-01 (Info Disclosure via filter values in errors) | `grep -n 'f"invalid_filter_op:.*{' src/mcp_zeeker/core/filter_compiler.py` returns 0 lines. `raise ... from None` in numeric coercion failure paths suppresses `__cause__` chain. |
| T-03-02 (Side-channel between hidden and nonexistent columns) | `_visible_columns` is the SOLE column-existence gate; `raise_unknown_column` is the SOLE error emission. Counter-patch tests in `tests/tools/test_retrieval_side_channel.py` await Plan 03-02 wiring. |
| T-03-03 (in/notin nesting tampering) | `isinstance(value, list)` and `isinstance(v, (str, int, float))` checks before `","`-join; generic `invalid_filter_op` message on rejection. |
| T-03-04 (URL-construction f-string injection) | `get_table_rows` passes `params=list[tuple]` to httpx — httpx URL-encodes; no f-string concat into URL anywhere in compile_filters or the new method. |
| T-03-05 (DoS — large filter lists / 5 KB values) | Accept (Phase 7 rate limiter). No regression here. |

## Self-Check

### Files claimed created

- ✅ `src/mcp_zeeker/core/visibility.py` (found at HEAD)
- ✅ `src/mcp_zeeker/core/filter_compiler.py`
- ✅ `tests/test_filter_compiler.py`
- ✅ `tests/test_cursor.py`
- ✅ `tests/test_filter_value_safety.py`
- ✅ `tests/tools/test_query_table.py`
- ✅ `tests/tools/test_query_table_errors.py`
- ✅ `tests/tools/test_retrieval_side_channel.py`
- ✅ `tests/tools/test_fetch.py`

### Files claimed modified

- ✅ `src/mcp_zeeker/config.py`
- ✅ `src/mcp_zeeker/core/datasette_client.py`
- ✅ `src/mcp_zeeker/tools/discovery.py`
- ✅ `tests/conftest.py`
- ✅ `tests/tools/test_discovery_side_channel.py`

### Commits

- ✅ `ee42d84` — Task 1: visibility move + config constants
- ✅ `20b324c` — Task 2: filter_compiler + get_table_rows
- ✅ `3c79b76` — Task 3: Wave 0 stubs + conftest consolidation

## Self-Check: PASSED
