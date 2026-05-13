---
phase: 03-structured-retrieval-url-keyed-fetch
reviewed: 2026-05-14T00:00:00Z
depth: standard
files_reviewed: 19
files_reviewed_list:
  - src/mcp_zeeker/config.py
  - src/mcp_zeeker/core/config_lookup.py
  - src/mcp_zeeker/core/cursor.py
  - src/mcp_zeeker/core/datasette_client.py
  - src/mcp_zeeker/core/envelope.py
  - src/mcp_zeeker/core/filter_compiler.py
  - src/mcp_zeeker/core/visibility.py
  - src/mcp_zeeker/tools/discovery.py
  - src/mcp_zeeker/tools/retrieval.py
  - tests/conftest.py
  - tests/manual/PHASE3-CLIENT-VERIFY.md
  - tests/test_cursor.py
  - tests/test_filter_compiler.py
  - tests/test_filter_value_safety.py
  - tests/tools/test_discovery_side_channel.py
  - tests/tools/test_fetch.py
  - tests/tools/test_query_table_errors.py
  - tests/tools/test_query_table.py
  - tests/tools/test_retrieval_side_channel.py
findings:
  critical: 1
  warning: 6
  info: 4
  total: 11
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-05-14
**Depth:** standard
**Files Reviewed:** 19
**Status:** issues_found

## Summary

Phase 3 ships `query_table` + `fetch` with strong INJ-05 discipline. The
side-channel and value-echo invariants are well-covered by tests, the
`from None` discipline is applied at every place that wraps an upstream
exception, and the locked error catalog is mostly respected.

The review surfaces one BLOCKER (a documented single-source-of-truth invariant
for `config.URL_COLUMNS` is violated in `tools/discovery.py`), one
correctness WARNING in the numeric coercion path (silent float truncation for
INTEGER columns), and several WARNING-tier defensive/quality issues. None of
the warnings appear to be exploitable as INJ-05 leaks today; they widen the
attack surface or mis-classify errors.

## Critical Issues

### CR-01: Direct read of `config.URL_COLUMNS` in `tools/discovery.py` violates D2-10 / D3-04 single-source-of-truth

**File:** `src/mcp_zeeker/core/config_lookup.py:38-53` and `src/mcp_zeeker/tools/discovery.py:228`

**Issue:** The phase-3 invariant locks `core.config_lookup.url_column_for` as
the SOLE call-site for `config.URL_COLUMNS` (mirror of `hidden_columns_for`
for `HIDDEN_COLUMNS`). The docstring in `config_lookup.py` states explicitly:

> "This is the ONLY call-site for config.URL_COLUMNS. The fetch handler
> (Plan 03-04, D3-14 step 2) consumes this helper..."

However, `describe_table` reads the dict directly:

```python
# tools/discovery.py:228
url_keyed = f"{database}.{table}" in config.URL_COLUMNS
```

This bypasses `url_column_for` and creates a second call-site. The invariant
is asserted in code comments / module docstrings but not enforced by any test
(unlike `HIDDEN_COLUMNS`, which is exercised by `_visible_columns`). Two
real consequences:

1. Any future change to the URL_COLUMNS shape (e.g. adding a "*" global key
   for parity with HIDDEN_COLUMNS, or supporting multiple URL columns per
   table) silently desyncs `describe_table` from `fetch`.
2. The `url_keyed` flag in `describe_table` could disagree with whether
   `fetch` actually accepts the table — exactly the divergence the
   invariant was created to prevent.

**Fix:**

```python
# tools/discovery.py
from mcp_zeeker.core.config_lookup import hidden_columns_for, url_column_for
...
url_keyed = url_column_for(database, table) is not None
```

Optionally add a regression test mirroring the discipline of
`hidden_columns_for` usage:

```python
# tests/test_config_lookup_single_source.py (new)
def test_url_columns_only_read_via_helper():
    """D3-04: config.URL_COLUMNS must not be referenced outside config_lookup."""
    import pathlib, re
    root = pathlib.Path("src/mcp_zeeker")
    offenders = []
    for py in root.rglob("*.py"):
        if py.name in {"config.py", "config_lookup.py"}:
            continue
        text = py.read_text()
        if re.search(r"config\.URL_COLUMNS", text):
            offenders.append(str(py))
    assert not offenders, f"direct URL_COLUMNS reads: {offenders}"
```

