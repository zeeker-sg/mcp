"""
Issue #18 — FTS index warmer (cold-index timeout mitigation).

Production symptom (2026-08-29, mcp.zeeker.sg): an unscoped `search()` against a
COLD upstream FTS index failed `judgments_fragments` with HTTP 400 "SQL query
took too long" at 1.1–1.9s, while the identical query once warm returned in
0.05–0.4s. Warmth decays, and a daily briefing runs against a cold index most
mornings — the connector-side decay the outline called out for the zeeker-mcp
task list.

Fix shape: a background task inside the MCP process that periodically replays
representative FTS queries against every searchable table (including
`*_fragments` passage-search sources) so upstream FTS/VFS pages stay cached.
The warmer reuses the EXACT production dispatch surfaces — `build_bm25_sql` /
`build_maxp_sql` over `DatasetteClient.execute_sql` on the SQL path,
`get_table_rows(_search=...)` on the legacy path — plus the same discovery
gates so it can never warm (or fail on) a target real search would not touch.

Contract under test:
  1. `select_warm_targets` — discovery-driven target selection (tables +
     fragment passage sources); a hidden fragment table drops out of the plan.
  2. `FtsWarmer.warm_once` — one bounded FTS query per target via the exact
     production dispatch path; per-target outcomes; never raises.
  3. Failure tolerance — a failing target is recorded with its error class and
     does not abort the pass or the loop.
  4. Reentrancy — a second warm pass while one is in flight is a no-op.
  5. `run_forever` — immediate first pass, then fixed-interval passes;
     cancellation exits cleanly; per-target failures never kill the loop.
  6. Config knobs — locked defaults for enable/interval/timeout/warm query.
  7. Lifespan wiring — app.py starts the warmer when enabled, cancels it on
     shutdown, and stays warm-neutral when disabled.
"""

from __future__ import annotations

import re

import anyio
import httpx
import pytest
import pytest_httpx
from starlette.applications import Starlette

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatabaseSummary, TableSummary

_BASE = re.escape(config.UPSTREAM_URL.rstrip("/"))


def _summary_url_re(database: str) -> re.Pattern[str]:
    """Matcher for the discovery GET /{db}.json (no query string)."""
    return re.compile(rf"^{_BASE}/{re.escape(database)}\.json$")


