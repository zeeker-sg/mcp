"""
Shared test fixtures for the mcp-zeeker test suite.

Live fixtures replacing the Wave-0 stubs. Provides:
- mcp_client: FastMCP in-memory client (async context manager)
- asgi_client: httpx.AsyncClient backed by ASGITransport
- stub_upstream: pre-registers the 4 upstream DB responses via httpx_mock
- bound_metadata_cache: MetadataCache bound to the current context with a stub
- pytest_collection_modifyitems: auto-skips @pytest.mark.live tests unless ZEEKER_LIVE=1
- live_server: random-port uvicorn server in a daemon thread (Pattern C)
- _free_port: OS-assigned free port helper
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import pytest_httpx
import uvicorn
from fastmcp import Client

from mcp_zeeker import config
from mcp_zeeker.app import app
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.server import mcp

# Phase 4 (D4-20): captured fixture directory for cross-DB search tests.
# All 15 fixtures (12 GREEN per-table + 1 zero-hits + 1 fts-error + 1
# ignored/pdpc) were captured against live data.zeeker.sg in research commit
# cb645bd. Filename convention: `<db_underscored>__<table>.json` (e.g.
# `sg_gov_newsrooms__acra_news.json` — dashes in DB names become underscores
# to keep filenames POSIX-portable).
_SEARCH_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "datasette" / "search"


def _load_search_fixture(filename: str) -> dict:
    """Load a captured per-table FTS response — Phase 4 (D4-20).

    Filenames follow the `<db_underscored>__<table>.json` convention. All 15
    fixtures captured in research commit cb645bd are reachable. Phase 4
    search tests replay these fixtures via `httpx_mock.add_response(
    url=..., json=_load_search_fixture(...))`.

    Available fixtures (15):
      zeeker_judgements__judgments.json (5 GREEN rows)
      zeeker_judgements__judgments__fts_error.json (HTTP 400 body)
      pdpc__enforcement_decisions__search_ignored.json (search_ignored: pdpc
        has no FTS — captured response shows rowid-ordered rows)
      sg_gov_newsrooms__acra_news.json / agc_news / ccs_news / ipos_news /
        judiciary_news / mlaw_news / mom_news / pdpc_news (8 GREEN)
      sg_gov_newsrooms__acra_news__zero_hits.json (empty rows)
      sglawwatch__about_singapore_law.json / commentaries / headlines (3 GREEN)
    """
    return json.loads((_SEARCH_FIXTURE_DIR / filename).read_text())


# Metadata stub for test fixtures.
# NOTE: DB key is "Zeeker-Judgements" (mixed-case) to deliberately exercise the
# D2-05 normalize-at-ingest path — MetadataCache._fetch_and_normalize lowercases
# it so lookups with "zeeker-judgements" will hit.
METADATA_STUB = {
    "databases": {
        "Zeeker-Judgements": {"tables": {"judgments": {"description": "Singapore court judgments"}}}
    }
}


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.live tests unless ZEEKER_LIVE env var is set."""
    if not os.getenv("ZEEKER_LIVE"):
        skip_live = pytest.mark.skip(reason="Set ZEEKER_LIVE=1 to run live tests")
        for item in items:
            if item.get_closest_marker("live"):
                item.add_marker(skip_live)


