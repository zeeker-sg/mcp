"""
Search orchestration — auto-discovery + per-table fan-out + round-robin merge
(D4-02 / D4-05 / D4-12 / D4-18 — Phase 4 Plan 04-01 skeleton, Plan 04-02 body).

This module is the SOLE search orchestrator. The handler in `tools/search.py`
delegates discovery (`searchable_tables_for`, `resolve_preview_columns`) and
the concurrent fan-out (`fan_out_search`) here; no inline per-table dispatch.

Plan 04-01 ships:
- `resolve_preview_columns` (CONCRETE pure helper — D4-12).
- `searchable_tables_for` (SKELETON only, raises NotImplementedError).
- `fan_out_search` (SKELETON only, raises NotImplementedError).
- The import block + `log = structlog.get_logger()` binding so Plan 04-02 can
  body-fill without touching imports.

Plan 04-02 ships the bodies of `searchable_tables_for` (FOUR-gate filter:
fts_table-not-null / visible / not-denylist / preview-columns-resolvable)
and `fan_out_search` (anyio.create_task_group + move_on_after(0.8) per
D4-06 + zip_longest round-robin merge per D4-05).

Security properties (auditable by inspection):
- All HTTP IO routes through `DatasetteClient.current().get_table_rows(...)`
  (D-13 / D-14 / D-16 — retry-once-with-jitter inherited unchanged).
- `fts_table is not None` is the LOAD-BEARING discovery gate (04-RESEARCH §3.2
  / Pitfall 3): Datasette silently ignores `_search=` on non-FTS tables and
  would otherwise surface rowid-ordered rows as fake "search results."
  pdpc.enforcement_decisions is the canonical case (no FTS index upstream).
- NO user-supplied query text in any ToolError message or log line (INJ-05 /
  D3-09 / D4-07). Per-table failure log bindings expose `database`, `table`,
  and `error_class` only — NEVER the query string.
- Heavy columns (config.HEAVY_COLUMNS) can NEVER be selected as preview
  fields — `resolve_preview_columns` filters at resolution time so an
  override entry naming a heavy column cannot smuggle it into the preview
  shape (D3-04 defense-in-depth / D4-12).

References: D4-02 / D4-05 / D4-12 / D4-18, 04-RESEARCH.md §3.1 / §3.2 / §3.7 /
§3.8, 04-PATTERNS.md (search orchestrator templates).
"""

from __future__ import annotations

from itertools import zip_longest  # noqa: F401 — Plan 04-02 uses for round-robin merge

import anyio  # noqa: F401 — Plan 04-02 uses anyio.create_task_group + move_on_after
import structlog
from fastmcp.exceptions import ToolError  # noqa: F401 — Plan 04-02 uses for upstream_unavailable

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import (  # noqa: F401 — used by Plan 04-02 fan-out body
    DatasetteClient,
    UpstreamCallFailed,
)
from mcp_zeeker.core.visibility import (  # noqa: F401 — Plan 04-02 reads _visible_columns / _visible_tables
    _visible_columns,
    _visible_tables,
)

log = structlog.get_logger()


def resolve_preview_columns(
    db: str,
    table: str,
    available: set[str],
) -> dict[str, str | None] | None:
    """Resolve the 4 preview fields → upstream column name (or None) — D4-12.

    Pure function. SOLE call-site for `config.SEARCH_PREVIEW_DEFAULTS` and
    `config.SEARCH_PREVIEW_OVERRIDES`. Heavy columns (`config.HEAVY_COLUMNS`)
    are filtered at resolution time so a heavy column name appearing in
    `SEARCH_PREVIEW_OVERRIDES` cannot smuggle a heavy field into the preview
    shape (D3-04 defense-in-depth / D4-12).

    Resolution order per field:
      1. `SEARCH_PREVIEW_OVERRIDES["<db>.<table>"][<field>]` if present →
         use that value verbatim (including `None`, which means "explicitly
         suppress this field — emit null in the preview row").
      2. First column in `SEARCH_PREVIEW_DEFAULTS[<field>]` (ordered tuple)
         that is in `available` AND NOT in `config.HEAVY_COLUMNS` → use it.
      3. Otherwise → `None` (field will be null in the preview row).

    Returns `None` (drop signal) when `title` or `url` cannot be resolved.
    The caller (`searchable_tables_for`) drops the table from the fan-out
    with a structured `search_table_no_preview_columns` warning.

    Args:
        db: database name (used to construct the `<db>.<table>` override key).
        table: table name (used to construct the `<db>.<table>` override key).
        available: set of column names visible on this table.

    Returns:
        Dict with keys exactly `{"title", "date", "summary", "url"}` mapping
        to a column name (`str`) or `None` (field suppressed); OR `None` when
        title/url cannot be resolved (drop the table).
    """
    overrides = config.SEARCH_PREVIEW_OVERRIDES.get(f"{db}.{table}", {})
    out: dict[str, str | None] = {}
    for field, candidates in config.SEARCH_PREVIEW_DEFAULTS.items():
        if field in overrides:
            out[field] = overrides[field]
            continue
        chosen: str | None = None
        for cand in candidates:
            if cand in available and cand not in config.HEAVY_COLUMNS:
                chosen = cand
                break
        out[field] = chosen
    if out.get("title") is None or out.get("url") is None:
        return None
    return out


