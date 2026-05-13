"""
Discovery tool handlers — list_databases (registered), list_tables, describe_table.

D-01: Per-domain grouping. list_databases was registered in Phase 1;
list_tables and describe_table are registered in Phase 2 (this plan).

DISC-05 / D2-14 / D2-15: Both hidden and nonexistent tables are rejected by the
SAME code path (_resolve_table → _visible_tables → raise_unknown_table). This
guarantees that callers cannot distinguish a hidden table from a nonexistent one.
NEVER add a separate HIDDEN_TABLES check before calling _resolve_table — that
would create a detectable side-channel (Pitfall 1).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolAnnotations
from pydantic import Field

from mcp_zeeker import config
from mcp_zeeker.core.config_lookup import hidden_columns_for
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed  # noqa: F401
from mcp_zeeker.core.envelope import Envelope
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.server import mcp
from mcp_zeeker.tools.discovery_models import ColumnInfo, TableSchema

log = structlog.get_logger()

_DESCRIPTION = (
    "List the four Singapore legal databases available on data.zeeker.sg, "
    "with one-line descriptions and visible table counts. "
    "Rate limits: 20/burst, 60/minute, 5000/day per IP. " + config.TOOL_TRAILER
)

_LIST_TABLES_DESCRIPTION = (
    "List visible tables in a Singapore legal database on data.zeeker.sg. "
    "Returns table names, row counts, and one-line descriptions. "
    "Hidden platform tables are excluded. "
    "Rate limits: 20/burst, 60/minute, 5000/day per IP. " + config.TOOL_TRAILER
)

_DESCRIBE_TABLE_DESCRIPTION = (
    "Describe the schema of a visible table on data.zeeker.sg, returning column names, "
    "types, light vs available column sets, URL-keyed support, and fragment support. "
    "Rate limits: 20/burst, 60/minute, 5000/day per IP. " + config.TOOL_TRAILER
)


# ---------------------------------------------------------------------------
# Shared helpers (DISC-05 / D2-14 / D2-15)
# ---------------------------------------------------------------------------


def raise_unknown_database(database: str) -> None:
    """Single emission point for unknown_database errors (ERR-02, D2-17).

    Raises ToolError with a stable message prefix so callers can match on it.
    Called ONLY when database is not in config.ALLOWED_DATABASES.
    """
    raise ToolError(f"unknown_database: Database not found: {database}")


def raise_unknown_table(database: str, table: str) -> None:
    """Single emission point for unknown_table errors (D2-14, DISC-05).

    Used for BOTH hidden tables AND genuinely nonexistent tables — same function,
    same message text, no side-channel (D2-15).

    INJ-05 note: {database}.{table} here are request parameter IDENTIFIERS (not
    user-supplied filter values), so echoing them in the error message is safe and
    intentional. Phase 3 filter values are a different threat surface.
    """
    raise ToolError(f"unknown_table: Table not found: {database}.{table}")


async def _visible_tables(database: str) -> set[str]:
    """Return the set of non-hidden table names for a database.

    Applies BOTH the upstream hidden flag AND the config denylist (HIDDEN_TABLES).
    Used by list_tables AND describe_table — single source of truth for what is
    visible (DISC-05: both hidden and nonexistent fail `name not in visible`).

    CRITICAL (Pitfall 1): NEVER add a separate HIDDEN_TABLES pre-check before
    calling this function. The DISC-05 side-channel test detects separate code
    paths by patching raise_unknown_table and counting calls. A pre-check would
    short-circuit some paths and break the counter assertion.
    """
    summary = await DatasetteClient.current().get_database(database)
    hidden_set = config.HIDDEN_TABLES.get(database, set())
    return {
        t.name
        for t in summary.tables
        if not t.hidden and t.name not in hidden_set
    }


async def _resolve_table(database: str, table: str) -> None:
    """Validate database exists and table is visible.

    Raises raise_unknown_database if database not in ALLOWED_DATABASES.
    Raises raise_unknown_table if table is hidden OR nonexistent.

    D2-15: same code path for hidden and nonexistent — no side-channel.
    Both hidden and nonexistent tables fail the `table not in visible` dict
    check; raise_unknown_table is the single emission point for both cases.
    """
    if database not in config.ALLOWED_DATABASES:
        raise_unknown_database(database)
    visible = await _visible_tables(database)
    if table not in visible:
        raise_unknown_table(database, table)


def _supports_fragments(database: str, table: str) -> bool:
    """Return True if the table participates in the fragment relationship (Open Q1).

    Dual-direction semantic (Open Q1 resolution): True for BOTH parent tables AND
    fragment tables. This lets describe_table convey to clients that a table has a
    corresponding fragment companion regardless of which direction they query.
    """
    key = f"{database}.{table}"
    if key in config.FRAGMENT_PARENTS:
        return True  # table IS a fragment table (has a parent)
    # table IS a parent — check if any fragment entry references it
    return any(
        v.get("parent_table") == table
        for k, v in config.FRAGMENT_PARENTS.items()
        if k.startswith(f"{database}.")
    )


# ---------------------------------------------------------------------------
# Registered tool handlers
# ---------------------------------------------------------------------------


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


@mcp.tool(
    name="list_tables",
    description=_LIST_TABLES_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def list_tables(
    database: Annotated[str, Field(description="Database name (e.g. 'zeeker-judgements')")],
) -> Envelope:
    """List visible tables for a database (DISC-02).

    Filters out upstream-hidden tables and config.HIDDEN_TABLES entries.
    Merges upstream metadata descriptions with config.TABLE_DESCRIPTIONS fallback (D2-01).
    row_count passes through honestly as None when upstream reports null (D2-13).
    """
    if database not in config.ALLOWED_DATABASES:
        raise_unknown_database(database)

    summary = await DatasetteClient.current().get_database(database)
    hidden_set = config.HIDDEN_TABLES.get(database, set())

    rows = []
    for t in summary.tables:
        if t.hidden or t.name in hidden_set:
            continue
        meta = await MetadataCache.current().get_table_metadata(database, t.name)
        description = (
            (meta or {}).get("description")
            or config.TABLE_DESCRIPTIONS.get(database, {}).get(t.name, "")
        )
        rows.append({"name": t.name, "row_count": t.count, "description": description})

    return Envelope.for_table_list(database=database, rows=rows)


@mcp.tool(
    name="describe_table",
    description=_DESCRIBE_TABLE_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def describe_table(
    database: Annotated[str, Field(description="Database name (e.g. 'zeeker-judgements')")],
    table: Annotated[str, Field(description="Table name (e.g. 'judgments')")],
) -> Envelope:
    """Describe a visible table's schema (DISC-03, DISC-04, DISC-05).

    Returns exactly 8 fields per D2-12 locked shape. Hidden columns are stripped
    before building available_columns (D2-11). light_columns are intersected with
    available_columns to prevent config drift from leaking hidden columns (D2-11).

    Security: _resolve_table is the FIRST call — it is the ONLY gate for hidden
    and nonexistent tables (D2-15, Pitfall 1). Do NOT add a pre-check.
    """
    await _resolve_table(database, table)

    summary = await DatasetteClient.current().get_database(database)
    t = next(ts for ts in summary.tables if ts.name == table)

    hidden_cols = hidden_columns_for(database, table)
    available_columns = [c for c in t.columns if c not in hidden_cols]
    light_columns = [
        c
        for c in config.LIGHT_COLUMNS.get(f"{database}.{table}", [])
        if c in available_columns
    ]

    column_types_map = await DatasetteClient.current().get_table_column_types(database)
    types_for_table = {
        **column_types_map.get(table, {}),
        **config.COLUMN_TYPES.get(f"{database}.{table}", {}),
    }
    # upstream wins: start from config fallback then overlay upstream
    types_for_table = {
        **config.COLUMN_TYPES.get(f"{database}.{table}", {}),
        **column_types_map.get(table, {}),
    }

    columns = []
    for c in available_columns:
        col_desc = await MetadataCache.current().get_column_description(database, table, c)
        if not col_desc:
            col_desc = config.COLUMN_DESCRIPTIONS.get(database, {}).get(table, {}).get(c, "")
        columns.append(
            ColumnInfo(
                name=c,
                type=types_for_table.get(c, "TEXT"),
                description=col_desc,
            )
        )

    url_keyed = f"{database}.{table}" in config.URL_COLUMNS

    supports_frags = _supports_fragments(database, table)

    meta = await MetadataCache.current().get_table_metadata(database, table)
    description = (
        (meta or {}).get("description")
        or config.TABLE_DESCRIPTIONS.get(database, {}).get(table, "")
    )

    schema = TableSchema(
        name=table,
        columns=columns,
        light_columns=light_columns,
        available_columns=available_columns,
        url_keyed=url_keyed,
        supports_fragments=supports_frags,
        row_count=t.count,
        description=description,
    )
    return Envelope.for_rows(database=database, table=table, rows=[schema.model_dump()])
