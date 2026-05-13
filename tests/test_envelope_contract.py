"""
Registry-introspection contract tests for ENV-07, ANNO-01, ANNO-02, ANNO-03, TRANSPORT-04.

Pattern F: enumerate every registered tool via `await mcp.list_tools()` and assert
the full contract holds dynamically. These tests survive every later-phase tool addition
because the assertion shape iterates over all registered tools.
"""

from __future__ import annotations

from mcp_zeeker import config
from mcp_zeeker.core.envelope import Envelope
from mcp_zeeker.server import mcp


async def test_every_registered_tool_returns_envelope():
    """ENV-07: Every registered tool return annotation is Envelope."""
    tools = await mcp.list_tools()
    assert tools, "No tools registered — at least list_databases should be present"
    for tool in tools:
        assert tool.return_type is Envelope, (
            f"tool '{tool.name}' return_type is {tool.return_type!r}, expected Envelope"
        )


async def test_every_registered_tool_description_ends_with_trailer():
    """ANNO-02 / INJ-01: Every tool description ends with config.TOOL_TRAILER verbatim."""
    tools = await mcp.list_tools()
    assert tools, "No tools registered"
    for tool in tools:
        assert tool.description, f"tool '{tool.name}' has no description"
        assert tool.description.rstrip().endswith(config.TOOL_TRAILER), (
            f"tool '{tool.name}' description does not end with TOOL_TRAILER. "
            f"Got: {tool.description[-80:]!r}"
        )


async def test_every_registered_tool_has_required_annotations():
    """ANNO-01: Every tool carries readOnlyHint=True, idempotentHint=True, openWorldHint=True."""
    tools = await mcp.list_tools()
    assert tools, "No tools registered"
    for tool in tools:
        assert tool.annotations is not None, f"tool '{tool.name}' has no annotations"
        assert tool.annotations.readOnlyHint is True, f"tool '{tool.name}' readOnlyHint is not True"
        assert tool.annotations.idempotentHint is True, (
            f"tool '{tool.name}' idempotentHint is not True"
        )
        assert tool.annotations.openWorldHint is True, (
            f"tool '{tool.name}' openWorldHint is not True"
        )


async def test_every_registered_tool_schema_is_flat():
    """TRANSPORT-04: Every tool's input schema is a flat type:object (no anyOf/oneOf/allOf)."""
    tools = await mcp.list_tools()
    assert tools, "No tools registered"
    for tool in tools:
        schema = tool.parameters
        assert schema.get("type") == "object", (
            f"tool '{tool.name}' top-level schema type is {schema.get('type')!r}, expected 'object'"
        )
        assert "anyOf" not in schema, f"tool '{tool.name}' schema has anyOf at top level"
        assert "oneOf" not in schema, f"tool '{tool.name}' schema has oneOf at top level"
        assert "allOf" not in schema, f"tool '{tool.name}' schema has allOf at top level"


async def test_every_registered_tool_description_mentions_rate_limit():
    """ANNO-03: Every tool description contains the strict rate-limit literal."""
    tools = await mcp.list_tools()
    assert tools, "No tools registered"
    for tool in tools:
        assert "20/burst, 60/minute, 5000/day" in (tool.description or ""), (
            f"tool '{tool.name}' description missing rate-limit literal"
        )
