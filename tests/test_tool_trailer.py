"""
Focused test for ANNO-02 (INJ-01): list_databases tool description ends with TOOL_TRAILER.

Per 01-PATTERNS.md, this file is a thin focused redirect. The primary iterating test lives
in test_envelope_contract.py::test_every_registered_tool_description_ends_with_trailer.
This file provides an explicit named test for list_databases so the 01-VALIDATION.md row
`tests/test_tool_trailer.py` is satisfiable with a dedicated automated command.

Phase 6 / Plan 06-02 ADDS a second test that broadens the trailer enforcement to every
registered tool via `mcp.list_tools()` (Pattern F — registry introspection). Both tests
coexist: the focused one names a specific tool so the 01-VALIDATION row stays satisfiable;
the broadened one prevents trailer drift on any future tool addition.
"""

from __future__ import annotations

from mcp_zeeker import config
from mcp_zeeker.server import mcp


async def test_list_databases_description_ends_with_trailer():
    """ANNO-02 / INJ-01: list_databases description ends with TOOL_TRAILER verbatim."""
    tools = await mcp.list_tools()
    tool = next((t for t in tools if t.name == "list_databases"), None)
    assert tool is not None, "list_databases tool not registered"
    assert tool.description is not None and tool.description.rstrip().endswith(
        config.TOOL_TRAILER
    ), (
        f"list_databases description does not end with TOOL_TRAILER. "
        f"Got: {(tool.description or '')[-80:]!r}"
    )


async def test_every_registered_tool_description_ends_with_trailer_via_registry():
    """INJ-01 / INJ-02 broadened: every tool description ends with TOOL_TRAILER.

    Pattern F (registry-introspection). Plan 06-02 broadens the focused
    list_databases-only test from Plan 01-05 to iterate every tool via
    `await mcp.list_tools()` so any future tool addition that forgets
    TOOL_TRAILER fails CI at test time (D6 INJ-02 + ANNO-02). Coexists
    with the focused test above — the focused test names a specific tool,
    this one enforces the invariant across the whole registry.
    """
    tools = await mcp.list_tools()
    assert tools, "No tools registered"
    for tool in tools:
        assert tool.description, f"tool '{tool.name}' has no description"
        assert tool.description.rstrip().endswith(config.TOOL_TRAILER), (
            f"tool '{tool.name}' description does not end with TOOL_TRAILER. "
            f"Got: {(tool.description or '')[-80:]!r}"
        )
