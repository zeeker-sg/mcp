"""Phase 5 — handler-level integration tests for the fragment-join path.

Plan 05-02 flips 7 of 9 RED stubs to GREEN: 3-pair happy path (FRAG-01),
FRAG-02 snapshot, 957-fragment walk (FRAG-05). The 1500-fragment synthetic
regression (FRAG-04) stays RED until Plan 05-03 ships the synth helper.

The 957-fragment walk replays the two captured fixture pages (large_page1 +
large_page10) plus 8 synthetic intermediate pages assembled at test time
from the page-1 row template.
"""

from __future__ import annotations

import copy
import re

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.filter_compiler import Filter
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.retrieval import query_table
from tests.conftest import _load_fragments_fixture

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


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
    """zeeker-judgements/.json — both judgments and judgments_fragments tables."""
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
                    "html_raw",
                    "footnote_text",
                    "has_footnotes",
                    "has_table",
                    "has_figure",
                    "figure_src",
                    "figure_descriptions",
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
# 957-fragment walk synthetic helpers
# ---------------------------------------------------------------------------


def _synth_intermediate_page(page_num: int, template_row: dict) -> dict:
    """Build a 100-row response shape for page `page_num` (1-indexed, page>=2,<10).

    Each row mutates ordinal and id from the template; the upstream `next`
    points to the (last_ordinal, last_id) tuple per Datasette _next wire
    format. truncated stays False; filtered_table_rows_count is null per the
    _nocount=1 response shape (RESEARCH §4.4).
    """
    rows: list[dict] = []
    for i in range(100):
        ordinal = (page_num - 1) * 100 + i
        new_row = copy.deepcopy(template_row)
        new_row["ordinal"] = ordinal
        new_row["id"] = f"66e73dfa5db4_{ordinal:04d}"
        rows.append(new_row)
    last = rows[-1]
    return {
        "rows": rows,
        "columns": list(template_row.keys()),
        "next": f"{last['ordinal']},{last['id']}",
        "truncated": False,
        "filtered_table_rows_count": None,
    }


# ---------------------------------------------------------------------------
# FRAG-01 — three-pair happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "database,fragment_table,parent_table,parent_url_col,user_url,parent_lookup_fixture,fragments_fixture,order_by_col,db_payload",
    [
        (
            "zeeker-judgements",
            "judgments_fragments",
            "judgments",
            "source_url",
            "https://www.elitigation.sg/gd/s/2026_SGFC_46",
            "zeeker_judgements__judgments__parent_lookup.json",
            "zeeker_judgements__judgments_fragments__page1.json",
            "ordinal",
            "judgments",
        ),
        (
            "sglawwatch",
            "about_singapore_law_fragments",
            "about_singapore_law",
            "item_url",
            "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01-the-singapore-legal-system",
            "sglawwatch__about_singapore_law__parent_lookup.json",
            "sglawwatch__about_singapore_law_fragments__page1.json",
            "fragment_order",
            "sglawwatch",
        ),
        (
            "pdpc",
            "enforcement_decisions_fragments",
            "enforcement_decisions",
            "decision_url",
            "https://www.pdpc.gov.sg/all-commissions-decisions/2025/12/sesami-singapore-pte-ltd-and-abecha-pte-ltd",
            "pdpc__enforcement_decisions__parent_lookup.json",
            "pdpc__enforcement_decisions_fragments__page1.json",
            "sequence",
            "pdpc",
        ),
    ],
)
async def test_three_pairs_happy_path(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
    database: str,
    fragment_table: str,
    parent_table: str,
    parent_url_col: str,
    user_url: str,
    parent_lookup_fixture: str,
    fragments_fixture: str,
    order_by_col: str,
    db_payload: str,
) -> None:
    """FRAG-01: all 3 fragment-table pairs route through the same
    URL→parent_pk→fragment_fk join and return ordered fragments."""
    db_payload_map = {
        "judgments": _judgments_db_payload(),
        "sglawwatch": _sglawwatch_db_payload(),
        "pdpc": _pdpc_db_payload(),
    }
    httpx_mock.add_response(
        url=_db_url(database), json=db_payload_map[db_payload], is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url(database), json=_empty_schema_payload(), is_reusable=True
    )
    # Parent lookup (Call 1)
    httpx_mock.add_response(
        url=_table_url_re(database, parent_table),
        json=_load_fragments_fixture(parent_lookup_fixture),
    )
    # Fragments (Call 2)
    httpx_mock.add_response(
        url=_table_url_re(database, fragment_table),
        json=_load_fragments_fixture(fragments_fixture),
    )

    envelope = await query_table(
        database=database,
        table=fragment_table,
        filters=[Filter(column=parent_url_col, op="exact", value=user_url)],
    )

    assert len(envelope.data) >= 1, "expected at least one fragment row"

    # Order_by column is part of the fragment table's light set and surfaces
    # at the row top level — assert monotonically non-decreasing order.
    order_values = [row[order_by_col] for row in envelope.data if order_by_col in row]
    assert order_values, f"expected order_by column '{order_by_col}' on every row"
    assert order_values == sorted(order_values), "fragments must be ordered ascending"


