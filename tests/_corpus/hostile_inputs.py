"""
Shared hostile-input canary corpus — INJ-05 consolidation (D6 Phase 6).

Verbatim copy of `CANARY_STRINGS` (D3-09 minimum-viable corpus) and the
`_surfaces_contain` leak-detection helper from `tests/test_filter_value_safety.py`
lines 43-49 and 147-167 (Phase 3). The Phase 4 / Phase 5 per-tool corpora at
`tests/test_search_value_safety.py:59-65` and
`tests/core/test_fragment_join_value_safety.py:38-42` deliberately remain in
place as regression coverage (CONTEXT.md `<deferred>` — per-phase corpora are
the existing audit signal; this shared module consolidates them for Plan 06-03's
parametrized 3-tool fan-out test).

Plan 06-03 fills `tests/test_hostile_inputs_consolidated.py` with the
parametrized `query_table` × `search` × `fetch` × 5-canary matrix; Plan 06-01
ships only the shared module + Wave-0 stub.
"""

from __future__ import annotations

# D3-09 minimum-viable corpus. Order matches the verbatim copy from
# tests/test_filter_value_safety.py:43-49 for traceability.
CANARY_STRINGS: list[str] = [
    "</system>",  # HTML/system tag injection sentinel
    "NEAR('data' 'protection') AND NOT",  # FTS5 operators (would error if forwarded)
    "x" * 5001,  # 5 KB oversized string
    "ZEEKER_CANARY_42",  # plain round-trip detector
    "\udc80",  # lone surrogate — UTF-8 boundary handling
    "﻿",  # BOM (byte-order mark) — invisible char; often round-trips through naive string handling
    "‮",  # RTL override — flips display direction; potential confusion in agent display surface
    "\udcc0\udc80",  # malformed UTF-8 surrogate pair — UTF-8 boundary canary
    "MATCH 'data' AND NEAR(",  # FTS5 operator string — upstream syntax that must never echo back
]


def _surfaces_contain(
    canary: str, *, captured_out: str, captured_err: str, log_text: str, error_text: str
) -> list[str]:
    """Return the list of surface names where the canary appears.

    For the lone-surrogate canary, also check repr() so backslash-escape leakage
    is caught (e.g. '\\udc80' in any text stream).
    """
    leaks: list[str] = []
    for surface_name, surface_text in (
        ("stdout", captured_out),
        ("stderr", captured_err),
        ("log", log_text),
        ("error", error_text),
    ):
        if canary in surface_text:
            leaks.append(surface_name)
        # Defense in depth: catch backslash-escape leakage of unprintable canaries.
        if repr(canary).strip("'\"") in surface_text and repr(canary) != repr(""):
            leaks.append(f"{surface_name}_repr")
    return leaks
