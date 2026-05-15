"""
Canonical Phase 7 error catalog (ERR-02) — single source of truth for the 11
locked error codes the MCP server emits.

Co-existence model (NOT migration):
    The pre-existing raise helpers in `core/visibility.py`
    (raise_unknown_database, raise_unknown_table, raise_unknown_column,
    raise_unsupported_table_for_fetch, raise_not_found, raise_invalid_query)
    and the inline `ToolError("invalid_filter_op: ...")` /
    `ToolError("invalid_cursor: ...")` raise sites in
    `core/filter_compiler.py` and `tools/retrieval.py` REMAIN where they are.
    They predate this module and are still the authoritative emission points
    for those codes. This module only ADDS new helpers + the catalog tuple.

Reasons this module exists (Phase 7 plan 07-04):
    (a) Provide one literal `CATALOG: tuple[str, ...]` containing every code
        the server may emit. The mechanical assertion
        `test_all_11_codes_in_catalog` in `tests/test_error_catalog.py`
        defends the locked-set property — adding or renaming a code without
        touching this tuple breaks the test (T-07-07 mitigation).
    (b) Provide canonical raise helpers for the two codes Phase 7 introduces
        outside the existing `visibility.py` family:
            - `raise_query_timeout()` for `httpx.TimeoutException` mapping
              (raise site lands in `core/datasette_client.py` in plan 07-05).
            - `raise_upstream_unavailable()` as the canonical string-form for
              the upstream-failure path (already raised inline as
              `UpstreamCallFailed -> ToolError("upstream_unavailable: ...")`
              in tool handlers; this helper is the SINGLE source of truth for
              the literal message string that the catalog test asserts on).
    (c) Name `rate_limited` in the catalog tuple even though it is NEVER a
        ToolError — it is emitted only by the ASGI 429 body in
        `core/middleware/rate_limit.py`. Including it here keeps the catalog
        a single literal source of truth across both ToolError paths and the
        ASGI short-circuit path.
    (d) Document where each code is RAISED so future maintainers can follow
        the trail without grepping the whole codebase.

Where each catalog code is raised (file references):
    unknown_database               → core/visibility.py:raise_unknown_database
    unknown_table                  → core/visibility.py:raise_unknown_table
    unknown_column                 → core/visibility.py:raise_unknown_column
                                     core/filter_compiler.py:115 (defense-in-depth)
    invalid_filter_op              → core/filter_compiler.py:129, 134, 138, 158, 168, 177, 191
    invalid_cursor                 → tools/retrieval.py (multiple sites)
    invalid_query                  → core/visibility.py:raise_invalid_query
                                     (raise sites in tools/search.py — Phase 4 / SEARCH-06)
    unsupported_table_for_fetch    → core/visibility.py:raise_unsupported_table_for_fetch
    not_found                      → core/visibility.py:raise_not_found
    query_timeout                  → core/errors.py:raise_query_timeout
                                     (raise site in core/datasette_client.py — Phase 7 plan 07-05)
    rate_limited                   → core/middleware/rate_limit.py (ASGI 429 body only)
    upstream_unavailable           → core/errors.py:raise_upstream_unavailable
                                     (also raised inline via UpstreamCallFailed → ToolError
                                     mapping in tools/retrieval.py:263, 493, 716 and
                                     tools/search.py:209)

INJ-05 / T-03-01 carry-forward:
    Both new helpers below produce FIXED literal messages. No `{variable}`
    substitution. No upstream body echo. No user input echo. The catalog test
    `test_upstream_4xx_no_echo` asserts this property mechanically.
"""

from __future__ import annotations

from typing import NoReturn

from fastmcp.exceptions import ToolError

# Order MUST match REQUIREMENTS.md ERR-02 ordering (preserved verbatim). The
# `test_all_11_codes_in_catalog` mechanical assertion checks BOTH the set
# membership AND the tuple ordering — any reorder requires editing both this
# constant AND the test in one commit, surfacing the contract change in code
# review (T-07-07 / Tampering / Repudiation mitigation).
CATALOG: tuple[str, ...] = (
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


def raise_query_timeout() -> NoReturn:
    """Single emission point for query_timeout errors (ERR-02, ERR-05).

    Triggered when an upstream Datasette request raises `httpx.TimeoutException`
    (Phase 7 plan 07-05 wires the catch in `core/datasette_client.py`). The
    canonical raise site is `_request_with_retry`, which catches
    `httpx.TimeoutException` BEFORE the generic `httpx.RequestError` branch
    and translates it into a distinct `QueryTimeoutError` exception that tool
    handlers map to this helper.

    INJ-05 / T-03-01 / T-07-08: the message is a FIXED literal. No URL,
    upstream body, query string, or user-supplied parameter is interpolated.
    Tested by `test_upstream_4xx_no_echo` in `tests/test_error_catalog.py`.
    """
    raise ToolError("query_timeout: Query timed out")


def raise_upstream_unavailable() -> NoReturn:
    """Single emission point for upstream_unavailable errors (ERR-02, ERR-05).

    Tool handlers already raise `ToolError("upstream_unavailable: ...")`
    inline at the catch site for `UpstreamCallFailed` (see
    `tools/retrieval.py:263, 493, 716` and `tools/search.py:209` — not
    migrated, since those sites add status-class context to the surrounding
    log line). This helper exists as the canonical string-form so the catalog
    test (`test_upstream_4xx_no_echo`) can assert on the EXACT literal
    message that proves no upstream body bleeds through.

    INJ-05 / T-03-01 / ERR-05 / T-07-08: the message is a FIXED literal.
    The `UpstreamCallFailed` exception's constructor argument (which may
    contain the upstream URL or the upstream's response body for diagnostic
    logging) is NEVER consumed by this helper — it is discarded entirely.
    Tested by `test_upstream_4xx_no_echo` in `tests/test_error_catalog.py`.
    """
    raise ToolError("upstream_unavailable: upstream call failed")
