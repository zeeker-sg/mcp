"""
Filter compiler — pure-function security boundary for query_table (D3-01, D3-02, D3-10).

This module is the SOLE filter→Datasette URL-param translator. Mirroring the
single-call-site discipline of `core/config_lookup.py`, no handler may construct
filter URL pairs inline. Every supported operator (D3-02, 13 ops) has exactly
one row in the op-dispatch table here.

Security properties (auditable by inspection):
- NO IO — pure function. No httpx, no DatasetteClient access.
- NO f-string-into-URL paths. httpx URL-encodes via params=list[tuple].
- NO user-supplied filter value text in any ToolError message (D3-09 / INJ-05).
  Every `invalid_filter_op:` message is a fixed literal. T-03-01 guarantee.
- Defense-in-depth column visibility re-check (handler-level check comes first;
  this re-validates per D3-08).

References: D3-01 (Filter model + compile_filters), D3-02 (13 ops + verified
URL forms from RESEARCH.md `__in`/`__notin` decision), D3-09 (no value echo),
D3-10 (anti-nesting for in/notin lists).
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

# All supported filter operators (D3-02 — exactly 13 strings, locked).
FilterOp = Literal[
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
]


class Filter(BaseModel):
    """A single user-supplied filter clause (D3-01).

    Pydantic enforces:
    - `column` is a string
    - `op` is one of the 13 FilterOp literals — anything else raises pydantic
      ValidationError before reaching compile_filters
    - `value` is permissive (Any) — type/shape validation happens in
      compile_filters per-op, with generic invalid_filter_op messages

    `extra="forbid"` blocks unknown fields — prevents callers from sneaking
    in a `column__exact` synthetic op via a JSON payload.
    """

    model_config = ConfigDict(extra="forbid")

    column: str
    op: FilterOp
    value: Any = None


def compile_filters(
    filters: list[Filter],
    *,
    visible_columns: set[str],
    column_types: dict[str, str],
) -> list[tuple[str, str]]:
    """Compile a list of Filter clauses to httpx query params (D3-01, D3-02).

    Returns list-of-pairs (NOT a dict) because httpx accepts repeated keys via
    `params=[(k, v), ...]`; some downstream paths (e.g., column projection
    `_col=a&_col=b`) rely on that — and `__in` may coexist with another `__in`
    on a different column.

    Args:
        filters: list of Filter clauses (already pydantic-validated for op set)
        visible_columns: result of `_visible_columns(database, table)` — used
            as a defense-in-depth re-check (the HANDLER does the primary check
            with the {db}.{table} echo; this catches programmer error if any
            future handler omits the per-field loop).
        column_types: merged map of column → SQL type (config fallback overlaid
            with upstream `_zeeker_schemas` per D3-08). INTEGER/REAL trigger
            numeric coercion for gt/gte/lt/lte.

    Raises:
        ToolError(unknown_column): if a filter references a column not in
            visible_columns. NO {db}.{table} echo here — the handler emits the
            fully-qualified message via raise_unknown_column FIRST in D3-08
            order; this is only a defense-in-depth catch-all.
        ToolError(invalid_filter_op): for every failure mode below. Message is
            a FIXED literal — NO f-string interpolation of {value}, {op}, or
            {filter} (T-03-01).
            - value not coercible for numeric op
            - in/notin value not a flat list of str/int/float
            - value required for an op that needs one (everything except
              isnull/notnull)

    Op-dispatch (D3-02 — verified URL forms from RESEARCH.md):
        exact, not, contains, startswith, endswith → ("{col}__{op}", str(value))
        gt, gte, lt, lte                          → numeric coercion per column_types
        in, notin                                 → comma-joined: ("{col}__in", "a,b,c")
        isnull, notnull                           → ("{col}__isnull", "1") (value ignored)
    """
    out: list[tuple[str, str]] = []

    for f in filters:
        # Defense-in-depth visibility re-check (D3-08 — handler did primary check)
        if f.column not in visible_columns:
            raise ToolError(f"unknown_column: Column not found: {f.column}")

        op = f.op

        # isnull / notnull — value is ignored entirely (Datasette emits "1")
        if op == "isnull":
            out.append((f"{f.column}__isnull", "1"))
            continue
        if op == "notnull":
            out.append((f"{f.column}__notnull", "1"))
            continue

        # All remaining ops require a value
        if f.value is None:
            raise ToolError("invalid_filter_op: value required for this operator")

        # in / notin — comma-join flat list (RESEARCH.md __in form decision)
        if op in ("in", "notin"):
            if not isinstance(f.value, list):
                raise ToolError(
                    "invalid_filter_op: in/notin value must be a flat list of str/int/float"
                )
            if not all(isinstance(v, (str, int, float)) for v in f.value):
                raise ToolError(
                    "invalid_filter_op: in/notin value must be a flat list of str/int/float"
                )
            joined = ",".join(str(v) for v in f.value)
            out.append((f"{f.column}__{op}", joined))
            continue

        # Numeric comparison ops — coerce per column_types (D3-10)
        if op in ("gt", "gte", "lt", "lte"):
            col_type = column_types.get(f.column, "TEXT")
            if col_type == "INTEGER":
                try:
                    coerced: int | float = int(f.value)
                except (TypeError, ValueError):
                    # `from None` suppresses the original exception chain — the
                    # raw ValueError text often echoes the offending value (e.g.
                    # "invalid literal for int(): 'abc'"), which would leak via
                    # the exception __cause__ chain (T-03-01 / INJ-05).
                    raise ToolError("invalid_filter_op: value not coercible for operator") from None
            elif col_type == "REAL":
                try:
                    coerced = float(f.value)
                except (TypeError, ValueError):
                    # `from None` suppresses the original exception chain — the
                    # raw ValueError text often echoes the offending value (e.g.
                    # "invalid literal for int(): 'abc'"), which would leak via
                    # the exception __cause__ chain (T-03-01 / INJ-05).
                    raise ToolError("invalid_filter_op: value not coercible for operator") from None
            else:
                # TEXT (and any unknown type) — pass through stringified
                coerced = f.value  # type: ignore[assignment]
            out.append((f"{f.column}__{op}", str(coerced)))
            continue

        # String ops — exact / not / contains / startswith / endswith
        if op in ("exact", "not", "contains", "startswith", "endswith"):
            out.append((f"{f.column}__{op}", str(f.value)))
            continue

        # Unreachable: pydantic FilterOp Literal already constrained op. Kept
        # as a defensive guard for future op-set additions that miss a branch.
        raise ToolError("invalid_filter_op: filter operator not in supported set")

    return out
