"""
RetrievedAtMiddleware — capture start-of-tool-call wallclock UTC into a ContextVar.

Implements D6-09 (FastMCP middleware bound on every `on_call_tool` entry,
reset on exit — success AND exception paths), D6-10 (contextvar-based capture
keeps the timestamp out of the handler signature), D6-11 (DEBUG `retrieved_at_fallback`
log + wallclock-now fallback when the contextvar is unbound — safety net for
direct-handler-call unit tests that bypass the middleware).

FastMCP FIFO middleware ordering requirement: this middleware MUST be added
FIRST in `server.py` so the timestamp is captured on every call that reaches
the handler. Plan 06-02 wires the `mcp.add_middleware(...)` call; Plan 06-01
ships the helper module only (D-21 single-source-of-truth — middleware module
does NOT self-register).

Pattern source: `core/middleware/access_log.py` (FastMCP `on_call_tool` shape +
structlog logger) and `core/metadata_cache.py:21-32` (ContextVar declaration +
set/reset lifecycle).
"""

from __future__ import annotations

import contextvars
from datetime import UTC, datetime

import structlog
from fastmcp.server.middleware import Middleware, MiddlewareContext

log = structlog.get_logger()

# D6-10: ContextVar storing the start-of-tool-call wallclock UTC datetime.
# Default None — `get_tool_started_at()` distinguishes the unbound case
# (DEBUG log + wallclock-now fallback) from the bound case (return the
# token value as-is).
tool_started_at: contextvars.ContextVar[datetime | None] = contextvars.ContextVar(
    "tool_started_at", default=None
)


def get_tool_started_at() -> datetime:
    """Return the bound start-of-call instant, or wallclock-now with DEBUG log.

    D6-11 safety-net: callers (envelope factories, citation synthesizer) ask
    "what's the retrieved_at value?" without needing to know whether the
    middleware was set on the current call. If the middleware is bound, the
    bound instant is returned. If not (direct-handler-call unit tests, or any
    path that bypassed the middleware), emit a DEBUG `retrieved_at_fallback`
    event so the bypass is visible in audit logs without polluting INFO/WARN.
    """
    bound = tool_started_at.get(None)
    if bound is not None:
        return bound
    log.debug("retrieved_at_fallback", reason="middleware not bound")
    return datetime.now(tz=UTC)


class RetrievedAtMiddleware(Middleware):
    """Bind `tool_started_at` on every MCP tool call entry; reset on exit.

    D6-09: `on_call_tool` is the only hook needed (other MCP methods like
    `list_tools` / `list_resources` don't carry retrieved_at semantics). The
    try/finally ensures the contextvar is reset on BOTH the success path AND
    the exception path — leaking a bound contextvar across cooperatively-
    scheduled coroutines would silently smear timestamps across calls.

    Pitfall 4 (RESEARCH): this middleware MUST be added FIRST in the FastMCP
    middleware list so the timestamp is captured on every call that reaches
    the handler. Plan 06-02 owns the `mcp.add_middleware(...)` wiring.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        token = tool_started_at.set(datetime.now(tz=UTC))
        try:
            return await call_next(context)
        finally:
            tool_started_at.reset(token)
