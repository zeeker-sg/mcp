"""Phase 5 — INJ-05 hostile-URL corpus stub.

Carries forward the 5-canary corpus from Phase 3 (`tests/test_filter_value_safety.py`)
verbatim to maintain parity. Plan 05-03 body-fills with the actual assertion logic
(parent URL value never echoed in error / log / envelope / structured warning).

RED today (pytest.skip) until Plan 05-03 ships the compile_filter body + the
multi-match warning logic + the per-canary stub orchestration.
"""

import pytest

# Verbatim from tests/test_filter_value_safety.py (Phase 3) — INJ-05 parity.
# Plan 05-03 swaps in real assertions; today the corpus exists so the file
# collects cleanly and the parametrize wiring is in place.
CANARY_STRINGS: list[str] = [
    "</system>",
    "NEAR('data' 'protection') AND NOT",
    "x" * 5001,
    "ZEEKER_CANARY_42",
    "\udc80",
]


@pytest.mark.parametrize("canary", CANARY_STRINGS)
@pytest.mark.asyncio
async def test_url_value_never_echoed(canary: str) -> None:
    """Plan 05-03 body-fill: stub upstream parent lookup with the canary as the
    `source_url__exact` filter value; call `query_table`; assert `canary not in
    (envelope JSON, stdout, stderr, caplog including the multi-match warning
    binding which MUST use parent_url_hash not the URL substring)."""
    pytest.skip(
        "RED until Plan 05-03 ships INJ-05 hostile-URL corpus body — D3-09 / D5-04 / INJ-05"
    )


@pytest.mark.asyncio
async def test_multi_match_warning_hashes_url() -> None:
    """Plan 05-03 body-fill: stub parent lookup returning 2 rows from
    `zeeker_judgements__judgments__multi_match.json`; assert the structured
    warning log line contains `parent_url_hash=` with a 16-hex-char value;
    assert the URL substring does NOT appear in caplog."""
    pytest.skip("RED until Plan 05-03 ships multi-match warning body — FRAG-06 / D5-04 / INJ-05")