# ---------------------------------------------------------------------------
# FRAG-02 — no internal ids in response
# ---------------------------------------------------------------------------


async def test_no_internal_ids_in_response(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """FRAG-02: id / judgment_id / item_id / parent_id NEVER appear in any
    envelope row top-level OR under retrieved_content."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"), json=_empty_schema_payload()
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json"),
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=_load_fragments_fixture("zeeker_judgements__judgments_fragments__page1.json"),
    )

    envelope = await query_table(
        database="zeeker-judgements",
        table="judgments_fragments",
        filters=[
            Filter(
                column="source_url",
                op="exact",
                value="https://www.elitigation.sg/gd/s/2026_SGFC_46",
            )
        ],
    )

    forbidden = {"id", "judgment_id", "item_id", "parent_id"}
    for row in envelope.data:
        assert set(row.keys()) & forbidden == set(), (
            f"forbidden keys leaked into row: {set(row.keys()) & forbidden}"
        )
        retrieved = row.get("retrieved_content")
        if retrieved:
            assert set(retrieved.keys()) & forbidden == set(), (
                f"forbidden keys leaked into retrieved_content: {set(retrieved.keys()) & forbidden}"
            )


# ---------------------------------------------------------------------------
# FRAG-05 — 957-fragment walk
# ---------------------------------------------------------------------------


async def test_957_fragment_walk(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """FRAG-05: walk 957 fragments across 10 pages (page1 + page10 captured;
    pages 2-9 synthesized from page-1 row template) via repeated query_table
    calls using the returned next_cursor. Assert total row count, monotonic
    ordinals, truncated=False on every page, terminal next_cursor=None."""
    page1 = _load_fragments_fixture("zeeker_judgements__judgments_fragments__large_page1.json")
    page10 = _load_fragments_fixture("zeeker_judgements__judgments_fragments__large_page10.json")

    # Set up DB metadata + schema stubs (re-usable across the 10 calls).
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )
    # Parent lookup — used on EVERY call because the qhash binds the URL and
    # ParentPKCache rehydrates parent_pk per call. With bound_parent_pk_cache
    # (ttl=0) the cache always misses so Call 1 fires every time. Mark as
    # reusable so the 10 calls share one stub.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json"),
        is_reusable=True,
    )

    # Stub 10 fragment pages in order: page1 (captured), pages 2-9 (synth),
    # page10 (captured). pytest_httpx consumes ordered stubs by registration
    # order; each request to /zeeker-judgements/judgments_fragments.json
    # consumes the next registered response.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=page1,
    )
    template_row: dict = page1["rows"][0]
    for page_num in range(2, 10):
        httpx_mock.add_response(
            url=_table_url_re("zeeker-judgements", "judgments_fragments"),
            json=_synth_intermediate_page(page_num, template_row),
        )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=page10,
    )

    # Walk
    all_ordinals: list[int] = []
    cursor: str | None = None
    page_count = 0
    max_pages = 12  # safety bound
    while page_count < max_pages:
        envelope = await query_table(
            database="zeeker-judgements",
            table="judgments_fragments",
            filters=[
                Filter(
                    column="source_url",
                    op="exact",
                    value="https://www.elitigation.sg/gd/s/2026_SGFC_46",
                )
            ],
            cursor=cursor,
            limit=100,
        )
        page_count += 1
        all_ordinals.extend(row["ordinal"] for row in envelope.data if "ordinal" in row)
        assert envelope.pagination is not None
        assert envelope.pagination.truncated is False, f"page {page_count} reports truncated=True"
        cursor = envelope.pagination.next_cursor
        if cursor is None:
            break

    assert page_count == 10, f"expected 10 pages, walked {page_count}"
    assert cursor is None, "terminal page should produce next_cursor=None"
    assert len(all_ordinals) == 957, (
        f"expected 957 total fragments across 10 pages, got {len(all_ordinals)}"
    )
    assert all_ordinals == sorted(all_ordinals), "ordinals must be monotonic ascending"
    assert all_ordinals[0] == 0 and all_ordinals[-1] == 956


# ---------------------------------------------------------------------------
# FRAG-04 — 1500-fragment synthetic walk (RED stub — Plan 05-03 owns)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_1500_fragment_walk_synthetic() -> None:
    """Plan 05-03 body-fill via the `_synth_page(page_num, has_next)` helper.
    Each page contains 100 synthetic rows with ordinal 0..1499 and id
    "synth_judg_NNNN"; `next` is `"<last_ord>,<last_id>"` when has_next else
    None; `truncated: false`; `filtered_table_rows_count: null` per the
    `_nocount=1` response shape per 05-RESEARCH §4.4."""
    pytest.skip("RED until Plan 05-03 ships 1500-frag synthetic regression — FRAG-04")
