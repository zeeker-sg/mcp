# src/mcp_zeeker/core/middleware/origin.py
# Source: 01-RESEARCH.md Pattern H lines 629–690 (paste verbatim)
from __future__ import annotations

from collections.abc import Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class OriginAllowlistMiddleware:
    """
    ASGI middleware enforcing the MCP-spec-mandated Origin check.

    - Missing Origin: ALLOW (CLI clients and Anthropic server-side proxy
      do not send it).
    - Origin in allowlist: ALLOW.
    - Origin set to anything else: DENY 403.

    Also handles CORS preflight (OPTIONS) for allowed origins so a future
    browser-based MCP debug client works.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        origin = headers.get("origin")

        # Handle CORS preflight before any allowlist enforcement
        if scope["method"] == "OPTIONS" and origin is not None:
            if origin in self.allowed:
                response = JSONResponse(
                    {},
                    status_code=204,
                    headers={
                        "access-control-allow-origin": origin,
                        "access-control-allow-methods": "POST, GET, DELETE, OPTIONS",
                        "access-control-allow-headers": (
                            "content-type, mcp-session-id, mcp-protocol-version"
                        ),
                        "access-control-max-age": "600",
                    },
                )
            else:
                response = JSONResponse({"error": "origin_not_allowed"}, status_code=403)
            await response(scope, receive, send)
            return

        # Non-preflight: enforce allowlist when Origin is present
        if origin is not None and origin not in self.allowed:
            response = JSONResponse(
                {"error": "origin_not_allowed"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
