"""
Phase 1 — draft input models for discovery tools.
Phase 2 — output models for describe_table (TableSchema, ColumnInfo).

Phase 3 (retrieval) / Phase 4 (search) will revise per D-04 caveat.

These models are INTERNAL validators (ANNO-04: extra='forbid').
They are NOT registered as FastMCP tool parameters — tool signatures use
plain Annotated[T, Field(...)] per-parameter style (Pattern E / TRANSPORT-04).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ListDatabasesInput(BaseModel):
    """Input model for list_databases. No parameters (PRD §7.1)."""

    model_config = ConfigDict(extra="forbid")


class ListTablesInput(BaseModel):
    """Input model for list_tables (Phase 2 will register). PRD §7.2."""

    model_config = ConfigDict(extra="forbid")

    database: str


class DescribeTableInput(BaseModel):
    """Input model for describe_table (Phase 2 will register). PRD §7.3."""

    model_config = ConfigDict(extra="forbid")

    database: str
    table: str


# ---------------------------------------------------------------------------
# Phase 2 — describe_table OUTPUT models (DISC-03, D2-12)
# ---------------------------------------------------------------------------


class ColumnInfo(BaseModel):
    """Per-column descriptor in TableSchema (DISC-03, Pattern N).

    extra='forbid' ensures downstream schema drift is caught at construction time.
    Upstream foreign_keys/indexes/triggers are NEVER read into this model — only
    the flat columns list flows through (T-02-schema-leak threat mitigation).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str          # SQLite affinity type: TEXT, INTEGER, REAL, BLOB, NUMERIC
    description: str = ""


class TableSchema(BaseModel):
    """Locked 8-field output schema for describe_table responses (D2-12, DISC-03).

    Field declaration ORDER is load-bearing per D2-12 — model_dump() preserves it
    so Envelope.for_rows(rows=[schema.model_dump()]) produces a deterministic response.

    extra='forbid' blocks any attempt to pass foreign_keys, indexes, or triggers from
    upstream Datasette payloads into the response (T-02-schema-leak mitigation).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    columns: list[ColumnInfo]
    light_columns: list[str]
    available_columns: list[str]
    url_keyed: bool
    supports_fragments: bool
    row_count: int | None
    description: str = ""
