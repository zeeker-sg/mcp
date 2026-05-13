"""
Discovery tool handlers — list_databases (registered), list_tables and describe_table (stubs).

D-01: Per-domain grouping. Only list_databases is registered with the mcp.tool decorator
in Phase 1; list_tables and describe_table are unregistered async stubs raising
NotImplementedError until Phase 2 overwrites them.
"""

from __future__ import annotations

import asyncio

import structlog
from fastmcp.tools.tool import ToolAnnotations

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed  # noqa: F401
from mcp_zeeker.core.envelope import Envelope
from mcp_zeeker.server import mcp

log = structlog.get_logger()

_DESCRIPTION = (
    "List the four Singapore legal databases available on data.zeeker.sg, "
    "with one-line descriptions and visible table counts. "
    "Rate limits: 20/burst, 60/minute, 5000/day per IP. "
    + config.TOOL_TRAILER
)


@mcp.tool(
    name="list_databases",
    description=_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_databases() -> Envelope:
    """Phase 1 first tool — proves the end-to-end transport + envelope path."""
    client = DatasetteClient.current()
    summaries = await asyncio.gather(
        *(client.get_database(name) for name in config.ALLOWED_DATABASES),
        return_exceptions=False,
    )
    rows = []
    for name, summary in zip(config.ALLOWED_DATABASES, summaries, strict=True):
        hidden = config.HIDDEN_TABLES.get(name, set())
        visible_count = sum(1 for t in summary.tables if t.name not in hidden)
        rows.append(
            {
                "name": name,
                "description": config.DATABASE_DESCRIPTIONS.get(name, ""),
                "table_count": visible_count,
            }
        )
    return Envelope.for_database_list(rows=rows)


# --- Unregistered stubs (Phase 2 will register these) ---
# D-01: Per-domain grouping — handlers live here, registration deferred.


async def list_tables(database: str) -> Envelope:
    """Stub — list_tables is registered in Phase 2 (discovery + denylists)."""
    raise NotImplementedError("list_tables is registered in Phase 2 (discovery + denylists)")


async def describe_table(database: str, table: str) -> Envelope:
    """Stub — describe_table is registered in Phase 2 (discovery + denylists)."""
    raise NotImplementedError(
        "describe_table is registered in Phase 2 (discovery + denylists)"
    )
