"""
Regression coverage for JSON-encoded-string param coercion (WR-260517-dvf).

Three params, three shapes each (9 parametrized cases):

  Params:
    - search.databases        (list[str] | None)
    - query_table.filters     (list[Filter] | None)
    - query_table.columns     (list[str] | None)

  Shapes:
    A. direct list passthrough — caller passes a Python list; pydantic accepts it
       directly. The BeforeValidator coercion is a no-op on this path.
    B. JSON-encoded string is coerced — caller passes a string like
       '["pdpc"]' or '[{"column":"case_name","op":"contains","value":"x"}]'.
       Before the patch, FastMCP's pydantic dispatch rejects with `list_type`;
       after the patch, `BeforeValidator(_coerce_json_list)` runs `json.loads`
       and the decoded list flows through normal validation.
    C. malformed JSON still raises `list_type` — caller passes a string that
       fails `json.loads`. The helper falls through unchanged and pydantic emits
       its canonical `list_type` error against the original string.

Dispatch path: each call goes through `mcp_client.call_tool(...)` so the param
hits FastMCP's real pydantic validation pipeline — the same one that broke in
the production Claude session. Direct `await search(...)` would bypass pydantic
entirely (the handler is a plain async function; the `Annotated[..., Field]`
metadata is only consumed at FastMCP dispatch time).

Shapes A/B assertion shape: `"list_type" not in str(error)` — any subsequent
domain error from the handler body (`unknown_database`, `upstream_unavailable`,
`unknown_column`, etc.) is acceptable; the regression is exclusively about
clearing pydantic's `list_type` gate.

Shape C assertion shape: `pytest.raises(ToolError, match="list_type")` — the
coercion helper is soft; malformed input must still surface the canonical
pydantic error verbatim.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.server import mcp

# Shape A/B handler bodies may issue upstream calls (e.g. for db metadata or
# table rows); we install a catch-all 503 in `datasette_client` so those reach
# `upstream_unavailable` instead of pytest-httpx's "unexpected request"
# assertion. Shape C short-circuits in pydantic and never reaches upstream.
pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    assert_all_requests_were_expected=False,
)


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    # Catch-all 503 — non-400, non-2xx so the retry-once path inside
    # _request_with_retry settles into upstream_unavailable for any handler
    # that reaches the network phase.
    httpx_mock.add_response(status_code=503, json={"error": "stub"}, is_reusable=True)
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


# ---------------------------------------------------------------------------
# Shape A — direct list passthrough (3 cases). MUST NOT surface list_type.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        pytest.param(
            "search",
            {"query": "appeal", "databases": ["pdpc"]},
            id="search.databases:direct_list",
        ),
        pytest.param(
            "query_table",
            {
                "database": "zeeker-judgements",
                "table": "judgments",
                "filters": [{"column": "case_name", "op": "contains", "value": "Law Society"}],
            },
            id="query_table.filters:direct_list",
        ),
        pytest.param(
            "query_table",
            {
                "database": "zeeker-judgements",
                "table": "judgments",
                "columns": ["case_name", "decision_date"],
            },
            id="query_table.columns:direct_list",
        ),
    ],
)
async def test_direct_list_passthrough(datasette_client, tool: str, args: dict) -> None:
    """Shape A: passing a real list still works — coercion is a no-op."""
    err_str = await _capture_error_str(tool, args)
    assert "list_type" not in err_str, (
        f"direct-list path must not surface a pydantic list_type error; got: {err_str!r}"
    )


# ---------------------------------------------------------------------------
# Shape B — JSON-encoded string is coerced (3 cases). MUST NOT surface
# list_type after the patch; before the patch, all three fail with list_type.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        pytest.param(
            "search",
            {"query": "appeal", "databases": '["pdpc"]'},
            id="search.databases:json_string",
        ),
        pytest.param(
            "query_table",
            {
                "database": "zeeker-judgements",
                "table": "judgments",
                "filters": '[{"column": "case_name", "op": "contains", "value": "Law Society"}]',
            },
            id="query_table.filters:json_string",
        ),
        pytest.param(
            "query_table",
            {
                "database": "zeeker-judgements",
                "table": "judgments",
                "columns": '["case_name", "decision_date"]',
            },
            id="query_table.columns:json_string",
        ),
    ],
)
async def test_json_string_coerced(datasette_client, tool: str, args: dict) -> None:
    """Shape B: JSON-encoded list strings must reach the handler body.

    Before the WR-260517-dvf patch these fail with a pydantic list_type error.
    After the patch the BeforeValidator decodes them and pydantic accepts the
    resulting list.
    """
    err_str = await _capture_error_str(tool, args)
    assert "list_type" not in err_str, (
        f"JSON-encoded string must be coerced past pydantic list_type validation; got: {err_str!r}"
    )


# ---------------------------------------------------------------------------
# Shape C — malformed JSON still raises list_type (3 cases). The helper falls
# through unchanged and pydantic emits its canonical list_type error.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        pytest.param(
            "search",
            {"query": "appeal", "databases": "[notvalid"},
            id="search.databases:malformed_json",
        ),
        pytest.param(
            "query_table",
            {
                "database": "zeeker-judgements",
                "table": "judgments",
                "filters": "[{bad json",
            },
            id="query_table.filters:malformed_json",
        ),
        pytest.param(
            "query_table",
            {
                "database": "zeeker-judgements",
                "table": "judgments",
                "columns": "[bad json",
            },
            id="query_table.columns:malformed_json",
        ),
    ],
)
async def test_malformed_json_still_raises_list_type(
    datasette_client, tool: str, args: dict
) -> None:
    """Shape C: malformed JSON still surfaces pydantic's standard list_type error.

    The coercion helper is a soft pre-step — never a new failure mode. Malformed
    input falls through to pydantic, which echoes the original string in the
    canonical list_type error message.
    """
    with pytest.raises(ToolError) as excinfo:
        async with Client(mcp) as client:
            await client.call_tool(tool, args)
    assert "list_type" in str(excinfo.value), (
        f"malformed JSON must still raise pydantic list_type error; got: {str(excinfo.value)!r}"
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _capture_error_str(tool: str, args: dict) -> str:
    """Dispatch `tool` with `args` through FastMCP and return the error string.

    Returns the empty string on success. Shapes A and B assert the captured
    string does NOT contain `list_type`; any other domain error is acceptable.
    """
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(tool, args)
            if getattr(result, "is_error", False):
                return str(result.content)
            return ""
    except ToolError as exc:
        return str(exc)
    except Exception as exc:
        return str(exc)
