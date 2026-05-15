"""
TEST-02 owner: Phase 8, Plan 08-04 — live golden-path tests against data.zeeker.sg.

All 6 tests are gated by ``@pytest.mark.live`` and the ``ZEEKER_LIVE=1`` env var.
The existing ``pytest_collection_modifyitems`` hook at ``tests/conftest.py:76-83``
auto-skips them unless ``ZEEKER_LIVE=1`` is set.

Run live tests sequentially via ``-p no:xdist`` (08-RESEARCH.md Pitfall 3 — parallel
execution shares the upstream anonymous-tier rate-limit budget of 60 req/min; running
concurrently would self-DoS).

Live-test invariant (08-PATTERNS.md / RESEARCH.md V12 line 1280): assert on SHAPE,
not content.  Literal license strings and citations drift within 48h of any
operator-side metadata edit (Phase 6.1 D6.1-04 trap).  Shape-only assertions are stable
over the 8-week test-stability horizon.

Assumed CI context: the GitHub Actions runner IP is either allowlisted or below the
anonymous-tier 60 req/min budget.  Six sequential calls per nightly run is trivial.

Canonical TEST-02 command::

    ZEEKER_LIVE=1 uv run pytest -m live -p no:xdist tests/test_live_golden_path.py -x
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.fragment_join import ParentPKCache
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.core.middleware.retrieved_at import tool_started_at
from mcp_zeeker.tools.discovery import describe_table, list_databases, list_tables
from mcp_zeeker.tools.retrieval import fetch, query_table
from mcp_zeeker.tools.search import search

# Known-stable URL from tests/manual/PHASE3-CLIENT-VERIFY.md — a published Singapore
# District Court judgment present since Phase 3 UAT.  If this URL 404s upstream, the
# test fails loudly with ``not_found`` and the operator updates the URL (T-FETCH-URL-DRIFT
# is an accepted low-frequency maintenance task per the 08-04 threat model).
_STABLE_FETCH_URL = "https://www.elitigation.sg/gd/s/2026_SGDC_136"
_FETCH_DATABASE = "zeeker-judgements"
_FETCH_TABLE = "judgments"


@pytest.fixture
async def bound_live_clients():
    """Bind DatasetteClient + MetadataCache + ParentPKCache + tool_started_at.

    LIFO reset in ``finally`` mirrors tests/test_heavy_column_upstream.py:62-85.
    The fixture is defined INLINE in this file (NOT in conftest.py) per the
    single-plan-touch discipline (08-RESEARCH.md "Wave 0 Requirements" line 88).
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc_token = DatasetteClient.bind(DatasetteClient(http))
        mc_token = MetadataCache.bind(
            MetadataCache(http, config.UPSTREAM_URL, ttl=config.METADATA_TTL_SECONDS)
        )
        pk_token = ParentPKCache.bind(ParentPKCache())
        rt_token = tool_started_at.set(datetime(2026, 1, 1, tzinfo=UTC))
        try:
            yield
        finally:
            # LIFO reset order — reverse of bind order.
            tool_started_at.reset(rt_token)
            ParentPKCache.reset(pk_token)
            MetadataCache.reset(mc_token)
            DatasetteClient.reset(dc_token)
            MetadataCache.clear_singleton()
            DatasetteClient.clear_singleton()
            ParentPKCache.clear_singleton()


@pytest.mark.live
async def test_live_list_databases(bound_live_clients) -> None:
    """TEST-02: live list_databases returns all 4 configured DBs from data.zeeker.sg.

    Shape invariant: provenance.source + non-empty data list.
    Content invariant: exactly 4 databases (catches a silent fifth DB appearing upstream).
    """
    envelope = await list_databases()

    assert envelope.provenance.source == "data.zeeker.sg"
    assert len(envelope.data) >= 1
    # The four configured databases are stable since Phase 1 — assert count to
    # catch "fifth DB silently appearing upstream" without asserting on names.
    assert len(envelope.data) == 4  # noqa: PLR2004


@pytest.mark.live
async def test_live_list_tables(bound_live_clients) -> None:
    """TEST-02: live list_tables("pdpc") returns tables including enforcement_decisions.

    "pdpc" is the simplest database — single canonical table ``enforcement_decisions``.
    Shape invariant: provenance.source + enforcement_decisions in the returned names.
    """
    envelope = await list_tables("pdpc")

    assert envelope.provenance.source == "data.zeeker.sg"
    assert len(envelope.data) >= 1
    names = {row["name"] for row in envelope.data}
    assert "enforcement_decisions" in names


@pytest.mark.live
async def test_live_describe_table(bound_live_clients) -> None:
    """TEST-02: live describe_table("pdpc", "enforcement_decisions") returns columns.

    Shape invariant: provenance.source + "columns" key in envelope.data with >= 1 column.
    NEVER assert a specific column count — the schema may add columns over time.
    """
    envelope = await describe_table("pdpc", "enforcement_decisions")

    assert envelope.provenance.source == "data.zeeker.sg"
    assert "columns" in envelope.data
    assert len(envelope.data["columns"]) >= 1


@pytest.mark.live
async def test_live_search(bound_live_clients) -> None:
    """TEST-02: live search("data protection", limit=5) returns a well-formed envelope.

    Shape invariant: provenance.source + isinstance(data, list).
    Content: search MAY return zero hits for an unusual query — the shape invariant
    is that the envelope is well-formed, not that hits are present (08-PATTERNS.md
    "Live-test invariant").
    """
    envelope = await search(query="data protection", limit=5)

    assert envelope.provenance.source == "data.zeeker.sg"
    assert isinstance(envelope.data, list)


@pytest.mark.live
async def test_live_query_table(bound_live_clients) -> None:
    """TEST-02: live query_table("pdpc", "enforcement_decisions", limit=1) returns >= 1 row.

    Shape invariant: provenance.source + at least one row returned.
    """
    envelope = await query_table(database="pdpc", table="enforcement_decisions", limit=1)

    assert envelope.provenance.source == "data.zeeker.sg"
    assert len(envelope.data) >= 1


@pytest.mark.live
async def test_live_fetch(bound_live_clients) -> None:
    """TEST-02: live fetch(zeeker-judgements, judgments, <stable-url>) returns a non-None result.

    Uses the known-stable URL ``_STABLE_FETCH_URL`` from tests/manual/PHASE3-CLIENT-VERIFY.md
    (a published Singapore District Court judgment present since Phase 3 UAT).

    Shape invariant: provenance.source + envelope.data is not None.
    Threat: T-FETCH-URL-DRIFT (accepted) — if the URL ever 404s upstream, the test fails
    loudly; the operator updates ``_STABLE_FETCH_URL`` and re-runs.
    """
    envelope = await fetch(
        database=_FETCH_DATABASE,
        table=_FETCH_TABLE,
        url=_STABLE_FETCH_URL,
    )

    assert envelope.provenance.source == "data.zeeker.sg"
    assert envelope.data is not None
