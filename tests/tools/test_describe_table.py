"""
Wave-0 stub for describe_table tool tests — DISC-03/DISC-04.

Plan 02 implements the actual describe_table handler. This stub exists so
that the test surface is counted as present-but-pending during Phase 2 Plan 01.

Function-body imports: imports of mcp_zeeker.tools.discovery symbols are
intentionally inside the test function body (NOT at module level). This is
required because Plan 02-02 Task 1 deletes the existing describe_table stub
before Task 2 re-adds it. During that gap the symbol is temporarily absent,
and a module-level import would cause pytest collection to fail with ImportError.
"""

import pytest


@pytest.mark.skip(reason="Plan 02 implements describe_table handler — DISC-03/DISC-04")
async def test_describe_table_wave0_pending():
    from mcp_zeeker.tools.discovery import describe_table

    assert describe_table is not None
