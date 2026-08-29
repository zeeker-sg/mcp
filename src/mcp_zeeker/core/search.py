"""
Search orchestration — auto-discovery + per-table fan-out + merge
(round-robin per D4-05, or Reciprocal Rank Fusion per issue #12 Phase 2)
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
  `anyio.create_task_group` + `move_on_after(config.SEARCH_FAN_OUT_TIMEOUT_S)`
  per D4-06; `zip_longest`
  round-robin merge per D4-05 ("legacy" / "bm25" modes) or `_rrf_merge`
  ("bm25_rrf" — issue #12 Phase 2); returns 4-tuple including per-failure
  status codes for D4-09 case (c) detection (Plan 04-02).

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

Phase 6 / Plan 06-02 extends per-row shape from 6 keys to 9 keys: each
normalized search row now also carries `license`, `license_url`, and
`citation` per D6-03 (per-row license/license_url on multi-DB envelopes) and
D6-05 (per-row citation via `synthesize_citation`). The envelope-level
provenance still carries `LICENSE_MIXED` + `license_url=None` because the
response spans multiple DBs.

Issue #12 Phase 1 extends the shape to 10 keys with `_score` — the raw
negative bm25() relevance score on the SQL-ranked path
(`config.SEARCH_RANKING != "legacy"`, requires the owner UPSTREAM_TOKEN),
None on the legacy path. `build_bm25_sql` is the pure SQL builder; the user
query reaches SQL ONLY as the bound `:search_query` named parameter, and the
only interpolated identifiers are table/fts names from upstream discovery
metadata plus validated numeric bm25 weights from config.SEARCH_BM25_WEIGHTS.

Issue #12 Phase 2 replaces the round-robin merge with Reciprocal Rank Fusion
(`_rrf_merge`) when `config.SEARCH_RANKING == "bm25_rrf"`: per-table lists
are already relevance-ordered (BM25 ascending), fused score(doc) =
Σ 1/(k + rank_in_list) across every list the doc appears in, dedup ACROSS
lists by the row's non-null `url` (null-url rows never merge). On this merge
path each output row carries an ELEVENTH key, `_fused_score` (float, higher =
better) — rows merged by `_round_robin_merge` ("legacy" and "bm25" modes)
keep the 10-key shape. Round-robin therefore stays available behind the flag.

Issue #12 Phase 3 adds fragment passage search (MaxP feeding RRF): body text
(e.g. full judgment paragraphs) is only FTS-indexed in `*_fragments_fts`
tables, which discovery denylists. `fragment_sources_for` discovers
config.SEARCH_FRAGMENT_SOURCES entries whose fragment fts exists upstream AND
whose PARENT table passes the existing gates (visible + preview-resolvable);
`build_maxp_sql` rolls fragment matches up to the parent document
(`MIN(bm25(<frag_fts>, ...))` per parent — bm25 is negative, so MIN = the
best passage = MaxP). The resulting rows ARE parent rows (`database=<db>`,
`table=<parent_table>`), so the D4-13 post-filter, citations, and the RRF
url-identity dedup all stay coherent — a judgment found via both its summary
and its body fuses upward. Fragment lists join the fan-out as additional
merge inputs keyed `(db, "<frag_table>→<parent_table>")`, and their
upstream totals use the distinct key `"<db>.<fragment_table>"` so
observability distinguishes passage hits. Legacy mode adds NO fragment
sources (denylist behavior unchanged — the SQL path needs the owner token).

References: D4-02 / D4-05 / D4-12 / D4-18, 04-RESEARCH.md §3.1 / §3.2 / §3.7 /
§3.8, 04-PATTERNS.md (search orchestrator templates).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest

import anyio
import structlog

from mcp_zeeker import config
from mcp_zeeker.core.citation import placeholder_columns, synthesize_citation
from mcp_zeeker.core.datasette_client import DatabaseSummary, DatasetteClient, UpstreamCallFailed
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.core.middleware.retrieved_at import get_tool_started_at

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


def _fts_indexed_columns(summary: DatabaseSummary, fts_table: str) -> list[str]:
    """Derive the FTS5 indexed-column ORDER for `fts_table` from the summary.

    Issue #12 Phase 1: the fts virtual table appears in the same
    /{db}.json summary the discovery path already fetched (hidden=True
    upstream, but this lookup scans `summary.tables` directly — visibility
    gates apply to content tables, not the fts sidecar). Its indexed columns
    are its column list MINUS the two pseudo-columns sqlite-utils/Datasette
    surface: one named the same as the fts table itself, and `rank`.
    ORDER IS LOAD-BEARING: `bm25(<fts>, w1, w2, ...)` takes one weight per
    indexed column in FTS column order.

    Returns [] when the fts table is absent from the summary (defensive —
    the caller falls back to legacy dispatch for that table rather than
    building an unweightable SQL query). No extra upstream round-trip
    (#6b / #9 memoization pattern preserved).
    """
    for t in summary.tables:
        if t.name == fts_table:
            return [c for c in t.columns if c != fts_table and c != "rank"]
    return []


async def searchable_tables_for(
    db: str,
    *,
    summary: DatabaseSummary | None = None,
    visible: set[str] | None = None,
) -> list[tuple[str, dict[str, str | None], str, list[str]]]:
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

    Reads from `DatasetteClient.current().get_database(db)` (or the provided
    `summary` for request-scoped memoization — #6b / #9). Does NOT consume
    `MetadataCache.get_table_metadata` because /-/metadata.json is sparse and
    lacks the `fts_table` field (04-RESEARCH §3.2 corrected planner input).

    #6b / #9: Returns `(table_name, preview, fts_table, fts_columns)` tuples
    so the handler no longer needs to re-fetch columns via
    `_visible_columns(db, table)` per table — the preview is resolved once
    here from `TableSummary.columns`, which is the same source
    `_visible_columns` would read. This eliminates the per-table
    `get_database` round-trip that dominated the old discovery cost.

    Issue #12 Phase 1 extends the tuple with `fts_table` (the FTS5 virtual
    table name — non-None by gate 1) and `fts_columns` (its indexed-column
    ORDER via `_fts_indexed_columns`, derived from the SAME summary — no
    extra round-trip). `fts_columns` may be [] when the fts sidecar is
    absent from the summary; the SQL dispatch path treats that as a
    fall-back-to-legacy signal for the table.

    The optional `summary` and `visible` parameters enable request-scoped
    memoization (#6b): when the handler pre-fetches `get_database(db)` once
    and computes `_visible_tables` from it, passing both in collapses
    `2 + N` `get_database` calls per DB to exactly 1 (#9).

    Returns a list in upstream metadata order — deterministic for tests.
    Iteration order matches `summary.tables` upstream order so the handler's
    alphabetical-DB sort plus this in-DB order gives a fully deterministic
    round-robin merge (D4-05 / 04-RESEARCH §3.9).

    INJ-05 / D4-07: the `search_table_no_preview_columns` log binding exposes
    `database` and `table` only — the query string is never bound here (this
    function does not receive the query at all; the contract isolates the
    discovery surface from query content).
    """
    if summary is None:
        summary = await DatasetteClient.current().get_database(db)
    if visible is None:
        from mcp_zeeker.core.visibility import _visible_tables

        visible = await _visible_tables(db)
    assert summary is not None  # for type checkers; always assigned above
    out: list[tuple[str, dict[str, str | None], str, list[str]]] = []
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
        # to avoid an extra upstream round-trip (#6b / #9: the preview resolved
        # here is threaded out to the handler so it doesn't re-resolve).
        available = set(t.columns)
        preview = resolve_preview_columns(db, t.name, available)
        if preview is None:
            log.warning("search_table_no_preview_columns", database=db, table=t.name)
            continue
        # Issue #12: derive the fts indexed-column order from the SAME summary
        # (no extra upstream round-trip — #6b / #9). Gate 1 guarantees
        # t.fts_table is not None here.
        out.append((t.name, preview, t.fts_table, _fts_indexed_columns(summary, t.fts_table)))
    return out


def _qi(name: str) -> str:
    """Defensive SQLite identifier quoting (shared by both SQL builders).

    Names come from upstream discovery metadata / config (trusted relative to
    user input) but are quoted defensively anyway — doubling any embedded `"`
    is the SQLite identifier-escape rule. User query text NEVER flows here
    (it travels only as the bound :search_query parameter — INJ-05 / D3-09).
    """
    return '"' + name.replace('"', '""') + '"'


def _bm25_weight_literals(db: str, table: str, fts_columns: list[str]) -> list[str]:
    """Validated bm25() weight literals in FTS-INDEX COLUMN ORDER (issue #12).

    Config lookup is `config.SEARCH_BM25_WEIGHTS["<db>.<table>"]`; missing
    columns/tables default to 1.0. Weight values are validated numeric only
    (bool is an int subclass and is explicitly excluded); anything else falls
    back to 1.0 rather than being interpolated — weights are the ONLY
    config-sourced literals in the ranked-search SQL (INJ defense).
    float() then repr keeps each literal unambiguous ("10.0").
    """
    weights = config.SEARCH_BM25_WEIGHTS.get(f"{db}.{table}", {})
    literals: list[str] = []
    for col in fts_columns:
        w = weights.get(col, 1.0)
        if isinstance(w, bool) or not isinstance(w, (int, float)):
            w = 1.0
        literals.append(repr(float(w)))
    return literals


def _search_select_columns(
    db: str,
    table: str,
    preview: dict[str, str | None],
) -> list[str]:
    """SELECT-list columns for the ranked-search SQL builders (issue #12).

    Mirrors the legacy `_col=` projection: preview columns in preview-field
    order (skipping unmapped None entries), then citation-placeholder columns
    (sorted) that the (db, table) template needs but the preview doesn't
    carry — the same augmentation as the legacy path (D6.1-02 / Finding #2).
    Deduped; config.HEAVY_COLUMNS is filtered defensively so heavy text can
    NEVER enter the select list (D3-04 defense-in-depth).
    """
    template = config.CITATION_TEMPLATES.get((db, table), config.DEFAULT_CITATION_TEMPLATE)
    placeholders = placeholder_columns(template)
    preview_cols = [c for c in preview.values() if c is not None]
    added = sorted(placeholders - set(preview_cols))
    select_cols: list[str] = []
    for c in [*preview_cols, *added]:
        if c in config.HEAVY_COLUMNS or c in select_cols:
            continue
        select_cols.append(c)
    return select_cols


def build_bm25_sql(
    db: str,
    table: str,
    fts_table: str,
    fts_columns: list[str],
    preview: dict[str, str | None],
    escaped_query: str,
    per_table_limit: int,
) -> tuple[str, dict[str, str]]:
    """Build the per-table BM25-ranked SQL query — pure function (issue #12).

    Returns `(sql, params)` where `params == {"search_query": escaped_query}`
    and the SQL references it ONLY as the bound named parameter
    `:search_query`. INJ-05 / D3-09 invariant, auditable by inspection:
      - The user query string NEVER appears in the SQL text — it travels via
        `params`, bound server-side by sqlite3.
      - The only interpolated identifiers are `table` / `fts_table` /
        column names, all sourced from upstream discovery metadata (NOT from
        user input), and defensively double-quote-escaped via `_qi`.
      - The only interpolated values are the bm25 weights (validated numeric
        — non-numeric config entries fall back to 1.0, bool excluded) and the
        integer LIMIT.

    Shape (three levels — the nesting is LOAD-BEARING, see below):
      SELECT <preview cols + citation-placeholder cols, table-qualified>,
             _ranked._score AS _score, _ranked._total AS _total
      FROM (SELECT _hits._rid AS _rid, _hits._score AS _score,
                   count(*) OVER () AS _total
            FROM (SELECT rowid AS _rid, bm25(<fts>, w1, ...) AS _score
                  FROM <fts> WHERE <fts> MATCH :search_query LIMIT -1) _hits
            ORDER BY _hits._score ASC LIMIT <n>) _ranked
      JOIN <table> ON <table>.rowid = _ranked._rid
      ORDER BY _score ASC LIMIT <n>

    - WHY THE NESTING (WR-260829 / production regression): SQLite FTS5
      auxiliary functions (`bm25`, `snippet`, `highlight`) are only callable
      while the fts5 cursor is positioned on the matched row. Putting a
      WINDOW function (`count(*) OVER ()`) or an AGGREGATE in the SAME
      SELECT as `bm25(...)` forces SQLite to buffer/sort rows first, which
      tears down that cursor context and raises
      `unable to use function bm25 in the requested context` — surfaced by
      Datasette as HTTP 400. The failure is DATA-DEPENDENT: a zero-match
      query never invokes bm25 and returns 200 with an empty row set, so
      the pre-fix shape silently reported "0 hits" for every table that had
      no hits and failed with a 400 for every table that did. bm25() is
      therefore isolated in the innermost `_hits` SELECT, which contains no
      window function, no aggregate, and no join.
    - `LIMIT -1` on `_hits` is not a row cap (it means "no limit" in SQLite);
      it BLOCKS the subquery-flattening optimization, which would otherwise
      merge `_hits` back into the window-function level and re-break the
      bm25 context.
    - Weights are emitted in FTS-INDEX COLUMN ORDER (`fts_columns`), one per
      indexed column — bm25() is positional. Config lookup is
      `config.SEARCH_BM25_WEIGHTS["<db>.<table>"]`; missing columns/tables
      default to 1.0 (the single tuning point lives in config).
    - bm25() returns NEGATIVE scores (best = most negative) → ORDER BY ASC.
    - `count(*) OVER ()` sits at the `_ranked` level, where it sees the FULL
      MATCH result set (window functions are evaluated before LIMIT), giving
      the upstream-total-hits count in the SAME query (no second COUNT
      round-trip). `_one_table` reads `_total` from the first row.
    - Only the top-`<n>` rowids reach the JOIN against the content table, so
      the (potentially large) preview/citation columns are materialized for
      at most `per_table_limit` rows — this is what keeps the biggest corpus
      (zeeker-judgements.judgments) inside the fan-out budget.
    - SELECT list mirrors the legacy `_col=` projection: preview columns in
      preview-field order, then citation-placeholder columns (sorted) that
      the template needs but preview doesn't carry — the same augmentation
      as the legacy path (D6.1-02 / Finding #2). config.HEAVY_COLUMNS is
      filtered defensively so heavy text can NEVER enter the select list
      (D3-04 defense-in-depth).
    """
    # Weight literals + SELECT list via the shared helpers (Phase 3 factored
    # them out so build_maxp_sql reuses the exact same INJ-validated logic).
    weight_literals = _bm25_weight_literals(db, table, fts_columns)
    select_cols = _search_select_columns(db, table, preview)

    tq = _qi(table)
    fq = _qi(fts_table)
    select_list = ", ".join(f"{tq}.{_qi(c)}" for c in select_cols)
    # Zero-arg fallback (fts_columns unknown): bm25(<fts>) uses default 1.0
    # weights — still valid SQL, still relevance-ordered.
    bm25_expr = f"bm25({fq}, {', '.join(weight_literals)})" if weight_literals else f"bm25({fq})"
    n = int(per_table_limit)
    # Innermost level: bm25() alone with the fts5 cursor context intact.
    hits = (
        f"SELECT rowid AS _rid, {bm25_expr} AS _score "
        f"FROM {fq} WHERE {fq} MATCH :search_query LIMIT -1"
    )
    # Middle level: relevance order + full-match-set count, no bm25 call.
    ranked = (
        f"SELECT _hits._rid AS _rid, _hits._score AS _score, count(*) OVER () AS _total "
        f"FROM ({hits}) _hits ORDER BY _hits._score ASC LIMIT {n}"
    )
    sql = (
        f"SELECT {select_list}, "  # noqa: S608 — identifiers from upstream metadata only
        f"_ranked._score AS _score, _ranked._total AS _total "
        f"FROM ({ranked}) _ranked "
        f"JOIN {tq} ON {tq}.rowid = _ranked._rid "
        f"ORDER BY _score ASC LIMIT {n}"
    )
    return sql, {"search_query": escaped_query}


@dataclass(frozen=True)
class FragmentSource:
    """Discovered fragment passage-search source — issue #12 Phase 3.

    Produced by `fragment_sources_for` from config.SEARCH_FRAGMENT_SOURCES +
    upstream discovery metadata; consumed by `build_maxp_sql` /
    `_one_fragment`. All identifier fields come from config or upstream
    discovery metadata — NEVER from user input (they are interpolated into
    SQL via `_qi`; the user query travels only as the bound :search_query).

    `preview` is resolved against the PARENT table's columns — the rows this
    source produces ARE parent rows (database=<db>, table=<parent_table>).
    """

    fragment_table: str  # e.g. "judgments_fragments"
    fragment_fts: str  # e.g. "judgments_fragments_fts"
    fragment_fts_columns: list[str]  # indexed-column ORDER (may be [] — zero-arg bm25)
    parent_table: str  # e.g. "judgments"
    parent_link: str  # fragment column referencing the parent id
    parent_key: str  # parent pk column
    preview: dict[str, str | None]  # resolved against PARENT columns (D4-12)

    @property
    def merge_key(self) -> str:
        """Distinct per-list merge-dict key component (`(db, merge_key)`) so a
        fragment list can never collide with the parent table's own list."""
        return f"{self.fragment_table}→{self.parent_table}"


async def fragment_sources_for(
    db: str,
    *,
    summary: DatabaseSummary | None = None,
    visible: set[str] | None = None,
) -> list[FragmentSource]:
    """Discover fragment passage-search sources for `db` — issue #12 Phase 3.

    Returns [] in "legacy" mode unconditionally: fragment passage search runs
    on the owner-token SQL path only (anonymous `?sql=` is 403 upstream), so
    legacy deployments keep the pre-#12 denylist behavior byte-identical.

    Gates per config.SEARCH_FRAGMENT_SOURCES entry keyed `"<db>.<frag_table>"`:
      1. The fragment table exists in the summary WITH a non-null fts_table
         (the fragment fts index exists upstream — same LOAD-BEARING gate as
         searchable_tables_for gate 1).
      2. The fragment table is in `visible` (respects the hidden flag +
         HIDDEN_TABLES — an operator hiding a fragment table turns its
         passage search off too).
      3. The PARENT table passes the existing gates: present in the summary,
         visible, and preview-resolvable via `resolve_preview_columns` (the
         preview is resolved against the PARENT columns — rows surface as
         parent rows, D4-12/D4-13 coherent).

    Entries failing any gate are skipped (config may safely list sources
    ahead of upstream state). Same request-scoped memoization contract as
    `searchable_tables_for` (#6b / #9): pass `summary` + `visible` in and no
    extra upstream round-trips happen. Iteration follows config-dict order —
    deterministic for tests.

    INJ-05: the `search_fragment_parent_no_preview` log binding exposes
    `database` and `table` only — this function never sees the query string.
    """
    if config.SEARCH_RANKING == "legacy":
        return []
    if summary is None:
        summary = await DatasetteClient.current().get_database(db)
    if visible is None:
        from mcp_zeeker.core.visibility import _visible_tables

        visible = await _visible_tables(db)
    assert summary is not None  # for type checkers; always assigned above
    tables_by_name = {t.name: t for t in summary.tables}
    out: list[FragmentSource] = []
    for key, spec in config.SEARCH_FRAGMENT_SOURCES.items():
        cfg_db, _, frag_name = key.partition(".")
        if cfg_db != db:
            continue
        # Gate 1 — fragment table present upstream with an fts index.
        frag = tables_by_name.get(frag_name)
        if frag is None or frag.fts_table is None:
            continue
        # Gate 2 — fragment table visibility (hidden flag + HIDDEN_TABLES).
        if frag_name not in visible:
            continue
        # Gate 3 — parent present, visible, preview-resolvable (D4-12).
        parent = tables_by_name.get(spec["parent_table"])
        if parent is None or parent.name not in visible:
            continue
        preview = resolve_preview_columns(db, parent.name, set(parent.columns))
        if preview is None:
            log.warning("search_fragment_parent_no_preview", database=db, table=parent.name)
            continue
        out.append(
            FragmentSource(
                fragment_table=frag_name,
                fragment_fts=frag.fts_table,
                # Same-summary derivation — no extra round-trip (#6b / #9).
                # [] (fts sidecar absent from summary) → zero-arg bm25()
                # fallback in build_maxp_sql; unlike the parent-table path
                # there is no legacy dispatch to fall back to.
                fragment_fts_columns=_fts_indexed_columns(summary, frag.fts_table),
                parent_table=parent.name,
                parent_link=spec["parent_link"],
                parent_key=spec.get("parent_key", "id"),
                preview=preview,
            )
        )
    return out


def build_maxp_sql(
    db: str,
    source: FragmentSource,
    escaped_query: str,
    per_table_limit: int,
) -> tuple[str, dict[str, str]]:
    """Build the fragment→parent MaxP rollup SQL — pure function (issue #12 P3).

    Same INJ-05 / D3-09 contract as `build_bm25_sql`, auditable by inspection:
    the user query string travels ONLY via `params` as the bound
    `:search_query`; the only interpolated identifiers are table / fts /
    column names from config + upstream discovery metadata (defensively
    quoted via `_qi`); the only interpolated values are the validated numeric
    bm25 weights and the integer LIMIT.

    Shape (MaxP: best passage per parent document — three levels, the nesting
    is LOAD-BEARING for the same reason as `build_bm25_sql`):
      SELECT p.<parent preview + citation-placeholder cols>,
             _parents._score AS _score, _parents._total AS _total
      FROM (SELECT fr.<parent_link> AS _pid,
                   MIN(_hits._score) AS _score,
                   count(*) OVER () AS _total
            FROM (SELECT rowid AS _rid, bm25(<frag_fts>, w1, ...) AS _score
                  FROM <frag_fts> WHERE <frag_fts> MATCH :search_query
                  LIMIT -1) _hits
            JOIN <frag_table> fr ON fr.rowid = _hits._rid
            GROUP BY fr.<parent_link>
            ORDER BY _score ASC LIMIT <n>) _parents
      JOIN <parent> p ON p.<parent_key> = _parents._pid
      ORDER BY _score ASC LIMIT <n>

    - WHY THE NESTING (WR-260829 / production regression): an FTS5 auxiliary
      function only works while the fts5 cursor sits on the matched row.
      `MIN(bm25(...))` — an AGGREGATE over bm25 — tears that context down and
      raises `unable to use function bm25 in the requested context` (HTTP 400
      via Datasette) for any query that actually matches something; a
      zero-match query never calls bm25 and returns 200/empty, which is what
      made the pre-fix bug read as "0 hits everywhere". bm25() therefore lives
      alone in the innermost `_hits` SELECT and the aggregate consumes its
      plain `_score` column. `LIMIT -1` ("no limit" in SQLite) blocks the
      subquery-flattening optimization that would undo the separation.
    - bm25() is NEGATIVE (best = most negative) → MIN() picks each parent's
      BEST passage score (that's the MaxP), and ORDER BY ASC ranks parents.
    - Grouping is on the FRAGMENT's link column, not the parent pk, so the
      parent table is joined only for the top `<n>` groups.
    - `count(*) OVER ()` runs AFTER grouping and BEFORE LIMIT → the number of
      DISTINCT matched parent documents, surfaced as the per-source upstream
      total under the "<db>.<fragment_table>" key (no second round-trip).
    - Weights come from SEARCH_BM25_WEIGHTS["<db>.<fragment_table>"] in FTS
      column order with 1.0 default — most fragment fts tables index a single
      body column, so the default is the norm. [] → zero-arg bm25(<fts>).
    - SELECT columns resolve against the PARENT table (preview + citation
      placeholders, heavy-filtered) via the shared `_search_select_columns`.
    """
    weight_literals = _bm25_weight_literals(db, source.fragment_table, source.fragment_fts_columns)
    select_cols = _search_select_columns(db, source.parent_table, source.preview)

    fq = _qi(source.fragment_fts)
    frq = _qi(source.fragment_table)
    pq = _qi(source.parent_table)
    pk = _qi(source.parent_key)
    link = _qi(source.parent_link)
    select_list = ", ".join(f"p.{_qi(c)}" for c in select_cols)
    bm25_expr = f"bm25({fq}, {', '.join(weight_literals)})" if weight_literals else f"bm25({fq})"
    n = int(per_table_limit)
    # Innermost level: bm25() alone with the fts5 cursor context intact.
    hits = (
        f"SELECT rowid AS _rid, {bm25_expr} AS _score "
        f"FROM {fq} WHERE {fq} MATCH :search_query LIMIT -1"
    )
    # Middle level: MaxP rollup per parent + distinct-parent count, no bm25 call.
    parents = (
        f"SELECT fr.{link} AS _pid, MIN(_hits._score) AS _score, "  # noqa: S608
        f"count(*) OVER () AS _total "
        f"FROM ({hits}) _hits "
        f"JOIN {frq} fr ON fr.rowid = _hits._rid "
        f"GROUP BY fr.{link} "
        f"ORDER BY _score ASC LIMIT {n}"
    )
    sql = (
        f"SELECT {select_list}, "  # noqa: S608 — identifiers from config/upstream metadata only
        f"_parents._score AS _score, _parents._total AS _total "
        f"FROM ({parents}) _parents "
        f"JOIN {pq} p ON p.{pk} = _parents._pid "
        f"ORDER BY _score ASC LIMIT {n}"
    )
    return sql, {"search_query": escaped_query}


def _normalize_search_rows(
    db: str,
    table: str,
    preview: dict[str, str | None],
    raw_rows: list[dict],
    *,
    scored: bool,
) -> list[dict]:
    """Normalize raw upstream rows to the fixed search row shape (D4-12 / D4-21).

    Emits EXACTLY 10 keys per row (Phase 6 extended the original 6-key shape
    with license / license_url / citation per D6-03 + D6-05; issue #12 adds
    `_score`). resolve_preview_columns already filtered HEAVY_COLUMNS at
    resolution time (defense-in-depth — D3-04 / D4-12 / Plan 04-01), so heavy
    columns cannot be inlined here even if upstream returned them. The
    whitelist reshape below naturally strips any extra upstream columns —
    including the SQL path's `_total` window count. Search returns
    preview-only rows (no retrieved_content block), so D6-14 keeps `_policy`
    out of the search response entirely.

    Shared by `_one_table` (table dispatch — `table` is the content table)
    and `_one_fragment` (issue #12 Phase 3 MaxP rollup — `table` is the
    PARENT table; the rows ARE parent rows, so citations and the D4-13
    post-filter resolve against the parent). `scored=True` on the SQL paths
    (raw negative bm25 float — MIN-of-passages on the fragment path);
    `scored=False` keeps `_score: None` on the legacy path so the row shape
    stays uniform.
    """
    normalized: list[dict] = []
    # Hoist per-dispatch values outside the per-row loop — `db` and the bound
    # retrieved_at are constant across every row produced by one dispatch
    # (D6-09: single timestamp per tool call). One license_for_sync /
    # one get_tool_started_at per dispatch, not per row (T-06-13 DoS bound).
    retrieved_at_for_call = get_tool_started_at()
    # MetadataCache binding is guaranteed in production by app.py lifespan; in
    # direct-handler-call unit tests the cache may be unbound — fall back to
    # config.LICENSES so the row-shape contract holds. Mirrors the
    # `_license_pair` helper in core/envelope.py (Plan 06-02 Task 1).
    try:
        license_text, license_url_val = MetadataCache.current().license_for_sync(db)
    except RuntimeError:
        license_text, license_url_val = config.LICENSES.get(db, ("", ""))
    for r in raw_rows:
        title_col = preview.get("title")
        date_col = preview.get("date")
        summary_col = preview.get("summary")
        url_col = preview.get("url")
        raw_score = r.get("_score") if scored else None
        normalized.append(
            {
                "title": r.get(title_col) if title_col else None,
                "date": r.get(date_col) if date_col else None,
                "summary": r.get(summary_col) if summary_col else None,
                "url": r.get(url_col) if url_col else None,
                "database": db,
                "table": table,
                # Issue #12: raw negative bm25 score (best = most negative);
                # None on the legacy path (no ranking signal upstream).
                "_score": float(raw_score) if raw_score is not None else None,
                # D6-03: per-row license + license_url. Empty-string license_url
                # collapses to None for clean wire payload.
                "license": license_text,
                "license_url": license_url_val or None,
                # D6-05/06/07/08: per-row citation. synthesize_citation reads
                # config.CITATION_TEMPLATES[(db, table)] with DEFAULT_CITATION_TEMPLATE
                # fallback; _SafeDict handles None values + injects {retrieved_at}.
                # Underscore-prefixed `_citation` key matches the canonical
                # convention in core/citation.py (avoids collision with
                # upstream columns literally named `citation`).
                "_citation": synthesize_citation(db, table, r, retrieved_at_for_call),
            }
        )
    return normalized


async def _one_table(
    db: str,
    table: str,
    preview: dict[str, str | None],
    escaped: str,
    per_table_limit: int,
    fts: tuple[str, list[str]] | None,
    out_rows: dict[tuple[str, str], list[dict]],
    out_totals: dict[str, int],
    failures: list[Exception],
    sem: anyio.Semaphore,
) -> None:
    """Single per-table FTS dispatch — never raises (D4-07 / INJ-05).

    Issue #12 Phase 1: dual dispatch. When `config.SEARCH_RANKING` is not
    "legacy" AND `fts` carries `(fts_table, fts_columns)` discovery metadata,
    dispatch goes through the owner-token SQL path (`build_bm25_sql` +
    `execute_sql`) and rows are BM25-ordered with a float `_score`. Otherwise
    the legacy table-view `_search=` dispatch runs UNCHANGED (metadata date
    order) and `_score` is None — row shape is uniform either way.

    Failures are captured in the shared `failures` list so the orchestrator
    can inspect `UpstreamCallFailed.status` for the all-tables-400 → invalid_query
    promotion (D4-09 case (c)). The log binding exposes `database`, `table`,
    and `error_class` only — the query string is NEVER bound (INJ-05 / D4-07).
    """
    # Issue #12: SQL-path gate. The flag alone is not enough — the fts
    # discovery metadata must be present AND carry a non-empty indexed-column
    # list (defensive fall-back to legacy when the fts sidecar was absent
    # from the summary).
    use_sql = config.SEARCH_RANKING != "legacy" and fts is not None and bool(fts[1])
    try:
        async with sem:
            if use_sql:
                # BM25-ranked SQL path (issue #12). The user query travels
                # ONLY as the bound :search_query named parameter — see
                # build_bm25_sql's INJ-05 contract.
                assert fts is not None  # for type checkers; guarded by use_sql
                sql, sql_params = build_bm25_sql(
                    db, table, fts[0], fts[1], preview, escaped, per_table_limit
                )
                result = await DatasetteClient.current().execute_sql(db, sql, sql_params)
            else:
                # Legacy table-view dispatch — byte-identical to pre-#12.
                # Build dispatch params: ordered list-of-tuples for httpx.
                # `_shape=objects` is prepended automatically by
                # DatasetteClient.get_table_rows. `_col` projection skips any
                # preview field whose resolved column is None (D4-12 —
                # date/summary may legitimately be unmapped).
                # D6.1-02 / Finding #2: transparent citation-column
                # augmentation for search rows. `preview` resolves to up to 4
                # upstream column names (title / date / summary / url); a
                # citation template may reference additional columns (e.g.,
                # judgments uses {case_name}, {citation}, {court},
                # {decision_date}, {source_url} — only {source_url} overlaps
                # `preview["url"]`). Add the missing columns to `_col` so the
                # row dict `r` has values for synthesize_citation to
                # substitute; the per-row normalize loop below emits only the
                # fixed key set, so the augmented columns are naturally not
                # leaked at the row top level — no strip needed
                # (whitelist-shaped reshape).
                search_template = config.CITATION_TEMPLATES.get(
                    (db, table), config.DEFAULT_CITATION_TEMPLATE
                )
                search_placeholders = placeholder_columns(search_template)
                search_preview_cols = {c for c in preview.values() if c is not None}
                search_added_columns = search_placeholders - search_preview_cols
                params: list[tuple[str, str]] = [
                    ("_search", escaped),
                    ("_size", str(per_table_limit)),
                    *[("_col", c) for c in preview.values() if c is not None],
                    *[("_col", c) for c in sorted(search_added_columns)],
                ]
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
    # Normalize per D4-12 / D4-21 via the shared helper (Phase 3 of #12
    # factored it out so `_one_fragment` produces the identical row shape).
    raw_rows = result.get("rows") or []
    out_rows[(db, table)] = _normalize_search_rows(db, table, preview, raw_rows, scored=use_sql)
    if use_sql:
        # Issue #12: the SQL endpoint has no filtered_table_rows_count.
        # `count(*) OVER ()` in build_bm25_sql computed the full MATCH count
        # BEFORE LIMIT — identical on every returned row; read it from the
        # first raw row (0 when the query matched nothing).
        first_total = raw_rows[0].get("_total") if raw_rows else 0
        out_totals[f"{db}.{table}"] = int(first_total or 0)
    else:
        # filtered_table_rows_count is always present per 04-RESEARCH Probe 6
        # (even on zero-hit and `_search`-omitted responses). Default to 0 for
        # safety in case upstream omits the key under exceptional conditions.
        out_totals[f"{db}.{table}"] = int(result.get("filtered_table_rows_count") or 0)


async def _one_fragment(
    db: str,
    source: FragmentSource,
    escaped: str,
    per_table_limit: int,
    out_rows: dict[tuple[str, str], list[dict]],
    out_totals: dict[str, int],
    failures: list[Exception],
    sem: anyio.Semaphore,
) -> None:
    """Single fragment-source MaxP dispatch — never raises (issue #12 Phase 3).

    SQL-path only (the caller gates on SEARCH_RANKING != "legacy" at
    discovery time; there is no legacy fallback for fragments — they were
    denylisted pre-#12 and stay that way in legacy mode). Runs inside the
    SAME per-call semaphore + outer move_on_after budget as the table
    dispatches — fragment queries join bigger tables but get NO extra budget
    (latency guard per the Phase 3 spec).

    Rows are normalized as PARENT rows (database=db, table=parent_table) via
    the shared `_normalize_search_rows`, so the D4-13 post-filter, per-row
    citations, and the RRF url-identity dedup stay coherent. The per-source
    upstream total uses the DISTINCT key "<db>.<fragment_table>" so
    observability distinguishes passage hits from parent-table hits.

    INJ-05 / D4-07: same failure contract as `_one_table` — captured in the
    shared `failures` list; the log binding exposes `database`, `table`
    (the FRAGMENT table — that's the failing query target), and
    `error_class` only, NEVER the query string.
    """
    try:
        async with sem:
            # The user query travels ONLY as the bound :search_query named
            # parameter — see build_maxp_sql's INJ-05 contract.
            sql, sql_params = build_maxp_sql(db, source, escaped, per_table_limit)
            result = await DatasetteClient.current().execute_sql(db, sql, sql_params)
    except UpstreamCallFailed as exc:
        failures.append(exc)
        log.warning(
            "search_table_failed",
            database=db,
            table=source.fragment_table,
            error_class=type(exc).__name__,
        )
        return
    raw_rows = result.get("rows") or []
    out_rows[(db, source.merge_key)] = _normalize_search_rows(
        db, source.parent_table, source.preview, raw_rows, scored=True
    )
    # `count(*) OVER ()` in build_maxp_sql runs after GROUP BY and before
    # LIMIT → the number of DISTINCT matched parent documents; identical on
    # every returned row (0 when the query matched nothing).
    first_total = raw_rows[0].get("_total") if raw_rows else 0
    out_totals[f"{db}.{source.fragment_table}"] = int(first_total or 0)


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


def _rrf_merge(
    rows_by_table: dict[tuple[str, str], list[dict]],
    limit: int,
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion merge — issue #12 Phase 2 (SEARCH_RANKING="bm25_rrf").

    Input lists are already relevance-ordered per table (BM25 ascending — the
    SQL path's ORDER BY _score ASC). For each row, rank_L is its 1-based
    position within its own list; the fused score of a document is
    Σ 1/(k + rank_L) over every list it appears in (classic RRF, k=60 default
    per the original Cormack/Clarke/Buettcher formulation).

    Document identity ACROSS lists is the row's `url` value when non-null —
    the same document can surface from multiple lists (critical once Phase 3
    adds fragment lists), and a doc appearing in several sources accumulates
    contributions from each, ranking it higher. Rows with a null `url` are
    their own identity (keyed by object id — an int, so it can never collide
    with a str url key) and NEVER merge with anything.

    When duplicates merge, the KEPT row is the one from the list where the
    doc ranked best (lowest rank; first-encountered wins exact rank ties via
    the strict `<` comparison), while score contributions from ALL lists sum.

    Output is sorted by fused score DESC with a deterministic tie-break on
    (url, database, table) ascending — stable ordering for tests regardless
    of task-completion order upstream. Each output row gains the
    `_fused_score` float (higher = better; underscore prefix marks it
    internal-ish per the resolved issue #12 open question 2 — exposed,
    underscore-prefixed, same convention as `_score` / `_citation`). Rows are
    sliced to `limit` AFTER fusion so dedup cannot under-fill the page.
    """
    # identity → {"row": best-ranked row, "score": running RRF sum,
    #             "best_rank": best (lowest) rank seen for this doc}
    fused: dict[object, dict] = {}
    for rows in rows_by_table.values():
        for rank, row in enumerate(rows, start=1):
            url = row.get("url")
            # Null-url rows key on id(row) (int) — disjoint from str url keys,
            # so they can never merge with a url'd row or with each other.
            identity: object = url if url is not None else id(row)
            contribution = 1.0 / (k + rank)
            entry = fused.get(identity)
            if entry is None:
                fused[identity] = {"row": row, "score": contribution, "best_rank": rank}
            else:
                entry["score"] += contribution
                if rank < entry["best_rank"]:
                    entry["row"] = row
                    entry["best_rank"] = rank
    ordered = sorted(
        fused.values(),
        key=lambda e: (
            -e["score"],
            e["row"].get("url") or "",
            e["row"].get("database") or "",
            e["row"].get("table") or "",
        ),
    )
    out: list[dict] = []
    for entry in ordered[:limit]:
        row = entry["row"]
        # 11th row key on the bm25_rrf merge path (module docstring documents
        # the 10-key base shape from _one_table). Rows are freshly normalized
        # per call — in-place attach is safe.
        row["_fused_score"] = entry["score"]
        out.append(row)
    return out


async def fan_out_search(
    escaped_query: str,
    target_tables: list[tuple[str, str, dict[str, str | None]]],
    per_table_limit: int,
    fts_info: dict[tuple[str, str], tuple[str, list[str]]] | None = None,
    fragment_sources: list[tuple[str, FragmentSource]] | None = None,
) -> tuple[list[dict], dict[str, int], int, list[int | None]]:
    """Concurrent per-table FTS fan-out + merge (D4-05 / D4-06 / D4-18).

    Issue #12 Phase 1: `fts_info` optionally maps `(db, table)` →
    `(fts_table, fts_columns)` discovery metadata. Tables WITH an entry are
    dispatched via the BM25-ranked SQL path when `config.SEARCH_RANKING` is
    not "legacy"; tables without one (or when fts_info is None — the
    pre-#12 call shape) fall back to the legacy `_search=` dispatch. Both
    paths produce the same normalized row shape (`_score` float vs None).

    Issue #12 Phase 2: the merge strategy is chosen by `config.SEARCH_RANKING`
    at merge time — "bm25_rrf" fuses the per-table relevance-ordered lists via
    `_rrf_merge` (Reciprocal Rank Fusion; rows gain `_fused_score`); "bm25"
    and "legacy" keep the original `_round_robin_merge` (D4-05) unchanged, so
    round-robin remains available behind the flag.

    Issue #12 Phase 3: `fragment_sources` optionally carries `(db,
    FragmentSource)` passage-search sources (from `fragment_sources_for`).
    Each becomes an ADDITIONAL fan-out task (`_one_fragment` — MaxP rollup
    SQL) whose rows are PARENT rows, merged as one more per-list input keyed
    `(db, source.merge_key)`; its upstream total lands under the distinct
    `"<db>.<fragment_table>"` key. Fragment tasks run inside the SAME
    semaphore + move_on_after budget — no extra latency budget. In
    "legacy" mode fragment sources are ignored defensively (discovery
    already returns none — the SQL path needs the owner token).

    Returns a 4-tuple:
        (merged_rows, upstream_total_hits, failed_tables, failure_statuses)

    - `merged_rows`: merged preview rows (RRF-fused on "bm25_rrf", round-robin
      otherwise), sliced to `per_table_limit`.
    - `upstream_total_hits`: dict keyed `"<db>.<table>"` → upstream
      `filtered_table_rows_count`. A failed table does NOT get an entry —
      the caller can derive failures via `failed_tables` count + missing keys.
    - `failed_tables`: count of dispatched targets that did NOT return rows —
      those that raised `UpstreamCallFailed` PLUS those still in flight when
      the `config.SEARCH_FAN_OUT_TIMEOUT_S` budget expired and were
      cancelled. WR-260829: cancellation used to contribute nothing, which
      made a timed-out table indistinguishable from a genuine "0 hits" — the
      envelope silently under-reported instead of saying it never heard back.
      A cancelled target is absent from `upstream_total_hits` AND counted
      here, so the caller can always tell "asked, got nothing back" from
      "asked, upstream said zero".
    - `failure_statuses`: ordered list of per-failure
      `UpstreamCallFailed.status` values (`None` for transport-layer failures
      and for budget-cancelled targets). The handler uses this to detect
      all-tables-400 and map to `invalid_query` per D4-09 case (c) /
      04-RESEARCH §3.7; a cancelled target contributes `None`, so a timeout
      can never be misread as an FTS5 syntax error.

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
    # Issue #12 Phase 3: fragment lists are seeded AFTER the table lists, in
    # caller order — deterministic merge-input order on both merge paths.
    # Defensive legacy gate: discovery returns no fragment sources in legacy
    # mode, but a direct caller passing them explicitly must not trigger the
    # (owner-token-only) SQL path either.
    frag_targets: list[tuple[str, FragmentSource]] = (
        list(fragment_sources or []) if config.SEARCH_RANKING != "legacy" else []
    )
    for db, source in frag_targets:
        out_rows[(db, source.merge_key)] = []
    out_totals: dict[str, int] = {}
    failures: list[Exception] = []

    # Per-call semaphore caps concurrent upstream connections for this search
    # request. With ~15 searchable tables, an unbounded task group would open
    # 15 simultaneous connections per call; at soak concurrency=50 with 20%
    # search traffic that peaks at 150 connections, exhausting the httpx pool
    # and causing PoolTimeout → 502. Semaphore(10) bounds each call to 10
    # concurrent connections: worst case 10 searches × 10 = 100 (search) +
    # 40 (non-search) = 140, within the pool limit of 150.
    sem = anyio.Semaphore(10)
    # D4-06: structured concurrency under the outer
    # config.SEARCH_FAN_OUT_TIMEOUT_S budget. The move_on_after cancellation
    # surfaces as task cancellation inside the task group; tasks that
    # completed before the deadline have already populated out_rows /
    # out_totals. WR-260829: cancelled tasks are reconciled into the failure
    # count AFTER the block (see below) so a timeout is never reported as a
    # zero-hit table.
    with anyio.move_on_after(config.SEARCH_FAN_OUT_TIMEOUT_S):
        async with anyio.create_task_group() as tg:
            for db, table, preview in target_tables:
                tg.start_soon(
                    _one_table,
                    db,
                    table,
                    preview,
                    escaped_query,
                    per_table_limit,
                    (fts_info or {}).get((db, table)),
                    out_rows,
                    out_totals,
                    failures,
                    sem,
                )
            # Issue #12 Phase 3: fragment passage-search tasks share the SAME
            # semaphore and outer fan-out budget — no extra latency budget.
            for db, source in frag_targets:
                tg.start_soon(
                    _one_fragment,
                    db,
                    source,
                    escaped_query,
                    per_table_limit,
                    out_rows,
                    out_totals,
                    failures,
                    sem,
                )

    # Issue #12 Phase 2: merge-strategy dispatch. RRF only makes sense when
    # the per-table lists are relevance-ordered (the bm25_rrf SQL path);
    # "bm25" keeps round-robin by design (acceptance criterion: round-robin
    # stays available behind the flag) and "legacy" is the pre-#12 baseline.
    if config.SEARCH_RANKING == "bm25_rrf":
        merged = _rrf_merge(out_rows, per_table_limit)
    else:
        merged = _round_robin_merge(out_rows, per_table_limit)
    failure_statuses: list[int | None] = [getattr(exc, "status", None) for exc in failures]

    # WR-260829: reconcile budget-cancelled targets. Every dispatched target
    # either recorded an upstream total (success) or appended to `failures`;
    # anything unaccounted for was cancelled by the move_on_after budget.
    # Count it as a failure with status None so the envelope's failed_tables
    # is honest and the handler's all-400 → invalid_query promotion can never
    # fire on a timeout.
    dispatched = len(target_tables) + len(frag_targets)
    cancelled = max(0, dispatched - len(out_totals) - len(failures))
    if cancelled:
        log.warning(
            "search_fan_out_budget_exceeded",
            dispatched=dispatched,
            cancelled=cancelled,
            budget_s=config.SEARCH_FAN_OUT_TIMEOUT_S,
        )
        failure_statuses.extend([None] * cancelled)
    return merged, out_totals, len(failures) + cancelled, failure_statuses
