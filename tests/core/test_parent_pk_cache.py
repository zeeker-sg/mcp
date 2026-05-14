"""Phase 5 — ParentPKCache unit tests.

Covers positive cache hit, negative cache hit (suppresses upstream retry on
missing parents), and TTL=0 immediate expiry (treated as miss).

Mirrors the test shape of `tests/test_metadata_cache.py` (Phase 2) — function-
body imports + async tests under `pytest-asyncio` auto-mode.
"""

import pytest


@pytest.mark.asyncio
async def test_parent_pk_cache_hit_positive() -> None:
    from mcp_zeeker.core.fragment_join import ParentPKCache

    cache = ParentPKCache(ttl=60)
    await cache.set("zeeker-judgements", "judgments", "https://example.gov.sg/x", "pk_abc")
    hit, pk = await cache.get("zeeker-judgements", "judgments", "https://example.gov.sg/x")
    assert hit is True
    assert pk == "pk_abc"


@pytest.mark.asyncio
async def test_parent_pk_cache_hit_negative() -> None:
    """Negative cache: `set(..., None)` records `no matching parent upstream`
    so repeat queries return a cache hit with `pk is None` (suppresses retry)."""
    from mcp_zeeker.core.fragment_join import ParentPKCache

    cache = ParentPKCache(ttl=60)
    await cache.set("zeeker-judgements", "judgments", "https://example.gov.sg/missing", None)
    hit, pk = await cache.get("zeeker-judgements", "judgments", "https://example.gov.sg/missing")
    assert hit is True
    assert pk is None


@pytest.mark.asyncio
async def test_parent_pk_cache_ttl_expiry() -> None:
    """TTL=0 → every read after set returns `(False, None)` (treated as miss)."""
    from mcp_zeeker.core.fragment_join import ParentPKCache

    cache = ParentPKCache(ttl=0)
    await cache.set("zeeker-judgements", "judgments", "https://example.gov.sg/x", "pk_abc")
    hit, pk = await cache.get("zeeker-judgements", "judgments", "https://example.gov.sg/x")
    assert hit is False
    assert pk is None
