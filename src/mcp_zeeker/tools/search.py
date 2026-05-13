"""
Search tool handler — search (unregistered stub).

D-01: Per-domain grouping. This handler is an unregistered async stub raising
NotImplementedError until Phase 4 overwrites it with a real implementation.
"""

from __future__ import annotations

from mcp_zeeker.core.envelope import Envelope


async def search(query: str, databases=None, limit=20) -> Envelope:
    """Stub — search is registered in Phase 4 (cross-database search)."""
    raise NotImplementedError("search is registered in Phase 4 (cross-database search)")
