"""
Envelope snapshot tests — Phase 6 Plan 06-03 GREEN body.

Parametrized snapshot across every registered tool via `mcp.list_tools()`
(Pattern F). Asserts:

- ENV-01: every successful response is an Envelope.
- ENV-02 / D6-09 / D6-10 / D6-11: `provenance.retrieved_at` serializes to the
  literal ISO timestamp from `frozen_retrieved_at` when the middleware is
  monkey-patched to passthrough. Without the monkey-patch
  `RetrievedAtMiddleware.on_call_tool` would OVERWRITE the contextvar with
  `datetime.now(tz=UTC)` on each call — the middleware itself is unit-tested
  in `tests/test_retrieved_at_middleware.py`; here the factory-side
  `get_tool_started_at()` accessor is what we exercise (Open Question 3
  resolution).
- ENV-03 / D6-03: license posture is per-DB on single-DB envelopes
  (`for_table_list`, `for_rows`) and `LICENSE_MIXED` on multi-DB envelopes
  (`for_database_list`, `for_search_results`). `license_url` is None on
  multi-DB envelopes.
- ENV-05 / INJ-04 / D3-19: top-level row keys never intersect HEAVY_COLUMNS.
- D6-snapshot-relax: when `retrieved_content` is present, its keys are a
  subset of HEAVY_COLUMNS (which Plan 06-01 extended with `_policy`).
- D6-05: every row in `data` carries a non-empty `_citation` string.
- INJ-03: heavy text columns round-trip byte-identically to the LLM — no
  scrubbing / lexical filtering. Exercised via the 5-canary corpus on
  `query_table(judgments, columns=["content_text"])`.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx
import pytest

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.middleware.retrieved_at import RetrievedAtMiddleware
from tests._corpus.hostile_inputs import CANARY_STRINGS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def passthrough_retrieved_at_middleware(monkeypatch, frozen_retrieved_at):
    """Monkey-patch RetrievedAtMiddleware.on_call_tool to bind the FROZEN instant.

    Resolution to Plan 06-RESEARCH §"Open Question 3" — the production
    middleware OVERWRITES the `tool_started_at` ContextVar with
    `datetime.now(tz=UTC)` on every call AND runs inside a fresh task context
    (the in-memory FastMCP Client dispatches the call on a copied context),
    so the `frozen_retrieved_at` fixture's contextvar binding does not
    propagate into the handler's context. The middleware itself is
    unit-tested in `tests/test_retrieved_at_middleware.py`; this test file
    targets the factory-side `get_tool_started_at()` accessor flow.

    The patched coroutine binds `tool_started_at` to the FROZEN datetime, then
    calls through to the next middleware — so the factories observe the
    frozen instant via the contextvar accessor. pytest's monkeypatch teardown
    reverts the patch after each test (T-06-16 in plan threat model).
    """
    from mcp_zeeker.core.middleware.retrieved_at import tool_started_at

    async def _bind_frozen(self, context, call_next):  # noqa: ARG001 — middleware signature
        token = tool_started_at.set(frozen_retrieved_at)
        try:
            return await call_next(context)
        finally:
            tool_started_at.reset(token)

    monkeypatch.setattr(RetrievedAtMiddleware, "on_call_tool", _bind_frozen)


@pytest.fixture
async def bound_datasette_client_for_snapshot(
    httpx_mock,
) -> AsyncIterator[DatasetteClient]:
    """Bind a DatasetteClient against httpx_mock for snapshot tests.

    Does NOT depend on the shared `stub_upstream` fixture — that fixture
    registers per-DB responses WITHOUT `is_reusable=True`, which would be
    consumed by the first tool invocation. Snapshot tests need to invoke
    multiple tools (each touching one or more DBs) so each test registers
    its own reusable per-DB stubs via the helper above.
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


