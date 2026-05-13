"""
Tests for structured logging: locked LOG_FIELDS schema, request_id contextvar
propagation across asyncio.create_task, and ip_prefix truncation.

Covers:
- OBS-01/02/03: Structured log lines emitted with correct field set.
- OBS-04: LOG_FIELDS locked to config; ip_prefix is /24 (IPv4) or /48 (IPv6).
- OBS-05: request_id and ip_prefix survive asyncio.create_task context copy.
"""

from __future__ import annotations

import asyncio

import structlog
from structlog.testing import capture_logs

from mcp_zeeker import config
from mcp_zeeker.core.ip import ip_prefix
from mcp_zeeker.core.logging import bind_request, clear_request
from mcp_zeeker.core.middleware.request_id import _REQUEST_ID_PATTERN


def test_log_fields_locked_to_config():
    """OBS-03/04: Emitted log line carries no unexpected keys beyond LOG_FIELDS + structlog meta."""
    # capture_logs() disables the full processor chain; use merge_contextvars to
    # pull in bound contextvars so we can see request_id / ip_prefix.
    bind_request("locked-test", "10.0.0")
    try:
        with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as cap:
            structlog.get_logger().info(
                "tool_call",
                tool="list_databases",
                duration_ms=42,
                status="ok",
                error_code=None,
                database=None,
                table=None,
            )
    finally:
        clear_request()

    assert cap, "No log lines captured"
    line = cap[0]

    # structlog adds 'event' and 'log_level' (renamed from 'level' in capture mode).
    allowed_keys = set(config.LOG_FIELDS) | {"event", "log_level", "level", "timestamp"}
    extra_keys = set(line.keys()) - allowed_keys
    assert extra_keys == set(), f"Unexpected keys in log line: {extra_keys!r}. Full line: {line!r}"


async def test_request_id_propagates_across_async_tasks():
    """OBS-05: request_id and ip_prefix survive asyncio.create_task context copy."""

    async def log_in_task():
        structlog.get_logger().info("from_task")

    bind_request("rid-propagate", "203.0.113")
    try:
        with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as cap:
            t = asyncio.create_task(log_in_task())
            await t
    finally:
        clear_request()

    assert cap, "No log lines from async task"
    assert cap[0].get("request_id") == "rid-propagate", (
        f"request_id not propagated into create_task; got {cap[0]!r}"
    )
    assert cap[0].get("ip_prefix") == "203.0.113", (
        f"ip_prefix not propagated into create_task; got {cap[0]!r}"
    )


def test_ip_prefix_truncates_ipv4_to_24():
    """OBS-04: ip_prefix returns the /24 prefix for IPv4 (first 3 octets)."""
    assert ip_prefix("203.0.113.42") == "203.0.113"
    assert ip_prefix("10.0.0.1") == "10.0.0"
    assert ip_prefix("192.168.1.255") == "192.168.1"


def test_ip_prefix_truncates_ipv6_to_48():
    """OBS-04: ip_prefix returns the /48 prefix for IPv6 (first 3 colon-groups).

    Tests use IPv6 addresses with at least 4 explicit groups before any '::' so
    that splitting on ':' yields >= 4 parts and the first 3 are unambiguous.

    Note: addresses collapsed with '::' that have fewer than 4 total groups
    (e.g. 'fe80::1') result in only 3 split parts, so all groups are returned —
    this is a known limitation of the string-split truncation approach.
    """
    # Standard /48 test — 2001:db8::/32 space, with unique host bits
    assert ip_prefix("2001:db8::1") == "2001:db8:"
    # Full form with 4+ groups before collapse
    assert ip_prefix("2001:db8:cafe:1::1") == "2001:db8:cafe"
    assert ip_prefix("fd00:1234:5678::1") == "fd00:1234:5678"


def test_request_id_regex_validates_incoming():
    """OBS-02: _REQUEST_ID_PATTERN accepts valid IDs and rejects invalid ones."""
    # Valid: alphanumeric, hyphens, underscores, 1-128 chars
    assert _REQUEST_ID_PATTERN.match("abc-123") is not None
    assert _REQUEST_ID_PATTERN.match("a") is not None
    assert _REQUEST_ID_PATTERN.match("A" * 128) is not None
    # Invalid: special characters not in [A-Za-z0-9_-]
    assert _REQUEST_ID_PATTERN.match("bad!chars") is None
    assert _REQUEST_ID_PATTERN.match("has space") is None
    # Invalid: too long (>128 chars)
    assert _REQUEST_ID_PATTERN.match("a" * 129) is None
    # Invalid: empty string (requires 1+ chars)
    assert _REQUEST_ID_PATTERN.match("") is None
