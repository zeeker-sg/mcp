"""
Envelope / Provenance / Pagination Pydantic models — ENV-06.

These three models are the ONLY path through which tool handlers emit responses
(ENV-06, ENV-07). Every @mcp.tool handler must return an Envelope produced by
one of the classmethod factories below. Plan 05's registry-introspection
contract test enforces this at CI time.

Phase 6 / Plan 06-02 rewires the four factory bodies:
- `retrieved_at` reads from `get_tool_started_at()` (the ContextVar bound by
  `RetrievedAtMiddleware` at the start of every tool call — D6-09 / D6-10 /
  D6-11 safety-net).
- `for_table_list` and `for_rows` read license via
  `MetadataCache.current().license_for_sync(database)` — D6-01 / D6-04 fallback
  chain: upstream `/-/metadata.json` non-empty value wins, otherwise
  `config.LICENSES`, otherwise empty tuple.
- `for_database_list` and `for_search_results` keep envelope-level
  `license=LICENSE_MIXED, license_url=None` per D6-03 (multi-DB envelopes
  carry envelope-level "mixed"; per-row license/license_url is populated by
  the tool handlers — `tools/discovery.py:list_databases` and
  `core/search.py:_one_table`).
- The `citation` parameter on `for_rows` is dropped per D6-05 (per-row
  citation lives in `data[i]["citation"]` attached by the handler row
  reshape — synthesize_citation; envelope-level citation is moot).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from mcp_zeeker import config
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.core.middleware.retrieved_at import get_tool_started_at


def _license_pair(database: str) -> tuple[str, str]:
    """D6-04 cold-cache acceptance extended to the cold-binding edge case.

    `MetadataCache.current()` raises `RuntimeError` when neither the contextvar
    nor the process singleton is bound (direct-handler-call unit tests that
    construct envelopes without going through the lifespan). The same degraded
    contract that handles cold-cache (`_data is None` → `("", "")`) extends
    naturally: read from `config.LICENSES` if available, otherwise empty.
    Production paths always have the cache bound by `app.py` lifespan.
    """
    try:
        return MetadataCache.current().license_for_sync(database)
    except RuntimeError:
        return config.LICENSES.get(database, ("", ""))


class Provenance(BaseModel):
    """Citation-ready provenance block attached to every response (PRD §8).

    Phase 6 D6-02: adds `license_url` (optional, defaults to None for back-compat
    with existing Phase 1-5 construction sites). Plan 06-02 wires the field in
    every envelope factory by reading MetadataCache.license_for_sync().
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    database: str | None  # D-07: nullable for list_databases (spans all 4 DBs)
    table: str | None  # D-07: nullable for list_databases
    retrieved_at: datetime  # D-09: UTC, serialised as ISO 8601 by Pydantic JSON mode
    license: str
    license_url: str | None = None  # D6-02: per-row license URL; back-compat default None
    attribution: str


class Pagination(BaseModel):
    """Optional pagination cursor block; omitted for single-record tools."""

    model_config = ConfigDict(extra="forbid")

    # Phase 1 fields — kept for forward-compat with Phase 6 ENV-04 wiring.
    total: int | None = None
    next_offset: int | None = None

    # D3-12 (Phase 3): qhash cursor + truncation surface.
    # `next_cursor` is produced by mcp_zeeker.core.cursor.encode_cursor() when the
    # upstream Datasette response includes a non-null `next` token.
    # `truncated` defaults to False; Phase 3 surfaces the value honestly from the
    # upstream response and Phase 5 FRAG-04 will wire the real use site.
    next_cursor: str | None = None
    truncated: bool = False

    # D4-17 (Phase 4): cross-DB search drill-down hint + failure count.
    # `upstream_total_hits` is keyed `"<db>.<table>"` and populated from each
    # per-table response's `filtered_table_rows_count`. Empty dict (not None)
    # on non-search calls keeps the envelope schema stable — Pydantic 2 deep-
    # copies the default per-instance so the mutable default is safe.
    # `failed_tables` is the count of per-table fan-out tasks that raised
    # UpstreamCallFailed (timeout / 5xx after retry / malformed JSON).
    upstream_total_hits: dict[str, int] = {}
    failed_tables: int = 0


