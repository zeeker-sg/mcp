"""
Envelope / Provenance / Pagination Pydantic models — ENV-06.

These three models are the ONLY path through which tool handlers emit responses
(ENV-06, ENV-07). Every @mcp.tool handler must return an Envelope produced by
one of the classmethod factories below. Plan 05's registry-introspection
contract test enforces this at CI time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from mcp_zeeker import config


class Provenance(BaseModel):
    """Citation-ready provenance block attached to every response (PRD §8)."""

    model_config = ConfigDict(extra="forbid")

    source: str
    database: str | None  # D-07: nullable for list_databases (spans all 4 DBs)
    table: str | None  # D-07: nullable for list_databases
    retrieved_at: datetime  # D-09: UTC, serialised as ISO 8601 by Pydantic JSON mode
    license: str
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

        D-08: license is config.LICENSE_MIXED ("mixed") because the response
        spans all four databases, each potentially with a different per-DB license.
        D-07: database and table are None — the response is DB-agnostic.
        D-09: retrieved_at is wallclock UTC at call time.
        """
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=None,
                table=None,
                retrieved_at=datetime.now(tz=UTC),
                license=config.LICENSE_MIXED,
                attribution=config.DEFAULT_ATTRIBUTION,
            ),
        )

    @classmethod
    def for_table_list(cls, *, database: str, rows: list[dict]) -> Envelope:
        """Factory for list_tables responses (DISC-02).

        D2-06: provenance scoped to a single database; table is None because
        this response spans all visible tables in the DB.
        License: config.LICENSES.get(database, "") — empty string in Phase 1;
        Phase 6 ENV-03 will wire MetadataCache-driven license strings.
        Open Q3 (RESEARCH): license value will be wired from MetadataCache in Phase 6.
        D-09: retrieved_at is wallclock UTC at call time.
        """
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=database,
                table=None,
                retrieved_at=datetime.now(tz=UTC),
                license=config.LICENSES.get(database, ""),
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
        citation: str | None = None,  # TODO(phase-6, ENV-04): wire citation into provenance
    ) -> Envelope:
        """Factory for per-table row responses (query_table, fetch, search).

        Provides a stable signature so future-phase handlers compile without
        revisiting envelope code. Phase 1 ignores the citation parameter.
        D-09: retrieved_at is wallclock UTC at call time.
        """
        return cls(
            data=rows,
            provenance=Provenance(
                source="data.zeeker.sg",
                database=database,
                table=table,
                retrieved_at=datetime.now(tz=UTC),
                license=config.LICENSES.get(database, ""),
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
        table=None, license=LICENSE_MIXED — because the response spans multiple
        databases, each potentially with a different per-DB license. Adds a
        `Pagination` block carrying the per-(db.table) `upstream_total_hits`
        drill-down hint (D4-17) and the count of per-table fan-out tasks that
        failed (`failed_tables` — D4-17).

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
                retrieved_at=datetime.now(tz=UTC),
                license=config.LICENSE_MIXED,
                attribution=config.DEFAULT_ATTRIBUTION,
            ),
            pagination=Pagination(
                upstream_total_hits=upstream_total_hits,
                failed_tables=failed_tables,
            ),
        )
