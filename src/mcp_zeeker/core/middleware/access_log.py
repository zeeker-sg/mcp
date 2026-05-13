# src/mcp_zeeker/core/middleware/access_log.py
# Source: 01-RESEARCH.md Pattern I lines 714–755 (paste verbatim)
from __future__ import annotations

import time

import structlog
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = structlog.get_logger()


class StructuredLogMiddleware(Middleware):
    """OBS-03/04 — emit one JSON log line per MCP tool call.

    Runs on every on_call_tool. The request_id is already bound to the
    contextvar by the ASGI RequestIdMiddleware, so structlog's
    merge_contextvars processor picks it up.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = context.message.name  # MCP tool name
        # database/table only known per-tool; tools that touch one
        # database will bind it via structlog.contextvars.bind_contextvars
        # inside the handler before the log line is emitted.
        start = time.perf_counter()
        status = "ok"
        error_code: str | None = None
        try:
            return await call_next(context)
        except Exception as exc:
            status = "error"
            error_code = getattr(exc, "code", type(exc).__name__)
            raise
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "tool_call",
                tool=tool_name,
                duration_ms=duration_ms,
                status=status,
                error_code=error_code,
            )
