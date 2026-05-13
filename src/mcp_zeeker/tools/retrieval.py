"""
Retrieval tool handlers — query_table (registered) and fetch (unregistered stub).

D-01: Per-domain grouping. Both retrieval handlers live in this module — never
split into per-tool files (cross-`tools/` imports are a smell; see 03-PATTERNS.md).

D3-08: Validation order — _resolve_table → _visible_columns → per-field
unknown_column checks (filter / sort / columns) → compile_filters →
DatasetteClient.get_table_rows → row reshape → Envelope.for_rows.
NEVER add a separate HIDDEN_COLUMNS pre-check before _visible_columns; the
counter-patch test in tests/tools/test_retrieval_side_channel.py detects
separate code paths via raise_unknown_column invocation counts.

D3-09 / INJ-05: NO ToolError message or log line interpolates a user-supplied
filter VALUE. Every error string in this module is either a fixed literal or
echoes only request identifiers ({database}, {table}, {column}) — never the
value field of a Filter clause.

Slice B (Plan 03-03 ships here): query_table is feature-complete for QUERY-01..10.
- Heavy-column projection: callers can list HEAVY_COLUMNS in `columns=[...]`;
  those columns surface ONLY under the `retrieved_content` key on each row
  (D3-05 / D3-19). Default (`columns=None`) emits the light set and no
  `retrieved_content` key is present.
- qhash cursors (D3-03): the handler computes `canonical_shape_str(...)` after
  the visibility checks and BEFORE the upstream call. If `cursor is not None`,
  `decode_cursor(cursor, canonical_shape)` raises ToolError(`invalid_cursor`)
  on malformed / shape-mismatched cursors — no upstream request is issued in
  either failure path. On success the wrapped `_next` token flows to upstream
  via the `_next` param.
- The two Plan 03-02 scope-boundary raises ("cursor not yet supported on this
  slice" / "heavy column projection not yet supported on this slice") and
  their grep-discoverable cleanup markers have been removed entirely.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolAnnotations
from pydantic import Field

from mcp_zeeker import config
from mcp_zeeker.core.config_lookup import url_column_for
from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed
from mcp_zeeker.core.envelope import Envelope, Pagination
from mcp_zeeker.core.filter_compiler import Filter, compile_filters
from mcp_zeeker.core.visibility import (
    _resolve_table,
    _visible_columns,
    raise_not_found,
    raise_unknown_column,
    raise_unsupported_table_for_fetch,
)
from mcp_zeeker.server import mcp

log = structlog.get_logger()

# D3-16: Tool description text. MUST end with config.TOOL_TRAILER (INJ-01 /
# ANNO-02) and mention the rate-limit literal "20/burst, 60/minute, 5000/day"
# (ANNO-03) and case-insensitivity of LIKE-family ops (QUERY-10).
_QUERY_TABLE_DESCRIPTION = (
    "Retrieve rows from a Singapore legal table on data.zeeker.sg with filters, sort, "
    "pagination, and an explicit column allow-list. Default columns are the table's "
    "light set; heavy text columns return under 'retrieved_content' when explicitly "
    "requested. SQLite LIKE 'contains'/'startswith'/'endswith' is case-insensitive for "
    "ASCII. Rate limits: 20/burst, 60/minute, 5000/day per IP. " + config.TOOL_TRAILER
)


@mcp.tool(
    name="query_table",
    description=_QUERY_TABLE_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def query_table(
    database: Annotated[str, Field(description="Database name (e.g. 'zeeker-judgements')")],
    table: Annotated[str, Field(description="Table name (e.g. 'judgments')")],
    filters: Annotated[
        list[Filter] | None,
        Field(
            default=None,
            description=(
                "List of filter clauses. Each clause has {column, op, value}. "
                "Supported ops: exact, not, contains, startswith, endswith, "
                "gt, gte, lt, lte, in, notin, isnull, notnull. "
                "contains / startswith / endswith are case-insensitive for ASCII."
            ),
        ),
    ] = None,
    sort: Annotated[
        str | None,
        Field(
            default=None,
            description="Column to sort by; prefix with '-' for descending.",
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            default=50,
            ge=1,
            le=200,
            description="Max rows to return; default 50, max 200.",
        ),
    ] = 50,
    cursor: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Opaque pagination cursor returned in the previous response's "
                "pagination.next_cursor. Reusing a cursor with a different "
                "sort / filters / columns shape returns invalid_cursor."
            ),
        ),
    ] = None,
    columns: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="Explicit column allow-list; when omitted, returns the table's light set.",
        ),
    ] = None,
) -> Envelope:
    """Slice B — feature-complete query_table (QUERY-01..10).

    Validation order (D3-08):
      0. limit clamp (T-03-09 belt-and-suspenders)
      1. normalize filters (Pydantic Filter coercion for direct callers)
      2. _resolve_table — table-existence gate
      3. _visible_columns — column visibility (single source of truth)
      4. per-field unknown_column checks (filter / sort / columns)
      5. merge column types (config fallback + upstream _zeeker_schemas)
      6. compile_filters → httpx URL params
      7. column partition: light_to_emit vs heavy_to_emit (D3-04 / D3-05)
      8. sort param mapping (_sort / _sort_desc)
      9. canonical_shape_str + cursor decode (D3-03 — short-circuits before
         the upstream table-rows fetch on shape mismatch / malformed token)
     10. DatasetteClient.get_table_rows — single upstream call
     11. row reshape — light at top level, heavy under retrieved_content (D3-05)
     12. encode_cursor on upstream.next + Envelope.for_rows
    """
    # Step 0: belt-and-suspenders limit clamp. Pydantic Field(ge=1, le=200) is
    # the primary gate when FastMCP dispatches via MCP; direct Python callers
    # (unit tests, internal callers) bypass that validation, so re-check here.
    # T-03-09: rejecting limit=201 before issuing any upstream request. The
    # message is a FIXED literal (no f-string interpolation of limit value) —
    # mirrors the D3-09 / INJ-05 discipline used in compile_filters.
    if limit < 1 or limit > config.MAX_QUERY_LIMIT:
        raise ToolError("invalid_filter_op: limit must be between 1 and 200")

    # Step 1: normalize filters. When dispatched via FastMCP, Pydantic has already
    # coerced filters to list[Filter]; when the handler is called directly as a
    # Python function (unit tests, internal callers) the items may still be dicts.
    # Model-validate each entry so the per-field iteration below can rely on
    # attribute access (.column / .op / .value) regardless of call path.
    normalized_filters: list[Filter] = [
        f if isinstance(f, Filter) else Filter.model_validate(f) for f in (filters or [])
    ]

    # Step 2: table-existence gate (D3-08 — single emission via raise_unknown_table)
    await _resolve_table(database, table)

    # Step 3: column visibility (single source of truth, mirrors _visible_tables)
    visible = await _visible_columns(database, table)

    # Step 4: per-field unknown_column checks BEFORE compile_filters / upstream calls
    # (D3-07 single-emission identity — counter-patched in test_retrieval_side_channel.py)
    for f in normalized_filters:
        if f.column not in visible:
            raise_unknown_column(database, table, f.column)
    if sort:
        sort_col = sort.lstrip("-")
        if sort_col not in visible:
            raise_unknown_column(database, table, sort_col)
    for col in columns or []:
        if col not in visible:
            raise_unknown_column(database, table, col)

    # Step 5: merge column types — config fallback overlaid with upstream (D3-08, D2-07)
    column_types_map = await DatasetteClient.current().get_table_column_types(database)
    types_for_table = {
        **config.COLUMN_TYPES.get(f"{database}.{table}", {}),
        **column_types_map.get(table, {}),
    }

    # Step 6: compile filter clauses to httpx URL params (D3-01 / D3-02)
    filter_params = compile_filters(
        normalized_filters,
        visible_columns=visible,
        column_types=types_for_table,
    )

    # Step 7: column partition (D3-04 / D3-05). Default (columns is None) emits
    # the configured light set — `retrieved_content` MUST NOT appear on any row.
    # When the caller passes an explicit allow-list, any HEAVY_COLUMNS member is
    # routed into `heavy_to_emit`; the upstream request includes both light and
    # heavy column names so Datasette returns them, and the row-reshape step
    # below puts the heavy values under the `retrieved_content` key.
    if columns is None:
        configured_light = config.LIGHT_COLUMNS.get(f"{database}.{table}", [])
        if configured_light:
            light_to_emit = [c for c in configured_light if c in visible]
        else:
            light_to_emit = sorted(visible - config.HEAVY_COLUMNS)
        heavy_to_emit: list[str] = []
    else:
        light_to_emit = [c for c in columns if c not in config.HEAVY_COLUMNS]
        heavy_to_emit = [c for c in columns if c in config.HEAVY_COLUMNS]

    # Step 8: build sort param (D3-08, Datasette _sort / _sort_desc mapping)
    if sort and sort.startswith("-"):
        sort_params: list[tuple[str, str]] = [("_sort_desc", sort.lstrip("-"))]
    elif sort:
        sort_params = [("_sort", sort)]
    else:
        sort_params = []

    # Step 9: canonical shape + cursor decode (D3-03). Computed AFTER the
    # visibility checks (so unknown_column on an arbitrary cursored request
    # still surfaces the user-friendly column name) but BEFORE the upstream
    # table-rows call — `decode_cursor` raises ToolError(invalid_cursor) on
    # malformed/shape-mismatched tokens, and we want that rejection to happen
    # without burning an upstream Datasette query. The canonical shape is
    # rebuilt at the end of the request to re-key the next cursor.
    canonical_shape = canonical_shape_str(database, table, sort, normalized_filters, columns)
    datasette_next: str | None = None
    if cursor is not None:
        # decode_cursor raises ToolError(invalid_cursor: ...) on any failure;
        # propagate untouched — the fixed-literal message is the contract.
        datasette_next = decode_cursor(cursor, canonical_shape)

    # Step 10: compose Datasette params; _shape=objects is prepended by get_table_rows.
    # upstream_cols = light + heavy so Datasette returns every column we plan to
    # emit (heavy values get re-keyed under retrieved_content in step 11).
    upstream_cols = [*light_to_emit, *heavy_to_emit]
    params: list[tuple[str, str]] = [
        *filter_params,
        *sort_params,
        *[("_col", c) for c in upstream_cols],
        ("_size", str(limit)),
    ]
    if datasette_next is not None:
        params.append(("_next", datasette_next))

    # Step 11: bind contextvars logging (OBS-03/04). NEVER bind filter values (INJ-05).
    log.debug(
        "query_table_invoked",
        database=database,
        table=table,
        filter_count=len(normalized_filters),
    )

    # Step 12: upstream call (D-16 retry-once-with-jitter inherited from
    # _request_with_retry). On failure, surface the generic upstream_unavailable
    # ToolError — never echo the underlying httpx/Datasette error text (INJ-05).
    try:
        result = await DatasetteClient.current().get_table_rows(database, table, params)
    except UpstreamCallFailed:
        raise ToolError("upstream_unavailable: upstream call failed") from None

    # Step 13: reshape rows (D3-05). Light columns stay at the top level; heavy
    # columns are re-keyed under `retrieved_content`. The `retrieved_content` key
    # appears ONLY when heavy_to_emit is non-empty — default-light responses
    # MUST NOT carry a retrieved_content key (D3-19 snapshot contract).
    # rowid never appears at top level because it is never in light_to_emit.
    reshaped: list[dict] = []
    for upstream_row in result.get("rows", []) or []:
        row: dict = {c: upstream_row[c] for c in light_to_emit if c in upstream_row}
        if heavy_to_emit:
            row["retrieved_content"] = {
                c: upstream_row[c] for c in heavy_to_emit if c in upstream_row
            }
        reshaped.append(row)

    # Step 14: encode next cursor + surface truncation. encode_cursor binds the
    # canonical_shape (digest) to upstream.next so a follow-up request reusing
    # the same sort/filters/columns succeeds — any shape change invalidates.
    # `truncated` flows through honestly from upstream; Phase 5 FRAG-04 wires
    # the consumer side.
    next_cursor = encode_cursor(canonical_shape, result["next"]) if result.get("next") else None
    truncated = bool(result.get("truncated", False))

    # Step 15: emit via Envelope.for_rows with the populated Pagination.
    return Envelope.for_rows(
        database=database,
        table=table,
        rows=reshaped,
        pagination=Pagination(next_cursor=next_cursor, truncated=truncated),
    )


# D3-16: fetch tool description. MUST end with config.TOOL_TRAILER (INJ-01 /
# ANNO-02) and mention the rate-limit literal (ANNO-03). Plan 03-04 ships this
# verbatim — exact-match discipline + "no normalization" wording satisfies the
# acceptance grep (`'exact string equality' in desc.lower()` OR `'no normalization'`).
_FETCH_DESCRIPTION = (
    "Fetch a single row by its URL from a URL-keyed table on data.zeeker.sg. "
    "URL match is exact string equality (no normalization). Returns non-heavy, "
    "non-fragment columns only; use query_table on the matching *_fragments "
    "table for paragraph-level content. Rate limits: 20/burst, 60/minute, "
    "5000/day per IP. " + config.TOOL_TRAILER
)


@mcp.tool(
    name="fetch",
    description=_FETCH_DESCRIPTION,
    annotations=ToolAnnotations(
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def fetch(
    database: Annotated[str, Field(description="Database name")],
    table: Annotated[str, Field(description="Table name (must be URL-keyed)")],
    url: Annotated[
        str,
        Field(
            description=(
                "Exact URL to look up. No silent normalization — "
                "'?utm=...' is treated as a different URL and will not match."
            )
        ),
    ],
) -> Envelope:
    """Fetch a single row by exact URL match (D3-13 / D3-14 / D3-15 / D3-16).

    Validation order (D3-14):
      1. _resolve_table — unknown_database / unknown_table (shared with query_table).
      2. url_column_for(database, table) — None means table is not URL-keyed
         → raise_unsupported_table_for_fetch (FETCH-04). NO upstream call.
      3. Datasette query: `?<url_col>__exact=<url>&_size=2` — `_size=2` lets
         us detect multi-match without fetching the whole result set.
      4. len(rows) == 0 → raise_not_found (FETCH-05). The URL is NOT a param
         to raise_not_found (INJ-05) — error message is a fixed literal.
      5. Column projection: emit_cols = (visible - HEAVY_COLUMNS) - {parent_fk}.
         `visible` already excludes hidden columns (HIDDEN_COLUMNS["*"], e.g. `id`).
         For fragment tables (currently unreachable via fetch because none are in
         URL_COLUMNS), the parent_fk would also be stripped — defensive coverage.
      6. len(rows) > 1 → log.warning("fetch_ambiguous_url", database=..., table=...,
         match_count=...). URL is NEVER bound (INJ-05). Return the FIRST row.
      7. Reshape row to emit_cols (sorted) and wrap in Envelope.for_rows — no
         pagination (single-row response).
    """
    # Step 1: shared table-existence gate (D2-15 — hidden + nonexistent share
    # one emission point, counter-patched in 03-02).
    await _resolve_table(database, table)

    # Step 2: URL_COLUMNS lookup. Single call-site for the URL_COLUMNS dict
    # (Plan 03-04 / D2-10 mirror discipline). None → table is not URL-keyed,
    # raise BEFORE issuing any table-row upstream call (FETCH-04).
    url_col = url_column_for(database, table)
    if url_col is None:
        raise_unsupported_table_for_fetch(database, table)

    # Step 3: exact-match upstream query. `_size=2` is the multi-match detector;
    # we never need more than 2 rows to distinguish "single" / "ambiguous".
    params: list[tuple[str, str]] = [(f"{url_col}__exact", url), ("_size", "2")]
    try:
        result = await DatasetteClient.current().get_table_rows(database, table, params)
    except UpstreamCallFailed:
        raise ToolError("upstream_unavailable: upstream call failed") from None

    rows = result.get("rows", []) or []

    # Step 4: zero rows → not_found (FETCH-05 / INJ-05). raise_not_found takes
    # ONLY (database, table) — the URL is NOT an argument. The threat-model
    # grep (T-03-15) enforces this from the visibility-helper side.
    if not rows:
        raise_not_found(database, table)

    # Step 5: compute the emit column set. `visible` is _visible_columns() which
    # ALREADY excludes hidden columns (HIDDEN_COLUMNS["*"], e.g. `id`). Drop
    # HEAVY_COLUMNS (FETCH-03 "non-heavy"); also drop the fragment FK if this
    # table is a fragment-parent key in FRAGMENT_PARENTS (defensive — no
    # currently-URL-keyed table is a fragments table, so this is a no-op for
    # production config but guards against future config drift).
    visible = await _visible_columns(database, table)
    fk_to_exclude: set[str] = set()
    fragment_meta = config.FRAGMENT_PARENTS.get(f"{database}.{table}")
    if fragment_meta is not None:
        fk_to_exclude.add(fragment_meta["parent_fk"])
    emit_cols = (visible - config.HEAVY_COLUMNS) - fk_to_exclude

    # Step 6: multi-match path. Emit a structured WARNING — bind ONLY identifier
    # parameters and the match count. Never bind `url` (INJ-05 / T-03-16). The
    # threat-model grep enforces absence of `url=` on the fetch_ambiguous_url
    # log line.
    if len(rows) > 1:
        log.warning(
            "fetch_ambiguous_url",
            database=database,
            table=table,
            match_count=len(rows),
        )

    # Step 7: reshape the first row into a single emit_cols-keyed dict.
    # `sorted(emit_cols)` gives deterministic key order — useful for snapshot
    # tests. Skip columns that are absent from the upstream response (sparse
    # row fields cope gracefully).
    first = rows[0]
    row_dict = {c: first[c] for c in sorted(emit_cols) if c in first}

    # Step 8: single-row envelope. No pagination — fetch never paginates.
    return Envelope.for_rows(database=database, table=table, rows=[row_dict])
