"""
Unit tests for describe_table tool handler — DISC-03 / DISC-04.

Covers:
- Locked 8-field schema shape (DISC-03, D2-12)
- No foreign_keys / indexes / triggers leakage (T-02-schema-leak)
- Hidden columns stripped from available_columns (D2-10, T-02-hidden-col-leak)
- light_columns ⊂ available_columns, len > 0 (DISC-04)
- Heavy text column in available but NOT in light (D2-11)
- url_keyed=True for zeeker-judgements.judgments
- supports_fragments=True for parent table (judgments) and fragment table (judgments_fragments)
- row_count=None passthrough (D2-13)
- column_types fallback to config.COLUMN_TYPES when upstream returns error (Pitfall 5)
"""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.discovery import describe_table
from fastmcp.exceptions import ToolError


def _db_url(name: str) -> str:
    """Full upstream URL for /{db}.json."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _zeeker_schemas_url(db: str) -> str:
    """Full upstream URL for /{db}/_zeeker_schemas.json."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{db}/_zeeker_schemas.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


def _judgments_db_payload(
    *,
    count: int | None = None,
    extra_tables: list[dict] | None = None,
) -> dict:
    """Canonical zeeker-judgements.json payload for describe_table tests.

    judgments columns include a global-hidden 'id', light columns, and a heavy
    text column 'content_text' to exercise DISC-04 light/available distinction.
    """
    tables = [
        {
            "name": "judgments",
            "hidden": False,
            "count": count,
            "columns": ["id", "citation", "case_name", "content_text", "html_raw", "source_url"],
            "primary_keys": ["id"],
        },
        {
            "name": "judgments_fragments",
            "hidden": False,
            "count": None,
            "columns": ["id", "judgment_id", "ordinal", "content_text"],
            "primary_keys": ["id"],
        },
        {"name": "_zeeker_schemas", "hidden": False, "count": None, "columns": [], "primary_keys": []},
        {"name": "_zeeker_updates", "hidden": False, "count": None, "columns": [], "primary_keys": []},
    ]
    if extra_tables:
        tables.extend(extra_tables)
    return {"tables": tables}


def _judgments_schema_payload() -> dict:
    """/_zeeker_schemas.json response for zeeker-judgements.

    Provides column types for judgments table (single row).
    judgments_fragments is intentionally absent — testing config fallback.
    """
    col_defs = {
        "citation": "TEXT",
        "case_name": "TEXT",
        "content_text": "TEXT",
        "html_raw": "TEXT",
        "source_url": "TEXT",
    }
    return {
        "columns": ["resource_name", "schema_version", "schema_hash", "column_definitions", "created_at", "updated_at"],
        "rows": [
            ["judgments", 1, "abc123", json.dumps(col_defs), "2024-01-01", "2024-01-01"],
        ],
    }


@pytest.fixture
def datasette_client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:
    """Bind a DatasetteClient without pre-stubbing upstream (tests supply custom payloads)."""
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    dc = DatasetteClient(http)
    token = DatasetteClient.bind(dc)
    yield dc
    DatasetteClient.reset(token)


@pytest.fixture
def metadata_cache(httpx_mock: pytest_httpx.HTTPXMock) -> MetadataCache:
    """Bind a MetadataCache with empty metadata (tests use config fallback by default)."""
    httpx_mock.add_response(
        url=_metadata_url(),
        json={"databases": {}},
        is_reusable=True,
    )
    mc = MetadataCache(httpx.AsyncClient(base_url=config.UPSTREAM_URL), config.UPSTREAM_URL, ttl=0)
    token = MetadataCache.bind(mc)
    yield mc
    MetadataCache.reset(token)
    MetadataCache.clear_singleton()


async def test_locked_field_set(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """DISC-03, D2-12: describe_table returns exactly the locked 8-field schema."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")

    assert len(envelope.data) == 1
    schema = envelope.data[0]
    assert set(schema.keys()) == {"name", "columns", "light_columns", "available_columns", "url_keyed", "supports_fragments", "row_count", "description"}


async def test_no_foreign_keys_or_indexes_leak(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """T-02-schema-leak: no foreign_keys, indexes, triggers in response (TableSchema extra='forbid')."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")
    schema = envelope.data[0]

    assert "foreign_keys" not in schema
    assert "indexes" not in schema
    assert "triggers" not in schema


async def test_hidden_columns_stripped(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """T-02-hidden-col-leak: global-hidden 'id' not in available_columns or light_columns."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")
    schema = envelope.data[0]

    assert "id" not in schema["available_columns"]
    assert "id" not in schema["light_columns"]


async def test_light_subset_of_available(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """DISC-04: light_columns ⊂ available_columns and len(light_columns) > 0."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")
    schema = envelope.data[0]

    light = set(schema["light_columns"])
    available = set(schema["available_columns"])
    assert light <= available, f"light_columns not subset of available_columns: {light - available}"
    assert len(schema["light_columns"]) > 0, "light_columns should not be empty"


async def test_heavy_in_available_not_in_light(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-11: heavy text column (content_text) in available but NOT in light_columns."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")
    schema = envelope.data[0]

    assert "content_text" in schema["available_columns"]
    assert "content_text" not in schema["light_columns"]


async def test_url_keyed_true_for_judgments(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """URL_COLUMNS: zeeker-judgements.judgments has url_keyed=True."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")

    assert envelope.data[0]["url_keyed"] is True


async def test_supports_fragments_for_parent(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Open Q1 dual-direction: parent table (judgments) has supports_fragments=True."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")

    assert envelope.data[0]["supports_fragments"] is True


async def test_supports_fragments_for_fragment(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Open Q1 dual-direction: fragment table (judgments_fragments) has supports_fragments=True."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    # judgments_fragments is absent from _zeeker_schemas — uses config fallback
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments_fragments")

    assert envelope.data[0]["supports_fragments"] is True


async def test_row_count_null_passthrough(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D2-13: row_count=None passes through honestly — not substituted with -1 or 0."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(count=None), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments")

    assert envelope.data[0]["row_count"] is None


async def test_column_types_fallback_to_config(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Pitfall 5: when upstream _zeeker_schemas returns 502, types come from config.COLUMN_TYPES."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    # Stub _zeeker_schemas with 502 so get_table_column_types returns {}
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), status_code=502)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), status_code=502)

    envelope = await describe_table("zeeker-judgements", "judgments_fragments")

    schema = envelope.data[0]
    columns_by_name = {col["name"]: col for col in schema["columns"]}
    # ordinal must be INTEGER from config.COLUMN_TYPES (not TEXT default)
    assert "ordinal" in columns_by_name
    assert columns_by_name["ordinal"]["type"] == "INTEGER"


async def test_fragment_table_hidden_columns_stripped(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """T-02-hidden-col-leak: per-table hidden columns (judgment_id) stripped for fragments table."""
    httpx_mock.add_response(url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True)
    httpx_mock.add_response(url=_zeeker_schemas_url("zeeker-judgements"), json=_judgments_schema_payload())

    envelope = await describe_table("zeeker-judgements", "judgments_fragments")
    schema = envelope.data[0]

    # 'judgment_id' is in HIDDEN_COLUMNS["zeeker-judgements.judgments_fragments"]
    assert "id" not in schema["available_columns"]
    assert "judgment_id" not in schema["available_columns"]
