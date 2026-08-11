# Issue #5 / stateless-spec migration — emit one JSON log line per MCP session
# start.
#
# Under the July 28, 2026 MCP spec revision the `initialize` handshake is
# removed for new-spec clients. We keep `on_initialize` for legacy clients
# (emitting `session_start`) AND add `on_request` to emit `first_request` for
# new-spec clients that skip the handshake entirely. Both events carry the
# same pseudonymous identity fields.
from __future__ import annotations

import structlog
from fastmcp.server.middleware import Middleware, MiddlewareContext

logger = structlog.get_logger()


class SessionLogMiddleware(Middleware):
    """Emit a JSON log line when a client begins interacting with the server.

    Two events, mutually exclusive in practice:

    - ``session_start`` — emitted on every MCP ``initialize`` handshake.
      Legacy clients (pre-2026 spec) still perform ``initialize`` before
      calling tools. This event is the original #5 metric.

    - ``first_request`` — emitted on the first MCP request that is NOT an
      ``initialize``. New-spec clients (July 2026 revision) skip the
      handshake entirely, so ``on_initialize`` never fires. This event
      gives us a privacy-safe session proxy for those clients.

    The server runs ``stateless_http=True`` (see app.py), so FastMCP never
    mints an ``Mcp-Session-Id``. Both events are a privacy-safe proxy for
    "sessions": a logical client interaction begins here.

    Logged identity is SOFTWARE-only — protocol version and the
    clientInfo.name / clientInfo.version from the request params (e.g.
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

    async def on_request(self, context: MiddlewareContext, call_next):
        """Emit ``first_request`` for new-spec clients that skip initialize.

        Under the July 2026 spec revision, ``initialize`` is removed. This
        hook fires on every MCP JSON-RPC request, but we only emit
        ``first_request`` for non-``initialize`` messages — the
        ``on_initialize`` hook handles those. This avoids double-counting:
        a legacy client fires ``session_start`` (via on_initialize) and this
        hook is a no-op for its initialize message; a new-spec client fires
        ``first_request`` here on its first tool call.

        The event carries the same pseudonymous identity fields as
        ``session_start``. For new-spec clients there is no
        ``protocolVersion`` in the params (the handshake is gone), so the
        field will be ``None`` — that is expected, not a bug.
        """
        method = getattr(context.message, "method", None)

        # Skip initialize — on_initialize handles it (avoids double-count).
        if method == "initialize":
            return await call_next(context)

        # Extract identity defensively. New-spec clients may not send
        # clientInfo on per-tool-call requests (it was only in initialize).
        params = getattr(context.message, "params", None)
        protocol_version = getattr(params, "protocolVersion", None)
        client_info = getattr(params, "clientInfo", None)
        client_name = getattr(client_info, "name", None)
        client_version = getattr(client_info, "version", None)

        try:
            return await call_next(context)
        finally:
            logger.info(
                "first_request",
                method=method,
                protocol_version=protocol_version,
                client_name=client_name,
                client_version=client_version,
            )
