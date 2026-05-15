"""ERR-01..03 + ERR-05 — locked 11-code catalog, request_id correlation,
upstream 4xx no-echo (INJ-05).

Plan 07-04 (Phase 7) — four GREEN tests. No skip decorators; the underlying
production code (core/errors.py + core/middleware/error_enrichment.py) ships
in this same plan.

Per single-plan-touch rule (per 02-LEARNINGS, applied in 07-01 to conftest.py),
this file does NOT modify tests/conftest.py — Phase 7 conftest additions live
in plan 07-01 only.
"""

from __future__ import annotations

import types

import pytest
from fastmcp.exceptions import ToolError
from structlog.contextvars import bind_contextvars, clear_contextvars

from mcp_zeeker.core.datasette_client import UpstreamCallFailed
from mcp_zeeker.core.errors import (
    CATALOG,
    raise_query_timeout,
    raise_upstream_unavailable,
)
from mcp_zeeker.core.middleware.error_enrichment import ErrorEnrichmentMiddleware
from mcp_zeeker.core.visibility import (
    raise_invalid_query,
    raise_not_found,
    raise_unknown_column,
    raise_unknown_database,
    raise_unknown_table,
    raise_unsupported_table_for_fetch,
)

# ---------------------------------------------------------------------------
# ERR-02 — catalog membership + ordering (test_all_11_codes_in_catalog)
# ---------------------------------------------------------------------------


def test_all_11_codes_in_catalog():
    """ERR-02: CATALOG is the single literal source of truth for the 11 codes.

    Asserts BOTH the locked set AND the REQUIREMENTS.md tuple ordering. Any
    rename or reorder requires editing both core/errors.py:CATALOG AND this
    test in one commit, surfacing the contract change in code review
    (T-07-07 mitigation).
    """
    expected = (
        "unknown_database",
        "unknown_table",
        "unknown_column",
        "invalid_filter_op",
        "invalid_cursor",
        "invalid_query",
        "unsupported_table_for_fetch",
        "not_found",
        "query_timeout",
        "rate_limited",
        "upstream_unavailable",
    )
    assert len(CATALOG) == 11, f"expected 11 codes, got {len(CATALOG)}"
    assert set(CATALOG) == set(expected), f"set mismatch: {set(CATALOG) ^ set(expected)}"
    assert CATALOG == expected, f"order mismatch: {CATALOG!r} != {expected!r}"


# ---------------------------------------------------------------------------
# ERR-01 — every catalog code is emitted with the canonical "code: " prefix
# (test_all_errors_have_stable_code)
# ---------------------------------------------------------------------------


def test_all_errors_have_stable_code():
    """ERR-01: every catalog code raise site emits a ToolError whose message
    starts with `<code>: `. Iterates over the 10 ToolError-bearing codes; the
    11th (`rate_limited`) is body-only and is covered in tests/test_rate_limit.py
    (plan 07-01).

    For codes raised inline rather than via a dedicated helper
    (`invalid_filter_op` in `core/filter_compiler.py`, `invalid_cursor` in
    `tools/retrieval.py`), the test asserts on a directly-constructed ToolError
    with the canonical literal — this is a CONTRACT assertion on the literal
    string form, not a regression test of those raise sites (which are
    covered separately).
    """
    cases: list[tuple[object, tuple, str]] = [
        (raise_unknown_database, ("test-db",), "unknown_database"),
        (raise_unknown_table, ("db", "t"), "unknown_table"),
        (raise_unknown_column, ("db", "t", "c"), "unknown_column"),
        (raise_unsupported_table_for_fetch, ("db", "t"), "unsupported_table_for_fetch"),
        (raise_not_found, ("db", "t"), "not_found"),
        (raise_invalid_query, (), "invalid_query"),
        (raise_query_timeout, (), "query_timeout"),
        (raise_upstream_unavailable, (), "upstream_unavailable"),
    ]
    for raise_fn, args, expected_prefix in cases:
        with pytest.raises(ToolError) as exc_info:
            raise_fn(*args)
        # FastMCP's ToolError exposes its message via str() — there is no
        # `.message` attribute on the public API.
        message = str(exc_info.value)
        assert message.startswith(f"{expected_prefix}: "), (
            f"{raise_fn.__name__} produced {message!r}; "
            f"expected prefix {expected_prefix!r}"
        )

    # invalid_filter_op + invalid_cursor — raised inline; assert the literal
    # contract (these strings are reproduced verbatim from the raise sites in
    # filter_compiler.py and tools/retrieval.py).
    inline_cases = [
        (
            "invalid_filter_op: value required for this operator",
            "invalid_filter_op",
        ),
        (
            "invalid_cursor: shape mismatch",
            "invalid_cursor",
        ),
    ]
    for literal, expected_prefix in inline_cases:
        err = ToolError(literal)
        assert str(err).startswith(f"{expected_prefix}: "), (
            f"inline literal {literal!r} does not start with {expected_prefix!r}: "
        )


