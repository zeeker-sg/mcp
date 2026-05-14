"""Phase 5 — handler error-path stubs for the fragment-join contract.

Wave 0 RED stubs until Plan 05-02 ships the handler keyset-cursor swap,
limit re-clamp, and fall-through path.
"""

import pytest


@pytest.mark.asyncio
async def test_keyset_cursor_malformed_message() -> None:
    """Plan 05-02 body-fill: stub parent lookup (so join activates); call
    `query_table(..., cursor="!!!not-base64!!!")`; assert
    `pytest.raises(ToolError, match="invalid_cursor: keyset cursor is malformed")`.
    The cursor contents are NEVER echoed in the error message (D5-07 / INJ-05)."""
    pytest.skip("RED until Plan 05-02 ships handler keyset cursor swap — D5-07")


@pytest.mark.asyncio
async def test_limit_cap_on_fragment_join() -> None:
    """Plan 05-02 body-fill: stub parent lookup; call `query_table(..., limit=101)`;
    assert `pytest.raises(ToolError, match="invalid_filter_op: limit exceeds
    fragment-join cap of 100")`. Fixed-literal — no value echo (D5-08 / INJ-05)."""
    pytest.skip("RED until Plan 05-02 ships limit re-clamp — D5-08")


@pytest.mark.asyncio
async def test_fragment_table_without_eq_filter_falls_through() -> None:
    """Plan 05-02 body-fill: NO parent lookup stub; stub fragment table directly
    via stub_table_rows; call `query_table` with no source_url filter (e.g.,
    `filters=[Filter(column="ordinal", op="gt", value=5)]`); assert exactly
    1 upstream call (no Call 1) via `httpx_mock.get_requests()` — fall-through
    behavior preserves FRAG-02 via HIDDEN_COLUMNS but skips the join."""
    pytest.skip("RED until Plan 05-02 ships fall-through path — D5-03")
