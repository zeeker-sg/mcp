"""
Wave-0 stub for discovery side-channel tests — DISC-05.

Plan 02 implements the raise_unknown_table helper and the no-presence
side-channel guarantee. This stub exists so that the test surface is
counted as present-but-pending during Phase 2 Plan 01.

Function-body imports: imports of mcp_zeeker.tools.discovery symbols are
intentionally inside the test function body (NOT at module level). This is
required because Plan 02-02 Task 1 deletes the existing stubs before Task 2
re-adds them. During that gap the symbols are temporarily absent, and a
module-level import would cause pytest collection to fail with ImportError.
"""

import pytest


@pytest.mark.skip(reason="Plan 02 implements raise_unknown_table helper — DISC-05")
async def test_discovery_side_channel_wave0_pending():
    from mcp_zeeker.tools.discovery import describe_table

    assert describe_table is not None