## Warnings

### WR-01: Silent float-to-int truncation in numeric filter coercion

**File:** `src/mcp_zeeker/core/filter_compiler.py:146-156`

**Issue:** For `gt`/`gte`/`lt`/`lte` on INTEGER columns, the code calls
`int(f.value)` directly. When `f.value` is a Python float (which is what JSON
numbers like `3.99` deserialize to), `int(3.99) == 3` — silent truncation. A
caller that sends `{"column": "penalty_amount", "op": "gte", "value": 3.99}`
will get rows where `penalty_amount >= 3`, not `>= 4` as semantically
expected. The user receives subtly wrong results with no error.

The companion test `test_thirteen_ops_end_to_end` only exercises integer
values for numeric ops, so this regression is undetected.

`int(True) == 1` is the same problem class (booleans coerced to integers
without error) — less likely in practice but the same root cause.

**Fix:** Reject non-integer-coercible numerics explicitly, or use a strict
parser:

```python
if op in ("gt", "gte", "lt", "lte"):
    col_type = column_types.get(f.column, "TEXT")
    if col_type == "INTEGER":
        # Reject floats and bools — only accept clean int / int-string.
        if isinstance(f.value, bool) or isinstance(f.value, float):
            raise ToolError("invalid_filter_op: value not coercible for operator") from None
        try:
            coerced: int | float = int(f.value)
        except (TypeError, ValueError):
            raise ToolError("invalid_filter_op: value not coercible for operator") from None
    elif col_type == "REAL":
        ...
```

Add a test:

```python
def test_int_column_rejects_float_value():
    with pytest.raises(ToolError, match="invalid_filter_op"):
        compile_filters(
            [Filter(column="penalty_amount", op="gte", value=3.99)],
            visible_columns=VISIBLE, column_types=TYPES,
        )
```

### WR-02: Limit-clamp error uses the wrong code from the locked catalog

**File:** `src/mcp_zeeker/tools/retrieval.py:156-157`

**Issue:** The belt-and-suspenders limit clamp raises
`ToolError("invalid_filter_op: limit must be between 1 and 200")`. Per the
phase context (D3-12 LOCKED error code catalog: `unknown_table`,
`unknown_column`, `invalid_filter_op`, `invalid_cursor`,
`unsupported_table_for_fetch`, `not_found`), `invalid_filter_op` is for
filter-clause shape errors — the limit parameter is not a filter. Reusing
the code conflates two distinct error classes for log/metrics consumers
that grep on error_code.

`tests/tools/test_query_table_errors.py::test_limit_201_rejected_before_upstream`
only asserts `pytest.raises((ValidationError, ToolError))` — does NOT pin
the error code, so this slip-through is invisible to the test suite.

**Fix:** Either add `invalid_limit` to the catalog (config + plan update +
test), or use the closest existing code. If the catalog is genuinely locked
to those six, prefer `invalid_filter_op` and add a code comment justifying
the overload. A clean alternative is to mirror Pydantic's path and let
`Field(ge=1, le=200)` always be the gate — direct Python callers can
accept the `ValidationError` form. Recommend the catalog addition for
clarity:

```python
# config.py — add to D3-12 catalog
# ERROR_CODES = (..., "invalid_limit")

# retrieval.py:156
if limit < 1 or limit > config.MAX_QUERY_LIMIT:
    raise ToolError("invalid_limit: limit must be between 1 and 200")
```

### WR-03: `LIGHT_COLUMNS` config drift can leak heavy columns at top level

**File:** `src/mcp_zeeker/tools/retrieval.py:207-213`

**Issue:** In the default-projection branch (`columns is None`):

```python
configured_light = config.LIGHT_COLUMNS.get(f"{database}.{table}", [])
if configured_light:
    light_to_emit = [c for c in configured_light if c in visible]
```

`configured_light` is filtered against `visible` but NOT against
`HEAVY_COLUMNS`. If a future config drift adds a heavy column name (e.g.
`content_text`) to `LIGHT_COLUMNS["zeeker-judgements.judgments"]`, the
column would be emitted at the top level instead of under
`retrieved_content`. This violates the D3-19 snapshot contract
(`set(row.keys()) ∩ HEAVY_COLUMNS == ∅` for default projections).

