# src/mcp_zeeker/server.py
# Source: 01-PATTERNS.md "src/mcp_zeeker/server.py" section
from __future__ import annotations

from fastmcp import FastMCP

from mcp_zeeker.core.middleware.access_log import StructuredLogMiddleware
from mcp_zeeker.core.middleware.error_enrichment import ErrorEnrichmentMiddleware
from mcp_zeeker.core.middleware.retrieved_at import RetrievedAtMiddleware

mcp = FastMCP(name="zeeker", version="0.1.0")
# MUST be the FIRST mcp.add_middleware() call — Phase 6 D6-09 / D6-10 /
# Pitfall 4. FastMCP middleware executes FIFO ("first added is first in,
# last out"). Placing RetrievedAtMiddleware first guarantees the
# tool_started_at contextvar is bound on every call that reaches the
# handler, even after Phase 7 adds rate-limit middleware that may raise
# ToolError before call_next.
mcp.add_middleware(RetrievedAtMiddleware())
# ERR-03: append [request_id: ...] to ToolError messages — registered AFTER
# RetrievedAt so retrieved_at stays bound during error handling.
mcp.add_middleware(ErrorEnrichmentMiddleware())
mcp.add_middleware(StructuredLogMiddleware())

# Tool modules register themselves on import via @mcp.tool decorator.
# These imports MUST run before mcp.http_app() is called.
# Plan 04 overwrites the placeholder tool files with real implementations.
from mcp_zeeker.tools import discovery, retrieval, search  # noqa: F401, E402
from mcp_zeeker import prompts  # noqa: F401, E402