class Envelope(BaseModel):
    """Top-level response wrapper for every MCP tool call (PRD §8, ENV-06)."""

    model_config = ConfigDict(extra="forbid")  # ENV-06: reject schema drift

    data: list[dict]
    provenance: Provenance
    pagination: Pagination | None = None

    @classmethod
    def for_database_list(cls, *, rows: list[dict]) -> Envelope:
        """Factory for list_databases responses.

        D-08 / D6-03: license is config.LICENSE_MIXED ("mixed") because the
        response spans all four databases, each potentially with a different
        per-DB license. `license_url=None` at envelope level — per-row
        `license` + `license_url` are populated by `tools/discovery.py:list_databases`
        from `MetadataCache.current().license_for_sync(name)`.
        D-07: database and table are None — the response is DB-agnostic.
        D6-09 / D6-11: retrieved_at sourced from the `RetrievedAtMiddleware`
        ContextVar bound at start-of-tool-call. Safety-net DEBUG fallback to
        wallclock-now when the middleware is bypassed (direct-handler-call unit
        tests).
        """
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=None,
                table=None,
                retrieved_at=get_tool_started_at(),
                license=config.LICENSE_MIXED,
                license_url=None,
                attribution=config.DEFAULT_ATTRIBUTION,
            ),
        )

    @classmethod
    def for_table_list(cls, *, database: str, rows: list[dict]) -> Envelope:
        """Factory for list_tables responses (DISC-02).

        D2-06: provenance scoped to a single database; table is None because
        this response spans all visible tables in the DB.
        D6-01 / D6-02 / D6-04: license + license_url sourced from
        `MetadataCache.current().license_for_sync(database)` — upstream
        `/-/metadata.json` non-empty value wins, otherwise `config.LICENSES`,
        otherwise empty tuple. Empty-string `license_url` collapses to None
        so the wire payload renders `null` rather than `""`.
        D6-09 / D6-11: retrieved_at via the contextvar accessor.
        """
        license_text, license_url = _license_pair(database)
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=database,
                table=None,
                retrieved_at=get_tool_started_at(),
                license=license_text,
                license_url=license_url or None,
                attribution=config.DEFAULT_ATTRIBUTION,
            ),
        )

    @classmethod
    def for_rows(
        cls,
        *,
        database: str,
        table: str,
        rows: list[dict],
        pagination: Pagination | None = None,
    ) -> Envelope:
        """Factory for per-table row responses (query_table, fetch).

        D6-01 / D6-02 / D6-04: license + license_url sourced from
        `MetadataCache.current().license_for_sync(database)`. D6-05: the
        factory no longer accepts a `citation` parameter — per-row citation
        lives in `data[i]["citation"]` attached by the handler row reshape
        via `synthesize_citation(database, table, row, retrieved_at)`.
        D6-09 / D6-11: retrieved_at via the contextvar accessor.
        """
        license_text, license_url = _license_pair(database)
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=database,
                table=table,
                retrieved_at=get_tool_started_at(),
                license=license_text,
                license_url=license_url or None,
                attribution=config.DEFAULT_ATTRIBUTION,
            ),
            pagination=pagination,
        )

    @classmethod
    def for_search_results(
        cls,
        *,
        rows: list[dict],
        upstream_total_hits: dict[str, int],
        failed_tables: int = 0,
    ) -> Envelope:
        """Factory for cross-database search responses (D4-16, SEARCH-01..06).

        Mirrors `for_database_list`'s multi-DB provenance shape — database=None,
        table=None, license=LICENSE_MIXED, license_url=None per D6-03 — because
        the response spans multiple databases. Per-row `license` + `license_url`
        + `citation` are populated by `core/search.py::_one_table`, NOT by this
        factory. D6-09 / D6-11: retrieved_at via the contextvar accessor.

        - rows: round-robin-merged preview rows (D4-05) already truncated to
          the caller-provided `limit` inside fan_out_search.
        - upstream_total_hits: per-(db.table) `filtered_table_rows_count`
          surfaced from each per-table response so the LLM can decide whether
          to narrow the query or paginate via query_table.
        - failed_tables: count of per-table fan-out tasks that raised
          UpstreamCallFailed after retry (default 0).
        """
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=None,
                table=None,
                retrieved_at=get_tool_started_at(),
                license=config.LICENSE_MIXED,
                license_url=None,
                attribution=config.DEFAULT_ATTRIBUTION,
            ),
            pagination=Pagination(
                upstream_total_hits=upstream_total_hits,
                failed_tables=failed_tables,
            ),
        )
