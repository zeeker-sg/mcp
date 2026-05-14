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
from mcp_zeeker.core import fragment_join  # Phase 5 / D5-01 — sole delegation point
from mcp_zeeker.core.config_lookup import url_column_for
from mcp_zeeker.core.cursor import (
    canonical_shape_str,
    decode_cursor,
    decode_keyset_cursor,  # Phase 5 / D5-07 — keyset variant on join path
    encode_cursor,
    encode_keyset_cursor,  # Phase 5 / D5-07 — keyset variant on join path
)
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
    "ASCII. "
    # Phase 5 D5-09 — fragment-join note. Inserted BEFORE TOOL_TRAILER so the
    # injection-resistance trailer INJ-01 / ANNO-02 remains the LAST sentence.
    "On *_fragments tables, an `exact` filter on the parent's URL column triggers a "
    "transparent join — fragments are returned sorted by paragraph order with `limit` "
    "capped at 100 per call. "
    "Rate limits: 20/burst, 60/minute, 5000/day per IP. " + config.TOOL_TRAILER
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
    #
    # WR-02 / D3-12 note: `limit` is not a filter clause but `invalid_filter_op`
    # is still the catalog code we emit here. The PRD §12 error-code catalog
    # is LOCKED to six entries (unknown_database, unknown_table, unknown_column,
    # invalid_filter_op, invalid_cursor, unsupported_table_for_fetch, not_found)
    # — extending it requires a planning re-loop, not a code-fix. The catalog
    # extension to `invalid_limit` is deferred to Phase 7 (ERR-02), where the
    # whole catalog gets revisited. Until then, log/metrics consumers grepping
    # on `invalid_filter_op:` should rely on the message suffix
    # ("limit must be between") to disambiguate. The Pydantic Field(ge=1, le=200)
    # gate above is the dominant code path in production; this branch is only
    # reachable from direct Python callers and is intentionally chatty.
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

    # Phase 5 (D5-02 / 05-RESEARCH §4.6 / Pitfall 4) — fragment-join visibility
    # exemption. When (db, table) is a fragment table in FRAGMENT_PARENTS, the
    # LLM's filter column is the PARENT table's URL column (e.g., source_url
    # for judgments_fragments) — which is NOT in `visible` for the fragment
    # table. We add it to `allowed_extra_columns` so the per-field loop below
    # accepts it; fragment_join.compile_filter (Step 3.5 below) then rewrites
    # the filter into the internal parent_fk eq parent_pk form. The rewritten
    # filter bypasses the loop because the loop has already run.
    fragment_parent_meta = config.FRAGMENT_PARENTS.get(f"{database}.{table}")
    allowed_extra_columns: set[str] = set()
    parent_url_for_qhash: str | None = None  # captured before Step 3.5 rewrite
    if fragment_parent_meta is not None:
        parent_table_for_exempt = fragment_parent_meta["parent_table"]
        parent_url_column = url_column_for(database, parent_table_for_exempt)
        if parent_url_column:
            allowed_extra_columns.add(parent_url_column)
            # Capture the user-supplied URL value for canonical_shape rebuild
            # later (D5-06: qhash binds normalized URL, NOT parent_pk).
            for _f in normalized_filters:
                if _f.column == parent_url_column and _f.op == "exact":
                    parent_url_for_qhash = fragment_join.normalize_url(str(_f.value))
                    break

    # Step 4: per-field unknown_column checks BEFORE compile_filters / upstream calls
    # (D3-07 single-emission identity — counter-patched in test_retrieval_side_channel.py)
    for f in normalized_filters:
        if f.column not in visible and f.column not in allowed_extra_columns:
            raise_unknown_column(database, table, f.column)
    if sort:
        sort_col = sort.lstrip("-")
        if sort_col not in visible:
            raise_unknown_column(database, table, sort_col)
    for col in columns or []:
        if col not in visible:
            raise_unknown_column(database, table, col)

    # Step 3.5 (Phase 5 / D5-01 / D5-04) — fragment-join orchestration.
    # Detect join trigger via (db, table) in FRAGMENT_PARENTS AND exactly-one-eq
    # filter on the parent URL column. If active, fragment_join.compile_filter
    # fires Call 1 (parent lookup) and returns rewritten filters with parent_fk
    # substituted. If inactive (non-fragment table OR no eq-URL filter), returns
    # the original list unchanged (fall-through per D5-03).
    #
    # INJ-05: UpstreamCallFailed from Call 1 maps to the generic
    # upstream_unavailable literal — never echoes httpx/Datasette error text.
    try:
        normalized_filters, _fragment_warning_state = await fragment_join.compile_filter(
            database, table, normalized_filters
        )
    except UpstreamCallFailed:
        raise ToolError("upstream_unavailable: upstream call failed") from None

    # Did fragment_join.compile_filter rewrite the filter list? Detect by
    # presence of the internal parent_fk filter (config-computed, never
    # user-supplied — the rewrite is the sole producer of this filter shape).
    fragment_join_active: bool = (
        fragment_parent_meta is not None
        and parent_url_for_qhash is not None
        and any(
            f.column == fragment_parent_meta["parent_fk"] and f.op == "exact"
            for f in normalized_filters
        )
    )

    # Step 5: merge column types — config fallback overlaid with upstream (D3-08, D2-07)
    column_types_map = await DatasetteClient.current().get_table_column_types(database)
    types_for_table = {
        **config.COLUMN_TYPES.get(f"{database}.{table}", {}),
        **column_types_map.get(table, {}),
    }

    # Step 6: compile filter clauses to httpx URL params (D3-01 / D3-02).
    # On the fragment-join path, `visible_columns` is augmented with the parent
    # URL column (via allowed_extra_columns) — but at this point the
    # user-supplied source_url filter has already been REPLACED with the
    # internal parent_fk filter by fragment_join.compile_filter, so the
    # filter_compiler only sees the parent_fk column. parent_fk is not in
    # `visible` (HIDDEN_COLUMNS strips it), so we need to add it here.
    visible_for_compile = visible | allowed_extra_columns
    if fragment_join_active and fragment_parent_meta is not None:
        visible_for_compile = visible_for_compile | {fragment_parent_meta["parent_fk"]}
    filter_params = compile_filters(
        normalized_filters,
        visible_columns=visible_for_compile,
        column_types=types_for_table,
    )

    # Step 7.5 (Phase 5 / D5-08) — limit re-clamp on the fragment-join path.
    # Pydantic Field(le=200) is the primary gate; this is the belt-and-suspenders
    # tighter cap for fragment tables. Fixed-literal — NO {limit} f-string
    # interpolation (INJ-05).
    if fragment_join_active and limit > 100:
        raise ToolError("invalid_filter_op: limit exceeds fragment-join cap of 100")

    # Step 7: column partition (D3-04 / D3-05). Default (columns is None) emits
    # the configured light set — `retrieved_content` MUST NOT appear on any row.
    # When the caller passes an explicit allow-list, any HEAVY_COLUMNS member is
    # routed into `heavy_to_emit`; the upstream request includes both light and
    # heavy column names so Datasette returns them, and the row-reshape step
    # below puts the heavy values under the `retrieved_content` key.
    if columns is None:
        configured_light = config.LIGHT_COLUMNS.get(f"{database}.{table}", [])
        if configured_light:
            # WR-03: defend against config drift adding a HEAVY_COLUMNS member
            # to LIGHT_COLUMNS[<db>.<table>]. Without the explicit subtraction
            # here, a heavy column smuggled into the configured light set
            # would surface at the row TOP level instead of under
            # `retrieved_content`, violating the D3-19 snapshot contract
            # (`set(row.keys()) ∩ HEAVY_COLUMNS == ∅` for default projections).
            # The fallback branch below already does this subtraction; mirror
            # it here so both branches enforce the same invariant.
            light_to_emit = [
                c for c in configured_light if c in visible and c not in config.HEAVY_COLUMNS
            ]
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
    #
    # Phase 5 (D5-05 / D5-06 / RESEARCH §4.3): on the fragment-join path,
    # qhash binds the NORMALIZED USER URL (NOT the resolved parent_pk), so
    # continuation calls across the ParentPKCache TTL still match shape after
    # the parent_pk re-resolves. Build a synthetic filter list that puts the
    # normalized-URL filter back in place of the rewritten parent_fk filter
    # for canonical_shape purposes only — the upstream Call 2 still uses
    # parent_fk via the rewritten filter list above.
    datasette_next: str | None = None
    if fragment_join_active and fragment_parent_meta is not None:
        parent_url_col_for_shape = url_column_for(database, fragment_parent_meta["parent_table"])
        synthetic_filters_for_qhash: list[Filter] = [
            Filter(
                column=parent_url_col_for_shape,
                op="exact",
                value=parent_url_for_qhash,
            ),
            *[
                f
                for f in normalized_filters
                if not (f.column == fragment_parent_meta["parent_fk"] and f.op == "exact")
            ],
        ]
        canonical_shape = canonical_shape_str(
            database, table, None, synthetic_filters_for_qhash, columns
        )
        if cursor is not None:
            # decode_keyset_cursor raises ToolError(invalid_cursor: keyset
            # cursor is malformed) on any decode failure — D5-07 fixed literal,
            # propagate untouched.
            last_order_by_value, last_id_suffix = decode_keyset_cursor(cursor, canonical_shape)
            # CR-01 fix: cursor encodes only the suffix (parent_pk-stripped);
            # reconstruct the full id by prepending the resolved parent_pk
            # from the rewritten normalized_filters (synthetic parent_fk eq
            # filter that compile_filter produced). Symmetric with the encode
            # site strip — FRAG-02 / D5-06 preserved: the parent_pk substring
            # never appears in the LLM-visible cursor token.
            _parent_pk_for_reconstruct = next(
                (
                    str(f.value)
                    for f in normalized_filters
                    if fragment_parent_meta is not None
                    and f.column == fragment_parent_meta["parent_fk"]
                    and f.op == "exact"
                ),
                None,
            )
            if _parent_pk_for_reconstruct is None:
                # Defensive: cursor decoded successfully but the rewritten
                # filter is gone (e.g., negative cache hit after the user's
                # original URL re-resolved to no-match across a TTL boundary).
                # Reject with the locked-catalog message — the user's cursor
                # was tied to a parent that no longer matches.
                raise ToolError("invalid_cursor: cursor does not match current request shape")
            last_id = f"{_parent_pk_for_reconstruct}_{last_id_suffix}"
            datasette_next = f"{last_order_by_value},{last_id}"
    else:
        canonical_shape = canonical_shape_str(database, table, sort, normalized_filters, columns)
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
    # Phase 5 (RESEARCH §4.4 / Pitfall 2): inject _nocount=1 on the fragment-join
    # path. judgments_fragments is the largest table (~957 fragments / parent);
    # without this flag Datasette's implicit COUNT(*) over the filtered subquery
    # exceeds sql_time_limit_ms and returns HTTP 400 SQL Interrupted. Harmless
    # on the smaller fragment tables. filtered_table_rows_count goes null in the
    # response but `truncated` still surfaces honestly (FRAG-04).
    if fragment_join_active:
        params.append(("_nocount", "1"))
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
    #
    # Phase 5 (D5-05 / D5-06 / RESEARCH §4.3): on the fragment-join path, the
    # upstream `next` token follows Datasette's keyset shape
    # "<last_order_by_value>,<last_id>" — split it and route through the
    # keyset encoder so the cursor payload preserves the (order_by, id)
    # tiebreak that FRAG-03 requires.
    if fragment_join_active and result.get("next"):
        # WR-03 (defensive): Datasette's keyset `next` token is documented as
        # "<order_by_value>,<id>"; if upstream ever returns a single-segment
        # token (e.g., a future Datasette wire-format change), `split(",", 1)`
        # would raise ValueError on unpack. Drop the next_cursor in that case
        # — the LLM gets a single-page response, FRAG-04 truncated still
        # surfaces honestly via the separate flag.
        _parts = result["next"].split(",", 1)
        if len(_parts) != 2:
            log.warning(
                "fragment_keyset_next_token_malformed",
                database=database,
                table=table,
            )
            next_cursor = None
        else:
            _last_ord, _last_id = _parts
            # CR-01 fix: STRIP the `f"{parent_pk}_"` prefix from `_last_id`
            # before encoding. Production fragment IDs follow the pattern
            # `<parent_pk>_<suffix>` (zero-padded ordinal for judgments,
            # section number for sglawwatch, `chunk_<seq>` for pdpc — all
            # 3 patterns verified). Leaving parent_pk in the encoded cursor
            # leaks it via trivially-reversible base64 (FRAG-02 / D5-06).
            # parent_pk is sourced from the rewritten `normalized_filters`
            # — the synthetic Filter(parent_fk, "exact", parent_pk) that
            # `fragment_join.compile_filter` produced. Re-resolved on the
            # decode side from the same source.
            _parent_pk_for_strip = next(
                (
                    str(f.value)
                    for f in normalized_filters
                    if fragment_parent_meta is not None
                    and f.column == fragment_parent_meta["parent_fk"]
                    and f.op == "exact"
                ),
                None,
            )
            if _parent_pk_for_strip is not None and _last_id.startswith(f"{_parent_pk_for_strip}_"):
                _suffix = _last_id[len(_parent_pk_for_strip) + 1 :]
            else:
                # Defensive: the prefix MUST be present for the 3 current
                # fragment tables; if a future table breaks the convention,
                # drop the next_cursor rather than leak the full id.
                log.warning(
                    "fragment_keyset_id_prefix_missing",
                    database=database,
                    table=table,
                )
                _suffix = None
            if _suffix is None:
                next_cursor = None
            else:
                next_cursor = encode_keyset_cursor(canonical_shape, _last_ord, _suffix)
    elif result.get("next"):
        next_cursor = encode_cursor(canonical_shape, result["next"])
    else:
        next_cursor = None
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
    # INJ-05 compliance — build the Datasette param key via string concatenation
    # (NOT f-string interpolation of `url_col`). The column name is config-sourced
    # so the literal pattern is safe, but mirrors fragment_join's discipline so
    # the Phase 5 INJ-05 grep over retrieval.py stays clean (the grep flags any
    # f-string starting with `{url` regardless of whether the substitution is a
    # column name or a URL value — concatenation sidesteps the false positive).
    params: list[tuple[str, str]] = [(url_col + "__exact", url), ("_size", "2")]
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
