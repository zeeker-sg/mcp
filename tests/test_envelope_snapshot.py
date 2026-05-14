"""
Envelope snapshot tests — Wave 0 STUB.

Plan 06-03 ships the GREEN body: parametrized snapshot across every tool
(list_databases, list_tables, describe_table, query_table, fetch, search)
asserting heavy-namespace contract (set(row.keys()) ∩ HEAVY_COLUMNS == ∅,
set(row['retrieved_content'].keys()) ⊆ HEAVY_COLUMNS), per-row citation
shape, and retrieved_at literal '2026-01-01T00:00:00+00:00' under the
frozen_retrieved_at fixture.

Coverage planned: ENV-02 (retrieved_at), ENV-05 (heavy namespace), INJ-04
(no heavy text in row level), INJ-03 (byte-identical round-trip).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mcp_zeeker import config
from mcp_zeeker.core.citation import _SafeDict, synthesize_citation
from mcp_zeeker.core.middleware.retrieved_at import tool_started_at
from mcp_zeeker.server import mcp  # noqa: F401 — Plan 06-03 iterates the registry


def test_wave0_envelope_snapshot_stub():
    """Wave-0 stub: structural check that Plan 06-01 / 06-02 symbols resolve."""
    # Plan 06-01 ships these symbols (Task 1 / Task 2)
    assert callable(synthesize_citation)
    assert callable(_SafeDict)
    assert isinstance(config.HEAVY_COLUMNS, frozenset)
    assert "_policy" in config.HEAVY_COLUMNS  # D6-snapshot-relax
    # frozen_retrieved_at fixture and tool_started_at contextvar are wired
    assert tool_started_at.get(None) is None
    # Quick sanity: synthesize_citation honors DEFAULT_CITATION_TEMPLATE
    rendered = synthesize_citation(
        "not-a-db", "not-a-table", {"url": "https://x.test"}, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert "x.test" in rendered

    pytest.skip(reason="Wave 0 stub — parametrized snapshot body fills in Plan 06-03")