def _free_port() -> int:
    """Bind to port 0 to get an OS-assigned free port, then release the socket."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live_server():
    """Spawn uvicorn on a random port in a daemon thread; yield the /mcp/ URL.

    The thread is cleaned up after each test. The server uses asyncio loop so
    pytest-httpx patches (which also use asyncio) are visible in the thread.

    Moved from tests/test_mcp_streamable_smoke.py to conftest.py (Phase 2 Plan 03)
    so that F-3 stateless-session tests can share the same fixture.
    """
    port = _free_port()
    cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Poll until the server is ready (up to 2.5s)
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "uvicorn did not start within 2.5s"
    yield f"http://127.0.0.1:{port}/mcp/"
    server.should_exit = True
    thread.join(timeout=5)


def _db_url(name: str) -> str:
    """Build the full upstream URL for a given DB name."""
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _table_url(database: str, table: str) -> str:
    """Build the full upstream URL for /{database}/{table}.json (Phase 3 retrieval).

    Mirrors _db_url's shape — used by query_table and fetch stub tests to
    register table-row responses with httpx_mock. Single helper avoids each
    retrieval test re-implementing the URL builder.
    """
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{database}/{table}.json"


def _tables_payload(
    names: list[str],
    *,
    fts_tables: dict[str, str] | None = None,
    columns: dict[str, list[str]] | None = None,
) -> dict:
    """Build a minimal Datasette /{db}.json payload — Phase 2 + Phase 4 fields.

    Backward-compat: `_tables_payload(names)` (no kwargs) emits exactly the
    Phase 2 shape — `fts_table=None`, `columns=[]` for every table — so all
    Phase 1/2/3 callers continue to work unchanged.

    Phase 4 kwargs (D4-02 / D4-12):
      `fts_tables`: per-table FTS5 virtual-table name (e.g.
        {"judgments": "judgments_fts"}). Absent entries get `fts_table=None`
        — matches pdpc reality (no FTS index upstream).
      `columns`: per-table column list — needed so resolve_preview_columns
        has something to resolve against.
    """
    fts_tables = fts_tables or {}
    columns = columns or {}
    return {
        "tables": [
            {
                "name": n,
                "hidden": False,
                "count": None,
                "columns": columns.get(n, []),
                "primary_keys": [],
                "fts_table": fts_tables.get(n),
            }
            for n in names
        ]
    }


# Phase 3 — minimal well-formed Datasette table-view response (_shape=objects).
# Used by retrieval stub tests to populate httpx_mock without re-declaring the
# full shape in each test. Real Datasette responses include filtered_table_rows_count;
# tests that care about that field provide a custom payload instead of this stub.
TABLE_ROWS_STUB: dict = {
    "rows": [
        {"citation": "2026 SGDC 136", "case_name": "Test v Test"},
    ],
    "columns": ["citation", "case_name"],
    "next": None,
    "truncated": False,
    "filtered_table_rows_count": 1,
}

# Phase 4 (D4-20) — minimal well-formed Datasette /{db}/{table}.json?_search=...
# response shape. Mirrors TABLE_ROWS_STUB but with preview-resolvable columns
# (title, case_name, decision_date, summary, source_url) so resolve_preview_columns
# can find candidates on it. Used by search stub tests to populate
# `stub_table_rows.add_response(url=_table_url(...), json=SEARCH_ROWS_STUB)`
# without re-declaring the full shape.
SEARCH_ROWS_STUB: dict = {
    "rows": [
        {
            "title": "Test judgment",
            "case_name": None,
            "decision_date": "2026-01-01",
            "summary": "A test row from a search fixture.",
            "source_url": "https://www.elitigation.sg/gd/s/2026_SGDC_999",
        },
    ],
    "columns": ["title", "case_name", "decision_date", "summary", "source_url"],
    "next": None,
    "truncated": False,
    "filtered_table_rows_count": 1,
}


@pytest.fixture
async def mcp_client():
    """
    Async fixture yielding a FastMCP in-memory client bound to the MCP app.

    The context manager entry triggers the MCP initialize handshake automatically.
    """
    async with Client(mcp) as c:
        yield c


@pytest.fixture
async def asgi_client():
    """
    Async fixture yielding an httpx.AsyncClient backed by ASGITransport(app).

    Uses the real Starlette app (including all middleware). Suitable for
    testing /healthz and the Origin allowlist without a live server.
    """
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.fixture
def stub_upstream(httpx_mock: pytest_httpx.HTTPXMock):
    """
    Pre-register the four upstream /{db}.json responses.

    sglawwatch gets 6 entries (4 hidden + 2 visible); all other DBs get 4 (2 platform + 2 visible).
    The hidden platform-internal tables (_zeeker_schemas, _zeeker_updates) are present
    in the upstream payload — the Phase 2 config denylist removes them at the handler layer.
    Returns the httpx_mock fixture for further customization by individual tests.
    """
    for db in config.ALLOWED_DATABASES:
        if db == "sglawwatch":
            # 4 hidden (2 legacy + 2 platform) + 2 visible
            tables = [
                "metadata",
                "schema_versions",
                "_zeeker_schemas",
                "_zeeker_updates",
                "t1",
                "t2",
            ]
        else:
            # 2 platform-internal + 2 visible
            tables = ["_zeeker_schemas", "_zeeker_updates", "t1", "t2"]
        httpx_mock.add_response(
            url=_db_url(db),
            json=_tables_payload(tables),
        )
    return httpx_mock


@pytest.fixture
def stub_table_rows(httpx_mock: pytest_httpx.HTTPXMock):
    """Phase 3 — thin facade around httpx_mock for table-row response staging.

    Returns the httpx_mock instance so tests can call .add_response(url=..., json=...)
    directly. The fixture exists primarily to give retrieval tests a consistent
    discovery name (`stub_table_rows`) and to mirror the `stub_upstream` shape.

    Usage:
        async def test_x(stub_table_rows):
            stub_table_rows.add_response(url=_table_url("pdpc", "enforcement_decisions"),
                                          json=TABLE_ROWS_STUB)
    """
    return httpx_mock


@pytest.fixture
async def bound_datasette_client(stub_upstream):
    """
    Bind a DatasetteClient backed by a real httpx.AsyncClient to the current context.

    The stub_upstream fixture is depended on to ensure upstream calls are intercepted.
    Tears down the binding and closes the HTTP client on fixture teardown.
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.fixture
async def bound_metadata_cache(httpx_mock: pytest_httpx.HTTPXMock):
    """
    Bind a MetadataCache backed by a real httpx.AsyncClient to the current context.

    Stubs /-/metadata.json with METADATA_STUB (is_reusable=True allows TTL/force_refresh tests).
    Uses ttl=0 to force re-fetch on every call (makes TTL-expiry tests simple).
    Tears down the binding, clears the singleton, and closes the HTTP client on teardown.
    """
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL}/-/metadata.json",
        json=METADATA_STUB,
        is_reusable=True,
    )
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        mc = MetadataCache(http, config.UPSTREAM_URL, ttl=0)
        token = MetadataCache.bind(mc)
        yield mc
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()