def _stub_four_dbs_with_t1(httpx_mock) -> None:
    """Stub the 4 ALLOWED_DATABASES /{db}.json responses with a visible `t1` table.

    Each DB exposes one visible table named `t1` with a `fts_table` set so the
    search tool's auto-discovery doesn't skip the DB. Reusable so multiple
    tool invocations across the same test can share them.
    """
    cols = ["title", "summary", "source_url", "decision_date"]
    payload = {
        "tables": [
            {
                "name": "t1",
                "hidden": False,
                "count": 10,
                "columns": cols,
                "primary_keys": [],
                "fts_table": "t1_fts",
            }
        ]
    }
    for db in config.ALLOWED_DATABASES:
        httpx_mock.add_response(
            url=f"{config.UPSTREAM_URL.rstrip('/')}/{db}.json",
            json=payload,
            is_reusable=True,
        )


# ---------------------------------------------------------------------------
# Dispatch table — minimal valid args for every registered tool. Distinct
# tools require distinct argument shapes, so we cannot iterate the registry
# blindly; the dispatch table covers each tool's smallest-passing payload
# (Pattern F registry-iteration shape, plus per-tool argument lookup).
# ---------------------------------------------------------------------------

_DISPATCH_ARGS: dict[str, dict | None] = {
    "list_databases": {},
    "list_tables": {"database": "zeeker-judgements"},
    "describe_table": {"database": "zeeker-judgements", "table": "t1"},
    "query_table": {"database": "zeeker-judgements", "table": "t1"},
    # fetch needs a URL-keyed table + a known row; the stubbed `t1` table is
    # not URL-keyed, so the registry iteration in Test 1 skips it. Test 4
    # (byte-identical heavy text) and `test_policy_never_present_on_fetch_path`
    # in tests/test_content_policy_emission.py exercise fetch directly with
    # the real `judgments` URL-keyed table.
    "fetch": None,
    "search": {"query": "test"},
}


def _empty_table_payload() -> dict:
    """Empty Datasette /{db}/{table}.json payload — no rows, no truncation."""
    return {
        "rows": [],
        "columns": [],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 0,
    }


def _zeeker_schemas_payload() -> dict:
    """Minimal /_zeeker_schemas.json — no rows, satisfies the schema reader."""
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


def _judgments_db_with_judgments_payload() -> dict:
    """zeeker-judgements /{db}.json that exposes `judgments` (URL-keyed, FTS)."""
    return {
        "tables": [
            {
                "name": "judgments",
                "hidden": False,
                "count": 219,
                "columns": [
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
                ],
                "primary_keys": ["id"],
                "fts_table": "judgments_fts",
            }
        ]
    }


