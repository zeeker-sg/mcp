"""Phase 5 — side-channel counter-patch test for fragment-join routing.

Counter-patches `mcp_zeeker.tools.retrieval.fragment_join.compile_filter` to
prove all three fragment-table pairs route through the SAME helper (single
auditable code path per D5-01).

Plan 05-02 flips this RED stub to GREEN.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core import fragment_join
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
                    "case_numbers",
                    "decision_date",
                    "court",
                    "subject_tags",
                    "source_url",
                    "pdf_url",
                    "summary",
                    "content_text",
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
                    "content_text",
                ],
                "primary_keys": ["id"],
            },
        ]
    }


def _sglawwatch_db_payload() -> dict:
    return {
        "tables": [
            {
                "name": "about_singapore_law",
                "hidden": False,
                "count": 50,
                "columns": [
                    "id",
                    "item_url",
                    "title",
                    "section",
                    "home_page",
                    "last_scraped",
                    "content_length",
                ],
                "primary_keys": ["id"],
            },
            {
                "name": "about_singapore_law_fragments",
                "hidden": False,
                "count": 500,
                "columns": [
                    "id",
                    "item_id",
                    "fragment_order",
                    "content_text",
                    "char_count",
                ],
                "primary_keys": ["id"],
            },
        ]
    }


def _pdpc_db_payload() -> dict:
    return {
        "tables": [
            {
                "name": "enforcement_decisions",
                "hidden": False,
                "count": 100,
                "columns": [
                    "id",
                    "title",
                    "organisation",
                    "decision_type",
                    "decision_date",
                    "decision_url",
                    "penalty_amount",
                    "summary",
                    "pdf_url",
                    "imported_on",
                ],
                "primary_keys": ["id"],
            },
            {
                "name": "enforcement_decisions_fragments",
                "hidden": False,
                "count": 1000,
                "columns": [
                    "id",
                    "parent_id",
                    "text",
                    "sequence",
                    "content_type",
                    "char_count",
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
# D5-01 — single auditable code path
# ---------------------------------------------------------------------------


async def test_three_pairs_route_through_same_helper(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-01: every fragment-table pair invokes fragment_join.compile_filter
    via the SAME delegation site in tools/retrieval.py — counter-patch
    increments a shared counter on each call and asserts n=3 after invoking
    query_table once per pair."""
    pairs = [
        (
            "zeeker-judgements",
            "judgments_fragments",
            "judgments",
            "source_url",
            "https://www.elitigation.sg/gd/s/2026_SGFC_46",
            "zeeker_judgements__judgments__parent_lookup.json",
            "zeeker_judgements__judgments_fragments__page1.json",
            _judgments_db_payload(),
        ),
        (
            "sglawwatch",
            "about_singapore_law_fragments",
            "about_singapore_law",
            "item_url",
            "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01-the-singapore-legal-system",
            "sglawwatch__about_singapore_law__parent_lookup.json",
            "sglawwatch__about_singapore_law_fragments__page1.json",
            _sglawwatch_db_payload(),
        ),
        (
            "pdpc",
            "enforcement_decisions_fragments",
            "enforcement_decisions",
            "decision_url",
            "https://www.pdpc.gov.sg/all-commissions-decisions/2025/12/sesami-singapore-pte-ltd-and-abecha-pte-ltd",
            "pdpc__enforcement_decisions__parent_lookup.json",
            "pdpc__enforcement_decisions_fragments__page1.json",
            _pdpc_db_payload(),
        ),
    ]

    # Register all stubs up-front so each query_table call finds its pair.
    for (
        db,
        frag,
        parent,
        _col,
        _url,
        parent_fixture,
        frag_fixture,
        db_payload,
    ) in pairs:
        httpx_mock.add_response(url=_db_url(db), json=db_payload, is_reusable=True)
        httpx_mock.add_response(
            url=_zeeker_schemas_url(db),
            json=_empty_schema_payload(),
            is_reusable=True,
        )
        httpx_mock.add_response(
            url=_table_url_re(db, parent),
            json=_load_fragments_fixture(parent_fixture),
        )
        httpx_mock.add_response(
            url=_table_url_re(db, frag),
            json=_load_fragments_fixture(frag_fixture),
        )

    counter = {"n": 0}
    original_compile = fragment_join.compile_filter

    async def counting_compile(*args, **kwargs):
        counter["n"] += 1
        return await original_compile(*args, **kwargs)

    # Patch the binding the handler resolved at import time
    # (mcp_zeeker.tools.retrieval.fragment_join.compile_filter) — patching
    # the source module binding would NOT swap the handler's reference.
    with patch(
        "mcp_zeeker.tools.retrieval.fragment_join.compile_filter",
        counting_compile,
    ):
        for db, frag, _parent, col, url, _pf, _ff, _dbp in pairs:
            await query_table(
                database=db,
                table=frag,
                filters=[Filter(column=col, op="exact", value=url)],
            )

    assert counter["n"] == 3, (
        f"all 3 fragment-table pairs must route through fragment_join.compile_filter "
        f"via the same delegation site; counted {counter['n']}"
    )
