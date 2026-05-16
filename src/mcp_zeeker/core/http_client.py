# src/mcp_zeeker/core/http_client.py
# Source: 01-PATTERNS.md "src/mcp_zeeker/core/http_client.py" section
# (extracted from Pattern A lines 247–258)
from __future__ import annotations

import httpx

from mcp_zeeker import config


def build_http_client() -> httpx.AsyncClient:
    """Single factory so tests can swap it for an ASGITransport-backed client."""
    return httpx.AsyncClient(
        base_url=config.UPSTREAM_URL,
        timeout=httpx.Timeout(connect=1.0, read=10.0, write=2.0, pool=2.0),
        limits=httpx.Limits(
            # 150 covers worst-case with the fan_out_search semaphore(10):
            # 10 concurrent search calls × 10 semaphore slots = 100 (search)
            # + 40 non-search calls × 1 connection = 140 peak. 150 adds headroom.
            max_connections=150,
            max_keepalive_connections=20,
            keepalive_expiry=30.0,
        ),
        headers={"User-Agent": config.USER_AGENT},
    )
