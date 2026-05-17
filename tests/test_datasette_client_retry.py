"""
Unit tests for DatasetteClient._request_with_retry retry policy (D-16, ERR-04).

Tests verify:
- 2xx returns immediately, no sleep.
- 502 retries once with sleep in [0.25, 0.5], succeeds on second attempt.
- 503 retries once with sleep in [0.25, 0.5], succeeds on second attempt.
- 502 twice → UpstreamCallFailed (retry exhausted, no third attempt). [ERR-04]
- 503 twice → UpstreamCallFailed (retry exhausted, no third attempt). [ERR-04]
- 504 raises UpstreamCallFailed immediately without retry. [ERR-04]
- httpx.TimeoutException → QueryTimeoutError immediately, no retry. [ERR-04 / Q-OPEN-3]

Phase 7 plan 07-05 added the four ERR-04 exhaustion + timeout cases. The names
match VALIDATION.md § Per-Task Verification Map exactly so a verifier grep stays
GREEN (test_502_twice_raises, test_503_twice_raises, test_504_raises_immediately,
test_timeout_raises_query_timeout_error).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import (
    DatasetteClient,
    QueryTimeoutError,
    UpstreamCallFailed,
)


@pytest.fixture
def client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:
    """Return a DatasetteClient backed by a real AsyncClient that pytest-httpx patches."""
    return DatasetteClient(httpx.AsyncClient(base_url=config.UPSTREAM_URL))


async def test_2xx_returns_immediately(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """200 response: _request_with_retry returns without sleeping."""
    httpx_mock.add_response(status_code=200, json={"ok": True})

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client._request_with_retry("GET", "/test.json")

    assert resp.status_code == 200
    mock_sleep.assert_not_called()
    assert len(httpx_mock.get_requests()) == 1


async def test_502_retries_once_then_succeeds(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """502 on first attempt: sleep once, retry, succeed on second attempt."""
    httpx_mock.add_response(status_code=502)
    httpx_mock.add_response(status_code=200, json={"ok": True})

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client._request_with_retry("GET", "/test.json")

    assert resp.status_code == 200
    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    assert 0.25 <= sleep_arg <= 0.50, f"sleep arg {sleep_arg!r} out of range [0.25, 0.50]"
    assert len(httpx_mock.get_requests()) == 2


async def test_503_retries_once_then_succeeds(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """503 on first attempt: sleep once, retry, succeed on second attempt (symmetric with 502)."""
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(status_code=200, json={"ok": True})

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        resp = await client._request_with_retry("GET", "/test.json")

    assert resp.status_code == 200
    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    assert 0.25 <= sleep_arg <= 0.50, f"sleep arg {sleep_arg!r} out of range [0.25, 0.50]"
    assert len(httpx_mock.get_requests()) == 2


async def test_504_raises_immediately(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """504: raises UpstreamCallFailed immediately, no sleep, only one request made.

    ERR-04 / VALIDATION.md: name aligned with Per-Task Verification Map (was
    `test_504_raises_immediately_no_retry` pre-07-05; the `_no_retry` suffix
    was dropped so a verifier grep stays GREEN).
    """
    httpx_mock.add_response(status_code=504)

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UpstreamCallFailed, match="upstream 504"):
            await client._request_with_retry("GET", "/test.json")

    mock_sleep.assert_not_called()
    assert len(httpx_mock.get_requests()) == 1


async def test_502_twice_raises(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """502 on both attempts: sleep once between, then raise (no third attempt).

    ERR-04 retry-exhaustion: after one retry (sleep + jitter in [0.25, 0.50])
    the second 502 falls through to the post-loop `UpstreamCallFailed("upstream
    retry exhausted on ...")`. Asserts:
    - exactly two upstream attempts (no third)
    - exactly one sleep between them
    - sleep arg is in [0.25, 0.50] (D-16 jitter window)
    - exception message contains "retry exhausted" so callers can disambiguate
      from a fresh-failure UpstreamCallFailed.
    """
    httpx_mock.add_response(status_code=502)
    httpx_mock.add_response(status_code=502)

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UpstreamCallFailed, match="retry exhausted"):
            await client._request_with_retry("GET", "/test.json")

    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    assert 0.25 <= sleep_arg <= 0.50, f"sleep arg {sleep_arg!r} out of range [0.25, 0.50]"
    assert len(httpx_mock.get_requests()) == 2


async def test_503_twice_raises(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """503 on both attempts: same exhaustion contract as 502 (symmetric)."""
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(status_code=503)

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UpstreamCallFailed, match="retry exhausted"):
            await client._request_with_retry("GET", "/test.json")

    assert mock_sleep.call_count == 1
    sleep_arg = mock_sleep.call_args[0][0]
    assert 0.25 <= sleep_arg <= 0.50, f"sleep arg {sleep_arg!r} out of range [0.25, 0.50]"
    assert len(httpx_mock.get_requests()) == 2


async def test_timeout_raises_query_timeout_error(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """httpx.ReadTimeout (subclass of TimeoutException) → QueryTimeoutError.

    ERR-04 / Q-OPEN-3 / 07-05: the new TimeoutException catch branch in
    _request_with_retry MUST raise QueryTimeoutError (a subclass of
    UpstreamCallFailed) so tool handlers can map to the `query_timeout`
    catalog code via isinstance(exc, QueryTimeoutError).

    Per D-16 (no retry on transport errors), there is NO retry — the catch
    branch raises immediately, mirroring the existing httpx.RequestError
    branch.
    """
    httpx_mock.add_exception(httpx.ReadTimeout("simulated timeout"))

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(QueryTimeoutError):
            await client._request_with_retry("GET", "/test.json")

    mock_sleep.assert_not_called()


async def test_sql_interrupted_400_raises_query_timeout(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """WR-260517-bki: Datasette 400 with {"title": "SQL Interrupted"} → QueryTimeoutError.

    Live incident: query_table against zeeker-judgements.judgments_fragments
    (81k rows, missing upstream index on judgment_id) returned HTTP 400 with
    body {"ok": false, "error": "SQL query took too long...", "status": 400,
    "title": "SQL Interrupted"}. The catch-all was mapping this to a bare
    UpstreamCallFailed → `upstream_unavailable`; correct catalog code is
    `query_timeout`. Subclass relationship preserved so existing
    `except UpstreamCallFailed:` handlers continue to catch this path.
    """
    httpx_mock.add_response(
        status_code=400,
        json={
            "ok": False,
            "error": "SQL query took too long. The time limit is controlled by ...",
            "status": 400,
            "title": "SQL Interrupted",
        },
    )

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(QueryTimeoutError) as excinfo:
            await client._request_with_retry("GET", "/zeeker-judgements/judgments_fragments.json")

    assert isinstance(excinfo.value, UpstreamCallFailed)
    mock_sleep.assert_not_called()
    assert len(httpx_mock.get_requests()) == 1


async def test_vanilla_400_still_raises_upstream_call_failed(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """Vanilla 400 (no SQL-Interrupted marker) must still raise bare UpstreamCallFailed.

    WR-260517-bki scope check: the new branch must fire ONLY for status 400
    AND `body.get("title") == "SQL Interrupted"`. A 400 without the marker
    (or with a different title) falls through to the existing catch-all.
    """
    httpx_mock.add_response(status_code=400, json={"error": "some other 400 reason"})

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UpstreamCallFailed) as excinfo:
            await client._request_with_retry("GET", "/test.json")

    assert not isinstance(excinfo.value, QueryTimeoutError)
    mock_sleep.assert_not_called()
    assert len(httpx_mock.get_requests()) == 1


async def test_non_json_400_still_raises_upstream_call_failed(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """A 400 with a non-JSON body must NOT trip the SQL-Interrupted branch.

    WR-260517-bki defensive guard: the `resp.json()` call inside the new
    branch is wrapped in try/except so a non-JSON 400 body falls through to
    the catch-all UpstreamCallFailed instead of bubbling JSONDecodeError.
    """
    httpx_mock.add_response(status_code=400, content=b"<html>Bad Request</html>")

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UpstreamCallFailed) as excinfo:
            await client._request_with_retry("GET", "/test.json")

    assert not isinstance(excinfo.value, QueryTimeoutError)
    mock_sleep.assert_not_called()
    assert len(httpx_mock.get_requests()) == 1
