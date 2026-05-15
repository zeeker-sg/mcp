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
        new_row["id"] = f"1021426d3e2a_{ordinal:04d}"
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
# Plan 05-03 — synthetic 1500-fragment walk helper (FRAG-04)
# ---------------------------------------------------------------------------


def _synth_page(
    page_num: int,
    *,
    total_pages: int = 15,
    page_size: int = 100,
    parent_id: str = "synth_parent_15hundred",
) -> dict:
    """Build one synthetic 100-row fragments page for the 1500-frag walk.

    page_num is 0-indexed. Each row carries:
      ordinal = page_num * page_size + i for i in range(page_size)
      id      = f"{parent_id}_{ordinal:04d}"  (matches production <parent_pk>_<suffix>
                pattern so the CR-01 cursor-encoding prefix-strip works correctly)

    The terminal page (page_num == total_pages - 1) has `next = None`;
    intermediate pages have `next = f"{last_ord},{last_id}"` per the Datasette
    `_next` token wire format (RESEARCH §4.3). `truncated` stays False on
    every page; `filtered_table_rows_count` is None per the `_nocount=1`
    response shape (RESEARCH §4.4). Column order mirrors the captured
    `large_page1.json` fixture exactly so the envelope-build path sees the
    same shape as production.

    The synthetic payload includes `id` and `judgment_id` at the top level
    (matching real Datasette responses); the handler's HIDDEN_COLUMNS strips
    both before they reach the envelope (FRAG-02).
    """
    start_ord = page_num * page_size
    rows = [
        {
            "id": f"{parent_id}_{start_ord + i:04d}",
            "judgment_id": parent_id,
            "ordinal": start_ord + i,
            "paragraph_number": start_ord + i,
            "class_name": "Synth-Para",
            "section_heading": None,
            "content_text": f"Synthetic fragment {start_ord + i} of the 1500-row regression.",
            "html_raw": None,
            "footnote_text": None,
            "has_footnotes": 0,
            "has_table": 0,
            "has_figure": 0,
            "figure_src": None,
            "figure_descriptions": None,
        }
        for i in range(page_size)
    ]
    last = rows[-1]
    has_next = page_num < total_pages - 1
    return {
        "rows": rows,
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
        "next": f"{last['ordinal']},{last['id']}" if has_next else None,
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
# CR-01 regression — keyset cursor must never carry parent_pk substring
# ---------------------------------------------------------------------------


async def test_cursor_never_contains_parent_pk_substring(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """CR-01 end-to-end regression — handler-level proof that the next_cursor
    returned to the LLM never contains the parent_pk as a base64-decodable
    substring. Production fragment IDs follow `<parent_pk>_<suffix>` so the
    raw `last_id` field of the upstream `next` token would leak parent_pk
    unless the handler strips the prefix before encoding (CR-01 fix in
    tools/retrieval.py)."""
    import base64

    page1 = _load_fragments_fixture("zeeker_judgements__judgments_fragments__page1.json")

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
    # Force a non-None upstream next so encode_keyset_cursor fires.
    page1_with_next = copy.deepcopy(page1)
    page1_with_next["next"] = "9,1021426d3e2a_0009"
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=page1_with_next,
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

    assert envelope.pagination is not None
    assert envelope.pagination.next_cursor is not None
    cursor = envelope.pagination.next_cursor
    parent_pk = "1021426d3e2a"  # from the parent_lookup fixture rows[0]["id"]

    # Base64 is trivially reversible — verify parent_pk is NOT in the decoded
    # payload (FRAG-02 / D5-06: cursor tokens that decode to internal IDs
    # are LLM-visible leaks).
    padded = cursor + "=" * (-len(cursor) % 4)
    decoded = base64.urlsafe_b64decode(padded).decode()
    assert parent_pk not in decoded, (
        f"FRAG-02 violation — parent_pk {parent_pk!r} substring found in "
        f"base64-decoded next_cursor payload {decoded!r}"
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
# FRAG-04 — 1500-fragment synthetic walk
# ---------------------------------------------------------------------------


async def test_1500_fragment_walk_synthetic(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """FRAG-04 / TEST-04: 15 synthetic page responses walked end-to-end via the keyset
    cursor — zero row loss, `truncated=False` on every page, terminal
    `next_cursor=None` on page 15. Verifies the qhash stays stable across all
    15 pages of identical-shape requests (D5-06) and that the keyset cursor
    encode/decode round-trip is durable beyond Datasette's 1000-row cap.

    TEST-04 owner: Phase 8 (regression test originated in Phase 5 D5-06).
    """
    synth_url = "https://synth.example.gov.sg/case/15hundred"
    synth_parent_id = "synth_parent_15hundred"

    # Build a synthetic parent_lookup by mutating the captured fixture.
    parent_lookup = copy.deepcopy(
        _load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json")
    )
    parent_lookup["rows"][0]["id"] = synth_parent_id
    parent_lookup["rows"][0]["source_url"] = synth_url
    parent_lookup["filtered_table_rows_count"] = 1

    # DB metadata + schema stubs — re-usable across the walk.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )
    # Parent lookup — fires on every walk step because bound_parent_pk_cache
    # uses ttl=0 (Plan 05-01 default). Mark reusable so 15 walk steps share
    # one stub.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=parent_lookup,
        is_reusable=True,
    )

    # Stub 15 synthetic fragment pages in walk order. Ordered consumption —
    # do NOT use `is_reusable=True` (02-LEARNINGS).
    for page_num in range(15):
        httpx_mock.add_response(
            url=_table_url_re("zeeker-judgements", "judgments_fragments"),
            json=_synth_page(page_num),
        )

    # Walk via repeated query_table calls; accumulate rows; safety bound to
    # fail fast on infinite-loop regression.
    all_rows: list[dict] = []
    cursor: str | None = None
    pages_walked = 0
    max_pages = 20  # safety bound
    while pages_walked < max_pages:
        envelope = await query_table(
            database="zeeker-judgements",
            table="judgments_fragments",
            filters=[Filter(column="source_url", op="exact", value=synth_url)],
            cursor=cursor,
            limit=100,
        )
        pages_walked += 1
        all_rows.extend(envelope.data)
        assert envelope.pagination is not None
        assert envelope.pagination.truncated is False, (
            f"page {pages_walked} reports truncated=True (synthetic data is honest=False)"
        )
        cursor = envelope.pagination.next_cursor
        if cursor is None:
            break

    # Post-walk assertions — FRAG-04 contract.
    assert pages_walked == 15, f"expected 15 pages, walked {pages_walked}"
    assert cursor is None, "terminal page (15) must produce next_cursor=None"
    assert len(all_rows) == 1500, f"expected 1500 rows total, got {len(all_rows)}"

    # Ordinals strictly monotonically equal range(1500) — every fragment
    # accounted for, no row loss to Datasette's 1000-row cap (T-05-20).
    ordinals = [row["ordinal"] for row in all_rows if "ordinal" in row]
    assert ordinals == list(range(1500)), (
        f"ordinals must be exactly [0..1499] sorted; "
        f"first={ordinals[:3]!r}, last={ordinals[-3:]!r}, len={len(ordinals)}"
    )

    # FRAG-02 carry-forward — id / judgment_id MUST NOT appear at row top
    # level (HIDDEN_COLUMNS strips them).
    forbidden = {"id", "judgment_id"}
    for row in all_rows:
        leaked = set(row.keys()) & forbidden
        assert not leaked, f"forbidden keys leaked into row: {leaked}"


# ---------------------------------------------------------------------------
# FRAG-04 — pagination.truncated honesty (T-05-21)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("truncated_value", [True, False])
async def test_pagination_truncated_honesty(
    bound_parent_pk_cache,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
    truncated_value: bool,
) -> None:
    """T-05-21: `envelope.pagination.truncated` mirrors the upstream
    `result["truncated"]` verbatim — no silent override. Tested across both
    True and False upstream values. Confirms Plan 05-02 Task 2 did not alter
    Phase 3's truncation pass-through despite the new `_nocount=1` injection.
    """
    parent_lookup = _load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json")
    # Build a synthetic single-page payload (small — 10 rows) and force the
    # `truncated` field to the parametrized value.
    page = _synth_page(0, total_pages=1, page_size=10)
    page["truncated"] = truncated_value
    # Single-page response → next must be None (synthesizing a 1-page walk).
    page["next"] = None

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
        json=parent_lookup,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=page,
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

    assert envelope.pagination is not None
    assert envelope.pagination.truncated is truncated_value, (
        f"envelope.pagination.truncated must mirror upstream value "
        f"{truncated_value!r}; got {envelope.pagination.truncated!r}"
    )


# ---------------------------------------------------------------------------
# D5-04 / T-05-22 — ParentPKCache positive-hit suppresses Call 1
# ---------------------------------------------------------------------------


@pytest.fixture
async def bound_parent_pk_cache_ttl60():
    """Override of Plan 05-01's `bound_parent_pk_cache` (ttl=0) with a
    60-second TTL so the cache positive-hit actually persists between two
    sequential query_table calls in the same test."""
    from mcp_zeeker.core.fragment_join import ParentPKCache

    cache = ParentPKCache(ttl=60)
    token = ParentPKCache.bind(cache)
    yield cache
    ParentPKCache.reset(token)
    ParentPKCache.clear_singleton()


async def test_parent_pk_cache_hit_skips_call_1(
    bound_parent_pk_cache_ttl60,
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """T-05-22 / D5-04: a SECOND query_table call with the same URL within
    the ParentPKCache TTL fires only Call 2 (no Call 1). Confirms the cache
    actually stores positive entries (a bug where the lock acquires but
    `_data` is never written would trip this assertion)."""
    parent_lookup = _load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json")
    fragments_page = _load_fragments_fixture("zeeker_judgements__judgments_fragments__page1.json")
    probe_url = "https://www.elitigation.sg/gd/s/2026_SGFC_46"

    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_empty_schema_payload(),
        is_reusable=True,
    )
    # Call 1 — register ONCE (not reusable). If the cache fails to store the
    # positive entry the second call will fire a second parent lookup and
    # pytest_httpx will raise (no matching response).
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=parent_lookup,
    )
    # Call 2 — registered TWICE, ordered. Both calls hit the fragments table.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=fragments_page,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments_fragments"),
        json=fragments_page,
    )

    # First call — fires Call 1 + Call 2.
    env1 = await query_table(
        database="zeeker-judgements",
        table="judgments_fragments",
        filters=[Filter(column="source_url", op="exact", value=probe_url)],
    )
    assert len(env1.data) >= 1

    # Second call — should HIT the ParentPKCache; fires Call 2 only.
    env2 = await query_table(
        database="zeeker-judgements",
        table="judgments_fragments",
        filters=[Filter(column="source_url", op="exact", value=probe_url)],
    )
    assert len(env2.data) >= 1

    # Count actual upstream requests against the two table endpoints.
    parent_table_calls = 0
    fragment_table_calls = 0
    for req in httpx_mock.get_requests():
        url_str = str(req.url)
        if "/zeeker-judgements/judgments.json" in url_str:
            parent_table_calls += 1
        elif "/zeeker-judgements/judgments_fragments.json" in url_str:
            fragment_table_calls += 1

    assert parent_table_calls == 1, (
        f"expected exactly ONE parent lookup (Call 1) across two query_table "
        f"calls — ParentPKCache positive-hit should suppress the second; "
        f"got {parent_table_calls} parent lookups"
    )
    assert fragment_table_calls == 2, (
        f"expected exactly TWO fragments calls (Call 2 fires per query_table "
        f"call); got {fragment_table_calls}"
    )
