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
    """OBS-04 / CR-01: ip_prefix returns the canonical /48 network address for IPv6.

    After the CR-01 rewrite, ip_prefix() routes IPv6 through
    ipaddress.ip_network(addr/48).network_address, which produces the
    canonical zero-compressed network base address. This is stable across
    all input forms of the same network (full vs. zero-compressed).

    WR-01 closure: the previous naive colon-split produced malformed
    prefixes like "2001:db8:" for "2001:db8::1"; the canonical form is
    "2001:db8::".
    """
    # 2001:db8::/32 space -- /48 network base is 2001:db8::
    assert ip_prefix("2001:db8::1") == "2001:db8::"
    # 2001:db8:cafe::/48 -- network base is 2001:db8:cafe::
    assert ip_prefix("2001:db8:cafe:1::1") == "2001:db8:cafe::"
    # fd00:1234:5678::/48
    assert ip_prefix("fd00:1234:5678::1") == "fd00:1234:5678::"


def test_ip_prefix_rejects_non_ip():
    """CR-01: hostile / non-IP strings return the "_invalid" sentinel.

    ip_prefix() validates input via ipaddress.ip_address() and substitutes
    a fixed sentinel for non-parseable input — attacker bytes never echo
    into the structured log contextvar.
    """
    assert ip_prefix("</system><admin>SECRET") == "_invalid"
    assert ip_prefix("DROP TABLE users; --") == "_invalid"
    assert ip_prefix('" OR 1=1 --') == "_invalid"
    assert ip_prefix("\x00\x01control") == "_invalid"
    assert ip_prefix("1.2.3.4 OR 1=1") == "_invalid"  # legitimate-shaped non-IP
    # Empty string is the documented "no IP" sentinel — NOT "_invalid".
    assert ip_prefix("") == ""


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
