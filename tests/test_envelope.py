"""
Tests for ENV-06: Envelope / Provenance / Pagination Pydantic models.

Tests:
- extra='forbid' rejects unknown keys on all three models.
- Envelope.for_database_list produces correct provenance shape.
- retrieved_at is timezone-aware UTC.
- Envelope.for_rows signature is stable and returns correct Envelope.
- DEFAULT_ATTRIBUTION is propagated as non-empty string.
- Envelope serializes to dict with correct key set.
"""

import pytest
from pydantic import ValidationError

from mcp_zeeker import config
from mcp_zeeker.core.envelope import Envelope, Provenance


def _valid_provenance_kwargs() -> dict:
    """Return minimal valid kwargs for a Provenance instance."""
    from datetime import UTC, datetime

    return {
        "source": "data.zeeker.sg",
        "database": "pdpc",
        "table": "enforcement_decisions",
        "retrieved_at": datetime.now(tz=UTC),
        "license": "CC-BY-4.0",
        "attribution": "Test Attribution",
    }


def test_envelope_extra_forbid_rejects_unknown_keys():
    """ENV-06: Envelope rejects extra fields at construction time."""
    provenance = Provenance(**_valid_provenance_kwargs())
    with pytest.raises(ValidationError):
        Envelope(data=[], provenance=provenance, bogus_field="x")


def test_provenance_extra_forbid():
    """ENV-06: Provenance rejects extra fields at construction time."""
    kwargs = _valid_provenance_kwargs()
    kwargs["mystery_field"] = "leaked"
    with pytest.raises(ValidationError):
        Provenance(**kwargs)


def test_for_database_list_sets_nullable_db_table():
    """D-07 + D-08: for_database_list sets database=None, table=None, correct license."""
    env = Envelope.for_database_list(rows=[{"name": "x"}])

    assert env.provenance.database is None
    assert env.provenance.table is None
    assert env.provenance.source == "data.zeeker.sg"
    assert env.provenance.license == config.LICENSE_MIXED
    assert env.data == [{"name": "x"}]
    assert env.pagination is None


def test_retrieved_at_is_utc():
    """D-09: retrieved_at is timezone-aware and UTC offset is zero."""
    env = Envelope.for_database_list(rows=[])

    assert env.provenance.retrieved_at.tzinfo is not None
    assert env.provenance.retrieved_at.utcoffset().total_seconds() == 0


def test_for_rows_signature_stable():
    """Phase 1 signature stability: for_rows compiles and returns an Envelope."""
    env = Envelope.for_rows(
        database="pdpc",
        table="enforcement_decisions",
        rows=[],
    )

    assert isinstance(env, Envelope)
    assert env.provenance.database == "pdpc"
    assert env.provenance.table == "enforcement_decisions"


def test_for_database_list_attribution_nonempty():
    """ENV-06 / CFG-02: provenance.attribution propagates DEFAULT_ATTRIBUTION (non-empty)."""
    env = Envelope.for_database_list(rows=[])

    assert env.provenance.attribution == config.DEFAULT_ATTRIBUTION
    assert env.provenance.attribution  # truthy — non-empty string


def test_envelope_serializable_to_dict():
    """Envelope serializes to JSON-mode dict with correct top-level keys."""
    env = Envelope.for_database_list(rows=[{"name": "x"}])
    dumped = env.model_dump(mode="json")

    assert set(dumped.keys()) == {"data", "provenance", "pagination"}
    # In JSON mode Pydantic serializes datetime to ISO 8601 string
    assert isinstance(dumped["provenance"]["retrieved_at"], str)
    # Basic ISO 8601 check: contains 'T'
    assert "T" in dumped["provenance"]["retrieved_at"]
