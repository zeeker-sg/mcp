"""
Search tool handler — cross-database FTS preview rows (D-01 / D4-15 / D4-19).

D-01: Per-domain grouping. The single Phase 4 search handler lives here; the
orchestrator (`core.search.fan_out_search`) and the pure helpers
(`core.search.resolve_preview_columns`, `core.fts_escape.escape_fts5`) live in
`core/` per the cross-`tools/` discipline.

D4-19: Validation order (9 steps):
  1. empty-query gate (strip() check BEFORE escape — Pitfall 2 prevents the
     empty `""` FTS5 syntax error upstream)
  2. limit clamp belt-and-suspenders (D4-11 — Pydantic Field(ge=1, le=100) is
     primary; direct-caller path bypasses it)
  3. databases default (D4-10 — None / empty list → all ALLOWED_DATABASES)
  4. unknown_database check per requested DB (D4-10)
  5. auto-discovery + preview-resolution per sorted DB (D4-02 / D4-12)
  6. empty-target short-circuit → empty envelope (D4-03)
  7. escape_fts5 wrap (D4-08)
  8. fan_out_search dispatch (D4-05 / D4-06)
  9. all-fail mapping (D4-09 case (c) — all-400 → invalid_query; otherwise
     upstream_unavailable) + D4-13 defense-in-depth post-filter + envelope

INJ-05 / D3-09 / D4-07: NO user query string interpolates into any ToolError
message, log line, or structlog binding emitted from this module. Every error
routes through a sole-emission helper (`raise_invalid_query`,
`raise_unknown_database`) with a FIXED literal message. The lone
upstream-unavailable raise site uses a fixed literal string —
"upstream_unavailable: all search targets failed". The locked Phase 4 search
error catalog has exactly one search code (`invalid_query`) alongside the 6
retrieval codes (D3-12 / WR-02).

D4-22: Auto-discovery means adding a fifth ALLOWED_DATABASE that follows
naming conventions requires ZERO code edits here — `searchable_tables_for`
finds new tables via `fts_table is not None` and `resolve_preview_columns`
resolves the preview shape from `SEARCH_PREVIEW_DEFAULTS`.
"""

from __future__ import annotations

import time
from typing import Annotated

import structlog
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolAnnotations
from pydantic import BeforeValidator, Field

from mcp_zeeker import config
from mcp_zeeker.core.envelope import Envelope
from mcp_zeeker.core.fts_escape import escape_fts5
from mcp_zeeker.core.search import (
    fan_out_search,
    searchable_tables_for,
)
from mcp_zeeker.core.visibility import (
    _get_database_summary,
    _visible_tables,
    raise_invalid_query,
    raise_unknown_database,
)
from mcp_zeeker.server import mcp
from mcp_zeeker.tools._param_coercion import _coerce_json_list

log = structlog.get_logger()


# D4-15 verbatim — ends with config.TOOL_TRAILER (INJ-01 / ANNO-02), mentions
# auto-discovery semantics, preview-field null possibility, heavy-text
# exclusion, round-robin bias, drill-down hint, default/max limits, and the
# anonymous-tier rate-limit literal (ANNO-03).
_SEARCH_DESCRIPTION = (
    "Full-text search across Singapore legal databases on data.zeeker.sg. "
    "Searchable tables are auto-discovered from upstream FTS metadata; "
    "databases without a full-text index upstream are silently skipped. "
    "Returns preview rows with title, date, summary, url, database, table — "
    "any field except database/table may be null when the source table doesn't "
    "have a matching column. Heavy text columns are never inlined. "
    "Results are merged round-robin across searchable tables (databases with more "
    "tables get more slots in top results — use the `databases` parameter to scope). "
    "When pagination.upstream_total_hits exceeds returned counts, narrow the query "
    "or follow up with query_table to drill into a specific table. "
    "Default limit 20, max 100. "
    "Rate limits: 20/burst, 60/minute, 5000/day per IP. " + config.TOOL_TRAILER
)


