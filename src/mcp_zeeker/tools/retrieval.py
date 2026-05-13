"""
Retrieval tool handlers — query_table and fetch (unregistered stubs).

D-01: Per-domain grouping. These handlers are unregistered async stubs raising
NotImplementedError until Phase 3 overwrites them with real implementations.
"""

from __future__ import annotations

from mcp_zeeker.core.envelope import Envelope


async def query_table(
    database: str,
    table: str,
    filters=None,
    sort=None,
    limit=50,
    cursor=None,
    columns=None,
) -> Envelope:
    """Stub — query_table is registered in Phase 3 (structured retrieval)."""
    raise NotImplementedError("query_table is registered in Phase 3 (structured retrieval)")


async def fetch(database: str, table: str, url: str) -> Envelope:
    """Stub — fetch is registered in Phase 3 (URL-keyed fetch)."""
    raise NotImplementedError("fetch is registered in Phase 3 (URL-keyed fetch)")
