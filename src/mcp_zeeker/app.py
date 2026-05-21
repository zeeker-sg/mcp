# src/mcp_zeeker/app.py
# Source: 01-RESEARCH.md Pattern A lines 218–285 (verbatim, with modifications per plan)
# Modifications vs Pattern A:
#   1. build_http_client() factory instead of inline httpx.AsyncClient construction
#   2. configure_logging() called at module top (before lifespan)
#   3. Envelope-contract sanity guard inside lifespan before yield (Pattern F adapted)
#   4. DatasetteClient contextvar binding in lifespan
from __future__ import annotations

import contextlib

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_zeeker import config
from mcp_zeeker.core.admin import admin_metrics
from mcp_zeeker.core.http_client import build_http_client
from mcp_zeeker.core.logging import configure_logging
from mcp_zeeker.core.middleware.origin import OriginAllowlistMiddleware
from mcp_zeeker.core.middleware.rate_limit import RateLimitMiddleware
from mcp_zeeker.core.middleware.request_id import RequestIdMiddleware
from mcp_zeeker.server import mcp  # FastMCP instance

# Configure structlog before any module imports structlog.get_logger()
configure_logging()

# Build the FastMCP HTTP app once at import time.
# path="/" because we mount the whole thing under /mcp below; FastMCP
# would otherwise prepend its default streamable_http_path.
#
# stateless_http=True per TRANSPORT-03: no per-client session state. Every
# tool call is a clean request/response cycle. Without this, FastMCP issues
# Mcp-Session-Id headers and rejects subsequent calls with 404 "Session not
# found" after a container restart — which breaks every long-lived MCP client
# (Claude Desktop's mcp-remote bridge, Claude Code) on every redeploy.
mcp_app = mcp.http_app(path="/", stateless_http=True)


async def _mcp_get_status(scope, receive, send):
    """ASGI handler for GET /mcp/ — returns lightweight status JSON.

    Some MCP client libraries probe the endpoint with GET before POSTing
    tool requests (discovery/health check). FastMCP's http_app 405s GET,
    which is noisy in logs and triggers false error-rate alerts. This
    wrapper returns a minimal 200 without interfering with POSTs.
    """
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"application/json")],
    })
    body = b'{"status":"ok","protocol":"2025-06-18","name":"zeeker"}'
    await send({"type": "http.response.body", "body": body})


async def mcp_wrapper(scope, receive, send):
    """Wrap the FastMCP app to intercept GET at the mount root.

    Mount("/mcp", app=...) strips the prefix, so the inner app sees
    path="/". We handle GET / here; everything else passes through.
    """
    if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == "/":
        await _mcp_get_status(scope, receive, send)
        return
    await mcp_app(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """
    Own the httpx.AsyncClient for the process lifetime AND nest the
    FastMCP session-manager lifespan. Both must run; if you only enter
    one, /mcp hangs on first POST.
    """
    async with build_http_client() as http_client:
        app.state.http_client = http_client

        # Envelope-contract sanity guard (Pattern F adapted, per plan Task 3).
        # Runs before yield so a bad deploy fails liveness immediately.
        # With zero registered tools (Wave 2 / before Plan 04), this is a no-op.
        try:
            from mcp_zeeker.core.envelope import Envelope

            tools = await mcp.list_tools()
            for tool in tools:
                # Check return type annotation — every tool must return Envelope
                if getattr(tool, "return_type", None) is not Envelope:
                    raise RuntimeError(
                        f"tool contract drift: {tool.name} return_type is not Envelope"
                    )
                # Check description ends with TOOL_TRAILER
                if not (tool.description or "").rstrip().endswith(config.TOOL_TRAILER):
                    raise RuntimeError(
                        f"tool contract drift: {tool.name} description missing TOOL_TRAILER"
                    )
        except ImportError:
            # Envelope not available (wave-2 stub not yet merged) — tolerate
            pass

        # Bind MetadataCache, DatasetteClient, and ParentPKCache into their
        # respective contextvars. All three are process-local singletons (single
        # Uvicorn worker per CFG / RATE-06 / NFR-04). Reset order is reverse of
        # bind order (LIFO).
        from mcp_zeeker.core.datasette_client import DatasetteClient
        from mcp_zeeker.core.fragment_join import ParentPKCache
        from mcp_zeeker.core.metadata_cache import MetadataCache

        mc = MetadataCache(http_client, config.UPSTREAM_URL, ttl=config.METADATA_TTL_SECONDS)
        mc_token = MetadataCache.bind(mc)
        token = DatasetteClient.bind(DatasetteClient(http_client))
        pk_token = ParentPKCache.bind(ParentPKCache())
        try:
            async with mcp_app.lifespan(mcp_app):  # MUST be nested (Pitfall 1)
                yield
        finally:
            ParentPKCache.reset(pk_token)
            DatasetteClient.reset(token)
            MetadataCache.reset(mc_token)


async def healthz(_request):
    # OBS-01: liveness only — never consult upstream
    return JSONResponse({"status": "ok"})


app = Starlette(
    routes=[
        Route("/healthz", healthz),
        # /admin/metrics — soak-token-gated RSS read-out; returns 404 when
        # unauthenticated (no surface). See core/admin.py.
        Route("/admin/metrics", admin_metrics),
        Mount("/mcp", app=mcp_wrapper),
    ],
    middleware=[
        # Outermost first. Request-ID binds the contextvar so subsequent
        # rejects (Origin, RateLimit) carry it in their log line.
        Middleware(RequestIdMiddleware),
        Middleware(
            OriginAllowlistMiddleware,
            allowed_origins=config.ALLOWED_ORIGINS,
        ),
        # RateLimit fires BEFORE Mount("/mcp", ...) so 429 short-circuits
        # at the ASGI layer (RATE-02). Reads the locked RATE_* knobs from
        # config; in-memory bucket store is single-process per RATE-06.
        Middleware(
            RateLimitMiddleware,
            burst=config.RATE_BURST,
            sustained_per_second=config.RATE_SUSTAINED_PER_SECOND,
            daily_limit=config.RATE_DAILY_LIMIT,
            store_cap=config.RATE_STORE_CAP,
            idle_ttl_seconds=config.RATE_IDLE_TTL_SECONDS,
        ),
    ],
    lifespan=lifespan,
)
