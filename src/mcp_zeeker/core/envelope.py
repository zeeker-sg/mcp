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

    # Filled in by later phases; Phase 1 keeps the type importable.
    total: int | None = None
    next_offset: int | None = None


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
