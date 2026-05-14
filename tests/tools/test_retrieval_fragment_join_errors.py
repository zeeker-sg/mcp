"""Phase 5 — handler error-path tests for the fragment-join contract.

Plan 05-02 flips all 3 RED stubs to GREEN: keyset cursor malformed (D5-07),
limit cap on fragment-join path (D5-08), fragment-table fall-through (D5-03).
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.filter_compiler import Filter
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.retrieval import query_table
from tests.conftest import _load_fragments_fixture


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _zeeker_schemas_url(db: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{db}/_zeeker_schemas.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _empty_schema_payload() -> dict:
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


def _judgments_db_payload() -> dict:
    return {
        "tables": [
            {
                "name": "judgments",
                "hidden": False,
                "count": 219,
                "columns": [
                    "id",
                    "citation",
                    "case_name",
                    "decision_date",
                    "source_url",
                    "summary",
                    "created_at",
                ],
                "primary_keys": ["id"],
            },
            {
                "name": "judgments_fragments",
                "hidden": False,
                "count": 5000,
                "columns": [
                    "id",
                    "judgment_id",
                    "ordinal",
                    "paragraph_number",
                    "class_name",
                    "section_heading",
                ],
                "primary_keys": ["id"],
            },
        ]
    }


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.fixture
async def metadata_cache(httpx_mock: pytest_httpx.HTTPXMock):
    httpx_mock.add_response(
        url=_metadata_url(), json={"databases": {}}, is_reusable=True, is_optional=True
    )
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
        token = MetadataCache.bind(mc)
        yield mc
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()


# ---------------------------------------------------------------------------
# D5-07 — keyset cursor malformed message (fixed literal, no value echo)
# ---------------------------------------------------------------------------


async def test_keyset_cursor_malformed_message(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-07: garbage cursor on fragment-join path raises the EXACT fixed
    literal `invalid_cursor: keyset cursor is malformed`. Cursor contents
    are NEVER echoed in the message (INJ-05)."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json"),
        is_reusable=True,
    )

    with pytest.raises(ToolError, match=r"^invalid_cursor: keyset cursor is malformed$"):
        await query_table(
            database="zeeker-judgements",
            table="judgments_fragments",
            filters=[
                Filter(
                    column="source_url",
                    op="exact",
                    value="https://www.elitigation.sg/gd/s/2026_SGFC_46",
                )
            ],
            cursor="!!!not-base64!!!",
        )


# ---------------------------------------------------------------------------
# D5-08 — limit cap on fragment-join path
# ---------------------------------------------------------------------------


async def test_limit_cap_on_fragment_join(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-08: limit=101 on fragment-join path raises the EXACT fixed literal
    `invalid_filter_op: limit exceeds fragment-join cap of 100`. No `{limit}`
    interpolation in the message (INJ-05)."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json"),
        is_reusable=True,
    )

    with pytest.raises(
        ToolError,
        match=r"^invalid_filter_op: limit exceeds fragment-join cap of 100$",
    ):
        await query_table(
            database="zeeker-judgements",
            table="judgments_fragments",
            filters=[
                Filter(
                    column="source_url",
                    op="exact",
                    value="https://www.elitigation.sg/gd/s/2026_SGFC_46",
                )
            ],
            limit=101,
        )


# ---------------------------------------------------------------------------
# D5-03 — fragment-table fall-through (no eq-URL filter)
# ---------------------------------------------------------------------------


async def test_fragment_table_without_eq_filter_falls_through(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-03 fall-through: a fragment-table query WITHOUT an exact-URL filter
    skips fragment_join entirely — only the fragment table itself is hit
    (no /judgments.json parent lookup). The standard query_table path
    handles the call; FRAG-02 (HIDDEN_COLUMNS) preserves the response edge."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )
    # NO parent lookup stub — fragment_join.compile_filter falls through.
    # Stub the fragment table directly with a synthetic 5-row payload.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json={
            "rows": [
                {
                    "id": f"x_{i:04d}",
                    "judgment_id": "x",
                    "ordinal": i + 6,
                    "paragraph_number": i + 7,
                    "class_name": "para",
                    "section_heading": "",
                }
                for i in range(5)
            ],
            "columns": [
                "id",
                "judgment_id",
                "ordinal",
                "paragraph_number",
                "class_name",
                "section_heading",
            ],
            "next": None,
            "truncated": False,
            "filtered_table_rows_count": 5,
        },
    )

    envelope = await query_table(
        database="zeeker-judgements",
        table="judgments_fragments",
        filters=[Filter(column="ordinal", op="gt", value=5)],
    )

    assert len(envelope.data) == 5

    # No request hit the parent table — proves the fall-through path skipped
    # the Call 1 parent lookup.
    parent_path = "/zeeker-judgements/judgments.json"
    parent_hits = [r for r in httpx_mock.get_requests() if r.url.path == parent_path]
    assert parent_hits == [], (
        f"fall-through path must skip parent lookup, but hit /judgments.json "
        f"{len(parent_hits)} times"
    )
