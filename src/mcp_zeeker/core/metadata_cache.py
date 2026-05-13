"""
MetadataCache — TTL-cached /-/metadata.json reader with singleton+contextvar lifecycle.

Implements D2-02 (MetadataCache class), D2-03 (lazy first fetch, TTL refresh,
anyio.Lock single-flight, stale-on-error), D2-04 (METADATA_TTL_SECONDS config),
D2-05 (DB-name normalize-at-ingest), D2-06 (app.py lifespan ownership),
D2-08 (metadata_gap INFO log), F-2 (cross-task singleton fallback).

NOTE: This is a placeholder stub created in Plan 01 Task 1 to allow conftest.py
to import MetadataCache before Plan 01 Task 2 provides the full implementation.
Plan 01 Task 2 replaces this with the complete implementation.
"""

from __future__ import annotations

import contextvars

import httpx


_current: contextvars.ContextVar["MetadataCache | None"] = contextvars.ContextVar(
    "metadata_cache", default=None
)


class MetadataCache:
    """Placeholder stub — full implementation in Plan 01 Task 2."""

    _singleton: "MetadataCache | None" = None

    def __init__(self, http: httpx.AsyncClient, upstream_url: str, ttl: int = 1800) -> None:
        self._http = http
        self._upstream_url = upstream_url
        self._ttl = ttl
        self._data: dict | None = None
        self._last_fetch: float = 0.0

    @classmethod
    def current(cls) -> "MetadataCache":
        cache = _current.get()
        if cache is not None:
            return cache
        if cls._singleton is not None:
            return cls._singleton
        raise RuntimeError("MetadataCache.current() called outside a bound scope")

    @classmethod
    def bind(cls, cache: "MetadataCache") -> contextvars.Token:
        cls._singleton = cache
        return _current.set(cache)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        _current.reset(token)

    @classmethod
    def clear_singleton(cls) -> None:
        cls._singleton = None