async def searchable_tables_for(db: str) -> tuple[str, ...]:
    """Discover FTS-indexed, visible, non-denied, preview-resolvable tables (D4-02).

    SKELETON — Plan 04-02 body-fills with the FOUR-gate filter per
    04-RESEARCH §3.2:

      1. `fts_table is not None` on TableSummary — FTS index exists upstream.
         This gate is LOAD-BEARING for safety (04-RESEARCH §3.2, Pitfall 3):
         Datasette silently ignores `_search=` on non-FTS tables.
      2. Table is in `_visible_tables(db)` — Phase 2 hidden-flag + HIDDEN_TABLES.
      3. Table name does NOT end with any pattern in
         `config.SEARCH_DENYLIST_PATTERNS` (initial value: `("_fragments",)` — D4-04).
      4. `resolve_preview_columns(db, table, available)` returns non-null
         title AND url — D4-12 drop signal.

    Reads from `DatasetteClient.current().get_database(db)`. Does NOT consume
    `MetadataCache.get_table_metadata` because /-/metadata.json is sparse and
    lacks the `fts_table` field (04-RESEARCH §3.2 corrected planner input).

    Returns a tuple in upstream metadata order — deterministic for tests.
    """
    raise NotImplementedError(
        "Plan 04-02 ships searchable_tables_for body — D4-02 (FOUR-gate filter)"
    )


async def fan_out_search(
    escaped_query: str,
    target_tables: list[tuple[str, str, dict[str, str | None]]],
    per_table_limit: int,
) -> tuple[list[dict], dict[str, int], int, list[int | None]]:
    """Concurrent per-table FTS fan-out + round-robin merge (D4-05 / D4-06 / D4-18).

    SKELETON — Plan 04-02 body-fills with the structured-concurrency
    orchestrator per D4-06:

    - Uses `anyio.create_task_group()` for structured concurrency.
    - Uses `anyio.move_on_after(0.8)` to enforce the per-fan-out latency
      budget (p95 < 1.5s envelope from CLAUDE.md / PRD §performance).
    - Each per-table task catches `UpstreamCallFailed` and records the
      exception in `failures` — NEVER raises out of the task group.
    - After the task group exits, merges per-table row lists round-robin
      via `itertools.zip_longest` so multi-DB queries interleave fairly
      (D4-05). Exhausted tables are skipped silently.

    Returns a 4-tuple:
        (merged_rows, upstream_total_hits, failed_tables, failure_statuses)

    - `merged_rows`: round-robin-merged preview rows, sliced to `per_table_limit`.
    - `upstream_total_hits`: dict keyed `"<db>.<table>"` → upstream
      `filtered_table_rows_count` value (drill-down hint for the LLM).
    - `failed_tables`: count of per-table tasks that raised
      `UpstreamCallFailed` (or timed out within the move_on_after budget).
    - `failure_statuses`: ordered list of per-failure `UpstreamCallFailed.status`
      values. Plan 04-02 handler uses this to detect the all-tables-400 case
      and map it to `invalid_query` per D4-09 case (c) / 04-RESEARCH §3.7.
      Entries are `int` for HTTP status codes (e.g. 400, 500) and `None` for
      transport-layer failures (no HTTP response parsed). The 4th element is
      a Plan 04-02 evolution of the 3-tuple from the Plan 04-01 plan text —
      see plan-checker LOW issue resolution: 04-01 stub matches Plan 04-02's
      binding to avoid a second-edit churn.
    """
    raise NotImplementedError(
        "Plan 04-02 ships fan_out_search body — D4-05 / D4-06 / D4-18 "
        "(structured concurrency + round-robin merge + status-aware failure list)"
    )