def _sql_url_re(database: str) -> re.Pattern[str]:
    """Matcher for the SQL endpoint GET /{db}.json?sql=..."""
    return re.compile(rf"^{_BASE}/{re.escape(database)}\.json\?.*$")


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Matcher for a legacy table view GET /{db}/{table}.json?..."""
    return re.compile(rf"^{_BASE}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _table(
    name: str,
    *,
    fts: str | None = None,
    columns: list[str] | None = None,
    hidden: bool = False,
) -> TableSummary:
    return TableSummary(name=name, fts_table=fts, columns=columns or [], hidden=hidden)


def _judgements_summary() -> DatabaseSummary:
    """Minimal zeeker-judgements summary: one content table + one fragment
    table (both FTS-indexed) + the fts sidecars + one platform table."""
    fts_cols = ["case_name", "content_text"]
    return DatabaseSummary(
        tables=[
            _table(
                "judgments",
                fts="judgments_fts",
                columns=["case_name", "decision_date", "source_url", "id"],
            ),
            _table(
                "judgments_fragments",
                fts="judgments_fragments_fts",
                columns=[
                    "judgments_fragments_fts",
                    *fts_cols,
                    "rank",
                    "ordinal",
                    "source_url",
                ],
            ),
            _table("judgments_fts", columns=["judgments_fts", *fts_cols, "rank"], hidden=True),
            _table(
                "judgments_fragments_fts",
                columns=["judgments_fragments_fts", *fts_cols, "rank"],
                hidden=True,
            ),
            _table("_zeeker_updates", hidden=True),
        ]
    )


def _stub_summary(httpx_mock: pytest_httpx.HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_summary_url_re("zeeker-judgements"),
        json={"tables": [t.model_dump() for t in _judgements_summary().tables]},
        is_reusable=True,
    )


_ROWS_OK = {"rows": [{"case_name": "A v B", "_score": -1.0, "_total": 1}]}


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    from mcp_zeeker.core.datasette_client import DatasetteClient

    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


# ---------------------------------------------------------------------------
# 1. Target selection
# ---------------------------------------------------------------------------


async def test_select_warm_targets_tables_and_fragments(monkeypatch) -> None:
    """Content tables with FTS + fragment passage sources both land in the plan.

    The fragment table is denylisted from the TABLE list (D4-04) but its
    passage source is selected through config.SEARCH_FRAGMENT_SOURCES —
    `judgments_fragments` is the exact table that times out cold (issue #18),
    so it MUST be warmed.
    """
    from mcp_zeeker.core.fts_warmer import select_warm_targets

    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    summary = _judgements_summary()
    visible = {t.name for t in summary.tables if not t.hidden}
    tables, fragments = await select_warm_targets("zeeker-judgements", summary, visible)
    table_names = [t[0] for t in tables]
    assert table_names == ["judgments"]  # denylist gate keeps the table list clean…
    # …while the fragment source is selected for warming (issue #18 target).
    assert [s.fragment_table for s in fragments] == ["judgments_fragments"]


async def test_select_warm_targets_hidden_fragment_dropped(monkeypatch) -> None:
    """An operator hiding the fragment table turns its warming off too
    (same gate-2 semantics as fragment_sources_for)."""
    from mcp_zeeker.core.fts_warmer import select_warm_targets

    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    summary = _judgements_summary()
    visible = {"judgments"}  # fragment table hidden now
    tables, fragments = await select_warm_targets("zeeker-judgements", summary, visible)
    assert [t[0] for t in tables] == ["judgments"]
    assert fragments == []


# ---------------------------------------------------------------------------
# 2. warm_once dispatch (SQL path — the production ranking mode)
# ---------------------------------------------------------------------------


async def test_warm_once_dispatches_sql_to_tables_and_fragments(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """One bounded FTS query per target via the owner-token SQL path.

    Asserts BOTH the content table and the fragment passage source are
    warmed, and the warm query travels ONLY as the bound :search_query
    parameter (INJ-05 carry-forward).
    """
    from mcp_zeeker.core.fts_warmer import FtsWarmer

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), json=_ROWS_OK, is_reusable=True)
    _stub_summary(httpx_mock)

    result = await FtsWarmer().warm_once()

    assert sorted(result) == [
        "zeeker-judgements.judgments",
        "zeeker-judgements.judgments_fragments",
    ]
    assert set(result.values()) == {"ok"}
    sql_calls = [
        r
        for r in httpx_mock.get_requests()
        if "sql=" in str(r.url) and r.url.path.endswith("zeeker-judgements.json")
    ]
    assert len(sql_calls) == 2
    for req in sql_calls:
        params = req.url.params
        assert "bm25(" in params["sql"]
        # INJ-05: the warm query is never interpolated into the SQL text.
        assert config.FTS_WARM_QUERY not in params["sql"]
        # Phrase-wrapped escape, matches the production dispatch contract.
        assert params["search_query"].startswith('"')


async def test_warm_once_legacy_mode_uses_table_search(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """Legacy ranking: warm via the anonymous `_search=` table-view dispatch
    (the exact dispatch `_one_table` takes in that mode) and skip fragment
    passage sources (no legacy dispatch exists for them)."""
    from mcp_zeeker.core.fts_warmer import FtsWarmer

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "legacy")
    payload = {
        "rows": [{"case_name": "A v B", "source_url": "https://x"}],
        "columns": ["case_name", "source_url"],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 1,
    }
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"), json=payload, is_reusable=True
    )
    _stub_summary(httpx_mock)

    result = await FtsWarmer().warm_once()

    assert sorted(result) == ["zeeker-judgements.judgments"]
    assert result["zeeker-judgements.judgments"] == "ok"
    table_reqs = httpx_mock.get_requests(url=_table_url_re("zeeker-judgements", "judgments"))
    assert len(table_reqs) == 1
    assert table_reqs[0].url.params["_search"].startswith('"')


# ---------------------------------------------------------------------------
# 3. Failure tolerance
# ---------------------------------------------------------------------------


async def test_warm_once_records_failure_and_continues(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """A failing target is recorded per-target with its error class; the
    remaining targets still warm and warm_once still returns."""
    import structlog.testing

    from mcp_zeeker.core.fts_warmer import FtsWarmer

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    _stub_summary(httpx_mock)
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), status_code=500)
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), json=_ROWS_OK)

    with structlog.testing.capture_logs() as logs:
        result = await FtsWarmer().warm_once()

    # Exactly one target failed, the other warmed — whichever ran first.
    ok_keys = [k for k, v in result.items() if v == "ok"]
    failed_keys = [k for k, v in result.items() if v.startswith("failed:")]
    assert len(ok_keys) == 1 and len(failed_keys) == 1
    assert set(ok_keys + failed_keys) == {
        "zeeker-judgements.judgments",
        "zeeker-judgements.judgments_fragments",
    }
    failure_events = [e for e in logs if e.get("event") == "fts_warm_table_failed"]
    assert len(failure_events) == 1
    assert failure_events[0]["table"] in {"judgments", "judgments_fragments"}
    assert "error_class" in failure_events[0]
    # INJ-05: no query text in the failure binding.
    assert config.FTS_WARM_QUERY not in str(failure_events[0])


# ---------------------------------------------------------------------------
# 4. Reentrancy guard
# ---------------------------------------------------------------------------


async def test_warm_once_reentrant_call_is_noop(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """A second warm pass while one is in flight returns {"skipped"}."""
    from mcp_zeeker.core.fts_warmer import FtsWarmer

    warmer = FtsWarmer()
    started = anyio.Event()
    gate = anyio.Event()

    async def _hold_guard() -> None:
        warmer._inflight = True
        started.set()
        try:
            await gate.wait()
        finally:
            warmer._inflight = False

    async with anyio.create_task_group() as tg:
        tg.start_soon(_hold_guard)
        await started.wait()
        second = await warmer.warm_once()
        assert second == {"status": "skipped"}
        gate.set()


# ---------------------------------------------------------------------------
# 5. run_forever scheduling
# ---------------------------------------------------------------------------


async def test_run_forever_immediate_then_interval(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """First pass fires immediately (cold start matters most); subsequent
    passes are spaced by FTS_WARMER_INTERVAL_S; cancellation exits cleanly."""
    from mcp_zeeker.core.fts_warmer import FtsWarmer

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    monkeypatch.setattr(config, "FTS_WARMER_INTERVAL_S", 0.05)
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), json=_ROWS_OK, is_reusable=True)
    _stub_summary(httpx_mock)

    warmer = FtsWarmer()
    with anyio.move_on_after(0.3):  # cancel mid-interval sleep
        await warmer.run_forever()

    # Immediate first pass plus interval passes — at least 2 SQL dispatches.
    sql_calls = [
        r
        for r in httpx_mock.get_requests()
        if "sql=" in str(r.url) and r.url.path.endswith("zeeker-judgements.json")
    ]
    assert len(sql_calls) >= 2


async def test_run_forever_survives_all_targets_failing(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """Every target fails on every pass — the loop keeps ticking (per-target
    failures are recorded, never raised)."""
    from mcp_zeeker.core.fts_warmer import FtsWarmer

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    monkeypatch.setattr(config, "FTS_WARMER_INTERVAL_S", 0.05)
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), status_code=500, is_reusable=True)
    _stub_summary(httpx_mock)

    warmer = FtsWarmer()
    passes: list[dict] = []
    with anyio.move_on_after(0.12):
        original = warmer.warm_once

        async def counting_warm_once():
            result = await original()
            passes.append(result)
            return result

        warmer.warm_once = counting_warm_once  # type: ignore[method-assign]
        await warmer.run_forever()

    assert len(passes) >= 2
    assert len(passes) == sum(1 for r in passes if r)  # every pass returned an outcome map
    assert all(v.startswith("failed:") for r in passes for v in r.values())


@pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)
async def test_warm_once_returns_promptly_with_failing_target(
    datasette_client, httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """A target that dies at the transport layer is recorded and the pass
    returns promptly — far below the 10s httpx read timeout (guard against
    ever letting a stalled warm pass stack up behind the request pool)."""
    from mcp_zeeker.core.fts_warmer import FtsWarmer

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    _stub_summary(httpx_mock)
    # Only ONE SQL response registered: whichever target consumes it warms;
    # the other hits pytest-httpx's no-response path → transport error →
    # recorded, not raised.
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), json=_ROWS_OK)

    started = anyio.current_time()
    result = await FtsWarmer().warm_once()
    elapsed = anyio.current_time() - started

    assert elapsed < 5.0
    assert len(result) == 2
    ok_keys = [k for k, v in result.items() if v == "ok"]
    assert len(ok_keys) >= 1


# ---------------------------------------------------------------------------
# 6. Config knobs
# ---------------------------------------------------------------------------


def test_fts_warmer_config_defaults() -> None:
    """Locked defaults: enabled, interval sized for index decay (≥ 10 min),
    bounded pass timeout, meaningful warm query."""
    assert config.FTS_WARMER_ENABLED is True
    assert config.FTS_WARMER_INTERVAL_S >= 600.0
    assert 1.0 <= config.FTS_WARMER_TIMEOUT_S <= config.FTS_WARMER_INTERVAL_S
    assert config.FTS_WARM_QUERY.strip() != ""


# ---------------------------------------------------------------------------
# 7. Lifespan wiring
# ---------------------------------------------------------------------------


async def test_lifespan_starts_and_cancels_warmer(
    httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """The REAL app lifespan starts the warmer when enabled (first pass fires
    immediately, using the DatabaseSummaryCache) and cancels it on exit."""
    from mcp_zeeker.app import lifespan
    from mcp_zeeker.server import mcp

    monkeypatch.setattr(config, "ALLOWED_DATABASES", ("zeeker-judgements",))
    monkeypatch.setattr(config, "SEARCH_RANKING", "bm25_rrf")
    monkeypatch.setattr(config, "FTS_WARMER_INTERVAL_S", 10_000.0)
    _stub_summary(httpx_mock)
    httpx_mock.add_response(url=_sql_url_re("zeeker-judgements"), json=_ROWS_OK, is_reusable=True)

    async def _fake_list_tools():
        return []

    monkeypatch.setattr(mcp, "list_tools", _fake_list_tools)

    async with lifespan(Starlette()):
        # First pass fires immediately on startup; a short sleep is enough.
        await anyio.sleep(0.15)

    sql_calls = [
        r
        for r in httpx_mock.get_requests()
        if "sql=" in str(r.url) and r.url.path.endswith("zeeker-judgements.json")
    ]
    assert len(sql_calls) >= 1
    # Discovery went through the DatabaseSummaryCache path (bare /{db}.json).
    summary_calls = [
        r
        for r in httpx_mock.get_requests()
        if not r.url.params and r.url.path.endswith("zeeker-judgements.json")
    ]
    assert len(summary_calls) >= 1


async def test_lifespan_disabled_keeps_warm_neutral(
    httpx_mock: pytest_httpx.HTTPXMock, monkeypatch
) -> None:
    """FTS_WARMER_ENABLED=False: the real lifespan enters/exits cleanly and
    the warmer dispatches zero upstream calls."""
    from mcp_zeeker.app import lifespan
    from mcp_zeeker.server import mcp

    monkeypatch.setattr(config, "FTS_WARMER_ENABLED", False)

    async def _fake_list_tools():
        return []

    monkeypatch.setattr(mcp, "list_tools", _fake_list_tools)

    async with lifespan(Starlette()):
        await anyio.sleep(0.05)

    assert httpx_mock.get_requests() == []
