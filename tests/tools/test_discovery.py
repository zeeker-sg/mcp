"""
Unit tests for the list_databases tool handler (DISC-01).

Tests stub 4 upstream httpx responses (one per config.ALLOWED_DATABASES) using
pytest-httpx and call list_databases() directly (not via MCP transport — that path
is Plan 05's smoke test).

Covers:
- DISC-01: 4 rows, names match ALLOWED_DATABASES, description, table_count.
- D-11: table_count math — visible = total minus HIDDEN_TABLES entries.
- D-17: UpstreamCallFailed propagated on upstream error.
- D-01: list_tables stub raises NotImplementedError (unregistered until Phase 2).
"""

from __future__ import annotations

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed
from mcp_zeeker.tools.discovery import list_databases, list_tables


def _db_url(name: str) -> str:
    """Build the full URL that DatasetteClient.get_database will request."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _tables_payload(names: list[str]) -> dict:
    """Build a minimal Datasette /{db}.json payload with the given table names."""
    return {"tables": [{"name": n} for n in names]}


@pytest.fixture
def bound_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Bind a DatasetteClient to the current context for the duration of the test."""
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    dc = DatasetteClient(http)
    token = DatasetteClient.bind(dc)
    yield dc
    DatasetteClient.reset(token)


async def test_list_databases(
    httpx_mock: pytest_httpx.HTTPXMock, bound_client: DatasetteClient
) -> None:
    """DISC-01: list_databases returns 4 rows with correct shape and provenance.

    sglawwatch payload includes 'metadata' and 'schema_versions' (HIDDEN_TABLES)
    plus 3 real tables, so visible_count should be 3. Other DBs have 3 tables each,
    none hidden, so visible_count is 3 for all.
    """
    # Stub responses for each database
    for db in config.ALLOWED_DATABASES:
        if db == "sglawwatch":
            # 5 tables: 2 hidden + 3 visible
            tables = ["metadata", "schema_versions", "t1", "t2", "t3"]
        else:
            # 3 visible tables, none hidden
            tables = ["t1", "t2", "t3"]
        httpx_mock.add_response(
            url=_db_url(db),
            json=_tables_payload(tables),
        )

    envelope = await list_databases()

    assert len(envelope.data) == 4
    names_in_response = {row["name"] for row in envelope.data}
    assert names_in_response == set(config.ALLOWED_DATABASES)

    for row in envelope.data:
        assert set(row.keys()) == {"name", "description", "table_count"}
        assert row["description"] == config.DATABASE_DESCRIPTIONS[row["name"]]
        if row["name"] == "sglawwatch":
            assert row["table_count"] == 3  # 5 total - 2 hidden
        else:
            assert row["table_count"] == 3  # 3 total - 0 hidden

    # Provenance checks (D-07, D-08)
    assert envelope.provenance.database is None
    assert envelope.provenance.table is None
    assert envelope.provenance.license == "mixed"
    assert envelope.provenance.source == "data.zeeker.sg"


async def test_list_databases_propagates_upstream_failure(
    httpx_mock: pytest_httpx.HTTPXMock, bound_client: DatasetteClient
) -> None:
    """D-17: UpstreamCallFailed propagates when one upstream DB returns 500."""
    # Stub first DB with 500; remaining DBs may or may not be called (gather short-circuits)
    for db in config.ALLOWED_DATABASES:
        httpx_mock.add_response(url=_db_url(db), status_code=500)

    with pytest.raises(UpstreamCallFailed):
        await list_databases()


async def test_list_databases_stubs_are_unregistered() -> None:
    """D-01: list_tables (Phase 2) raises NotImplementedError — not yet registered."""
    with pytest.raises(NotImplementedError, match="Phase 2"):
        await list_tables("zeeker-judgements")
