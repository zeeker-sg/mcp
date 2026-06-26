"""
DatabaseSummaryCache — per-DB /{db}.json TTL cache with single-flight refresh.

Implements #6c / #10: cross-request caching of `DatasetteClient.get_database()`
responses. The search discovery path (`core/search.py`) and the visibility
helpers (`core/visibility.py`) both call `get_database(db)` multiple times per
search request, and the same data is re-fetched across requests. This cache
eliminates those redundant upstream calls in steady state.

Design mirrors `MetadataCache`:
- anyio.Lock per key for single-flight refresh (prevents stampede on the
  8-parallel-search burst pattern from #6).
- TTL-based expiry, stale-on-error (failed refresh keeps serving stale data).
- Singleton + contextvar lifecycle (identical pattern to MetadataCache /
  DatasetteClient — production reads the singleton; tests read the contextvar).
- The cache wraps a `DatasetteClient` and delegates the actual HTTP call to it.
  This keeps the cache focused on lifecycle while the client owns transport.

Memory: 4 small `DatabaseSummary` objects — negligible against the <256MB budget.
"""

from __future__ import annotations

import contextvars
import time

import anyio
import structlog

from mcp_zeeker.core.datasette_client import DatabaseSummary, DatasetteClient

log = structlog.get_logger()

_current: contextvars.ContextVar[DatabaseSummaryCache | None] = contextvars.ContextVar(
    "database_summary_cache", default=None
)


class DatabaseSummaryCache:
    """TTL-cached reader for upstream /{db}.json with per-key single-flight refresh.

    Wraps a `DatasetteClient` and caches `get_database(db)` results. Each DB
    key has its own `anyio.Lock` so concurrent misses for the same DB share one
    upstream fetch — the 8-parallel-search burst pattern is the design target.
    """

    _singleton: DatabaseSummaryCache | None = None

    def __init__(self, client: DatasetteClient, ttl: int = 300) -> None:
        self._client = client
        self._ttl = ttl
        # Per-DB cached DatabaseSummary + last-fetch timestamp.
        self._data: dict[str, DatabaseSummary] = {}
        self._last_fetch: dict[str, float] = {}
        # Per-DB in-flight lock. Created on first access for each key to avoid
        # pre-allocating locks for DBs that are never requested.
        self._locks: dict[str, anyio.Lock] = {}
        # Guard lock for the _locks dict itself (adding new keys).
        self._locks_guard = anyio.Lock()

    @classmethod
    def current(cls) -> DatabaseSummaryCache:
        """Return the DatabaseSummaryCache bound to the current context, or
        the process-wide singleton. Raises RuntimeError if neither is set."""
        cache = _current.get()
        if cache is not None:
            return cache
        if cls._singleton is not None:
            return cls._singleton
        raise RuntimeError("DatabaseSummaryCache.current() called outside a bound scope")

    @classmethod
    def bind(cls, cache: DatabaseSummaryCache) -> contextvars.Token:
        """Bind a DatabaseSummaryCache. Sets BOTH the per-task contextvar
        (test isolation) and the process-wide singleton (production cross-task
        reads). Returns the contextvar Token for reset()."""
        cls._singleton = cache
        return _current.set(cache)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        """Restore the previous contextvar binding (LIFO). The process-wide
        singleton is intentionally NOT cleared — use clear_singleton() for
        that."""
        _current.reset(token)

    @classmethod
    def clear_singleton(cls) -> None:
        """Clear the process-wide singleton. Test-teardown only — production
        relies on the singleton living for the full process lifetime."""
        cls._singleton = None

    async def _get_lock(self, db: str) -> anyio.Lock:
        """Get or create the per-DB single-flight lock."""
        # Fast path: lock already exists.
        lock = self._locks.get(db)
        if lock is not None:
            return lock
        # Slow path: create a new lock under the guard lock.
        async with self._locks_guard:
            # Double-check after acquiring guard.
            lock = self._locks.get(db)
            if lock is not None:
                return lock
            lock = anyio.Lock()
            self._locks[db] = lock
            return lock

    async def get_database(self, db: str) -> DatabaseSummary:
        """Return the cached DatabaseSummary for `db`, fetching from upstream
        if stale or absent. Single-flight per key: concurrent callers for the
        same DB share one upstream fetch.

        Stale-on-error: if the upstream fetch fails, the previously cached
        value (if any) is served so the search can proceed with slightly stale
        metadata rather than failing outright.
        """
        now = time.monotonic()
        cached = self._data.get(db)
        last = self._last_fetch.get(db, 0.0)

        # Fast path: cache hit and fresh.
        if cached is not None and (now - last) < self._ttl:
            return cached

        # Slow path: need to refresh. Acquire the per-DB lock so only one
        # coroutine fetches; concurrent waiters get the result after the
        # holder completes (or serve stale if available).
        lock = await self._get_lock(db)

        # If another coroutine is already refreshing and we have stale data,
        # serve stale without waiting (thundering-herd protection).
        if lock.locked() and cached is not None:
            return cached

        async with lock:
            # Double-check after acquiring: another task may have refreshed.
            now2 = time.monotonic()
            cached2 = self._data.get(db)
            last2 = self._last_fetch.get(db, 0.0)
            if cached2 is not None and (now2 - last2) < self._ttl:
                return cached2

            try:
                summary = await self._client.get_database(db)
                self._data[db] = summary
                self._last_fetch[db] = time.monotonic()
                return summary
            except Exception:
                log.warning("database_summary_cache_refresh_failed", database=db)
                # Stale-on-error: if we have a cached value, serve it.
                if cached is not None:
                    return cached
                # No cached value — re-raise so the caller can handle the error.
                raise

    async def force_refresh(self, db: str | None = None) -> None:
        """Force cache expiry for `db` (or all DBs if None) and re-fetch.
        Test seam and manual refresh path."""
        if db is not None:
            self._last_fetch[db] = 0.0
            await self.get_database(db)
        else:
            for d in list(self._data.keys()):
                self._last_fetch[d] = 0.0
            for d in list(self._data.keys()):
                await self.get_database(d)
