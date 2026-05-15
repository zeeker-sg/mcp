"""
TEST-01 hidden-data enforcement sweep — list_tables + describe_table
strip every entry in HIDDEN_TABLES + HIDDEN_COLUMNS denylist.

Parametrizes across:
- Every (database, hidden_table) pair from config.HIDDEN_TABLES
  → test_list_tables_strips_hidden
- Every (database, table, hidden_column) triple derived via
  hidden_columns_for(db, table) for each production-known visible table
  → test_describe_table_strips_hidden_columns

Design notes (08-RESEARCH.md Pitfall 5):
- Each stub INCLUDES the hidden item in the upstream payload. This exercises
  the STRIPPING code path, not the trivially-pass-when-stub-omits path.
- hidden_columns_for is used for hidden-column lookup (D2-10). Direct reads
  of the HIDDEN_COLUMNS config dict are intentionally absent from this file.

08-VALIDATION.md canonical commands:
  pytest tests/test_hidden_data_enforcement.py::test_list_tables_strips_hidden -x
  pytest tests/test_hidden_data_enforcement.py::test_describe_table_strips_hidden_columns -x
"""

from __future__ import annotations

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.config_lookup import hidden_columns_for
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.discovery import describe_table, list_tables

# ---------------------------------------------------------------------------
# URL builders (mirrors tests/tools/test_list_tables.py)
# ---------------------------------------------------------------------------


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _zeeker_schemas_url(db: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{db}/_zeeker_schemas.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


def _empty_metadata_stub() -> dict:
    return {"databases": {}}


# ---------------------------------------------------------------------------
# Parametrize sources
# ---------------------------------------------------------------------------


def _iter_hidden_table_pairs() -> list[tuple[str, str]]:
    """Yield (database, hidden_table) for every entry in config.HIDDEN_TABLES."""
    result = []
    for database, hidden_set in config.HIDDEN_TABLES.items():
        for hidden_table in sorted(hidden_set):
            result.append((database, hidden_table))
    return result


# Production-known visible tables per database (sourced from config.LIGHT_COLUMNS keys).
# This avoids introspecting upstream and keeps the parametrize source deterministic.
_VISIBLE_TABLES: dict[str, list[str]] = {
    "zeeker-judgements": ["judgments", "judgments_fragments"],
    "pdpc": ["enforcement_decisions", "enforcement_decisions_fragments"],
    "sg-gov-newsrooms": [
        "acra_news",
        "agc_news",
        "ccs_news",
        "ipos_news",
        "judiciary_news",
        "mlaw_news",
        "mom_news",
        "pdpc_news",
    ],
    "sglawwatch": [
        "headlines",
        "commentaries",
        "about_singapore_law",
        "about_singapore_law_fragments",
    ],
}


def _iter_hidden_column_triples() -> list[tuple[str, str, str]]:
    """Yield (database, table, hidden_column) for every hidden column returned by
    hidden_columns_for(db, table) across all production-known visible tables.

    Uses hidden_columns_for — never reads the hidden-columns config dict directly (D2-10).
    """
    result = []
    for database in config.ALLOWED_DATABASES:
        for table in _VISIBLE_TABLES.get(database, []):
            for hidden_col in sorted(hidden_columns_for(database, table)):
                result.append((database, table, hidden_col))
    return result


# ---------------------------------------------------------------------------
# Local fixtures (per-file; DO NOT modify tests/conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:  # type: ignore[misc]
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.fixture
async def metadata_cache(httpx_mock: pytest_httpx.HTTPXMock) -> MetadataCache:  # type: ignore[misc]
    httpx_mock.add_response(
        url=_metadata_url(),
        json=_empty_metadata_stub(),
        is_reusable=True,
    )
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
        token = MetadataCache.bind(mc)
        yield mc
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()


# ---------------------------------------------------------------------------
# Helpers — payload builders
# ---------------------------------------------------------------------------


def _tables_payload_with_hidden(visible_names: list[str], hidden_table: str) -> dict:
    """Build a /{db}.json payload including the hidden_table in the table list.

    Per 08-RESEARCH.md Pitfall 5: the stub MUST include the hidden table so
    the test exercises the strip code path, not the trivially-pass path.
    """
    all_names = visible_names + [hidden_table]
    return {
        "tables": [
            {
                "name": n,
                "hidden": False,
                "count": None,
                "columns": [],
                "primary_keys": [],
            }
            for n in all_names
        ]
    }


def _db_payload_with_hidden_column(
    table: str, visible_columns: list[str], hidden_column: str
) -> dict:
    """Build a /{db}.json payload for a single table that includes hidden_column.

    Per 08-RESEARCH.md Pitfall 5: the stub MUST include the hidden column so
    the test exercises the strip code path.
    """
    all_columns = visible_columns + [hidden_column]
    return {
        "tables": [
            {
                "name": table,
                "hidden": False,
                "count": None,
                "columns": all_columns,
                "primary_keys": [],
            }
        ]
    }


def _empty_schemas_payload() -> dict:
    """Minimal /{db}/_zeeker_schemas.json with no rows (use config fallback for types)."""
    return {
        "columns": [
            "resource_name",
            "schema_version",
            "schema_hash",
            "column_definitions",
            "created_at",
            "updated_at",
        ],
        "rows": [],
    }


# ---------------------------------------------------------------------------
# TEST-01 (hidden-table): parametrized strip sweep across HIDDEN_TABLES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("database, hidden_table", _iter_hidden_table_pairs())
async def test_list_tables_strips_hidden(
    database: str,
    hidden_table: str,
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """TEST-01 / D2-09: list_tables must strip every entry in config.HIDDEN_TABLES.

    The upstream stub INCLUDES hidden_table in the table list — this exercises
    the actual strip code path (08-RESEARCH.md Pitfall 5). If the production code
    were broken (no denylist check), hidden_table would appear in envelope.data and
    this assertion would fail.
    """
    visible = _VISIBLE_TABLES.get(database, ["t1", "t2"])
    httpx_mock.add_response(
        url=_db_url(database),
        json=_tables_payload_with_hidden(visible, hidden_table),
    )
    envelope = await list_tables(database)

    names = {row["name"] for row in envelope.data}
    assert hidden_table not in names, (
        f"hidden_table {hidden_table!r} leaked from list_tables({database!r}); "
        f"envelope.data names: {sorted(names)!r}"
    )


# ---------------------------------------------------------------------------
# TEST-01 (hidden-column): parametrized strip sweep across HIDDEN_COLUMNS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("database, table, hidden_column", _iter_hidden_column_triples())
async def test_describe_table_strips_hidden_columns(
    database: str,
    table: str,
    hidden_column: str,
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """TEST-01 / D2-10: describe_table must strip every hidden column from envelope.

    The upstream stub INCLUDES hidden_column in the table's column list — this
    exercises the actual strip code path (08-RESEARCH.md Pitfall 5). Uses
    hidden_columns_for (not direct hidden-columns dict reads) per D2-10.
    """
    # Derive a minimal visible column list for the table (use first light col if available)
    light = config.LIGHT_COLUMNS.get(f"{database}.{table}", [])
    # Use first light column as a visible sentinel; fall back to a generic name
    visible_col = light[0] if light else "some_visible_column"

    httpx_mock.add_response(
        url=_db_url(database),
        json=_db_payload_with_hidden_column(table, [visible_col], hidden_column),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url(database),
        json=_empty_schemas_payload(),
        is_reusable=True,
    )

    envelope = await describe_table(database, table)

    col_names = {col["name"] for col in envelope.data[0]["columns"]}
    assert hidden_column not in col_names, (
        f"hidden_column {hidden_column!r} leaked from describe_table({database!r}, {table!r}); "
        f"columns: {sorted(col_names)!r}"
    )
