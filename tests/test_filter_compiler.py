"""
Pure-unit tests for compile_filters — Phase 3 Wave 0 stub (D3-02, D3-09, D3-10).

Module-level imports of mcp_zeeker.core.filter_compiler are safe because Plan
03-01 Task 2 shipped that module. These tests are GREEN as soon as Plan 03-01
ships — they exercise the 13-op coverage that QUERY-04 promises.

Tests cover:
- All 13 ops emit the verified URL form (D3-02 — exact, not, contains,
  startswith, endswith, gt/gte/lt/lte, in, notin, isnull, notnull)
- Numeric coercion for INTEGER / REAL columns (gt/gte/lt/lte) with generic
  error message on failure (T-03-01 / D3-09 — no value echo)
- in/notin comma-joined form (RESEARCH.md verified) and anti-nesting rejection
  (D3-10 / T-03-03)
- Unknown column raises unknown_column (defense-in-depth re-check, D3-08)
- Hostile filter-value canary stays out of the ToolError message (INJ-05)
"""

from __future__ import annotations

from typing import get_args

import pytest
from fastmcp.exceptions import ToolError

from mcp_zeeker.core.filter_compiler import Filter, FilterOp, compile_filters

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

VISIBLE = {"title", "organisation", "penalty_amount", "decision_type", "score"}
TYPES = {
    "title": "TEXT",
    "organisation": "TEXT",
    "decision_type": "TEXT",
    "penalty_amount": "INTEGER",
    "score": "REAL",
}


