"""
Shared visibility helpers — table-level and column-level gates (D3-06, D3-07).

This module hosts the SINGLE-CALL-SITE gates that ensure:
- Hidden and nonexistent databases / tables / columns route through ONE helper
  each (raise_unknown_database, raise_unknown_table, raise_unknown_column),
  with no presence side-channel (Pitfall 1).
- Both Phase 2's discovery tools and Phase 3's retrieval tools consume the same
  helpers — cross-`tools/` imports were a smell, so the helpers now live in
  `core/` and `tools/discovery.py` re-exports them for backward compat.

CRITICAL (Pitfall 1): NEVER add a separate `if column in HIDDEN_COLUMNS:`
pre-check before `_visible_columns`. The counter-patch test in
`tests/tools/test_retrieval_side_channel.py` (Plan 03-02) asserts code-path
identity by patching `raise_unknown_column` and counting invocations. A
short-circuiting pre-check would break that assertion and leak presence info.
"""

from __future__ import annotations

from typing import NoReturn

from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.config_lookup import hidden_columns_for
from mcp_zeeker.core.datasette_client import DatasetteClient

# ---------------------------------------------------------------------------
# Sole-emission `raise_unknown_*` helpers (D2-14, D2-17, D3-07, D3-14)
# ---------------------------------------------------------------------------


def raise_unknown_database(database: str) -> NoReturn:
    """Single emission point for unknown_database errors (ERR-02, D2-17).

    Raises ToolError with a stable message prefix so callers can match on it.
    Called ONLY when database is not in config.ALLOWED_DATABASES.
    """
    raise ToolError(f"unknown_database: Database not found: {database}")


def raise_unknown_table(database: str, table: str) -> NoReturn:
    """Single emission point for unknown_table errors (D2-14, DISC-05).

    Used for BOTH hidden tables AND genuinely nonexistent tables — same function,
    same message text, no side-channel (D2-15).

    INJ-05 note: {database}.{table} here are request parameter IDENTIFIERS (not
    user-supplied filter values), so echoing them in the error message is safe and
    intentional. Phase 3 filter values are a different threat surface.
    """
    raise ToolError(f"unknown_table: Table not found: {database}.{table}")


def raise_unknown_column(database: str, table: str, column: str) -> NoReturn:
    """Single emission point for unknown_column errors (D3-07).

    Used for BOTH hidden columns AND genuinely nonexistent columns — same function,
    no side-channel. The counter-patch test in test_retrieval_side_channel.py
    proves code-path identity (Plan 03-02).

    INJ-05: {column} is a request identifier (user-supplied column name in
    filters/sort/columns parameters), not a filter VALUE. Echoing it back is
    safe; filter values are the protected threat surface (D3-09).
    """
    raise ToolError(f"unknown_column: Column not found: {database}.{table}.{column}")


def raise_unsupported_table_for_fetch(database: str, table: str) -> NoReturn:
    """Single emission point for fetch on a non-URL-keyed table (D3-14, FETCH-04).

    Raised when the caller asks fetch() for a table absent from config.URL_COLUMNS.
    """
    raise ToolError(f"unsupported_table_for_fetch: Table {database}.{table} has no URL column")


def raise_not_found(database: str, table: str) -> NoReturn:
    """Single emission point for fetch zero-row responses (D3-14 step 4, FETCH-05).

    URL is NEVER echoed (INJ-05). The {database}.{table} identifiers are safe
    (request identifiers, not user content).
    """
    raise ToolError(f"not_found: No row found in {database}.{table} for the given URL")


def raise_invalid_query() -> NoReturn:
    """Single emission point for invalid_query errors (D4-09, SEARCH-06, INJ-05).

    Triggered by THREE handler paths in Phase 4 search:
      (a) Empty or whitespace-only query (D4-19 step 1 — gate fires BEFORE
          escape_fts5; empty string would otherwise produce an FTS5 syntax
          error upstream).
      (b) Limit out-of-range from a direct caller bypassing the Pydantic
          Field(ge=1, le=100) clamp (D4-11 belt-and-suspenders).
      (c) All per-table fan-out tasks failed with upstream HTTP 400 — every
          target table returned an FTS5 syntax error (defensive, 04-RESEARCH
          §3.7). The orchestrator uses UpstreamCallFailed.status to detect this
          case and the handler maps it through this helper.

    INJ-05 / D3-09 / D4-07 / 04-RESEARCH §3.3 (Pitfall 5): the message is a
    FIXED literal. The user query string is NEVER interpolated, f-string'd,
    or otherwise echoed. Locked-catalog discipline mirrors filter_compiler's
    `invalid_filter_op:` fixed-literal pattern (T-03-01 / WR-02). The locked
    Phase 4 search-error catalog has exactly one search code (`invalid_query`)
    alongside the 6 retrieval codes — see PRD §12 / D3-12 / WR-02.
    """
    raise ToolError("invalid_query: query syntax not supported")


# ---------------------------------------------------------------------------
# Table-level visibility (Phase 2 extract from tools/discovery.py)
# ---------------------------------------------------------------------------


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
    return {t.name for t in summary.tables if not t.hidden and t.name not in hidden_set}


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


# ---------------------------------------------------------------------------
# Column-level visibility (Phase 3, D3-06 — mirrors _visible_tables shape)
# ---------------------------------------------------------------------------


async def _visible_columns(database: str, table: str) -> set[str]:
    """Return the set of visible column names for database.table.

    Reuses describe_table's logic: upstream column set minus hidden_columns_for.
    Reads via `DatasetteClient.current().get_database(database)` — the same
    upstream call used by `_visible_tables` (D3-06 / Pitfall 5: single source
    of truth for column visibility).

    CRITICAL (Pitfall 1): NEVER add a separate HIDDEN_COLUMNS pre-check before
    this function — the counter-patch test in test_retrieval_side_channel.py
    detects separate code paths via raise_unknown_column invocation counts.
    """
    summary = await DatasetteClient.current().get_database(database)
    t = next(ts for ts in summary.tables if ts.name == table)
    return set(t.columns) - hidden_columns_for(database, table)