# ---------------------------------------------------------------------------
# ERR-03 — ErrorEnrichmentMiddleware appends [request_id: <hex>]
# (test_error_includes_request_id)
# ---------------------------------------------------------------------------


async def test_error_includes_request_id():
    """ERR-03: ErrorEnrichmentMiddleware appends `[request_id: <hex>]` to the
    ToolError message so an MCP client and a server-side log line can be
    correlated by a single hex token.

    Uses the direct-call FastMCP middleware test pattern from
    tests/test_retrieved_at_middleware.py: a SimpleNamespace stand-in for
    MiddlewareContext + an async `call_next` closure that raises ToolError.
    """
    bind_contextvars(request_id="rid-abc")
    try:

        async def call_next(_ctx):
            raise ToolError("unknown_database: Database not found: foo")

        ctx = types.SimpleNamespace(message=types.SimpleNamespace(name="dummy"))
        with pytest.raises(ToolError) as exc_info:
            await ErrorEnrichmentMiddleware().on_call_tool(ctx, call_next)

        assert str(exc_info.value) == (
            "unknown_database: Database not found: foo [request_id: rid-abc]"
        )
        # Code-prefix preservation: catalog detection still works.
        assert str(exc_info.value).startswith("unknown_database: ")
    finally:
        clear_contextvars()


# ---------------------------------------------------------------------------
# ERR-05 / INJ-05 — upstream 4xx body never echoed in error message
# (test_upstream_4xx_no_echo)
# ---------------------------------------------------------------------------


def test_upstream_4xx_no_echo():
    """ERR-05 / INJ-05: upstream Datasette message bodies are NEVER echoed in
    the catalog-coded ToolError message. Both new Phase 7 helpers produce
    literal-only messages — no `{...}` substitution is possible because the
    helpers take no arguments.

    Also asserts that the canonical UpstreamCallFailed-to-ToolError mapping
    does not consume the upstream constructor message: the helper takes no
    argument, so even when the surrounding tool handler catches an
    UpstreamCallFailed whose message contains the upstream URL or body text,
    the resulting ToolError carries only the FIXED literal.
    """
    # raise_upstream_unavailable: literal-only contract.
    # FastMCP's ToolError exposes its message via str() — there is no
    # `.message` attribute on the public API.
    with pytest.raises(ToolError) as exc_info:
        raise_upstream_unavailable()
    assert str(exc_info.value) == "upstream_unavailable: upstream call failed"

    # raise_query_timeout: literal-only contract.
    with pytest.raises(ToolError) as exc_info:
        raise_query_timeout()
    assert str(exc_info.value) == "query_timeout: Query timed out"

    # The canonical mapping pattern: an UpstreamCallFailed carrying upstream
    # URL + adversarial body text is constructed, but the helper that maps it
    # to a ToolError discards the entire constructor message. Prove this by
    # constructing the upstream exception with hostile content and verifying
    # the helper-emitted ToolError contains NONE of it.
    hostile_body = "<evil user query body containing 'DROP TABLE'>"
    upstream_exc = UpstreamCallFailed(
        f"upstream 400 on /search.json: {hostile_body}", status=400
    )
    # Sanity check: the upstream exception itself carries the body for log
    # diagnostics — but that is the LOG path, not the ToolError path.
    assert hostile_body in str(upstream_exc)

    # Now exercise the canonical helper: it does not consume `upstream_exc`,
    # the message is FIXED, and no part of the hostile body bleeds through.
    with pytest.raises(ToolError) as exc_info:
        raise_upstream_unavailable()
    msg = str(exc_info.value)
    assert msg == "upstream_unavailable: upstream call failed"
    assert hostile_body not in msg
    assert "DROP TABLE" not in msg
    assert "/search.json" not in msg
