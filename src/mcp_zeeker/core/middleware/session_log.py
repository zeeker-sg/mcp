# src/mcp_zeeker/core/middleware/session_log.py
# Issue #5 — emit one `session_start` JSON log line per MCP `initialize`.
from __future__ import annotations

import structlog
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = structlog.get_logger()


class SessionLogMiddleware(Middleware):
    """#5 — emit one JSON log line per MCP `initialize` handshake.

    The server runs stateless_http=True (see app.py), so FastMCP never mints
    an Mcp-Session-Id. We therefore count `initialize` handshakes as a
    privacy-safe proxy for "sessions": a client still performs `initialize`
    once per logical session before it can call tools.

    Logged identity is SOFTWARE-only — protocol version and the
    clientInfo.name / clientInfo.version from the initialize params (e.g.
    "claude-ai", "mcp-remote/x.y"). NEVER a user identifier, NEVER a full IP,
    NEVER tool args. request_id and ip_prefix are already bound to contextvars
    by the ASGI RequestIdMiddleware, so structlog's merge_contextvars processor
    picks them up — they are not passed explicitly here.
    """

    async def on_initialize(self, context: MiddlewareContext, call_next):
        # Read defensively: some clients may omit clientInfo entirely.
        params = getattr(context.message, "params", None)
        protocol_version = getattr(params, "protocolVersion", None)
        client_info = getattr(params, "clientInfo", None)
        client_name = getattr(client_info, "name", None)
        client_version = getattr(client_info, "version", None)
        try:
            return await call_next(context)
        finally:
            # Emit in finally so the handshake is counted even if init errors.
            logger.info(
                "session_start",
                protocol_version=protocol_version,
                client_name=client_name,
                client_version=client_version,
            )
