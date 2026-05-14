"""Phase 5 — pure unit tests for `core/fragment_join.py`.

Covers `normalize_url` (8 input/expected pairs per 05-RESEARCH §4.8) and the
`compile_filter` body — Plan 05-02 ships the trigger-detect + Call 1 + filter
rewrite + ParentPKCache short-circuit logic.

The Plan 05-01 skeleton-sentinel test was deleted by Plan 05-02 (the skeleton
no longer raises NotImplementedError; the body-fill is GREEN).
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.filter_compiler import Filter
from mcp_zeeker.core.fragment_join import compile_filter, normalize_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Scheme + netloc lowercased; path preserved
        ("https://Example.Gov.SG/Decision", "https://example.gov.sg/Decision"),
        # Trailing slash stripped (non-root path)
        ("https://example.gov.sg/decision/", "https://example.gov.sg/decision"),
        # http → https scheme upgrade
        ("http://example.gov.sg/decision", "https://example.gov.sg/decision"),
        # Root path stays
        ("https://example.gov.sg/", "https://example.gov.sg/"),
        # Whitespace stripped
        ("  https://example.gov.sg/x  ", "https://example.gov.sg/x"),
        # Empty stays empty
        ("", ""),
        # Query string preserved
        ("https://example.gov.sg/page?q=1&v=2", "https://example.gov.sg/page?q=1&v=2"),
        # Fragment preserved
        ("https://example.gov.sg/page#frag", "https://example.gov.sg/page#frag"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


# ---------------------------------------------------------------------------
# compile_filter — happy paths + fall-through + negative cache
# ---------------------------------------------------------------------------


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Local DatasetteClient bound to the current context (mirror Phase 3
    fixture in tests/tools/test_query_table.py)."""
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _parent_table_url_re(database: str, parent_table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(parent_table)}\.json(\?.*)?$")


async def test_compile_filter_triggers_join_on_eq_parent_url(
    bound_parent_pk_cache,
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-02: a single exact filter on the parent's URL column triggers the
    join. compile_filter fires Call 1, extracts parent_pk, and substitutes
    a Filter(column=parent_fk, op="exact", value=parent_pk) into the list."""
    from tests.conftest import _load_fragments_fixture

    httpx_mock.add_response(
        url=_parent_table_url_re("zeeker-judgements", "judgments"),
        json=_load_fragments_fixture("zeeker_judgements__judgments__parent_lookup.json"),
    )

    rewritten, warning = await compile_filter(
        "zeeker-judgements",
        "judgments_fragments",
        [
            Filter(
                column="source_url",
                op="exact",
                value="https://www.elitigation.sg/gd/s/2026_SGFC_46",
            )
        ],
    )

    # Single-match path → no warning state surfaced.
    assert warning is None
    # Rewrite emits a single internal filter on parent_fk.
    judgment_id_filters = [f for f in rewritten if f.column == "judgment_id"]
    assert len(judgment_id_filters) == 1
    assert judgment_id_filters[0].op == "exact"
    # The captured fixture's parent_pk is `1021426d3e2a` (rows[0]["id"]).
    assert judgment_id_filters[0].value == "1021426d3e2a"
    # The user's exact-on-source_url filter must NOT remain in the rewritten
    # list — fragment_join.compile_filter replaces it with the parent_fk
    # filter (no double-filtering of the same conceptual constraint).
    assert all(not (f.column == "source_url" and f.op == "exact") for f in rewritten)


async def test_compile_filter_fall_through_no_eq_url_filter(
    bound_parent_pk_cache,
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-03 fall-through: when filters do not include exactly-one-exact on
    the parent URL column, compile_filter returns the input verbatim and
    fires ZERO Call 1 lookups."""
    input_filters = [Filter(column="ordinal", op="gt", value=5)]

    rewritten, warning = await compile_filter(
        "zeeker-judgements", "judgments_fragments", input_filters
    )

    assert rewritten == input_filters
    assert warning is None
    # No upstream call — fall-through path must never touch httpx.
    assert len(httpx_mock.get_requests()) == 0


async def test_compile_filter_fall_through_non_fragment_table(
    bound_parent_pk_cache,
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-03 fall-through: non-fragment tables short-circuit immediately
    even when the filter set contains an exact-on-URL filter — the table
    is not in FRAGMENT_PARENTS, so no rewrite happens."""
    input_filters = [Filter(column="source_url", op="exact", value="https://x/y")]

    rewritten, warning = await compile_filter(
        "zeeker-judgements",
        "judgments",  # the PARENT table, not the fragment table
        input_filters,
    )

    assert rewritten == input_filters
    assert warning is None
    assert len(httpx_mock.get_requests()) == 0


async def test_compile_filter_negative_cache_short_circuits(
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """D5-04 negative caching: Call 1 returning zero rows caches the negative
    result; the second compile_filter call with the same URL re-uses the
    cache without re-hitting upstream.

    Binds its own ParentPKCache with ttl=60 (vs the conftest fixture's ttl=0
    which would expire entries on every read and defeat the negative cache).
    """
    from mcp_zeeker.core.fragment_join import ParentPKCache

    local_cache = ParentPKCache(ttl=60)
    local_token = ParentPKCache.bind(local_cache)

    try:
        httpx_mock.add_response(
            url=_parent_table_url_re("zeeker-judgements", "judgments"),
            json={
                "rows": [],
                "columns": ["id"],
                "next": None,
                "truncated": False,
                "filtered_table_rows_count": 0,
            },
        )

        filters = [
            Filter(
                column="source_url",
                op="exact",
                value="https://www.elitigation.sg/no/such/url",
            )
        ]

        # First call → fires Call 1, caches negative, returns ([], None).
        rewritten1, warning1 = await compile_filter(
            "zeeker-judgements", "judgments_fragments", filters
        )
        assert rewritten1 == []
        assert warning1 is None

        # Second call → cache hit on negative, no second upstream request.
        rewritten2, warning2 = await compile_filter(
            "zeeker-judgements", "judgments_fragments", filters
        )
        assert rewritten2 == []
        assert warning2 is None

        # Exactly one upstream call across both invocations.
        assert len(httpx_mock.get_requests()) == 1
    finally:
        ParentPKCache.reset(local_token)
        ParentPKCache.clear_singleton()
