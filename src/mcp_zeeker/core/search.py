"""
Search orchestration — auto-discovery + per-table fan-out + round-robin merge
(D4-02 / D4-05 / D4-12 / D4-18).

This module is the SOLE search orchestrator. The handler in `tools/search.py`
delegates discovery (`searchable_tables_for`, `resolve_preview_columns`) and
the concurrent fan-out (`fan_out_search`) here; no inline per-table dispatch.

Public surface:
- `resolve_preview_columns(db, table, available)` — pure helper, D4-12 (Plan
  04-01).
- `searchable_tables_for(db)` — FOUR-gate filter (fts_table-not-null / visible
  / not-denylist-suffix / preview-columns-resolvable), D4-02 (Plan 04-02).
- `fan_out_search(escaped_query, target_tables, per_table_limit)` —
  `anyio.create_task_group` + `move_on_after(0.8)` per D4-06; `zip_longest`
  round-robin merge per D4-05; returns 4-tuple including per-failure status
  codes for D4-09 case (c) detection (Plan 04-02).

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
- `fan_out_search` NEVER raises: per-table failures are captured in
  `failure_statuses` and the handler decides error mapping (D4-09).

References: D4-02 / D4-05 / D4-12 / D4-18, 04-RESEARCH.md §3.1 / §3.2 / §3.7 /
§3.8, 04-PATTERNS.md (search orchestrator templates).
"""

from __future__ import annotations

from itertools import zip_longest

