"""
Tests for #6a / #8 — search sub-timing instrumentation.

Asserts:
- A `search_timing` event is emitted on every search call.
- The event carries exactly the SEARCH_TIMING_FIELDS keys (+ structlog meta).
- The three sub-timings (discovery_ms, fan_out_ms, post_filter_ms) are present
  and are non-negative integers.
- The event is emitted even on the empty-target short-circuit path (Step 6).
- No query string is bound in the event (INJ-05).
"""

from __future__ import annotations

import re

import httpx
import pytest
import pytest_httpx
import structlog
from structlog.testing import capture_logs

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)
    DatasetteClient.clear_singleton()


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
                ],
                "primary_keys": ["id"],
                "fts_table": "judgments_fts",
            },
        ]
    }


def _empty_db_payload() -> dict:
    return {"tables": []}


def _stub_dbs(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """Stub only zeeker-judgements (tests scope to databases=['zeeker-judgements'])."""
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"), json=_judgments_db_payload(), is_reusable=True
    )


def _stub_all_dbs(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    """Stub all four ALLOWED_DATABASES (for default-databases search)."""
    for db in config.ALLOWED_DATABASES:
        httpx_mock.add_response(url=_db_url(db), json=_empty_db_payload(), is_reusable=True)


def _stub_table_response(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json={
            "rows": [{"title": "r1", "source_url": "https://r1"}],
            "filtered_table_rows_count": 1,
            "next": None,
            "truncated": False,
            "columns": ["title", "source_url"],
        },
        is_reusable=True,
    )


async def test_search_timing_event_emitted(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """#6a / #8: a search call emits a `search_timing` event with the three sub-timings."""
    from mcp_zeeker.tools.search import search

    _stub_dbs(httpx_mock)
    _stub_table_response(httpx_mock)

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as cap:
        await search(query="appeal", databases=["zeeker-judgements"])

    timing_events = [e for e in cap if e.get("event") == "search_timing"]
    assert len(timing_events) == 1, f"expected 1 search_timing event, got {len(timing_events)}"
    evt = timing_events[0]

    # All three sub-timings present and are non-negative ints.
    for field in ("discovery_ms", "fan_out_ms", "post_filter_ms"):
        assert field in evt, f"missing {field} in search_timing event: {evt!r}"
        assert isinstance(evt[field], int), f"{field} should be int, got {type(evt[field])}"
        assert evt[field] >= 0, f"{field} should be non-negative, got {evt[field]}"

    # tool is bound to "search".
    assert evt.get("tool") == "search"

    # INJ-05: no query string in the event.
    assert "query" not in evt
    assert "search_query" not in evt


async def test_search_timing_event_on_empty_short_circuit(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """#6a / #8: search_timing is emitted even when Step 6 short-circuits (no FTS tables)."""
    from mcp_zeeker.tools.search import search

    # All DBs have no FTS tables → empty target_tables → Step 6 short-circuit.
    _stub_all_dbs(httpx_mock)

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as cap:
        await search(query="appeal")

    timing_events = [e for e in cap if e.get("event") == "search_timing"]
    assert len(timing_events) == 1, "expected search_timing even on short-circuit"
    evt = timing_events[0]
    # Fan-out and post-filter are 0 on short-circuit.
    assert evt["fan_out_ms"] == 0
    assert evt["post_filter_ms"] == 0
    # Discovery still ran (it found no FTS tables).
    assert evt["discovery_ms"] >= 0


async def test_search_timing_fields_locked() -> None:
    """#6a / #8: SEARCH_TIMING_FIELDS is the locked exact tuple in exact order."""
    assert config.SEARCH_TIMING_FIELDS == (
        "request_id",
        "ip_prefix",
        "tool",
        "discovery_ms",
        "fan_out_ms",
        "post_filter_ms",
    )


async def test_search_timing_no_extra_keys(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """#6a / #8: search_timing event carries no extra keys beyond SEARCH_TIMING_FIELDS."""
    from mcp_zeeker.tools.search import search

    _stub_dbs(httpx_mock)
    _stub_table_response(httpx_mock)

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as cap:
        await search(query="appeal", databases=["zeeker-judgements"])

    timing_events = [e for e in cap if e.get("event") == "search_timing"]
    assert len(timing_events) == 1
    evt = timing_events[0]
    allowed_keys = set(config.SEARCH_TIMING_FIELDS) | {"event", "log_level", "level", "timestamp"}
    extra_keys = set(evt.keys()) - allowed_keys
    assert extra_keys == set(), f"Unexpected keys in search_timing event: {extra_keys!r}"
