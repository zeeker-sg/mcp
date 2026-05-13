"""
Wave-0 stub for list_tables tool tests — DISC-02.

Plan 02 implements the actual list_tables handler. This stub exists so that
the test surface is counted as present-but-pending during Phase 2 Plan 01.

Function-body imports: imports of mcp_zeeker.tools.discovery symbols are
intentionally inside the test function body (NOT at module level). This is
required because Plan 02-02 Task 1 deletes the existing list_tables stub
before Task 2 re-adds it. During that gap the symbol is temporarily absent,
and a module-level import would cause pytest collection to fail with ImportError.
"""

import pytest


@pytest.mark.skip(reason="Plan 02 implements list_tables handler — DISC-02")
async def test_list_tables_wave0_pending():
    from mcp_zeeker.tools.discovery import list_tables

    assert list_tables is not None
