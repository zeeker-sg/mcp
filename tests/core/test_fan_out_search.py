"""
Orchestrator tests for fan_out_search — GREEN (Plan 04-02 Task 1).

Tests `mcp_zeeker.core.search.fan_out_search` post body-fill:
  1. test_round_robin_merge — merge order interleaves rounds across tables (D4-05).
  2. test_exhausted_table_skipped — tables with fewer rows are skipped silently.
  3. test_partial_failure — one table 500s, the other succeeds: failed_tables=1.
  4. test_all_fail_returns_zero_rows — every table 500s: zero rows, full failure count.
  5. test_upstream_total_hits_aggregated — per-table `filtered_table_rows_count`
     surfaced verbatim into the returned dict.

Phase 2 LEARNING (reusable-response teardown trap): the transient-failure paths
below use EXPLICIT ORDERED `httpx_mock.add_response()` calls — never the
reusable-response kwarg. The retry-once-on-502/503 path (D-16) makes reusable
response semantics brittle (status 500 surfaces immediately without retry, but
the discipline applies across the failure suite).

Plan 04-02's `fan_out_search` returns a 4-tuple
`(merged_rows, upstream_total_hits, failed_tables, failure_statuses)`.
"""

from __future__ import annotations

import re

import anyio
import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for /{database}/{table}.json with any query string.

    pytest_httpx 0.36 matches `add_response(url=str)` on the FULL URL
    (including query params); since fan_out_search always issues `_search=...`,
    we match the path regardless of query string.
    """
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _rows_payload(rows: list[dict], filtered_count: int | None = None) -> dict:
    """Build a minimal _shape=objects rows payload with filtered_table_rows_count."""
    return {
        "rows": rows,
        "columns": ["title", "source_url"],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": filtered_count if filtered_count is not None else len(rows),
    }


_PREVIEW_TU: dict[str, str | None] = {
    "title": "title",
    "url": "source_url",
    "date": None,
    "summary": None,
}


async def test_round_robin_merge(datasette_client, httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """D4-05: merge order interleaves rounds across all target tables.

    Stub 2 tables under one DB, each returning 3 rows. Assert merge order
    `[t1_r1, t2_r1, t1_r2, t2_r2, t1_r3, t2_r3]`.
    """
    from mcp_zeeker.core.search import fan_out_search

    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        json=_rows_payload(
            [
                {"title": "A1", "source_url": "https://example.com/a1"},
                {"title": "A2", "source_url": "https://example.com/a2"},
                {"title": "A3", "source_url": "https://example.com/a3"},
            ]
        ),
    )
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t2"),
        json=_rows_payload(
            [
                {"title": "B1", "source_url": "https://example.com/b1"},
                {"title": "B2", "source_url": "https://example.com/b2"},
                {"title": "B3", "source_url": "https://example.com/b3"},
            ]
        ),
    )

    target = [
        ("dbA", "t1", _PREVIEW_TU),
        ("dbA", "t2", _PREVIEW_TU),
    ]
    rows, totals, failed, statuses = await fan_out_search('"x"', target, per_table_limit=6)

    assert [r["title"] for r in rows] == ["A1", "B1", "A2", "B2", "A3", "B3"]
    assert totals == {"dbA.t1": 3, "dbA.t2": 3}
    assert failed == 0
    assert statuses == []


async def test_exhausted_table_skipped(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-05: exhausted tables skipped silently after their last row.

    Table t1 returns 1 row; t2 returns 3 rows. Assert merge order
    `[t1_r1, t2_r1, t2_r2, t2_r3]`.
    """
    from mcp_zeeker.core.search import fan_out_search

    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        json=_rows_payload(
            [
                {"title": "A1", "source_url": "https://example.com/a1"},
            ]
        ),
    )
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t2"),
        json=_rows_payload(
            [
                {"title": "B1", "source_url": "https://example.com/b1"},
                {"title": "B2", "source_url": "https://example.com/b2"},
                {"title": "B3", "source_url": "https://example.com/b3"},
            ]
        ),
    )

    target = [
        ("dbA", "t1", _PREVIEW_TU),
        ("dbA", "t2", _PREVIEW_TU),
    ]
    rows, totals, failed, statuses = await fan_out_search('"x"', target, per_table_limit=4)

    assert [r["title"] for r in rows] == ["A1", "B1", "B2", "B3"]
    assert totals == {"dbA.t1": 1, "dbA.t2": 3}
    assert failed == 0
    assert statuses == []


async def test_partial_failure(datasette_client, httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """D4-07 / D-16: partial failure surfaces as failed_tables count.

    Table 1 returns 500 on BOTH the initial request and the retry (D-16 retries
    once on 502/503 — 500 is not retried, but we cover with explicit ordered
    add_response anyway because httpx_mock requires a registered response for
    every issued request). Table 2 succeeds.
    """
    from mcp_zeeker.core.search import fan_out_search

    # _request_with_retry only retries 502/503; status 500 surfaces immediately.
    # One add_response is sufficient (the matcher consumes it on the first call).
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        status_code=500,
        json={"error": "boom"},
    )
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t2"),
        json=_rows_payload(
            [
                {"title": "B1", "source_url": "https://example.com/b1"},
                {"title": "B2", "source_url": "https://example.com/b2"},
                {"title": "B3", "source_url": "https://example.com/b3"},
            ]
        ),
    )

    target = [
        ("dbA", "t1", _PREVIEW_TU),
        ("dbA", "t2", _PREVIEW_TU),
    ]
    rows, totals, failed, statuses = await fan_out_search('"x"', target, per_table_limit=6)

    assert failed == 1
    assert "dbA.t2" in totals
    assert "dbA.t1" not in totals
    assert [r["title"] for r in rows] == ["B1", "B2", "B3"]
    assert statuses == [500]