The fallback branch (`else: light_to_emit = sorted(visible - config.HEAVY_COLUMNS)`)
correctly subtracts HEAVY_COLUMNS — only the configured-light branch is
unprotected.

**Fix:** Apply the same subtraction in both branches:

```python
if configured_light:
    light_to_emit = [
        c for c in configured_light
        if c in visible and c not in config.HEAVY_COLUMNS
    ]
else:
    light_to_emit = sorted(visible - config.HEAVY_COLUMNS)
```

### WR-04: `raise_*` helpers declared `-> None` but never return (use `NoReturn`)

**File:** `src/mcp_zeeker/core/visibility.py:32-82`

**Issue:** All five `raise_unknown_*` / `raise_not_found` /
`raise_unsupported_table_for_fetch` helpers are typed `-> None` but always
raise. This causes a real type-narrowing miss in `tools/retrieval.py:362`:

```python
url_col = url_column_for(database, table)  # -> str | None
if url_col is None:
    raise_unsupported_table_for_fetch(database, table)  # actually no-return
# Type checkers do NOT narrow `url_col` to `str` here.
params = [(f"{url_col}__exact", url), ("_size", "2")]  # url_col seen as str | None
```

At runtime it works (the helper raises), but the type signature lies to
mypy/Pyright/ty. If anyone later adds a non-raising branch to one of these
helpers (e.g., "log and continue"), the assumption that they always raise
would silently break downstream code, including potentially the fetch
unsupported-table-for-fetch path (BLOCKER-class regression).

**Fix:**

```python
from typing import NoReturn

def raise_unknown_database(database: str) -> NoReturn:
    raise ToolError(f"unknown_database: Database not found: {database}")
# ... same for the other four helpers
```

### WR-05: `canonical_shape_str` conflates `columns=[]` with `columns=None`

**File:** `src/mcp_zeeker/core/cursor.py:80`

**Issue:** The docstring on lines 64-66 states:

> "`None` preserved verbatim (distinct from `[]` — empty list is 'explicit
> no projection' but never reaches this path in practice)."

But the implementation `"columns": sorted(columns) if columns else None`
uses Python truthiness — `columns=[]` evaluates as falsy and becomes
`None` in the shape. The docstring promise is violated.

In practice, `query_table`'s handler passes the raw `columns` parameter
through unchanged, and pydantic accepts `[]` as a valid `list[str]`. So a
caller who does `columns=[]` and later sends `columns=None` (or vice
versa) with the cursor will believe the shape differs, but the digest will
say it's the same. This is a minor correctness-vs-documentation drift, not
a security issue (no value leaked, no broken visibility check).

**Fix:** Either align the docstring with the implementation, or align the
implementation with the docstring (preferred — `None` ≠ `[]` matches
pydantic / TS / JSON intuitions):

```python
"columns": sorted(columns) if columns is not None else None,
```

(Note: `sorted([])` is `[]`, which the implementation already handles.)

### WR-06: `get_table_column_types` does not defend against malformed upstream JSON

**File:** `src/mcp_zeeker/core/datasette_client.py:131-152`

**Issue:** The function catches `UpstreamCallFailed` and returns `{}`, but
the JSON-parsing path that follows is brittle:

```python
payload = resp.json()                                    # may raise json.JSONDecodeError
col_idx = payload["columns"].index("resource_name")      # may raise KeyError or ValueError
defn_idx = payload["columns"].index("column_definitions")
for row in payload.get("rows", []):
    table_name = row[col_idx]                            # may raise IndexError
    raw_defn = row[defn_idx]
    result[table_name] = json.loads(raw_defn) if isinstance(raw_defn, str) else {}
```

If upstream Datasette returns HTTP 200 with a malformed JSON shape (e.g.
during a partial outage or schema drift), any of `JSONDecodeError`,
`KeyError`, `ValueError`, or `IndexError` will propagate as an
un-mapped exception. The handler in `retrieval.py:188` calls this
function directly — the exception will not be caught by the
`UpstreamCallFailed` handler and surfaces as a 500-class internal error
through FastMCP, exposing implementation details (Python traceback) in
the error envelope and breaking the locked error catalog.

