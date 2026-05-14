"""
Pure-unit tests for FTS5 phrase-wrap escape — Plan 04-01 (D4-08 / SEARCH-06).

The 13-input contract corpus is verbatim from 04-RESEARCH §3.6, where each
input was probed against the live `data.zeeker.sg` Datasette deployment to
confirm the FTS5 phrase-wrap neutralizes the operator (NEAR, OR, AND, `:col:`,
`*`, parentheses, asterisks, trailing backslash, etc.) without leaking through.

Tests cover:
- The 13 (raw, expected) input/output pairs from 04-RESEARCH §3.6.
- A defensive purity test that asserts `raise` / `await` never appear in the
  module source — catches if a future edit accidentally adds error-handling
  or IO (D4-08: escape_fts5 is a pure stdlib one-liner, NEVER raises, never
  awaits).
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Section 5(a)", '"Section 5(a)"'),
        ('he said "hi"', '"he said ""hi"""'),
        ("OR AND NEAR", '"OR AND NEAR"'),
        ("NEAR", '"NEAR"'),
        ("*", '"*"'),
        (":column:value", '":column:value"'),
        ("((((", '"(((("'),
        ("NEAR/5 word", '"NEAR/5 word"'),
        ("foo\\", '"foo\\"'),  # trailing backslash literal
        ("", '""'),  # handler-side strip() guard fires BEFORE this — verified separately
        ("   ", '"   "'),  # whitespace — same handler guard fires first
        ("a" * 5000, '"' + "a" * 5000 + '"'),  # 5 KB payload (04-RESEARCH §8 LOW-DoS)
        ("text:foo OR id:0", '"text:foo OR id:0"'),
    ],
)
def test_escape_fts5(raw: str, expected: str) -> None:
    """D4-08 / SEARCH-06: escape_fts5 phrase-wraps and doubles internal quotes.

    13-input contract corpus verbatim from 04-RESEARCH §3.6.
    """
    from mcp_zeeker.core.fts_escape import escape_fts5

    assert escape_fts5(raw) == expected


def test_escape_fts5_is_pure_string() -> None:
    """Defensive: the module source contains no `raise` or `await`.

    D4-08 / 04-PATTERNS.md security-property block: escape_fts5 is a pure
    stdlib string operation. It NEVER raises (empty-string handling is the
    handler's responsibility per D4-19 step 1) and NEVER awaits (no IO).
    Catches if a future edit accidentally adds error-handling or IO.
    """
    from pathlib import Path

    import mcp_zeeker.core.fts_escape as mod

    source = Path(mod.__file__).read_text()
    # Strip the docstring (which legitimately discusses raise/await semantics).
    # Heuristic: keep only lines after the closing triple-quote of the module docstring.
    parts = source.split('"""', 2)
    assert len(parts) == 3, "expected a module docstring delimited by triple quotes"
    code = parts[2]
    assert "raise " not in code, "escape_fts5 module must not raise (pure stdlib)"
    assert "await " not in code, "escape_fts5 module must not await (no IO)"
