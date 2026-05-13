"""
Focused test for ANNO-02 (INJ-01): list_databases tool description ends with TOOL_TRAILER.

Per 01-PATTERNS.md, this file is a thin focused redirect. The primary iterating test lives
in test_envelope_contract.py::test_every_registered_tool_description_ends_with_trailer.
This file provides an explicit named test for list_databases so the 01-VALIDATION.md row
`tests/test_tool_trailer.py` is satisfiable with a dedicated automated command.
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
