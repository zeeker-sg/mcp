"""
Hostile-input canary corpus — Slice A (Plan 03-02, D3-09 / INJ-05 / QUERY-09).

Drives the 5-canary minimum corpus through query_table for two distinct
compile_filters error paths, then asserts the canary VALUE never appears in:
- the ToolError message
- captured stdout / stderr
- the structlog log stream (caplog at DEBUG level)

Filter VALUES are the protected threat surface (D3-09); column / op / database
/ table are request identifiers and may be echoed (per D3-07). The handler and
compile_filters never interpolate user-supplied values into error strings —
this test enforces that contract end-to-end.

5 canaries (D3-09 minimum-viable corpus):
1. </system>                              — HTML/system-tag injection sentinel
2. NEAR('data' 'protection') AND NOT       — FTS5-operator string
3. "x" * 5001                              — 5 KB oversized payload
4. ZEEKER_CANARY_42                        — plain round-trip detector
5. "\udc80"                                — lone surrogate (UTF-8 boundary)

2 error paths through compile_filters:
- coercion failure (gt on INTEGER column with non-numeric value)
- nested list rejection (in op with nested dict value)
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.retrieval import query_table

# D3-09 minimum-viable corpus. Order matches the docstring header for traceability.
CANARY_STRINGS: list[str] = [
    "</system>",  # HTML/system tag injection sentinel
    "NEAR('data' 'protection') AND NOT",  # FTS5 operators (would error if forwarded)
    "x" * 5001,  # 5 KB oversized string
    "ZEEKER_CANARY_42",  # plain round-trip detector
    "\udc80",  # lone surrogate — UTF-8 boundary handling
]

# Two distinct compile_filters error paths.
ERROR_PATHS: list[str] = ["coercion", "nested_list"]


def _db_url(name: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{name}.json"


def _zeeker_schemas_url(db: str) -> str:
    base = config.UPSTREAM_URL.rstrip("/")
    return f"{base}/{db}/_zeeker_schemas.json"


def _metadata_url() -> str:
    return f"{config.UPSTREAM_URL}/-/metadata.json"


def _pdpc_db_payload() -> dict:
    """pdpc.enforcement_decisions with the visible column set used in this corpus."""
    return {
        "tables": [
            {
                "name": "enforcement_decisions",
                "hidden": False,
                "count": 100,
                "columns": [
                    "title",
                    "organisation",
                    "decision_type",
                    "decision_date",
                    "decision_url",
                    "penalty_amount",
                    "summary",
                ],
                "primary_keys": [],
            },
        ]
    }


def _pdpc_schema_payload() -> dict:
    """/_zeeker_schemas.json for pdpc — penalty_amount is INTEGER (drives coercion path)."""
    col_defs = {
        "title": "TEXT",
        "organisation": "TEXT",
        "decision_type": "TEXT",
        "decision_date": "TEXT",
        "decision_url": "TEXT",
        "penalty_amount": "INTEGER",  # gt-with-canary triggers numeric coercion failure
        "summary": "TEXT",
    }
    return {
        "columns": [
            "resource_name",
            "schema_version",
            "schema_hash",
            "column_definitions",
            "created_at",
            "updated_at",
        ],
        "rows": [
            [
                "enforcement_decisions",
                1,
                "abc123",
                json.dumps(col_defs),
                "2024-01-01",
                "2024-01-01",
            ],
        ],
    }


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


def _surfaces_contain(
    canary: str, *, captured_out: str, captured_err: str, log_text: str, error_text: str
) -> list[str]:
    """Return the list of surface names where the canary appears.

    For the lone-surrogate canary, also check repr() so backslash-escape leakage
    is caught (e.g. '\\udc80' in any text stream).
    """
    leaks: list[str] = []
    for surface_name, surface_text in (
        ("stdout", captured_out),
        ("stderr", captured_err),
        ("log", log_text),
        ("error", error_text),
    ):
        if canary in surface_text:
            leaks.append(surface_name)
        # Defense in depth: catch backslash-escape leakage of unprintable canaries.
        if repr(canary).strip("'\"") in surface_text and repr(canary) != repr(""):
            leaks.append(f"{surface_name}_repr")
    return leaks


@pytest.mark.parametrize("path", ERROR_PATHS)
@pytest.mark.parametrize("canary", CANARY_STRINGS)
async def test_filter_value_never_echoed_in_error_or_log(
    datasette_client,
    metadata_cache,
    httpx_mock: pytest_httpx.HTTPXMock,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    canary: str,
    path: str,
) -> None:
    """D3-09 / INJ-05 / QUERY-09: hostile filter values never leak to any output.

    5 canaries × 2 paths = 10 parametrized cases.

    Path 'coercion' — gt on INTEGER column with non-numeric value triggers
    compile_filters' numeric coercion (raise ... from None suppresses chain).
    Path 'nested_list' — in op with [{nested dict}] triggers the anti-nesting
    branch (T-03-03 mitigation).

    DEBUG-level caplog enforces the contract even on the chattiest log path
    (D3-09 reserves DEBUG for structural shape like value_type / value_length —
    NEVER the raw value).
    """
    httpx_mock.add_response(url=_db_url("pdpc"), json=_pdpc_db_payload(), is_reusable=True)
    httpx_mock.add_response(
        url=_zeeker_schemas_url("pdpc"), json=_pdpc_schema_payload(), is_reusable=True
    )

    if path == "coercion":
        filters_arg: list[dict] = [
            {"column": "penalty_amount", "op": "gt", "value": canary},
        ]
    elif path == "nested_list":
        filters_arg = [
            {"column": "organisation", "op": "in", "value": [{"nested": canary}]},
        ]
    else:  # pragma: no cover — parametrize guards this
        raise AssertionError(f"unknown path: {path}")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ToolError) as exc_info:
            await query_table("pdpc", "enforcement_decisions", filters=filters_arg)

    captured = capsys.readouterr()
    log_text = " ".join(r.getMessage() for r in caplog.records)
    error_text = str(exc_info.value)

    leaks = _surfaces_contain(
        canary,
        captured_out=captured.out,
        captured_err=captured.err,
        log_text=log_text,
        error_text=error_text,
    )
    assert not leaks, (
        f"Canary leaked into {leaks}; canary[:40]={canary[:40]!r}, path={path}, "
        f"error={error_text!r}"
    )