async def test_all_fail_returns_zero_rows(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-07: every table 500s → zero rows, full failure count.

    Stub every table with status_code=500 (explicit ordered add_response).
    """
    from mcp_zeeker.core.search import fan_out_search

    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        status_code=500,
        json={"error": "boom"},
    )
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t2"),
        status_code=500,
        json={"error": "boom"},
    )

    target = [
        ("dbA", "t1", _PREVIEW_TU),
        ("dbA", "t2", _PREVIEW_TU),
    ]
    rows, totals, failed, statuses = await fan_out_search('"x"', target, per_table_limit=6)

    assert rows == []
    assert totals == {}
    assert failed == 2
    assert sorted(statuses) == [500, 500]


async def test_upstream_total_hits_aggregated(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """D4-17: per-table `filtered_table_rows_count` surfaced verbatim.

    Stub two tables with `filtered_table_rows_count` = 7 and 42 respectively.
    """
    from mcp_zeeker.core.search import fan_out_search

    httpx_mock.add_response(
        url=_table_url_re("dbA", "t1"),
        json=_rows_payload(
            [{"title": "A1", "source_url": "https://example.com/a1"}],
            filtered_count=7,
        ),
    )
    httpx_mock.add_response(
        url=_table_url_re("dbA", "t2"),
        json=_rows_payload(
            [{"title": "B1", "source_url": "https://example.com/b1"}],
            filtered_count=42,
        ),
    )

    target = [
        ("dbA", "t1", _PREVIEW_TU),
        ("dbA", "t2", _PREVIEW_TU),
    ]
    _rows, totals, _failed, _statuses = await fan_out_search('"x"', target, per_table_limit=6)

    assert totals == {"dbA.t1": 7, "dbA.t2": 42}


# ---------------------------------------------------------------------------
# WR-260829 — budget-cancelled targets must be visible, not silently dropped
# ---------------------------------------------------------------------------


async def test_budget_cancelled_table_counts_as_failed(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target still in flight when the fan-out budget expires is reported.

    Production symptom (2026-08-29): `zeeker-judgements.judgments` — the
    largest and most relevant table — never appeared in `upstream_total_hits`
    and `failed_tables` was 0, so the envelope was indistinguishable from
    "asked, upstream said zero hits". It had simply blown the 0.8s budget and
    been cancelled. A cancelled target must be ABSENT from the totals map AND
    counted in `failed_tables`, with a `None` status so the handler's
    all-400 → invalid_query promotion can never misread a timeout as an FTS5
    syntax error.
    """
    from mcp_zeeker.core.search import fan_out_search

    monkeypatch.setattr(config, "SEARCH_FAN_OUT_TIMEOUT_S", 0.05)

    async def _slow(_request: httpx.Request) -> httpx.Response:
        await anyio.sleep(5)
        return httpx.Response(200, json=_rows_payload([]))

    httpx_mock.add_response(
        url=_table_url_re("dbA", "fast"),
        json=_rows_payload([{"title": "A1", "source_url": "https://example.com/a1"}], 7),
    )
    httpx_mock.add_callback(_slow, url=_table_url_re("dbA", "slow"))

    target = [
        ("dbA", "fast", _PREVIEW_TU),
        ("dbA", "slow", _PREVIEW_TU),
    ]
    rows, totals, failed, statuses = await fan_out_search('"x"', target, per_table_limit=6)

    # The fast table's result still comes back — partial results are the
    # documented behavior (D4-07); only the accounting changed.
    assert [r["title"] for r in rows] == ["A1"]
    assert totals == {"dbA.fast": 7}
    assert failed == 1, "the cancelled table must be counted, not silently dropped"
    assert statuses == [None], "a timeout carries no HTTP status"


async def test_budget_is_configurable(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fan-out budget reads config.SEARCH_FAN_OUT_TIMEOUT_S at dispatch time.

    A response slower than the default 0.8s that shipped originally must now
    complete, so raising the budget for the big corpus is a config change and
    not a code change.
    """
    from mcp_zeeker.core.search import fan_out_search

    monkeypatch.setattr(config, "SEARCH_FAN_OUT_TIMEOUT_S", 3.0)

    async def _slowish(_request: httpx.Request) -> httpx.Response:
        await anyio.sleep(1.0)
        return httpx.Response(
            200, json=_rows_payload([{"title": "A1", "source_url": "https://e/a1"}], 7)
        )

    httpx_mock.add_callback(_slowish, url=_table_url_re("dbA", "t1"))

    rows, totals, failed, statuses = await fan_out_search(
        '"x"', [("dbA", "t1", _PREVIEW_TU)], per_table_limit=6
    )

    assert [r["title"] for r in rows] == ["A1"]
    assert totals == {"dbA.t1": 7}
    assert failed == 0 and statuses == []
