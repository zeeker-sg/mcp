"""
Content policy emission tests — Phase 6 Plan 06-03 GREEN body.

Parametrized per-(db, table) assertion that requesting a heavy column emits
`_policy` under `retrieved_content` with the exact 4-key shape from
`config.CONTENT_POLICIES`. Covers:

- D6-13: `_policy` is the per-(db, table) content-license posture key.
- D6-14: `_policy` lives ONLY inside `retrieved_content` — fetch (which
  strips HEAVY_COLUMNS) never emits `_policy`.
- D6-15: fallback path — when (db, table) is absent from CONTENT_POLICIES, a
  minimal `{source, license, license_url, redistribution}` policy is
  synthesized from the envelope license.

Parametrize source is `list(config.CONTENT_POLICIES.keys())` (CFG-02
auto-discovery — adding a new entry automatically gains test coverage). The
fallback path uses a monkeypatched copy of CONTENT_POLICIES with one entry
removed (the simplest way to make a real (db, table) "absent").
"""

from __future__ import annotations

import re

import httpx
import pytest

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.middleware.retrieved_at import RetrievedAtMiddleware

# ---------------------------------------------------------------------------
# Per-(db, table) heavy-column lookup — which heavy column to request via
# columns=[<heavy_col>] for each parametrized case. Sourced from
# RESEARCH §"Per-(db, table) Column Inventory" + LIGHT_COLUMNS / HEAVY_COLUMNS
# inspection.
#
# NOTE: pdpc.enforcement_decisions has NO heavy column upstream — its
# `summary` is in LIGHT_COLUMNS, not HEAVY_COLUMNS (RESEARCH Probe 3 line 599).
# Plan 06-01 OMITTED that entry from CONTENT_POLICIES for that reason. The
# parametrize set below is therefore CONTENT_POLICIES.keys() verbatim, with no
# omissions required.
# ---------------------------------------------------------------------------

_HEAVY_COL_PER_TABLE: dict[tuple[str, str], str] = {
    ("zeeker-judgements", "judgments"): "content_text",
    ("zeeker-judgements", "judgments_fragments"): "content_text",
    ("pdpc", "enforcement_decisions_fragments"): "text",
    ("sg-gov-newsrooms", "acra_news"): "content_text",
    ("sg-gov-newsrooms", "agc_news"): "content_text",
    ("sg-gov-newsrooms", "ccs_news"): "content_text",
    ("sg-gov-newsrooms", "ipos_news"): "content_text",
    ("sg-gov-newsrooms", "judiciary_news"): "content_text",
    ("sg-gov-newsrooms", "mlaw_news"): "content_text",
    ("sg-gov-newsrooms", "mom_news"): "content_text",
    ("sg-gov-newsrooms", "pdpc_news"): "content_text",
    ("sglawwatch", "headlines"): "text",
    ("sglawwatch", "commentaries"): "full_text",
    ("sglawwatch", "about_singapore_law_fragments"): "content_text",
}


# ---------------------------------------------------------------------------
# Fixtures (mirror the envelope_snapshot fixture set per Plan 06-03 action —
# duplication is cheaper than violating the single-plan-touch rule on
# tests/conftest.py).
# ---------------------------------------------------------------------------


@pytest.fixture
def passthrough_retrieved_at_middleware(monkeypatch, frozen_retrieved_at):
    """Bind the frozen instant via the production middleware seam.

    See tests/test_envelope_snapshot.py for the rationale (Open Question 3).
    The patched on_call_tool binds `tool_started_at` to the frozen datetime
    so the factories observe it via the contextvar accessor.
    """
    from mcp_zeeker.core.middleware.retrieved_at import tool_started_at

    async def _bind_frozen(self, context, call_next):  # noqa: ARG001
        token = tool_started_at.set(frozen_retrieved_at)
        try:
            return await call_next(context)
        finally:
            tool_started_at.reset(token)

    monkeypatch.setattr(RetrievedAtMiddleware, "on_call_tool", _bind_frozen)


