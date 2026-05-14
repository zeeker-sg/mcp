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
from mcp_zeeker.tools.discovery import list_databases


def _db_url(name: str) -> str:
    """Build the full URL that DatasetteClient.get_database will request."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _tables_payload(names: list[str]) -> dict:
    """Build a minimal Datasette /{db}.json payload with the given table names."""
    return {"tables": [{"name": n} for n in names]}


def _tables_payload_with_hidden(visible: list[str], hidden: list[str]) -> dict:
    """Build a Datasette /{db}.json payload with explicit hidden flags.

    visible: table names that should have hidden=False
    hidden: table names that should have hidden=True (upstream FTS/aux tables)
    """
    rows = [
        {"name": n, "hidden": False, "count": None, "columns": [], "primary_keys": []}
        for n in visible
    ]
    rows += [
        {"name": n, "hidden": True, "count": None, "columns": [], "primary_keys": []}
        for n in hidden
    ]
    return {"tables": rows}


@pytest.fixture
async def bound_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Bind a DatasetteClient to the current context for the duration of the test."""
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
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

    # Phase 6 / D6-03: list_databases rows now carry per-row `license` +
    # `license_url` in addition to the original 3 keys. The cache is unbound
    # in this direct-handler-call test, so list_databases falls back to
    # config.LICENSES — license_text equals config.LICENSES[name][0] when the
    # DB has an entry, else "".
    for row in envelope.data:
        assert set(row.keys()) == {
            "name",
            "description",
            "table_count",
            "license",
            "license_url",
        }
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


@pytest.mark.skip(reason="Phase 2 implements list_tables — see test_list_tables.py")
async def test_list_databases_stubs_are_unregistered() -> None:
    """Retired: Phase 2 implemented list_tables. See tests/tools/test_list_tables.py."""
    pass


async def test_list_databases_table_count_excludes_upstream_hidden(
    httpx_mock: pytest_httpx.HTTPXMock, bound_client: DatasetteClient
) -> None:
    """CR-02 regression: list_databases table_count must exclude upstream hidden=True tables.

    sglawwatch gets 2 visible tables, 2 config-HIDDEN_TABLES entries, and 2 upstream-hidden
    FTS aux tables. Expected table_count == 2 (not 4 which would be the result without
    checking t.hidden).
    """
    for db in config.ALLOWED_DATABASES:
        if db == "sglawwatch":
            # 2 visible + 2 config-hidden + 2 upstream-hidden
            httpx_mock.add_response(
                url=_db_url(db),
                json=_tables_payload_with_hidden(
                    visible=["headlines", "commentaries", "metadata", "schema_versions"],
                    hidden=["headlines_fts", "headlines_fts_content"],
                ),
            )
        else:
            httpx_mock.add_response(
                url=_db_url(db),
                json=_tables_payload(["t1", "t2", "t3"]),
            )

    envelope = await list_databases()

    sglawwatch_row = next(r for r in envelope.data if r["name"] == "sglawwatch")
    # 2 upstream-hidden (fts) + 2 config-HIDDEN_TABLES (metadata, schema_versions) filtered out
    # → only "headlines" and "commentaries" remain
    assert sglawwatch_row["table_count"] == 2, (
        f"Expected 2 visible tables for sglawwatch, got {sglawwatch_row['table_count']}. "
        "list_databases must exclude both upstream hidden=True and config.HIDDEN_TABLES entries."
    )
