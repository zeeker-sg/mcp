"""
ErrorEnrichmentMiddleware — append `[request_id: <hex>]` to every ToolError
message so an MCP client and a server-side log line can be correlated by a
single hex token (ERR-03).

Why this lives at the FastMCP middleware layer (not ASGI):
    `request_id` is bound to the structlog contextvar by
    `core/middleware/request_id.py:RequestIdMiddleware` (ASGI layer, outermost).
    By the time `on_call_tool` runs the contextvar is already bound, so this
    middleware reads it passively via `structlog.contextvars.get_contextvars()`
    — no contextvar token set/reset is needed.

Code-prefix preservation:
    The catalog code is the substring BEFORE the first `: ` in the ToolError
    message. This middleware appends ` [request_id: <hex>]` to the END of the
    message, so the prefix-based catalog detector (used by
    `tests/test_error_catalog.py::test_all_errors_have_stable_code`) keeps
    working unchanged. Example transformation:
        before: "unknown_database: Database not found: foo"
        after:  "unknown_database: Database not found: foo [request_id: abc123]"

Middleware ordering:
    Registered AFTER `RetrievedAtMiddleware` in `server.py`. FastMCP middleware
    is FIFO ("first added is first in, last out"), so RetrievedAt's
    `tool_started_at` contextvar remains BOUND while ErrorEnrichment runs the
    `try/except`. This preserves D6-09 / D6-10 ordering and the F-7-3 ordering
    note in 07-CONTEXT.md (RequestId at ASGI → OriginAllowlist at ASGI →
    RateLimit at ASGI → FastMCP RetrievedAt → FastMCP ErrorEnrichment → handler).

Scope:
    Only `ToolError` is intercepted. Generic `Exception` is NOT caught — Phase 7
    ERR-03 is about correlating structured-error responses with log lines, not
    about silencing every unexpected crash. Non-ToolError exceptions still
    propagate to FastMCP's default error handling.

INJ-05 carry-forward:
    The `request_id` contextvar value is an opaque hex string set by
    `RequestIdMiddleware` (a uuid4 hex unless the client supplies a value
    matching `_REQUEST_ID_PATTERN = ^[A-Za-z0-9_\\-]{1,128}$`). It cannot be
    used to smuggle user content into the error message — the regex bounds
    the character set and length.
"""

from __future__ import annotations

import structlog
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from structlog.contextvars import get_contextvars

logger = structlog.get_logger()


class ErrorEnrichmentMiddleware(Middleware):
    """Append `[request_id: <hex>]` to every ToolError message (ERR-03).

    See module docstring for ordering, code-prefix preservation, and INJ-05
    properties.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except ToolError as exc:
            request_id = get_contextvars().get("request_id", "")
            if not request_id:
                # Test path or any path that bypassed RequestIdMiddleware:
                # re-raise the original unmodified so the catalog code prefix
                # stays exactly as the handler emitted it.
                raise
            # FastMCP's ToolError exposes its message via str() / args[0] —
            # there is no `.message` attribute on the public API. Use str(exc)
            # to obtain the human-readable message string.
            new_message = f"{exc!s} [request_id: {request_id}]"
            raise ToolError(new_message) from exc