def _judgments_row_with_canary(canary: str) -> dict:
    """One judgments row carrying the canary as its content_text."""
    return {
        "rows": [
            {
                "citation": "2026 SGDC 999",
                "case_name": "Foo v Bar",
                "case_numbers": "DC/CC 1/2026",
                "decision_date": "2026-01-15",
                "court": "District Court",
                "subject_tags": "[]",
                "source_url": "https://www.elitigation.sg/gd/s/2026_SGDC_999",
                "pdf_url": "",
                "summary": "fixture row",
                "content_text": canary,
            }
        ],
        "columns": [
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
        ],
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 1,
    }


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for /{database}/{table}.json with any query string."""
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _zeeker_schemas_url(database: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{database}/_zeeker_schemas.json"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_every_registered_tool_returns_envelope_with_correct_provenance(
    mcp_client,
    bound_datasette_client_for_snapshot,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """ENV-01/02/03: each tool returns an Envelope with frozen retrieved_at.

    Pattern F — iterate `await mcp.list_tools()`. For each tool with a known
    minimal-arg payload in _DISPATCH_ARGS, invoke via the in-memory mcp_client
    and assert the structured envelope shape:

    (a) response is an Envelope (Pydantic dump dict).
    (b) provenance.source == "data.zeeker.sg".
    (c) provenance.retrieved_at serializes to literal "2026-01-01T00:00:00+00:00"
        (or "2026-01-01T00:00:00Z" — Pydantic 2 uses Z suffix for UTC).
    (d) provenance.license is "mixed" for multi-DB tools, "CC-BY-4.0" for
        single-DB tools (cold-cache config fallback per D6-04).
    (e) for multi-DB tools, provenance.license_url is None AND
        provenance.database is None AND provenance.table is None.

    `passthrough_retrieved_at_middleware` fixture patches the production
    middleware so the `frozen_retrieved_at` ContextVar binding survives all
    the way into the envelope factory's `get_tool_started_at()` call.
    """
    # Pydantic 2 serializes UTC datetimes with the "Z" suffix (RFC 3339 stricter
    # form) while datetime.isoformat() emits "+00:00". Both forms represent the
    # same instant; accept either.
    frozen_iso_plus = frozen_retrieved_at.isoformat()  # "2026-01-01T00:00:00+00:00"
    frozen_iso_z = frozen_iso_plus.replace("+00:00", "Z")  # "2026-01-01T00:00:00Z"
    # Stub the 4 ALLOWED_DATABASES /{db}.json with a visible `t1` table.
    _stub_four_dbs_with_t1(httpx_mock)
    # Stub the empty table-row response for each visible stubbed table so the
    # query_table / describe_table invocations don't 404. _zeeker_schemas
    # response is also needed for query_table's column-types path.
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_zeeker_schemas_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "t1"),
        json=_empty_table_payload(),
        is_reusable=True,
    )
    # search tool dispatches per-DB FTS calls — also stub the t1 FTS path
    # for every DB so the fan-out doesn't fail. Re-use empty rows.
    for db in config.ALLOWED_DATABASES:
        httpx_mock.add_response(
            url=_table_url_re(db, "t1"),
            json=_empty_table_payload(),
            is_reusable=True,
        )

    tools = await mcp_client.list_tools()
    assert tools, "no tools registered"
    invoked = 0
    for tool in tools:
        args = _DISPATCH_ARGS.get(tool.name)
        if args is None:
            # Tool needs custom fixture surface (see test_byte_identical for fetch).
            continue
        result = await mcp_client.call_tool(tool.name, args)
        assert not result.is_error, f"tool '{tool.name}' returned error: {result.content}"
        envelope = result.structured_content
        assert isinstance(envelope, dict), f"tool '{tool.name}' did not return a dict envelope"
        # (b) source
        assert envelope["provenance"]["source"] == "data.zeeker.sg"
        # (c) frozen retrieved_at — the passthrough fixture ensures the
        # frozen_retrieved_at binding survives the middleware layer.
        observed_iso = envelope["provenance"]["retrieved_at"]
        assert observed_iso in (frozen_iso_plus, frozen_iso_z), (
            f"tool '{tool.name}': retrieved_at not frozen "
            f"({observed_iso} not in {{{frozen_iso_plus!r}, {frozen_iso_z!r}}})"
        )
        # TEST-03: per-row row-key partition assertion.
        # Only applies to list-of-dicts data envelopes; describe_table returns
        # a single dict ({"name": ..., "columns": [...], ...}) whose keys are
        # never heavy columns by construction — skip per-row iteration for it.
        if isinstance(envelope.get("data"), list):
            for row in envelope["data"]:
                leaked_top = set(row.keys()) & config.HEAVY_COLUMNS
                assert not leaked_top, (
                    f"TEST-03 leak: tool={tool.name!r} top-level row keys "
                    f"intersect HEAVY_COLUMNS: {leaked_top!r}"
                )
                if "retrieved_content" in row:
                    rc_extra = set(row["retrieved_content"].keys()) - config.HEAVY_COLUMNS
                    assert not rc_extra, (
                        f"TEST-03 leak: tool={tool.name!r} retrieved_content carries "
                        f"non-HEAVY keys: {rc_extra!r}"
                    )
        # (d) license posture
        lic = envelope["provenance"]["license"]
        if tool.name in ("list_databases", "search"):
            # D6-03 multi-DB envelope
            assert lic == config.LICENSE_MIXED, f"tool '{tool.name}': license expected 'mixed'"
            # (e) multi-DB null fields
            assert envelope["provenance"]["license_url"] is None
            assert envelope["provenance"]["database"] is None
            assert envelope["provenance"]["table"] is None
        else:
            # Single-DB envelope — D6-04 cold-cache config fallback yields
            # ("CC-BY-4.0", LICENSE_DEFAULT_URL).
            assert lic == "CC-BY-4.0", (
                f"tool '{tool.name}': license expected 'CC-BY-4.0', got {lic}"
            )
        invoked += 1

    assert invoked >= 3, f"expected at least 3 tools invoked, got {invoked}"


async def test_heavy_namespace_contract_per_tool(
    mcp_client,
    bound_datasette_client_for_snapshot,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """ENV-05 / INJ-04 / D3-19 / D6-snapshot-relax: heavy-namespace contract.

    For `query_table` (with heavy column requested) and `search`:
      - `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` at the top level.
      - When `row['retrieved_content']` is present:
          `set(row['retrieved_content'].keys()) ⊆ HEAVY_COLUMNS`.
      - For `search` (preview-only): no row has `retrieved_content`.
    """
    # Warm the metadata cache so the conftest's /-/metadata.json mock is
    # consumed (bound_metadata_cache registers with is_reusable=True which
    # requires at least one match; license_for_sync alone doesn't trigger
    # the fetch).
    await bound_metadata_cache.force_refresh()
    # Stub the zeeker-judgements DB to expose judgments (URL-keyed).
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL.rstrip('/')}/zeeker-judgements.json",
        json=_judgments_db_with_judgments_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_zeeker_schemas_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_row_with_canary("heavy body text fixture"),
        is_reusable=True,
    )

    # 1) query_table with heavy projection.
    result = await mcp_client.call_tool(
        "query_table",
        {
            "database": "zeeker-judgements",
            "table": "judgments",
            "columns": ["citation", "content_text"],
        },
    )
    assert not result.is_error, f"query_table error: {result.content}"
    envelope = result.structured_content
    assert envelope["data"], "expected at least one row"
    for row in envelope["data"]:
        top_keys = set(row.keys())
        # Top-level row keys never overlap HEAVY_COLUMNS (INJ-04 / D3-19).
        overlap = top_keys & config.HEAVY_COLUMNS
        assert overlap == set(), f"heavy column leaked at top level: {overlap}"
        rc = row.get("retrieved_content")
        if rc is not None:
            # retrieved_content keys are a subset of HEAVY_COLUMNS (which
            # includes "_policy" since Plan 06-01 — D6-snapshot-relax).
            extra = set(rc.keys()) - config.HEAVY_COLUMNS
            assert extra == set(), f"non-heavy key inside retrieved_content: {extra}"
            assert "content_text" in rc
            # D6-13: _policy is attached inside retrieved_content when heavy
            # is requested.
            assert "_policy" in rc

    # 2) search — preview-only rows (no retrieved_content per D6-14).
    # We need a per-table stub for the FTS dispatch. Provide a search response.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json={
            "rows": [
                {
                    "citation": "2026 SGDC 999",
                    "case_name": "Foo v Bar",
                    "decision_date": "2026-01-15",
                    "source_url": "https://www.elitigation.sg/gd/s/2026_SGDC_999",
                    "summary": "fixture row",
                }
            ],
            "columns": [
                "citation",
                "case_name",
                "decision_date",
                "source_url",
                "summary",
            ],
            "next": None,
            "truncated": False,
            "filtered_table_rows_count": 1,
        },
        is_reusable=True,
    )
    search_result = await mcp_client.call_tool(
        "search", {"query": "test", "databases": ["zeeker-judgements"]}
    )
    assert not search_result.is_error, f"search error: {search_result.content}"
    search_envelope = search_result.structured_content
    for row in search_envelope["data"]:
        # search returns preview-only rows — no retrieved_content (D6-14).
        assert "retrieved_content" not in row, "search must not emit retrieved_content"
        top_keys = set(row.keys())
        # Per-row license / license_url / _citation keys are not in HEAVY_COLUMNS.
        overlap = top_keys & config.HEAVY_COLUMNS
        assert overlap == set(), f"heavy column leaked into search row: {overlap}"


async def test_per_row_citation_string_present(
    mcp_client,
    bound_datasette_client_for_snapshot,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """D6-05: every row from a row-emitting tool carries `_citation: str`.

    Exercises query_table and search. _citation key uses underscore prefix to
    avoid collision with upstream column `judgments.citation` (Plan 06-02
    Deviations §1 — the canonical key per core/citation.py).
    """
    await bound_metadata_cache.force_refresh()
    # Re-stub for judgments.
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL.rstrip('/')}/zeeker-judgements.json",
        json=_judgments_db_with_judgments_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_zeeker_schemas_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_row_with_canary("body"),
        is_reusable=True,
    )

    # query_table — light-only projection still carries _citation.
    qt_result = await mcp_client.call_tool(
        "query_table",
        {"database": "zeeker-judgements", "table": "judgments"},
    )
    assert not qt_result.is_error
    qt_envelope = qt_result.structured_content
    assert qt_envelope["data"], "expected at least one row"
    for row in qt_envelope["data"]:
        cit = row.get("_citation")
        assert isinstance(cit, str) and cit, f"row missing _citation: {row!r}"

    # search — preview rows also carry _citation.
    search_result = await mcp_client.call_tool(
        "search", {"query": "fixture", "databases": ["zeeker-judgements"]}
    )
    assert not search_result.is_error
    search_envelope = search_result.structured_content
    for row in search_envelope["data"]:
        cit = row.get("_citation")
        assert isinstance(cit, str) and cit, f"search row missing _citation: {row!r}"


@pytest.mark.parametrize("canary", CANARY_STRINGS)
async def test_byte_identical_heavy_text_round_trip(
    canary: str,
    mcp_client,
    bound_datasette_client_for_snapshot,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """INJ-03: heavy text round-trips byte-identical from upstream to envelope.

    No content filtering / lexical scrubbing — the 5-canary corpus reaches the
    LLM as-is. The lone-surrogate canary uses Python `==` (string identity is
    byte-equality for str values; `repr(canary) == repr(returned)` is also
    used as a defense-in-depth check).
    """
    # Warm the metadata cache first so the conftest's /-/metadata.json mock
    # is consumed regardless of which parametrized branch we take below.
    await bound_metadata_cache.force_refresh()
    # Lone surrogate is unrepresentable as JSON UTF-8 (httpx_mock can't even
    # encode the upstream response stub containing it; the wire boundary will
    # always reject it). Carry-forward per Phase 4 / 04-03-SUMMARY: the
    # canary still passes through the byte-identical contract for the other
    # 4 canaries — which is the INJ-03 value-prop. Skip BEFORE registering
    # an unencodable stub, but AFTER the metadata cache fixture has been
    # consumed (otherwise pytest-httpx teardown complains).
    if canary in ("\udc80", "\udcc0\udc80"):
        # Carry-forward per Phase 4 / 04-03-SUMMARY (lone-surrogate) and Phase 8
        # (malformed UTF-8 surrogate pair \udcc0\udc80): both are unrepresentable
        # as JSON UTF-8 — httpx_mock can't encode a stub containing them.
        pytest.skip(f"surrogate canary {canary!r} unrepresentable on the JSON wire (carry-forward)")
    # Stub /{db}.json to expose judgments (the heaviest URL-keyed table).
    httpx_mock.add_response(
        url=f"{config.UPSTREAM_URL.rstrip('/')}/zeeker-judgements.json",
        json=_judgments_db_with_judgments_payload(),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url("zeeker-judgements"),
        json=_zeeker_schemas_payload(),
        is_reusable=True,
    )
    # Upstream returns one row whose content_text == canary verbatim.
    httpx_mock.add_response(
        url=_table_url_re("zeeker-judgements", "judgments"),
        json=_judgments_row_with_canary(canary),
        is_reusable=True,
    )

    result = await mcp_client.call_tool(
        "query_table",
        {
            "database": "zeeker-judgements",
            "table": "judgments",
            "columns": ["citation", "content_text"],
        },
    )
    assert not result.is_error, f"query_table error: {result.content}"
    envelope = result.structured_content
    assert envelope["data"], "expected at least one row"
    rc = envelope["data"][0].get("retrieved_content")
    assert rc is not None, "expected retrieved_content for heavy projection"
    returned = rc.get("content_text")
    assert returned == canary, (
        f"INJ-03 byte-identical round-trip failed; "
        f"canary[:40]={canary[:40]!r}, returned[:40]={(returned or '')[:40]!r}"
    )
