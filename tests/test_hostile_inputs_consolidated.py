"""
Consolidated hostile-inputs tests — Wave 0 STUB.

Plan 06-03 ships the GREEN body: parametrized 5-canary × 3-tool fan-out
(`query_table`, `search`, `fetch`) using the shared
`tests/_corpus/hostile_inputs.py` module. Each test asserts no canary leaks
to stdout, stderr, log, or error surfaces via `_surfaces_contain`.

The Phase 3 / Phase 4 / Phase 5 per-tool corpora at
`tests/test_filter_value_safety.py`, `tests/test_search_value_safety.py`, and
`tests/core/test_fragment_join_value_safety.py` remain in place as regression
coverage — this consolidated test is additive INJ-05 coverage, not a
replacement (CONTEXT.md `<deferred>`).
"""

from __future__ import annotations

import pytest

from tests._corpus.hostile_inputs import CANARY_STRINGS, _surfaces_contain


def test_wave0_hostile_inputs_consolidated_stub():
    """Wave-0 stub: structural check that the shared corpus imports cleanly."""
    assert isinstance(CANARY_STRINGS, list)
    assert len(CANARY_STRINGS) == 5
    assert callable(_surfaces_contain)
    # Sanity: verbatim canaries from tests/test_filter_value_safety.py:43-49
    assert "</system>" in CANARY_STRINGS
    assert "NEAR('data' 'protection') AND NOT" in CANARY_STRINGS
    assert "ZEEKER_CANARY_42" in CANARY_STRINGS
    assert "\udc80" in CANARY_STRINGS
    # Quick sanity that _surfaces_contain returns empty on clean surfaces.
    leaks = _surfaces_contain(
        "ZEEKER_CANARY_42",
        captured_out="",
        captured_err="",
        log_text="",
        error_text="",
    )
    assert leaks == []

    pytest.skip(reason="Wave 0 stub — 5 canaries × 3 tools fan-out body fills in Plan 06-03")