@mcp.tool(
    name="search",
    description=_SEARCH_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def search(
    query: Annotated[
        str,
        Field(description="Full-text query (FTS5 phrase-wrapped server-side)."),
    ],
    databases: Annotated[
        list[str] | None,
        BeforeValidator(_coerce_json_list),
        Field(
            default=None,
            description=(
                "Optional subset of databases to search. Defaults to all "
                "configured databases. Pass empty list for same effect as None."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            default=20,
            ge=1,
            le=100,
            description="Max rows to return; default 20, max 100.",
        ),
    ] = 20,
) -> Envelope:
    """Cross-database FTS search (SEARCH-01..06, D4-15 / D4-19).

    Validation order (D4-19):
      1. empty/whitespace query → invalid_query (BEFORE escape — Pitfall 2)
      2. limit out-of-range → invalid_query (belt-and-suspenders past Pydantic)
      3. databases default → all ALLOWED_DATABASES when None/empty
      4. unknown_database per requested DB
      5. auto-discover + preview-resolve per sorted DB (alphabetical → deterministic
         round-robin)
      6. empty target_tables → empty envelope (multi-DB provenance)
      7. escape_fts5(query)
      8. fan_out_search dispatch (concurrent + 0.8s outer budget)
      9. all-fail mapping (all-400 → invalid_query / otherwise → upstream_unavailable)
     10. D4-13 defense-in-depth post-filter (race-condition guard)
     11. slice to limit + Envelope.for_search_results
    """
    # Step 1: empty-query gate (D4-19 step 1 — fires BEFORE escape).
    # The strip() check catches both "" and "   ". escape_fts5("") would produce
    # an FTS5 syntax error upstream — Pitfall 2.
    if not query.strip():
        raise_invalid_query()

    # Step 2: belt-and-suspenders limit clamp (D4-11 — Pydantic Field(ge=1, le=100)
    # is the primary gate via FastMCP dispatch; direct-caller path bypasses it).
    if limit < 1 or limit > 100:
        raise_invalid_query()

    # Step 3: databases default (D4-10 — None or empty list both fall back to
    # the configured ALLOWED_DATABASES tuple).
    target_dbs = list(databases) if databases else list(config.ALLOWED_DATABASES)

    # Step 4: unknown_database per requested DB (D4-10 — sole-emission helper
    # from Phase 1).
    for db in target_dbs:
        if db not in config.ALLOWED_DATABASES:
            raise_unknown_database(db)

    # Step 5: auto-discovery + preview-resolve per sorted DB (D4-02 / D4-12).
    # Alphabetical-DB iteration gives deterministic round-robin merge ordering
    # (D4-05 / 04-RESEARCH §3.9); within each DB, searchable_tables_for preserves
    # upstream metadata order.
    #
    # #6b / #9: Request-scoped memoization — fetch get_database(db) once per
    # DB and compute visible_tables from it. Pass both into
    # searchable_tables_for so it doesn't re-fetch. The preview is resolved
    # inside searchable_tables_for from TableSummary.columns (same source
    # _visible_columns would read), so the handler no longer needs to call
    # _visible_columns per table. This collapses ~20 get_database calls to ~4.
    # The per-DB visible sets are also saved for the Step 10 post-filter so it
    # doesn't need to re-fetch either — the post-filter reads the same snapshot
    # as discovery (which is also what the race-guard wants).
    discovery_start = time.perf_counter()
    target_tables: list[tuple[str, str, dict[str, str | None]]] = []
    # Per-DB visible-table sets from discovery snapshot; reused in post-filter.
    discovery_visible: dict[str, set[str]] = {}
    for db in sorted(target_dbs):
        summary = await _get_database_summary(db)
        hidden_set = config.HIDDEN_TABLES.get(db, set())
        visible = {t.name for t in summary.tables if not t.hidden and t.name not in hidden_set}
        discovery_visible[db] = visible
        discovered = await searchable_tables_for(db, summary=summary, visible=visible)
        for table, preview in discovered:
            target_tables.append((db, table, preview))
    discovery_ms = int((time.perf_counter() - discovery_start) * 1000)

    # Step 6: empty-target short-circuit (D4-03 — multi-DB provenance applies).
    # This is the documented "this DB has no FTS" path; the description tells the
    # LLM the response is honest (empty rows + empty upstream_total_hits).
    if not target_tables:
        # #6a / #8: emit timing even on short-circuit so the event is always
        # present for a search call (fan_out_ms and post_filter_ms are 0).
        log.info(
            "search_timing",
            tool="search",
            discovery_ms=discovery_ms,
            fan_out_ms=0,
            post_filter_ms=0,
        )
        return Envelope.for_search_results(
            rows=[],
            upstream_total_hits={},
            failed_tables=0,
        )

    # Step 7: FTS5 phrase-wrap escape (D4-08). escape_fts5 is the SOLE escape
    # call-site in the handler — the orchestrator passes the escaped string
    # through to upstream without re-wrapping.
    escaped = escape_fts5(query)

    # Step 8: concurrent fan-out (D4-05 / D4-06). Per-table fetch quota equals
    # `limit` per D4-05; the round-robin merge inside fan_out_search already
    # slices to `per_table_limit` as belt-and-suspenders.
    fan_out_start = time.perf_counter()
    rows, upstream_total_hits, failed_tables, failure_statuses = await fan_out_search(
        escaped, target_tables, limit
    )
    fan_out_ms = int((time.perf_counter() - fan_out_start) * 1000)

    # Step 9: all-fail mapping (D4-09 case (c) / 04-RESEARCH §3.7). When every
    # dispatched table failed, inspect the failure statuses:
    #   - ALL 400  → invalid_query (defensive belt-and-suspenders against an
    #     FTS5 syntax error that escape_fts5 somehow missed; expected unreachable
    #     in production given the escape contract — RESEARCH §3.6).
    #   - any other status (5xx, transport, mixed) → upstream_unavailable.
    # The 400-only path uses the sole-emission helper to preserve counter-patch
    # identity (D4-09 / D2-15).
    if failed_tables == len(target_tables) and failed_tables > 0:
        if all(s == 400 for s in failure_statuses):
            raise_invalid_query()
        # Fixed-literal message — INJ-05; no query string echoed.
        raise ToolError("upstream_unavailable: all search targets failed") from None

    # Step 10: D4-13 defense-in-depth post-filter — re-check each returned row's
    # (db, table) against visible tables. Guards against the rare race where
    # a table disappears from upstream's visible set between dispatch and
    # response. Cached per-DB to avoid N round-trips when many rows share a DB.
    # #6b / #9: Reuses the discovery snapshot's visible sets (discovery_visible)
    # so no additional get_database calls are needed — the post-filter reads the
    # same snapshot as discovery, which collapses the race window and makes this
    # guard near-vacuous under caching. The guard still holds correctness against
    # the snapshot.
    # #6c / #10 note: under DatabaseSummaryCache, the snapshot is the cached one
    # — same effect. Acceptable; documented per the issue.
    post_filter_start = time.perf_counter()
    post_filtered: list[dict] = []
    for r in rows:
        db_for_row = r["database"]
        visible = discovery_visible.get(db_for_row)
        if visible is None:
            # DB not in discovery (e.g., all tables were non-FTS); fall back to
            # a live fetch. This path is rare — only if a row's DB wasn't in
            # target_dbs at all (shouldn't happen in normal operation).
            visible = await _visible_tables(db_for_row)
        if r["table"] in visible:
            post_filtered.append(r)
    rows = post_filtered
    post_filter_ms = int((time.perf_counter() - post_filter_start) * 1000)

    # #6a / #8: Emit search sub-timings as a separate event so the tool_call
    # schema test stays independent (same pattern as SESSION_START_FIELDS).
    # The request_id / ip_prefix are inherited from contextvars bound by
    # RequestIdMiddleware; tool is bound here. INJ-05: no query string bound.
    log.info(
        "search_timing",
        tool="search",
        discovery_ms=discovery_ms,
        fan_out_ms=fan_out_ms,
        post_filter_ms=post_filter_ms,
    )

    # Step 11: slice to limit (belt-and-suspenders — fan_out_search already
    # merged with per_table_limit=limit, but the merge can over-fill briefly
    # inside the zip_longest column iteration before the early-exit kicks in;
    # this slice keeps the contract crisp).
    rows = rows[:limit]

    # Step 12: emit via the multi-DB provenance factory (D4-16). database=None,
    # table=None, license=LICENSE_MIXED — set inside the factory.
    return Envelope.for_search_results(
        rows=rows,
        upstream_total_hits=upstream_total_hits,
        failed_tables=failed_tables,
    )
