"""Phase 5 — handler-level integration tests for the fragment-join path.

Wave 0 stub: every test is RED (pytest.skip) until Plan 05-02 ships
`fragment_join.compile_filter` body + handler delegation, and Plan 05-03
ships the synthetic 1,500-fragment regression + multi-match assertions.

Captured fixtures (10 files at `tests/fixtures/datasette/fragments/`) are
reachable via `_load_fragments_fixture(...)` from conftest. The 957-fragment
real-data walk (large_page1.json + large_page10.json) is the proxy for
FRAG-05 / FRAG-04; the 1,500-fragment synthetic walk is generated in-test
via the `_synth_page` helper Plan 05-03 will introduce.
"""

import pytest


@pytest.mark.parametrize(
    "database,fragment_table,parent_table",
    [
        ("zeeker-judgements", "judgments_fragments", "judgments"),
        ("sglawwatch", "about_singapore_law_fragments", "about_singapore_law"),
        ("pdpc", "enforcement_decisions_fragments", "enforcement_decisions"),
    ],
)
@pytest.mark.asyncio
async def test_three_pairs_happy_path(
    database: str, fragment_table: str, parent_table: str
) -> None:
    """Plan 05-02 body-fill: stub Call 1 + Call 2 via stub_fragment_join_two_step
    for the 3 fragment-table pairs; call `query_table(database, fragment_table,
    filters=[Filter(<parent_url_col>, eq, <real_url>)])`; assert the envelope
    contains ordered fragments and the parent_pk never appears in any row key
    or value."""
    pytest.skip(
        "RED until Plan 05-02 ships compile_filter body + handler delegation — FRAG-01 / D5-01"
    )


@pytest.mark.asyncio
async def test_no_internal_ids_in_response() -> None:
    """Plan 05-02 + 05-03 body-fill: iterate envelope rows; assert
    `set(row.keys()) & {"id","judgment_id","item_id","parent_id"} == set()`
    for every row; same constraint on `retrieved_content` if present."""
    pytest.skip(
        "RED until Plan 05-02 ships handler + Plan 05-03 ships snapshot assertion — FRAG-02"
    )


@pytest.mark.asyncio
async def test_957_fragment_walk() -> None:
    """Plan 05-02 body-fill: replay large_page1.json + large_page10.json (plus
    8 intermediate-page stubs derived from large_page1.json shape via the
    `_synth_intermediate_page` helper); walk via next_cursor; assert ordinals
    strictly increasing through [0..956]; assert truncated=False on every page;
    assert terminal `next_cursor is None` on page 10."""
    pytest.skip("RED until Plan 05-02 ships keyset cursor swap on join path — FRAG-05")


@pytest.mark.asyncio
async def test_1500_fragment_walk_synthetic() -> None:
    """Plan 05-03 body-fill via the `_synth_page(page_num, has_next)` helper.
    Each page contains 100 synthetic rows with ordinal 0..1499 and id
    "synth_judg_NNNN"; `next` is `"<last_ord>,<last_id>"` when has_next else
    None; `truncated: false`; `filtered_table_rows_count: null` per the
    `_nocount=1` response shape per 05-RESEARCH §4.4."""
    pytest.skip("RED until Plan 05-03 ships 1500-frag synthetic regression — FRAG-04")