The docstring says "Falls back to empty dict if the table is absent or
the upstream call fails" — which is the intent, not the implementation.

**Fix:** Wrap the parsing in a broad exception handler that maps to the
documented empty-dict fallback:

```python
try:
    resp = await self._request_with_retry("GET", f"/{database}/_zeeker_schemas.json")
    payload = resp.json()
    col_idx = payload["columns"].index("resource_name")
    defn_idx = payload["columns"].index("column_definitions")
    result: dict[str, dict[str, str]] = {}
    for row in payload.get("rows", []):
        table_name = row[col_idx]
        raw_defn = row[defn_idx]
        result[table_name] = json.loads(raw_defn) if isinstance(raw_defn, str) else {}
    return result
except (UpstreamCallFailed, KeyError, ValueError, IndexError, TypeError, json.JSONDecodeError):
    return {}
```

## Info

### IN-01: `query_table` makes two `get_database` round-trips per request

**File:** `src/mcp_zeeker/tools/retrieval.py:169, 172`

**Issue:** `_resolve_table` and `_visible_columns` both call
`DatasetteClient.current().get_database(database)`. No memoization at the
DatasetteClient or per-request level. Every `query_table` invocation
issues two identical `/{database}.json` round-trips before the actual
table-rows call. (`fetch` also incurs this pattern: `_resolve_table` +
`_visible_columns`.)

Per scope, performance is out of v1. Calling out so it is on the radar
for the Phase 6 metadata-cache work, where a per-request memoization
of `get_database` is the obvious fix and would not change semantics.

**Fix:** Defer; memoize either on the DatasetteClient (with a request-scoped
contextvar cache) or by inlining the visible-columns computation into
`_resolve_table`'s return value.

### IN-02: `_visible_columns` uses `next(...)` without a default

**File:** `src/mcp_zeeker/core/visibility.py:142`

**Issue:**

```python
t = next(ts for ts in summary.tables if ts.name == table)
```

If `_resolve_table` is bypassed (or if upstream returns inconsistent state
between the two calls — see IN-01), this `next()` raises
`StopIteration`, which is wrapped in a `RuntimeError` for generators in
modern Python and surfaces as a 500-class error.

**Fix:**

```python
t = next((ts for ts in summary.tables if ts.name == table), None)
if t is None:
    raise_unknown_table(database, table)
```

### IN-03: Filter `value` field has unconstrained type (`Any`)

**File:** `src/mcp_zeeker/core/filter_compiler.py:64-65`

**Issue:** `value: Any = None` accepts arbitrary Python objects. Most
exotic types fail downstream (numeric coercion, list-element validation),
but a `dict` value sent with op=`exact` is silently stringified and sent
upstream as `col__exact={'k':'v'}`. Not a security boundary problem
(httpx URL-encodes the result) and Datasette will return 0 rows or an
error — but the documented op semantics are "value is a string or
number or list-of-primitives". The Filter model could narrow this to
`str | int | float | bool | list[str | int | float] | None`.

**Fix:** Narrow the union, or document the implicit stringify in the
description for future-LLM clarity.

### IN-04: `compile_filters` defense-in-depth `unknown_column` message
diverges from the canonical `raise_unknown_column` format

**File:** `src/mcp_zeeker/core/filter_compiler.py:115`

**Issue:** When the handler has done the primary check, `compile_filters`
should never trigger its own visibility re-check. When it DOES trigger
(defense-in-depth), the message is `unknown_column: Column not found:
{f.column}` — missing the `{database}.{table}` prefix that
`raise_unknown_column` emits. If this defense-in-depth path ever fires
in production, log-correlators looking for `unknown_column: Column not
found: db.table.col` would miss it.

This is consistent with the comment ("NO {db}.{table} echo here — the
handler emits the fully-qualified message via raise_unknown_column FIRST")
but it would be cleaner to route this through `raise_unknown_column`
itself for message consistency. The trade-off is that compile_filters
no longer has the database/table identifiers in scope.

**Fix:** Either pass `database`/`table` into `compile_filters` so the
defense-in-depth path can call `raise_unknown_column(...)`, or accept
the divergence and document it more loudly. Lowest-effort option:
keep as-is and add a code comment confirming the asymmetric message
is intentional.

---

_Reviewed: 2026-05-14_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
