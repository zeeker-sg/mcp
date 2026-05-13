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
from fastmcp.tools.tool import ToolAnnotations
from pydantic import Field

from mcp_zeeker import config
from mcp_zeeker.core.config_lookup import hidden_columns_for, url_column_for
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.envelope import Envelope
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.core.visibility import (
    _resolve_table,
    _visible_tables,
    raise_unknown_database,
    raise_unknown_table,
)
from mcp_zeeker.server import mcp
from mcp_zeeker.tools.discovery_models import ColumnInfo, TableSchema

# Re-exports above keep `from mcp_zeeker.tools.discovery import raise_unknown_table`
# resolvable for Phase 2 tests (D3-06 — helpers moved to core/visibility.py in
# Phase 3 to avoid cross-`tools/` imports between discovery.py and retrieval.py).
__all__ = [
    "_resolve_table",
    "_visible_tables",
    "describe_table",
    "list_databases",
    "list_tables",
    "raise_unknown_database",
    "raise_unknown_table",
]

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
#
# raise_unknown_database, raise_unknown_table, _visible_tables, _resolve_table
# live in mcp_zeeker.core.visibility (Phase 3 / D3-06 move). They are re-exported
# at the top of this module for backward compatibility — Phase 2 tests still
# import them from `mcp_zeeker.tools.discovery`. The counter-patch identity
# `from mcp_zeeker.tools.discovery import raise_unknown_table` IS the same
# function object as `from mcp_zeeker.core.visibility import raise_unknown_table`
# (re-export is a name binding, not a wrapping shim).
# ---------------------------------------------------------------------------


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
        hidden_set = config.HIDDEN_TABLES.get(name, set())
        visible_count = sum(1 for t in summary.tables if not t.hidden and t.name not in hidden_set)
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
        description = (meta or {}).get("description") or config.TABLE_DESCRIPTIONS.get(
            database, {}
        ).get(t.name, "")
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
        c for c in config.LIGHT_COLUMNS.get(f"{database}.{table}", []) if c in available_columns
    ]

    column_types_map = await DatasetteClient.current().get_table_column_types(database)
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

    # CR-01 / D2-10 / D3-04 single-source-of-truth: read URL_COLUMNS via the
    # url_column_for helper so describe_table and fetch share one call-site
    # (mirror of hidden_columns_for discipline). Any future change to the
    # URL_COLUMNS shape stays a one-line edit in core/config_lookup.py.
    url_keyed = url_column_for(database, table) is not None

    supports_frags = _supports_fragments(database, table)

    meta = await MetadataCache.current().get_table_metadata(database, table)
    description = (meta or {}).get("description") or config.TABLE_DESCRIPTIONS.get(
        database, {}
    ).get(table, "")

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
