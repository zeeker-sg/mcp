"""Phase 5 — side-channel counter-patch test stub.

Counter-patches `mcp_zeeker.tools.retrieval.fragment_join.compile_filter` to
prove all three fragment-table pairs route through the SAME helper (single
auditable code path per D5-01).

Wave 0 RED stub until Plan 05-02 ships the handler delegation.
"""

import pytest


@pytest.mark.asyncio
async def test_three_pairs_route_through_same_helper() -> None:
    """Plan 05-02 body-fill:

    1. Replace `mcp_zeeker.tools.retrieval.fragment_join.compile_filter` with
       a wrapper that increments a counter on each call.
    2. Stub all 3 fragment-table pairs via `stub_fragment_join_two_step`.
    3. Call `query_table` once per pair with the eq-parent-URL filter.
    4. Assert the counter equals 3 — every pair routes through the same
       helper (D5-01 single auditable code path).
    5. Assert no other compile_filter-shaped function is invoked (proves
       the delegation is sole-emission)."""
    pytest.skip(
        "RED until Plan 05-02 ships handler delegation to fragment_join.compile_filter — D5-01"
    )