import anyio
import structlog

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed
from mcp_zeeker.core.visibility import _visible_tables

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

    Applies the FOUR-gate filter per 04-RESEARCH §3.2 in order:

      1. `fts_table is not None` on TableSummary — FTS index exists upstream.
         This gate is LOAD-BEARING for safety (04-RESEARCH §3.2, Pitfall 3):
         Datasette silently ignores `_search=` on non-FTS tables and would
         otherwise return rowid-ordered rows as fake "search results."
      2. Table is in `_visible_tables(db)` — Phase 2 hidden-flag + HIDDEN_TABLES.
      3. Table name does NOT end with any pattern in
         `config.SEARCH_DENYLIST_PATTERNS` (initial value: `("_fragments",)` — D4-04).
      4. `resolve_preview_columns(db, table, available)` returns non-null
         title AND url — D4-12 drop signal; logs `search_table_no_preview_columns`.

    Reads from `DatasetteClient.current().get_database(db)`. Does NOT consume
    `MetadataCache.get_table_metadata` because /-/metadata.json is sparse and
    lacks the `fts_table` field (04-RESEARCH §3.2 corrected planner input).

    Returns a tuple in upstream metadata order — deterministic for tests.
    Iteration order matches `summary.tables` upstream order so the handler's
    alphabetical-DB sort plus this in-DB order gives a fully deterministic
    round-robin merge (D4-05 / 04-RESEARCH §3.9).

    INJ-05 / D4-07: the `search_table_no_preview_columns` log binding exposes
    `database` and `table` only — the query string is never bound here (this
    function does not receive the query at all; the contract isolates the
    discovery surface from query content).
    """
    summary = await DatasetteClient.current().get_database(db)
    visible = await _visible_tables(db)
    out: list[str] = []
    for t in summary.tables:
        # Gate 1 — fts_table presence (LOAD-BEARING; Pitfall 3).
        if t.fts_table is None:
            continue
        # Gate 2 — Phase 2 visibility (hidden flag + HIDDEN_TABLES denylist).
        if t.name not in visible:
            continue
        # Gate 3 — denylist suffix (D4-04 — excludes *_fragments).
        if any(t.name.endswith(p) for p in config.SEARCH_DENYLIST_PATTERNS):
            continue
        # Gate 4 — preview-shape resolvable (D4-12). Uses TableSummary.columns
        # to avoid an extra upstream round-trip; the handler revisits via
        # _visible_columns for the per-table preview at dispatch time.
        available = set(t.columns)
        preview = resolve_preview_columns(db, t.name, available)
        if preview is None:
            log.warning("search_table_no_preview_columns", database=db, table=t.name)
            continue
        out.append(t.name)
    return tuple(out)


async def _one_table(
    db: str,
    table: str,
    preview: dict[str, str | None],
    escaped: str,
    per_table_limit: int,
    out_rows: dict[tuple[str, str], list[dict]],
    out_totals: dict[str, int],
    failures: list[Exception],
) -> None:
    """Single per-table FTS dispatch — never raises (D4-07 / INJ-05).

    Failures are captured in the shared `failures` list so the orchestrator
    can inspect `UpstreamCallFailed.status` for the all-tables-400 → invalid_query
    promotion (D4-09 case (c)). The log binding exposes `database`, `table`,
    and `error_class` only — the query string is NEVER bound (INJ-05 / D4-07).
    """
    # Build dispatch params: ordered list-of-tuples for httpx. `_shape=objects`
    # is prepended automatically by DatasetteClient.get_table_rows.
    # `_col` projection skips any preview field whose resolved column is None
    # (D4-12 — date/summary may legitimately be unmapped).
    params: list[tuple[str, str]] = [
        ("_search", escaped),
        ("_size", str(per_table_limit)),
        *[("_col", c) for c in preview.values() if c is not None],
    ]
    try:
        result = await DatasetteClient.current().get_table_rows(db, table, params)
    except UpstreamCallFailed as exc:
        failures.append(exc)
        log.warning(
            "search_table_failed",
            database=db,
            table=table,
            error_class=type(exc).__name__,
        )
        return
    # Normalize per D4-12 / D4-21 — emit EXACTLY 6 keys per row.
    # resolve_preview_columns already filtered HEAVY_COLUMNS at resolution time
    # (defense-in-depth — D3-04 / D4-12 / Plan 04-01), so heavy columns cannot
    # be inlined here even if upstream returned them.
    raw_rows = result.get("rows") or []
    normalized: list[dict] = []
    for r in raw_rows:
        title_col = preview.get("title")
        date_col = preview.get("date")
        summary_col = preview.get("summary")
        url_col = preview.get("url")
        normalized.append(
            {
                "title": r.get(title_col) if title_col else None,
                "date": r.get(date_col) if date_col else None,
                "summary": r.get(summary_col) if summary_col else None,
                "url": r.get(url_col) if url_col else None,
                "database": db,
                "table": table,
            }
        )
    out_rows[(db, table)] = normalized
    # filtered_table_rows_count is always present per 04-RESEARCH Probe 6
    # (even on zero-hit and `_search`-omitted responses). Default to 0 for
    # safety in case upstream omits the key under exceptional conditions.
    out_totals[f"{db}.{table}"] = int(result.get("filtered_table_rows_count") or 0)


def _round_robin_merge(
    rows_by_table: dict[tuple[str, str], list[dict]],
    limit: int,
) -> list[dict]:
    """Round-robin merge per D4-05 — iterate per-table lists in insertion order.

    `rows_by_table` insertion order is preserved per Python 3.7+ dict ordering;
    the caller (`fan_out_search`) seeds the dict with the `target_tables` order
    so the handler's alphabetical-DB / metadata-order-within-DB sequence
    survives the merge.

    Exhausted tables (lists shorter than the longest) are skipped silently —
    `zip_longest` yields `None` for missing positions.
    Stops at `limit` rows for early-exit safety; the orchestrator slices again
    post-merge as belt-and-suspenders.
    """
    out: list[dict] = []
    for col in zip_longest(*rows_by_table.values()):
        for row in col:
            if row is None:
                continue
            out.append(row)
            if len(out) >= limit:
                return out
    return out


async def fan_out_search(
    escaped_query: str,
    target_tables: list[tuple[str, str, dict[str, str | None]]],
    per_table_limit: int,
) -> tuple[list[dict], dict[str, int], int, list[int | None]]:
    """Concurrent per-table FTS fan-out + round-robin merge (D4-05 / D4-06 / D4-18).

    Returns a 4-tuple:
        (merged_rows, upstream_total_hits, failed_tables, failure_statuses)

    - `merged_rows`: round-robin-merged preview rows, sliced to `per_table_limit`.
    - `upstream_total_hits`: dict keyed `"<db>.<table>"` → upstream
      `filtered_table_rows_count`. A failed table does NOT get an entry —
      the caller can derive failures via `failed_tables` count + missing keys.
    - `failed_tables`: count of per-table tasks that raised
      `UpstreamCallFailed`. Cancellation via the move_on_after budget
      contributes nothing — partial results are the documented behavior
      (Pitfall 4 / 04-CONTEXT D4-07).
    - `failure_statuses`: ordered list of per-failure
      `UpstreamCallFailed.status` values (or `None` for transport-layer
      failures). The handler uses this to detect all-tables-400 and map
      to `invalid_query` per D4-09 case (c) / 04-RESEARCH §3.7.

    NEVER raises. Failures are aggregated; the orchestrator returns the
    4-tuple regardless. The handler decides whether to promote to
    `invalid_query`, `upstream_unavailable`, or surface a partial-result
    envelope based on the failure list.

    INJ-05: the query string never appears in any log binding emitted from
    this function (or `_one_table`); failure logs bind `database`, `table`,
    `error_class` only.
    """
    # Seed the per-table row dict with the caller's target_tables order so the
    # round-robin merge (Python 3.7+ dict insertion order) is deterministic.
    out_rows: dict[tuple[str, str], list[dict]] = {(db, t): [] for db, t, _ in target_tables}
    out_totals: dict[str, int] = {}
    failures: list[Exception] = []

    # D4-06: structured concurrency under an outer 0.8s budget. The
    # move_on_after cancellation surfaces as task cancellation inside the
    # task group; cancelled tasks contribute nothing (no failure increment)
    # per 04-CONTEXT D4-07. Tasks that completed before the deadline have
    # already populated out_rows / out_totals.
    with anyio.move_on_after(0.8):
        async with anyio.create_task_group() as tg:
            for db, table, preview in target_tables:
                tg.start_soon(
                    _one_table,
                    db,
                    table,
                    preview,
                    escaped_query,
                    per_table_limit,
                    out_rows,
                    out_totals,
                    failures,
                )

    merged = _round_robin_merge(out_rows, per_table_limit)
    failure_statuses: list[int | None] = [getattr(exc, "status", None) for exc in failures]
    return merged, out_totals, len(failures), failure_statuses