# ---------------------------------------------------------------------------
# Phase 5 — Fragment-join fixtures (single-plan-touch rule per 02-LEARNINGS)
# ---------------------------------------------------------------------------
# All Phase 5 conftest additions live in Plan 05-01 ONLY. Plans 05-02 / 05-03 /
# 05-04 MUST NOT modify this file. The cross-plan merge-conflict learning from
# Phase 2 / 3 / 4 dictates consolidation: any helper Wave 2 or Wave 3 needs is
# pre-included here.

_FRAGMENTS_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "datasette" / "fragments"


def _load_fragments_fixture(filename: str) -> dict:
    """Load a captured fragment-join response — Phase 5 (D5-04 / 05-RESEARCH §2).

    10 fixtures captured in research:
      zeeker_judgements__judgments__parent_lookup.json — Call 1, single-row parent
      zeeker_judgements__judgments_fragments__page1.json — Call 2, small page
      zeeker_judgements__judgments_fragments__large_page1.json — 957-frag walk, p1
      zeeker_judgements__judgments_fragments__large_page10.json — 957-frag walk,
        p10/10 (terminal — `next: null`)
      zeeker_judgements__judgments__multi_match.json — FRAG-06 multi-match
        (2 stale-duplicate rows)
      zeeker_judgements__judgments__url_encoding_probe.json — Special-char URL
      sglawwatch__about_singapore_law__parent_lookup.json
      sglawwatch__about_singapore_law_fragments__page1.json
      pdpc__enforcement_decisions__parent_lookup.json
      pdpc__enforcement_decisions_fragments__page1.json
    """
    return json.loads((_FRAGMENTS_FIXTURE_DIR / filename).read_text())


@pytest.fixture
async def bound_parent_pk_cache():
    """Bind a ParentPKCache (ttl=0 — every read after set returns miss) to the
    current context. Mirrors `bound_metadata_cache` shape.

    Function-body import avoids module-import cycles if fragment_join evolves
    its top-level imports.
    """
    from mcp_zeeker.core.fragment_join import ParentPKCache

    cache = ParentPKCache(ttl=0)
    token = ParentPKCache.bind(cache)
    yield cache
    ParentPKCache.reset(token)
    ParentPKCache.clear_singleton()


