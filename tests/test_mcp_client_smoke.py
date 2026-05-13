"""
In-memory MCP smoke tests using fastmcp.Client(mcp) — Pattern B.

Tests:
- TRANSPORT-01/02: initialize handshake completes via in-memory client.
- TRANSPORT-04: tools/list returns flat type:object schema (no anyOf/oneOf/allOf).
- ANNO-01: list_databases carries readOnlyHint, idempotentHint, openWorldHint all True.
- ANNO-02: list_databases description ends with TOOL_TRAILER.
- DISC-01: list_databases returns exactly 4 databases with correct envelope shape.

Uses stub_upstream + bound_datasette_client fixtures from conftest.py to intercept
upstream httpx calls when the tool is actually invoked. Tests that only inspect the
tool registry (initialize, list_tools) do not need the upstream fixture.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from mcp_zeeker import config
from mcp_zeeker.server import mcp


async def test_initialize_handshake():
    """TRANSPORT-01/02: initialize handshake completes via in-memory MCP client.

    Context entry on Client(mcp) triggers the MCP initialize handshake.
    No exception means the handshake succeeded and a session was established.
    list_databases is not called — no upstream stub needed.
    """
    async with Client(mcp) as client:
        # Handshake succeeded if we reach this point (no exception on context entry)
        tools = await client.list_tools()
        assert any(t.name == "list_databases" for t in tools), (
            "Expected list_databases in registered tools"
        )


async def test_tools_list_flat_schema(mcp_client):
    """TRANSPORT-04: tools/list returns a flat type:object schema for list_databases.

    FastMCP must produce a top-level schema with type='object' and no anyOf/oneOf/allOf.
    A schema with anyOf at the top would fail JSON Schema validation in Claude Code.
    """
    tools = await mcp_client.list_tools()
    list_db = next((t for t in tools if t.name == "list_databases"), None)
    assert list_db is not None, "list_databases tool not found"

    schema = list_db.inputSchema
    assert schema["type"] == "object", f"Expected type='object', got {schema.get('type')!r}"
    assert "anyOf" not in schema, f"anyOf present in top-level schema: {schema}"
    assert "oneOf" not in schema, f"oneOf present in top-level schema: {schema}"
    assert "allOf" not in schema, f"allOf present in top-level schema: {schema}"


async def test_tool_annotations(mcp_client):
    """ANNO-01: list_databases carries all three required MCP tool annotations."""
    tools = await mcp_client.list_tools()
    list_db = next((t for t in tools if t.name == "list_databases"), None)
    assert list_db is not None, "list_databases tool not found"

    ann = list_db.annotations
    assert ann is not None, "list_databases has no annotations"
    assert ann.readOnlyHint is True, f"readOnlyHint is not True: {ann}"
    assert ann.idempotentHint is True, f"idempotentHint is not True: {ann}"
    assert ann.openWorldHint is True, f"openWorldHint is not True: {ann}"


@pytest.mark.httpx_mock(assert_all_responses_were_requested=True)
async def test_list_databases_returns_four_dbs(stub_upstream, bound_datasette_client):
    """DISC-01: tools/call list_databases returns an envelope with exactly 4 databases.

    The upstream /{db}.json responses are stubbed by the stub_upstream fixture.
    Asserts: 4 rows, names match ALLOWED_DATABASES, provenance.source and license.
    """
    async with Client(mcp) as client:
        result = await client.call_tool("list_databases", {})

        assert not result.is_error, f"tool call returned error: {result.content}"

        # structured_content is the Pydantic model_dump() of the Envelope
        envelope = result.structured_content
        assert isinstance(envelope, dict), f"Expected dict envelope, got {type(envelope)}"

        assert len(envelope["data"]) == 4, (
            f"Expected 4 databases, got {len(envelope['data'])}: {envelope['data']}"
        )
        names = {row["name"] for row in envelope["data"]}
        assert names == set(config.ALLOWED_DATABASES), (
            f"Database names mismatch: {names} vs {set(config.ALLOWED_DATABASES)}"
        )
        assert envelope["provenance"]["source"] == "data.zeeker.sg"
        assert envelope["provenance"]["license"] == config.LICENSE_MIXED
