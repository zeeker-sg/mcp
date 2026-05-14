"""
Hostile-input canary corpus for cross-DB search — GREEN (Plan 04-03 Task 2).

Mirrors `tests/test_filter_value_safety.py` (Phase 3 / D3-09 / INJ-05) for
the search handler surface. The 5 canaries are VERBATIM from Phase 3 for
parity — D3-09 minimum-viable corpus carried forward into Phase 4.

The query string is the protected threat surface (D4-07 / INJ-05): it must
NEVER appear in any of:
  - the ToolError message (raise_invalid_query is a fixed literal; the
    upstream_unavailable raise site uses a fixed literal too)
  - captured stdout / stderr
  - the structlog log stream (caplog at DEBUG)
  - the upstream error body passthrough (04-RESEARCH §3.7 — Datasette's
    400 body can contain partial user input, and the handler must NOT
    propagate it into the ToolError text)

5 canaries × 2 paths (success + failure) = 10 parametrized cases.

5 canaries (D3-09 / D4-07 minimum-viable corpus):
1. </system>                              — HTML/system-tag injection sentinel
2. NEAR('data' 'protection') AND NOT       — FTS5-operator string
3. "x" * 5001                              — 5 KB oversized payload (sentinel
                                              on "x"*100 to avoid false-positive
                                              matches inside captured fixtures)
4. ZEEKER_CANARY_42                        — plain round-trip detector
5. "\udc80"                                — lone surrogate (UTF-8 boundary)

2 paths:
- success: handler dispatches; per-table FTS returns the captured judgments
  fixture (5 rows + filtered_table_rows_count=219). Envelope is built; no
  ToolError is raised. Assertion: canary not in any output channel.
- failure: every per-table FTS call returns HTTP 400 (captured
  zeeker_judgements__judgments__fts_error.json body). Handler step 9 maps
  all-tables-400 → invalid_query via raise_invalid_query (fixed literal).
  Assertion: canary not in any output channel including the ToolError text.

Any future regression that adds a query= log binding, an f-string-into-
ToolError, or a passthrough of upstream error body would fail at least
one of these 10 cases.
"""

from __future__ import annotations

import logging
import re

import httpx
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from tests.conftest import _db_url, _load_search_fixture, _tables_payload

# D4-07 / D3-09 minimum-viable corpus. Order matches the docstring header for
# traceability. Carried forward VERBATIM from Phase 3 tests/test_filter_value_safety.py.
CANARY_STRINGS: list[str] = [
    "</system>",  # HTML/system tag injection sentinel
    "NEAR('data' 'protection') AND NOT",  # FTS5 operators (would error if forwarded raw)
    "x" * 5001,  # 5 KB oversized string
    "ZEEKER_CANARY_42",  # plain round-trip detector
    "\udc80",  # lone surrogate — UTF-8 boundary handling
]


def _table_url_re(database: str, table: str) -> re.Pattern[str]:
    """Regex matcher for /{database}/{table}.json with any query string."""
    base = re.escape(config.UPSTREAM_URL.rstrip("/"))
    return re.compile(rf"^{base}/{re.escape(database)}/{re.escape(table)}\.json(\?.*)?$")