def stub_fragment_join_two_step(
    httpx_mock: pytest_httpx.HTTPXMock,
    *,
    database: str,
    parent_table: str,
    fragment_table: str,
    parent_lookup_payload: dict,
    fragments_payload: dict,
) -> None:
    """Register ordered upstream stubs for the two-step fragment join.

    Plan 05-02 / 05-03 tests call this helper. Order matters: Call 1 (parent
    lookup) is registered BEFORE Call 2 (fragments). Per 02-LEARNINGS, do
    NOT use `is_reusable=True` here — ordered consumption is required for
    deterministic test behavior. Use additional explicit add_response calls
    for multi-page walks (the helper handles ONE page; loop in the test).
    """
    import re as _re

    upstream = config.UPSTREAM_URL.rstrip("/")
    parent_url_re = _re.compile(
        rf"^{_re.escape(upstream)}/{_re.escape(database)}/{_re.escape(parent_table)}\.json(\?.*)?$"
    )
    fragments_url_re = _re.compile(
        rf"^{_re.escape(upstream)}/{_re.escape(database)}/{_re.escape(fragment_table)}\.json(\?.*)?$"
    )
    httpx_mock.add_response(url=parent_url_re, json=parent_lookup_payload)
    httpx_mock.add_response(url=fragments_url_re, json=fragments_payload)


# ---------------------------------------------------------------------------
# Phase 6 — Envelope hardening fixtures (single-plan-touch rule per 02-LEARNINGS)
# ---------------------------------------------------------------------------
# All Phase 6 conftest additions live in Plan 06-01 ONLY. Plans 06-02 / 06-03
# MUST NOT modify this file. The cross-plan merge-conflict learning from
# Phase 2 / 3 / 4 / 5 dictates consolidation: any helper Wave 2 or Wave 3
# needs is pre-included here. Phase 6 needs exactly one new fixture:
# frozen_retrieved_at, which binds tool_started_at to a fixed instant.


@pytest.fixture
def frozen_retrieved_at():
    """D6-12: Bind tool_started_at to a fixed instant for deterministic snapshots.

    Yields the bound datetime so tests can assert on both the contextvar
    binding and the literal ISO string '2026-01-01T00:00:00+00:00'.

    Plan 06-02 / 06-03 consume this fixture in envelope snapshot, citation
    synthesis, content policy emission, and consolidated hostile-input tests.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from mcp_zeeker.core.middleware.retrieved_at import tool_started_at

    frozen = _dt(2026, 1, 1, tzinfo=UTC)
    token = tool_started_at.set(frozen)
    try:
        yield frozen
    finally:
        tool_started_at.reset(token)


# ---------------------------------------------------------------------------
# Phase 7 — Rate limit fixtures (single-plan-touch rule per 02-LEARNINGS)
# ---------------------------------------------------------------------------
# All Phase 7 conftest additions live in Plan 07-01 ONLY. Plans 07-02 / 07-03 /
# 07-04 / 07-05 / 07-06 MUST NOT modify this file. The cross-plan merge-
# conflict learning from earlier phases dictates consolidation: any helper a
# downstream wave needs is pre-included here.


@pytest.fixture
def fake_clock():
    """Inject a controllable monotonic clock into RateLimitMiddleware.

    Returns a list `[0.0]` so tests can advance time by mutating fake_clock[0].
    The rate limiter constructor receives `time_provider=lambda: fake_clock[0]`.
    Matches the Phase 6 `frozen_retrieved_at` injection pattern (no freezegun
    — direct injection per 02-LEARNINGS / D6-12).
    """
    return [0.0]


@pytest.fixture
def rate_limiter(fake_clock):
    """RateLimitMiddleware instance with injected fake clock + locked test limits.

    Exposes the production RATE_* knobs (config.RATE_BURST etc.) so unit tests
    exercise the real burst/daily/store ceilings — no test-only constants. The
    `dummy_app` is a no-op ASGI app; unit tests typically drive _check_bucket
    directly, but a small subset await the full __call__ to verify the 429
    response shape.
    """
    from mcp_zeeker import config
    from mcp_zeeker.core.middleware.rate_limit import RateLimitMiddleware

    async def dummy_app(scope, receive, send):
        return None

    return RateLimitMiddleware(
        dummy_app,
        burst=config.RATE_BURST,
        sustained_per_second=config.RATE_SUSTAINED_PER_SECOND,
        daily_limit=config.RATE_DAILY_LIMIT,
        store_cap=config.RATE_STORE_CAP,
        idle_ttl_seconds=config.RATE_IDLE_TTL_SECONDS,
        time_provider=lambda: fake_clock[0],
    )


@pytest.fixture
def bucket_store(rate_limiter):
    """Direct access to the rate limiter's bucket store dict for assertion.

    Plans 07-02 / 07-03 use this to assert on store size + per-IP bucket
    state without going through the public middleware __call__ path.
    """
    return rate_limiter._store
