# src/mcp_zeeker/core/middleware/request_id.py
# Source: 01-RESEARCH.md Pattern K lines 826–875 (paste verbatim)
from __future__ import annotations

import re
import uuid

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mcp_zeeker.core.ip import client_ip, ip_prefix
from mcp_zeeker.core.logging import bind_request, clear_request

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        incoming = headers.get("x-request-id", "")
        if incoming and _REQUEST_ID_PATTERN.match(incoming):
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex

        conn = HTTPConnection(scope)
        ip = client_ip(conn)

        bind_request(request_id=request_id, ip_prefix=ip_prefix(ip))

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers_list}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            clear_request()
