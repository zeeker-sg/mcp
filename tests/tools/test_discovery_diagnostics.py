"""Offline regression tests for attributed database probes through the MCP tool."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import structlog
from structlog.testing import capture_logs

from mcp_zeeker import app as app_module
from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatabaseSummary, DatasetteClient
from mcp_zeeker.tools import discovery

_JUDGEMENTS = "zeeker-judgements"
_REQUEST_ID = "catalog-probe-test"
_SECRET = "upstream-stub-secret-do-not-echo"


async def _catalog_response(asgi_client, monkeypatch, upstream):
    """Run the real streamable HTTP ASGI path without a socket or real upstream."""
    monkeypatch.setattr(config, "FTS_WARMER_ENABLED", False)
    monkeypatch.setattr(
        app_module,
        "build_http_client",
        lambda: httpx.AsyncClient(
            base_url="https://catalog.test",
            transport=httpx.MockTransport(upstream),
        ),
    )
    async with app_module.app.router.lifespan_context(app_module.app):
        response = await asgi_client.post(
            "/mcp/",
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
                "X-Request-Id": _REQUEST_ID,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "list_databases", "arguments": {}},
            },
        )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == _REQUEST_ID
    if response.headers["content-type"].startswith("text/event-stream"):
        payload = json.loads(
            next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
        )
    else:
        payload = response.json()
    return payload["result"]


def _probe_logs(logs):
    return {
        event["database"]: event for event in logs if event["event"] == "discovery_database_probe"
    }


async def test_slow_judgements_does_not_block_healthy_siblings(asgi_client, monkeypatch):
    """All probes must actually overlap; a slow child cannot prevent sibling diagnostics."""
    monkeypatch.setattr(discovery, "_DATABASE_DISCOVERY_TIMEOUT_S", 0.15)
    started = []
    all_started = asyncio.Event()
    judgement_stopped = asyncio.Event()
    never_set = asyncio.Event()

    async def upstream(request):
        name = request.url.path.removeprefix("/").removesuffix(".json")
        started.append(name)
        if len(started) == len(config.ALLOWED_DATABASES):
            all_started.set()
        if name == _JUDGEMENTS:
            try:
                await never_set.wait()
            finally:
                judgement_stopped.set()
        await all_started.wait()
        return httpx.Response(200, json={"tables": [{"name": "test_table"}]})

    with capture_logs() as logs:
        monkeypatch.setattr(discovery, "log", structlog.get_logger())
        result = await _catalog_response(asgi_client, monkeypatch, upstream)

    assert started[0] == _JUDGEMENTS
    assert set(started) == set(config.ALLOWED_DATABASES)
    assert judgement_stopped.is_set()
    assert result["isError"]
    message = result["content"][0]["text"]
    assert message.startswith("upstream_unavailable: Database catalog not returned.")
    assert f"Failed: {_JUDGEMENTS} (timed out)." in message
    assert f"Responded: {', '.join(config.ALLOWED_DATABASES[1:])}." in message
    assert f"[request_id: {_REQUEST_ID}]" in message
    events = _probe_logs(logs)
    assert list(events) == list(config.ALLOWED_DATABASES)
    assert all(event["log_level"] == "warning" for event in events.values())
    assert events[_JUDGEMENTS]["failure_type"] == "TimeoutError"
    assert events[_JUDGEMENTS]["outcome"] == "failed"
    assert all(events[name]["outcome"] == "ok" for name in config.ALLOWED_DATABASES[1:])
    assert all(
        event["request_id"] == _REQUEST_ID and event["elapsed_ms"] >= 0 for event in events.values()
    )


@pytest.mark.parametrize("total_failure", [False, True])
async def test_every_failure_is_named_and_sensitive_upstream_data_is_redacted(
    asgi_client, monkeypatch, total_failure
):
    completed = []

    async def upstream(request):
        name = request.url.path.removeprefix("/").removesuffix(".json")
        completed.append(name)
        if name == _JUDGEMENTS:
            # Covers a secret in the underlying exception, not just in an ignored body.
            raise httpx.ReadTimeout(f"Bearer {_SECRET}?sql={_SECRET}", request=request)
        if name == "pdpc":
            return httpx.Response(200, json={"tables": _SECRET})
        if total_failure:
            return httpx.Response(504, text=_SECRET)
        return httpx.Response(200, json={"tables": []})

    with capture_logs() as logs:
        monkeypatch.setattr(discovery, "log", structlog.get_logger())
        result = await _catalog_response(asgi_client, monkeypatch, upstream)

    assert len(completed) == len(config.ALLOWED_DATABASES)
    assert result["isError"]
    message = result["content"][0]["text"]
    assert "Failed: zeeker-judgements (timed out), pdpc (invalid upstream response)" in message
    assert f"[request_id: {_REQUEST_ID}]" in message
    if total_failure:
        for name in config.ALLOWED_DATABASES[2:]:
            assert f"{name} (upstream unavailable)" in message
        assert "Responded: none." in message
    else:
        assert f"Responded: {', '.join(config.ALLOWED_DATABASES[2:])}." in message
    assert _SECRET not in repr(result)
    assert _SECRET not in repr(logs)
    events = _probe_logs(logs)
    assert list(events) == list(config.ALLOWED_DATABASES)
    assert all(event["log_level"] == "warning" for event in events.values())
    assert events[_JUDGEMENTS]["failure_type"] == "QueryTimeoutError"
    assert events[_JUDGEMENTS]["cause_type"] == "ReadTimeout"
    assert events["pdpc"]["failure_type"] == "ValidationError"
    if total_failure:
        assert all(events[name]["upstream_status"] == 504 for name in config.ALLOWED_DATABASES[2:])


async def test_healthy_catalog_keeps_existing_envelope_and_order(asgi_client, monkeypatch):
    async def upstream(request):
        return httpx.Response(200, json={"tables": [{"name": "public_example"}]})

    result = await _catalog_response(asgi_client, monkeypatch, upstream)

    assert not result.get("isError", False)
    envelope = result["structuredContent"]
    assert set(envelope) == {"data", "provenance", "pagination"}
    assert [row["name"] for row in envelope["data"]] == list(config.ALLOWED_DATABASES)
    assert all(row["table_count"] == 1 for row in envelope["data"])
    assert all(
        set(row) == {"name", "description", "table_count", "license", "license_url"}
        for row in envelope["data"]
    )
    assert envelope["provenance"]["source"] == "data.zeeker.sg"
    assert envelope["provenance"]["license"] == config.LICENSE_MIXED


async def test_caller_cancellation_drains_all_probes(monkeypatch):
    started, stopped = set(), set()
    all_started = asyncio.Event()
    never_set = asyncio.Event()

    async def get_database(_self, name):
        started.add(name)
        if len(started) == len(config.ALLOWED_DATABASES):
            all_started.set()
        try:
            await never_set.wait()
            return DatabaseSummary(tables=[])
        finally:
            stopped.add(name)

    monkeypatch.setattr(DatasetteClient, "get_database", get_database)
    async with httpx.AsyncClient(trust_env=False) as http:
        token = DatasetteClient.bind(DatasetteClient(http))
        try:
            task = asyncio.create_task(discovery.list_databases())
            try:
                await asyncio.wait_for(all_started.wait(), timeout=1)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        finally:
            DatasetteClient.reset(token)
    assert started == stopped == set(config.ALLOWED_DATABASES)
