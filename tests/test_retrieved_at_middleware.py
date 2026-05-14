"""
RetrievedAtMiddleware unit tests — D6-09 / D6-10 / D6-11.

Coverage:
- on_call_tool binds `tool_started_at` on entry and resets on exit (success).
- on_call_tool resets `tool_started_at` on exit even when call_next raises.
- get_tool_started_at() returns a wallclock-now fallback and emits a DEBUG
  `retrieved_at_fallback` event when the contextvar is unbound.
"""

from __future__ import annotations

import pytest


async def test_middleware_binds_on_entry_resets_on_exit():
    """D6-09 success path: token bound on entry, reset on exit."""
    import types
    from datetime import UTC, datetime

    from mcp_zeeker.core.middleware.retrieved_at import (
        RetrievedAtMiddleware,
        tool_started_at,
    )

    # Pre-condition: contextvar unbound at test entry.
    assert tool_started_at.get(None) is None

    captured: dict = {}

    async def call_next(_ctx):
        captured["bound"] = tool_started_at.get(None)
        return "ok"

    ctx = types.SimpleNamespace(message=types.SimpleNamespace(name="dummy"))

    result = await RetrievedAtMiddleware().on_call_tool(ctx, call_next)
    assert result == "ok"

    bound = captured["bound"]
    assert isinstance(bound, datetime)
    assert bound.tzinfo is UTC
    # Within 5 seconds of wallclock now — covers test-machine latency
    delta = abs((datetime.now(tz=UTC) - bound).total_seconds())
    assert delta < 5, f"bound time {bound} differs from now by {delta}s"
    # After the middleware returns, the contextvar is reset to default (None)
    assert tool_started_at.get(None) is None


async def test_middleware_resets_on_exception():
    """D6-09 exception path: token reset even when call_next raises."""
    import types

    from mcp_zeeker.core.middleware.retrieved_at import (
        RetrievedAtMiddleware,
        tool_started_at,
    )

    # Pre-condition: contextvar unbound at test entry.
    assert tool_started_at.get(None) is None

    async def call_next(_ctx):
        raise RuntimeError("boom")

    ctx = types.SimpleNamespace(message=types.SimpleNamespace(name="dummy"))

    with pytest.raises(RuntimeError, match="boom"):
        await RetrievedAtMiddleware().on_call_tool(ctx, call_next)

    # Critical: contextvar reset even on exception path
    assert tool_started_at.get(None) is None


def test_get_tool_started_at_fallback_emits_debug_log():
    """D6-11 safety-net: unbound contextvar → wallclock-now + DEBUG log event.

    Uses `structlog.testing.capture_logs()` (mirroring the established
    `test_metadata_gap_logged` pattern at tests/test_metadata_cache.py:147)
    rather than pytest's `caplog`, because structlog's BoundLogger pipeline
    is the source of truth for our event stream and capture_logs intercepts
    the rendered event dict directly.
    """
    import contextvars
    from datetime import UTC, datetime

    import structlog.testing

    from mcp_zeeker.core.middleware.retrieved_at import (
        get_tool_started_at,
        tool_started_at,
    )

    def _body():
        # Pre-condition: fresh context has the default None binding
        assert tool_started_at.get(None) is None

        with structlog.testing.capture_logs() as cap_logs:
            got = get_tool_started_at()

        assert isinstance(got, datetime)
        assert got.tzinfo is UTC
        delta = abs((datetime.now(tz=UTC) - got).total_seconds())
        assert delta < 5, f"fallback time differs from now by {delta}s"

        events = [e for e in cap_logs if e.get("event") == "retrieved_at_fallback"]
        assert events, (
            "expected at least one structlog event named 'retrieved_at_fallback'; "
            f"got {[e.get('event') for e in cap_logs]}"
        )
        # Defense in depth: the event payload binds only `reason` — no tool
        # name, no client identifier, no timestamp content (T-06-06 mitigation).
        assert events[0].get("reason") == "middleware not bound"

    contextvars.copy_context().run(_body)