@pytest.fixture
async def bound_datasette_client_for_policy(httpx_mock):
    """Bind a DatasetteClient for policy-emission tests.

    Does NOT pull in `stub_upstream` — each parametrized test stubs its OWN
    /{db}.json response shape so the upstream payload exposes only the table
    being tested. This avoids `stub_upstream`'s pre-registered tables polluting
    `_visible_tables` for tables that aren't in the per-test stub.
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _zeeker_schemas_url(database: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{database}/_zeeker_schemas.json"


def _zeeker_schemas_payload() -> dict:
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


def _db_payload_with_table(table: str, columns: list[str]) -> dict:
    """Build a minimal Datasette /{db}.json exposing one visible table."""
    return {
        "tables": [
            {
                "name": table,
                "hidden": False,
                "count": 10,
                "columns": columns,
                "primary_keys": [],
                "fts_table": None,
            }
        ]
    }


def _table_rows_payload(columns: list[str], row: dict) -> dict:
    return {
        "rows": [row],
        "columns": columns,
        "next": None,
        "truncated": False,
        "filtered_table_rows_count": 1,
    }


def _columns_for(database: str, table: str, heavy_col: str) -> list[str]:
    """Return the union of LIGHT_COLUMNS[(db, table)] + heavy_col."""
    light = list(config.LIGHT_COLUMNS.get(f"{database}.{table}", []))
    if heavy_col not in light:
        light.append(heavy_col)
    return light


def _stub_table_for_policy_test(
    httpx_mock,
    database: str,
    table: str,
    heavy_col: str,
) -> None:
    """Pre-register the upstream stubs required for one (db, table) policy test."""
    cols = _columns_for(database, table, heavy_col)
    httpx_mock.add_response(
        url=_db_url(database),
        json=_db_payload_with_table(table, cols),
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=_zeeker_schemas_url(database),
        json=_zeeker_schemas_payload(),
        is_reusable=True,
    )
    # Build a row containing every column the table exposes — so that downstream
    # citation synthesis (which is also exercised end-to-end here) has the
    # template's column-name fields available.
    row = {c: "fixture" for c in cols}
    row[heavy_col] = "heavy fixture body"
    httpx_mock.add_response(
        url=_table_url_re(database, table),
        json=_table_rows_payload(cols, row),
        is_reusable=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "db_table",
    sorted(config.CONTENT_POLICIES.keys()),
    ids=lambda dt: f"{dt[0]}.{dt[1]}",
)
async def test_policy_emitted_for_each_content_policy_entry(
    db_table: tuple[str, str],
    mcp_client,
    bound_datasette_client_for_policy,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """D6-13 / D6-15: per-(db, table) `_policy` matches CONTENT_POLICIES verbatim.

    For each (db, table) in CONTENT_POLICIES (14 entries per Plan 06-01),
    stub an upstream response and invoke `query_table(database=db, table=t,
    columns=[<heavy_col>])`. Assert `data[0]["retrieved_content"]["_policy"]`
    equals `config.CONTENT_POLICIES[(db, table)]` byte-identical via dict
    equality (all 4 keys: source, license, license_url, redistribution).
    """
    db, table = db_table
    heavy_col = _HEAVY_COL_PER_TABLE.get(db_table)
    if heavy_col is None:  # defensive
        pytest.skip(f"no heavy column mapping for {db_table}")

    # Warm the metadata cache so the conftest's /-/metadata.json mock is
    # consumed (license_for_sync inside the handler doesn't trigger a fetch).
    await bound_metadata_cache.force_refresh()
    _stub_table_for_policy_test(httpx_mock, db, table, heavy_col)

    result = await mcp_client.call_tool(
        "query_table",
        {"database": db, "table": table, "columns": [heavy_col]},
    )
    assert not result.is_error, f"{db_table}: query_table error: {result.content}"
    envelope = result.structured_content
    assert envelope["data"], f"{db_table}: expected at least one row"
    rc = envelope["data"][0].get("retrieved_content")
    assert rc is not None, f"{db_table}: expected retrieved_content (heavy projection)"
    policy = rc.get("_policy")
    assert policy is not None, f"{db_table}: expected _policy inside retrieved_content"
    assert policy == config.CONTENT_POLICIES[db_table], (
        f"{db_table}: _policy mismatch.\n"
        f"  expected: {config.CONTENT_POLICIES[db_table]!r}\n"
        f"  got:      {policy!r}"
    )


async def test_policy_fallback_when_table_missing_from_content_policies(
    monkeypatch,
    mcp_client,
    bound_datasette_client_for_policy,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """D6-15: minimal `_policy` synthesized when (db, table) absent from config.

    Remove `("zeeker-judgements", "judgments")` from CONTENT_POLICIES via
    monkeypatch, then invoke query_table with a heavy projection. The handler
    falls through to the D6-15 fallback path which synthesizes the policy
    from the envelope license.

    Cold-cache (no upstream license metadata served) yields ("CC-BY-4.0",
    LICENSE_DEFAULT_URL) per D6-04 config fallback.
    """
    db, table = "zeeker-judgements", "judgments"
    heavy_col = "content_text"

    # Warm the metadata cache.
    await bound_metadata_cache.force_refresh()

    # Build a new CONTENT_POLICIES dict missing the target key.
    patched = {k: v for k, v in config.CONTENT_POLICIES.items() if k != (db, table)}
    monkeypatch.setattr("mcp_zeeker.config.CONTENT_POLICIES", patched)
    # Also patch the import-site copy in tools/retrieval.py — that module
    # reads config.CONTENT_POLICIES at call-time (not at import time), so the
    # monkeypatch on the module attribute is sufficient.

    _stub_table_for_policy_test(httpx_mock, db, table, heavy_col)

    result = await mcp_client.call_tool(
        "query_table",
        {"database": db, "table": table, "columns": [heavy_col]},
    )
    assert not result.is_error, f"query_table error: {result.content}"
    envelope = result.structured_content
    rc = envelope["data"][0].get("retrieved_content")
    assert rc is not None
    policy = rc.get("_policy")
    assert policy is not None, "fallback _policy missing"
    # D6-15 fallback shape — minimal 4 keys with envelope-license values.
    expected = {
        "source": db,
        "license": "CC-BY-4.0",
        "license_url": config.LICENSE_DEFAULT_URL,
        "redistribution": "allowed",
    }
    assert policy == expected, (
        f"fallback _policy mismatch.\n  expected: {expected}\n  got: {policy}"
    )


async def test_policy_never_present_on_fetch_path(
    mcp_client,
    bound_datasette_client_for_policy,
    bound_metadata_cache,
    httpx_mock,
    frozen_retrieved_at,
    passthrough_retrieved_at_middleware,
) -> None:
    """D6-14: fetch path never emits `_policy` (it strips HEAVY_COLUMNS).

    `fetch` excludes HEAVY_COLUMNS at column-projection time so the returned
    row has no `retrieved_content` key at all — hence no `_policy` either.
    """
    db, table = "zeeker-judgements", "judgments"
    heavy_col = "content_text"
    # Warm the metadata cache.
    await bound_metadata_cache.force_refresh()
    # Stub /{db}.json with judgments visible.
    cols = _columns_for(db, table, heavy_col)
    httpx_mock.add_response(
        url=_db_url(db),
        json=_db_payload_with_table(table, cols),
        is_reusable=True,
    )
    # Row carries the URL so fetch's __exact match succeeds.
    target_url = "https://www.elitigation.sg/gd/s/2026_SGDC_999"
    row = {c: "fixture" for c in cols}
    row["source_url"] = target_url
    row["content_text"] = "heavy body (must NOT surface via fetch)"
    httpx_mock.add_response(
        url=_table_url_re(db, table),
        json=_table_rows_payload(cols, row),
        is_reusable=True,
    )

    result = await mcp_client.call_tool(
        "fetch",
        {"database": db, "table": table, "url": target_url},
    )
    assert not result.is_error, f"fetch error: {result.content}"
    envelope = result.structured_content
    assert envelope["data"], "expected a single fetched row"
    row_out = envelope["data"][0]
    # D6-14: no retrieved_content on fetch.
    assert "retrieved_content" not in row_out, "fetch must not emit retrieved_content"
    # And no _policy at any level.
    assert "_policy" not in row_out, "fetch must not emit _policy at top level"