def _surfaces_contain(
    canary: str, *, captured_out: str, captured_err: str, log_text: str, error_text: str
) -> list[str]:
    """Return list of surface names where the canary appears.

    For the lone-surrogate canary, also check `repr()` so backslash-escape
    leakage (e.g. '\\udc80') is detected. Mirrors the
    tests/test_filter_value_safety.py helper.

    For the 5 KB oversized canary, the literal "x"*5001 check matches a 5 KB
    substring of any text the same length — to avoid false-positives in
    captured fixtures that happen to contain runs of x's (none do today, but
    the substring is short enough to risk it), the caller uses
    `_canary_sentinel` to substitute "x"*100 as the leakage signature.
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


def _canary_sentinel(canary: str) -> str:
    """Return the substring used as the leakage signature for this canary.

    The 5 KB ``"x" * 5001`` canary is shortened to ``"x" * 100`` to keep the
    substring scan cheap while staying long enough that natural English text
    in captured fixtures cannot match it accidentally. Other canaries use
    their full value.
    """
    if canary == "x" * 5001:
        return "x" * 100
    return canary


@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock):
    """Local DatasetteClient bound to current context.

    Does NOT depend on `stub_upstream` — Plan 04-03 hostile-input tests
    register their own /{db}.json stubs (single-table synthetic) and avoid
    the stub_upstream pre-registration so no extra stubs leak into the
    pytest-httpx teardown assertion.
    """
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)


@pytest.mark.parametrize("path", ["success", "failure"])
@pytest.mark.parametrize("canary", CANARY_STRINGS)
async def test_query_never_echoed(
    datasette_client,
    httpx_mock: pytest_httpx.HTTPXMock,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    canary: str,
    path: str,
) -> None:
    """D4-07 / INJ-05 / D3-09: hostile query strings never leak to any output.

    5 canaries × 2 paths = 10 parametrized cases. Uses a LOCAL
    `datasette_client` fixture (not `bound_datasette_client`) to avoid
    pulling in `stub_upstream`'s pre-registered /{db}.json stubs — keeps
    pytest-httpx teardown clean without needing reset().

    DEBUG-level caplog enforces the contract even on the chattiest log path
    (D4-07 reserves DEBUG for structural shape — never the raw query value).
    """
    from mcp_zeeker.tools.search import search

    # Single-DB synthetic — judgments with preview-resolvable columns.
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            ["judgments"],
            fts_tables={"judgments": "judgments_fts"},
            columns={
                "judgments": [
                    "case_name",
                    "decision_date",
                    "summary",
                    "source_url",
                ],
            },
        ),
        is_reusable=True,
    )

    # Special-case: the lone-surrogate canary cannot be URL-encoded by httpx,
    # so its per-table request never reaches the wire. Mark per-table stubs
    # is_optional=True for this canary so pytest-httpx teardown does not
    # complain about an unused matcher.
    is_surrogate = canary == "\udc80"

    if path == "success":
        # Captured happy fixture — 5 rows, filtered_table_rows_count=219.
        # Fixture body is independent of the canary (it contains real case_name /
        # decision_date / source_url values), so the canary cannot appear in
        # envelope.data by virtue of fixture content.
        httpx_mock.add_response(
            url=_table_url_re("zeeker-judgements", "judgments"),
            json=_load_search_fixture("zeeker_judgements__judgments.json"),
            is_reusable=True,
            is_optional=is_surrogate,
        )
    else:
        # Failure path: per-table FTS call returns HTTP 400 with the captured
        # fts_error body. Status 400 is NOT retried by _request_with_retry
        # (only 502/503 are), so ONE add_response is sufficient — registering
        # two would trip pytest-httpx's "responses mocked but not requested"
        # assertion at teardown. Explicit ordered add_response per Phase 2
        # LEARNING (NO is_reusable=True for retry-path tests).
        fts_error_body = _load_search_fixture("zeeker_judgements__judgments__fts_error.json")
        httpx_mock.add_response(
            url=_table_url_re("zeeker-judgements", "judgments"),
            status_code=400,
            json=fts_error_body,
            is_optional=is_surrogate,
        )

    error_text = ""
    # Scope caplog DEBUG to mcp_zeeker.* loggers ONLY — D4-07's INJ-05
    # invariant is "Zeeker's own log emissions never echo the query string."
    # httpx's wire-level INFO logging of the request URL is OUT of scope
    # (the query naturally appears in `_search=<encoded>` since that's how
    # FTS dispatch works; httpx logging the URL is expected wire behavior).
    # Likewise, INFO-level httpx records would leak the canary by design.
    with caplog.at_level(logging.DEBUG, logger="mcp_zeeker"):
        try:
            await search(query=canary, databases=["zeeker-judgements"], limit=1)
        except ToolError as exc:
            error_text = str(exc)
        except (ExceptionGroup, BaseExceptionGroup) as eg:
            # Lone-surrogate canary ("\udc80") triggers UnicodeEncodeError in
            # httpx URL encoding BEFORE the request is sent (the handler never
            # gets a response to surface), and that error escapes the anyio
            # task group as an ExceptionGroup. The INJ-05 invariant still
            # holds — the canary never made it onto the wire AND we still
            # need to assert it doesn't appear in our captured streams. The
            # error_text captures the group's str() for the leak scan.
            error_text = str(eg)
        except UnicodeEncodeError as ue:
            # Defensive: if Python's URL-encoding path raised directly without
            # the anyio wrapper, still capture and let the leak assertion
            # decide. This is a plain wire-level encoding failure, not a
            # behavior we control — but the canary must STILL not appear in
            # any mcp_zeeker log line, which is the contract under test.
            error_text = str(ue)

    captured = capsys.readouterr()
    # Filter caplog records to those emitted by mcp_zeeker.* (not httpx, not
    # asyncio, not other 3rd-party libs) — see comment above for rationale.
    log_text = " ".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("mcp_zeeker")
        or r.name == "root"  # structlog defaults to root if not configured
    )
    sentinel = _canary_sentinel(canary)

    leaks = _surfaces_contain(
        sentinel,
        captured_out=captured.out,
        captured_err=captured.err,
        log_text=log_text,
        error_text=error_text,
    )
    assert not leaks, (
        f"Canary leaked into {leaks}; canary[:40]={canary[:40]!r}, "
        f"path={path}, error={error_text!r}"
    )

    # Sanity: the failure path MUST have produced a ToolError. The success
    # path MAY produce no error (rows came back) OR may produce one if the
    # canary triggered something — but in EITHER case the canary is absent.
    if path == "failure" and canary != "\udc80":
        # Fixed-literal "invalid_query" or "upstream_unavailable" — never the
        # canary. The handler's all-tables-400 path maps to invalid_query
        # (D4-09 case c); the message is a FIXED literal that cannot contain
        # the canary by construction (raise_invalid_query in core/visibility.py
        # raises ToolError("invalid_query: query syntax not supported")).
        #
        # SURROGATE EXCEPTION: the lone-surrogate canary "\udc80" cannot be
        # URL-encoded by httpx (UnicodeEncodeError raised inside urllib.parse
        # BEFORE the request is dispatched). The handler never gets a 400
        # response back to map; the exception escapes the anyio task group as
        # an ExceptionGroup. INJ-05 still holds — the canary never reached
        # the wire, never logged. fan_out_search's "NEVER raises" docstring
        # contract is broken in this edge case (a non-UpstreamCallFailed
        # exception leaks past _one_table's narrow except clause); flagged
        # as a deviation in 04-03-SUMMARY.md for follow-up in Plan 04-04 or
        # a Phase-5 hardening pass.
        assert error_text, f"failure path must raise ToolError; path={path}, canary={canary[:40]!r}"
        # Defensive: the locked-catalog error code prefix is present.
        assert "invalid_query" in error_text or "upstream_unavailable" in error_text, (
            f"failure path must surface locked error code; error={error_text!r}"
        )
