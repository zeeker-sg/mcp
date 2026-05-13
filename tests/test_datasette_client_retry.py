"""
Unit tests for DatasetteClient._request_with_retry retry policy (D-16).

Tests verify:
- 2xx returns immediately, no sleep.
- 502 retries once with sleep in [0.25, 0.5], succeeds on second attempt.
- 503 retries once with sleep in [0.25, 0.5], succeeds on second attempt.
- 504 raises UpstreamCallFailed immediately without retry.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_httpx

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient, UpstreamCallFailed


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


async def test_504_raises_immediately_no_retry(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    """504: raises UpstreamCallFailed immediately, no sleep, only one request made."""
    httpx_mock.add_response(status_code=504)

    with patch.object(asyncio, "sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(UpstreamCallFailed, match="upstream 504"):
            await client._request_with_retry("GET", "/test.json")

    mock_sleep.assert_not_called()
    assert len(httpx_mock.get_requests()) == 1