def test_exact_op_returns_url_pair():
    """D3-02: op='exact' compiles to ('{col}__exact', str(value))."""
    out = compile_filters(
        [Filter(column="title", op="exact", value="Data Protection")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("title__exact", "Data Protection")]


def test_not_op_returns_url_pair():
    """D3-02: op='not' compiles to ('{col}__not', str(value))."""
    out = compile_filters(
        [Filter(column="organisation", op="not", value="Grab")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("organisation__not", "Grab")]


def test_contains_op_returns_url_pair():
    """D3-02: op='contains' compiles to ('{col}__contains', str(value))."""
    out = compile_filters(
        [Filter(column="title", op="contains", value="protection")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("title__contains", "protection")]


def test_startswith_op_returns_url_pair():
    """D3-02: op='startswith' compiles to ('{col}__startswith', str(value))."""
    out = compile_filters(
        [Filter(column="title", op="startswith", value="Data")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("title__startswith", "Data")]


def test_endswith_op_returns_url_pair():
    """D3-02: op='endswith' compiles to ('{col}__endswith', str(value))."""
    out = compile_filters(
        [Filter(column="title", op="endswith", value="Decision")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("title__endswith", "Decision")]


def test_numeric_coercion_for_gt_gte_lt_lte():
    """D3-02 / D3-10: INTEGER and REAL columns coerce numeric ops."""
    out = compile_filters(
        [Filter(column="penalty_amount", op="gt", value="10000")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__gt", "10000")]

    out = compile_filters(
        [Filter(column="penalty_amount", op="gte", value=5000)],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__gte", "5000")]

    out = compile_filters(
        [Filter(column="penalty_amount", op="lt", value="20000")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__lt", "20000")]

    out = compile_filters(
        [Filter(column="penalty_amount", op="lte", value="15000")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__lte", "15000")]

    # REAL column coercion
    out = compile_filters(
        [Filter(column="score", op="gt", value="3.14")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("score__gt", "3.14")]


def test_numeric_coercion_failure_raises_invalid_filter_op():
    """D3-10 / T-03-01: non-numeric value on numeric op → generic invalid_filter_op."""
    with pytest.raises(ToolError) as exc_info:
        compile_filters(
            [Filter(column="penalty_amount", op="gt", value="not-a-number")],
            visible_columns=VISIBLE,
            column_types=TYPES,
        )
    msg = str(exc_info.value)
    assert "invalid_filter_op" in msg
    # Value MUST NOT appear (INJ-05)
    assert "not-a-number" not in msg


def test_int_column_rejects_float_value():
    """WR-01: INTEGER column must reject float values (no silent truncation).

    `int(3.99) == 3` would silently match rows the caller did not intend
    (`penalty_amount >= 3.99` would match `penalty_amount == 3`). Reject
    explicitly instead so the caller learns the column is integer-typed.
    """
    for op in ("gt", "gte", "lt", "lte"):
        with pytest.raises(ToolError, match=r"^invalid_filter_op:"):
            compile_filters(
                [Filter(column="penalty_amount", op=op, value=3.99)],
                visible_columns=VISIBLE,
                column_types=TYPES,
            )


def test_int_column_rejects_bool_value():
    """WR-01: INTEGER column must reject bool values.

    `int(True) == 1` and `int(False) == 0` — both would coerce silently
    because `isinstance(True, int)` is True in Python. Same fix-class as
    the float-truncation issue; explicit rejection forces the caller to
    pass an integer.
    """
    for op in ("gt", "gte", "lt", "lte"):
        for value in (True, False):
            with pytest.raises(ToolError, match=r"^invalid_filter_op:"):
                compile_filters(
                    [Filter(column="penalty_amount", op=op, value=value)],
                    visible_columns=VISIBLE,
                    column_types=TYPES,
                )


def test_in_uses_comma_join():
    """D3-02: op='in' compiles to ('{col}__in', 'a,b,c') — verified URL form."""
    out = compile_filters(
        [Filter(column="organisation", op="in", value=["Grab", "Shopee"])],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("organisation__in", "Grab,Shopee")]


def test_notin_uses_comma_join():
    """D3-02: op='notin' compiles to ('{col}__notin', 'a,b,c')."""
    out = compile_filters(
        [Filter(column="organisation", op="notin", value=["Grab", "Shopee"])],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("organisation__notin", "Grab,Shopee")]


def test_isnull_emits_one():
    """D3-02: op='isnull' compiles to ('{col}__isnull', '1') regardless of value."""
    out = compile_filters(
        [Filter(column="penalty_amount", op="isnull")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__isnull", "1")]

    # Value, if provided, is ignored
    out = compile_filters(
        [Filter(column="penalty_amount", op="isnull", value="anything")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__isnull", "1")]


def test_notnull_emits_one():
    """D3-02: op='notnull' compiles to ('{col}__notnull', '1') regardless of value."""
    out = compile_filters(
        [Filter(column="penalty_amount", op="notnull")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("penalty_amount__notnull", "1")]


def test_unknown_column_raises_unknown_column():
    """D3-07 / D3-08: column not in visible_columns raises ToolError(unknown_column)."""
    with pytest.raises(ToolError, match="unknown_column"):
        compile_filters(
            [Filter(column="hidden_id", op="exact", value="x")],
            visible_columns=VISIBLE,
            column_types=TYPES,
        )


def test_invalid_filter_op_no_value_echo():
    """T-03-01 / INJ-05: a recognizable canary string never appears in the error message."""
    canary = "ZEEKER_VAL_CANARY"
    with pytest.raises(ToolError) as exc_info:
        compile_filters(
            [Filter(column="penalty_amount", op="gt", value=canary)],
            visible_columns=VISIBLE,
            column_types=TYPES,
        )
    msg = str(exc_info.value)
    assert "invalid_filter_op" in msg
    assert canary not in msg


def test_in_rejects_nested_list():
    """D3-10 / T-03-03: in/notin value must be a flat list of str/int/float."""
    with pytest.raises(ToolError, match="invalid_filter_op"):
        compile_filters(
            [Filter(column="organisation", op="in", value=[{"nested": "dict"}])],
            visible_columns=VISIBLE,
            column_types=TYPES,
        )


def test_in_rejects_non_list_value():
    """D3-10 / T-03-03: in/notin value scalar is rejected (must be a list)."""
    with pytest.raises(ToolError, match="invalid_filter_op"):
        compile_filters(
            [Filter(column="organisation", op="in", value="Grab")],
            visible_columns=VISIBLE,
            column_types=TYPES,
        )


def test_multiple_filters_compile_to_list_of_pairs():
    """D3-01: compile_filters returns list[tuple[str,str]] — preserves order, repeats keys."""
    out = compile_filters(
        [
            Filter(column="title", op="exact", value="Data Protection"),
            Filter(column="organisation", op="in", value=["Grab", "Shopee"]),
            Filter(column="penalty_amount", op="isnull"),
        ],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert ("title__exact", "Data Protection") in out
    assert ("organisation__in", "Grab,Shopee") in out
    assert ("penalty_amount__isnull", "1") in out
    assert isinstance(out, list)
    assert all(isinstance(p, tuple) and len(p) == 2 for p in out)


# ---------------------------------------------------------------------------
# Phase 8 TEST-01: parametrized completeness sweep + numeric × column-type matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ALL_OPS)
def test_op_in_locked_set(op: str) -> None:
    """TEST-01 / D3-02: every op in ALL_OPS is in FilterOp AND the total count is exactly 13.

    Catches BOTH directions of drift:
    - op silently removed from FilterOp → assertion fails for that op
    - op silently added to FilterOp → len == 13 assertion fails

    Failure message names the actual op set so triage is one-line.
    """
    actual = get_args(FilterOp)
    assert op in actual, (
        f"FilterOp drift: op {op!r} missing from declared set. "
        f"FilterOp drift: declared={actual!r}, expected_count=13"
    )
    assert len(actual) == 13, f"FilterOp drift: declared={actual!r}, expected_count=13"


@pytest.mark.parametrize(
    "op, col_type",
    [(op, ct) for op in ("gt", "gte", "lt", "lte") for ct in ("INTEGER", "REAL", "TEXT")],
)
def test_numeric_ops_across_column_types(op: str, col_type: str) -> None:
    """TEST-01 / D3-10: numeric ops behave deterministically by column type.

    For each combination of (gt, gte, lt, lte) × (INTEGER, REAL, TEXT),
    compile_filters must return a list with exactly one tuple whose first
    element starts with 'col__' and whose second element is a non-empty string.
    Exercises the numeric coercion path per filter_compiler.py:146-181.
    """
    visible = {"col"}
    types = {"col": col_type}
    # Use a value that is coercible for all three column types: "10"
    f = Filter(column="col", op=op, value="10")
    out = compile_filters([f], visible_columns=visible, column_types=types)
    assert len(out) == 1, f"expected 1 pair for op={op!r} col_type={col_type!r}, got {out!r}"
    key, val = out[0]
    assert key.startswith("col__"), (
        f"expected key starting with 'col__', got {key!r} for op={op!r} col_type={col_type!r}"
    )
    assert val != "", f"expected non-empty value for op={op!r} col_type={col_type!r}, got {val!r}"
