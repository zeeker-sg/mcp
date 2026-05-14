"""
MetadataCache — TTL-cached /-/metadata.json reader with singleton+contextvar lifecycle.

Implements D2-02 (MetadataCache class), D2-03 (lazy first fetch, TTL refresh,
anyio.Lock single-flight, stale-on-error), D2-04 (METADATA_TTL_SECONDS config),
D2-05 (DB-name normalize-at-ingest only — NEVER at lookup boundary), D2-06
(app.py lifespan ownership), D2-08 (metadata_gap INFO log), F-2 (cross-task
singleton fallback identical to DatasetteClient pattern from commit 4184a64).

Key design constraints:
- anyio.Lock() constructed inside __init__ (called from lifespan, inside event loop) — Pitfall 3
- DB-name keys normalized lowercase at ingest in _fetch_and_normalize ONLY (D2-05)
  The four public lookup methods read self._data[database] directly — ZERO .lower() calls
  inside those methods. Pitfall 6 is prevented at ingest; Pitfall 6 cannot recur at lookup.
- bind() sets BOTH _singleton AND contextvar (F-2 fix): production cross-task reads hit
  singleton; test reads hit the contextvar-bound instance.
"""

from __future__ import annotations

import contextvars
import time

import anyio
import httpx
import structlog

log = structlog.get_logger()

_current: contextvars.ContextVar[MetadataCache | None] = contextvars.ContextVar(
    "metadata_cache", default=None
)


class MetadataCache:
    """TTL-cached reader for upstream /-/metadata.json with single-flight refresh.

    Mirrors DatasetteClient's singleton+contextvar lifecycle (D2-06, F-2).
    """

    _singleton: MetadataCache | None = None

    def __init__(self, http: httpx.AsyncClient, upstream_url: str, ttl: int = 1800) -> None:
        self._http = http
        self._upstream_url = upstream_url
        self._ttl = ttl
        self._data: dict | None = None  # normalized dict, lowercase DB keys (D2-05)
        self._last_fetch: float = 0.0
        self._refresh_lock = anyio.Lock()  # MUST be inside __init__ (Pitfall 3 — event loop)

    @classmethod
    def current(cls) -> MetadataCache:
        """Return the MetadataCache bound to the current context, or the
        process-wide singleton. Raises RuntimeError if neither is set."""
        cache = _current.get()
        if cache is not None:
            return cache
        if cls._singleton is not None:
            return cls._singleton
        raise RuntimeError("MetadataCache.current() called outside a bound scope")

    @classmethod
    def bind(cls, cache: MetadataCache) -> contextvars.Token:
        """Bind a MetadataCache. Sets BOTH the per-task contextvar (test isolation)
        and the process-wide singleton (production cross-task reads). Returns the
        contextvar Token for reset()."""
        cls._singleton = cache
        return _current.set(cache)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        """Restore the previous contextvar binding (LIFO). The process-wide
        singleton is intentionally NOT cleared — use clear_singleton() for that."""
        _current.reset(token)

    @classmethod
    def clear_singleton(cls) -> None:
        """Clear the process-wide singleton. Test-teardown only — production
        relies on the singleton living for the full process lifetime."""
        cls._singleton = None

    async def _fetch_and_normalize(self) -> dict:
        """Fetch /-/metadata.json and return normalized dict with DB keys lowercased.

        D2-05: normalize-at-ingest only. This is the SOLE location in this module
        where .lower() is called on a database name. The four public lookup methods
        below MUST NOT call .lower() — they read self._data[database] directly on
        a store that is already normalized.
        """
        resp = await self._http.get(f"{self._upstream_url}/-/metadata.json")
        resp.raise_for_status()
        raw = resp.json()
        normalized = {}
        for db_name, db_data in raw.get("databases", {}).items():
            normalized[db_name.lower()] = db_data  # D2-05: normalize at ingest
        return normalized

    async def _ensure_fresh(self) -> None:
        """Ensure cache data is fresh. Single-flight via anyio.Lock (D2-03).

        Fast path: cache hit — return immediately.
        Thundering-herd protection: if a refresh is in progress and stale data
        exists, serve stale without waiting for the lock.
        First-ever call with concurrent arrivals: wait for the holder to complete.
        Double-check after acquiring lock: another task may have refreshed.
        Stale-on-error: if refresh fails, _last_fetch is NOT updated so the next
        call retries immediately (serves stale data until a refresh succeeds).
        """
        now = time.monotonic()
        if self._data is not None and (now - self._last_fetch) < self._ttl:
            return  # cache hit — fast path

        # Another coroutine may already be refreshing
        if self._refresh_lock.locked():
            if self._data is not None:
                return  # serve stale while refresh runs (thundering-herd protection)
            # First ever call with concurrent arrivals — wait for the holder to complete
            async with self._refresh_lock:
                if self._data is None:
                    # Holder's first-ever fetch failed; warn so this waiter's failure is
                    # traceable (WR-01: concurrent waiters must not silently return None)
                    log.warning("metadata_cache_refresh_failed_concurrent_waiter", ttl=self._ttl)
                return

        async with self._refresh_lock:
            # Double-check pattern (another task may have refreshed between check and acquire)
            now2 = time.monotonic()
            if self._data is not None and (now2 - self._last_fetch) < self._ttl:
                return
            try:
                self._data = await self._fetch_and_normalize()
                self._last_fetch = time.monotonic()
            except Exception:
                log.warning("metadata_cache_refresh_failed", ttl=self._ttl)
                # stale-on-error: _last_fetch NOT updated → next call retries immediately

    async def get_table_metadata(self, database: str, table: str) -> dict | None:
        """Return upstream metadata dict for database.table, or None if absent.

        D2-05: DB-name lookup is direct dict access on normalized store (no case folding here).
        Logs metadata_gap at INFO when table metadata is absent (D2-08).
        """
        await self._ensure_fresh()
        if self._data is None:
            return None
        db_data = self._data.get(database, {})
        tables = db_data.get("tables", {})
        table_data = tables.get(table)
        if table_data is None:
            log.info("metadata_gap", database=database, table=table)
        return table_data

    async def get_column_description(self, database: str, table: str, column: str) -> str | None:
        """Return column description string from upstream metadata, or None.

        D2-05: delegates to get_table_metadata (normalized store; no case folding here).
        Logs metadata_gap at INFO when description is absent (D2-08).
        """
        table_data = await self.get_table_metadata(database, table)
        if table_data is None:
            return None
        cols = table_data.get("columns", {})
        desc = cols.get(column)
        if not desc:
            log.info("metadata_gap", database=database, table=table, column=column)
        return desc or None

    async def get_database_license(self, database: str) -> str | None:
        """Return license string for database from upstream metadata, or None.

        D2-05: database key read directly from self._data (normalized at ingest; no folding here).
        """
        await self._ensure_fresh()
        if self._data is None:
            return None
        db_data = self._data.get(database, {})
        return db_data.get("license") or None

    async def force_refresh(self) -> None:
        """Force cache expiry and re-fetch. Test seam and manual refresh path."""
        self._last_fetch = 0.0
        await self._ensure_fresh()

    async def license_for(self, database: str) -> tuple[str, str]:
        """Return (license_text, license_url) for `database` (D6-01 / D6-04).

        D6-04 fallback chain:
          1. Upstream `/-/metadata.json` non-empty `(license, license_url)` wins.
          2. Otherwise `config.LICENSES.get(database, ("", ""))`.
          3. Cold-cache / unknown DB → `("", "")` (no exception, no upstream
             error surfacing — provenance shipping with empty strings is the
             acceptable degraded mode).

        D2-05: database key read directly from self._data (normalized at ingest;
        no .lower() at this lookup boundary — Pitfall 2).
        """
        from mcp_zeeker import config

        await self._ensure_fresh()
        if self._data is not None:
            db_data = self._data.get(database, {})
            lic = db_data.get("license") or ""
            lurl = db_data.get("license_url") or ""
            if lic or lurl:
                return (lic, lurl)
        return config.LICENSES.get(database, ("", ""))

    def license_for_sync(self, database: str) -> tuple[str, str]:
        """Synchronous license accessor for envelope factories (D6-04).

        Mirrors `license_for` but cannot await. Cold-cache acceptance per D6-04:
        returns `("", "")` when `_data is None` (no upstream hit yet). Once the
        async path warms `_data`, this sync accessor reads the same underlying
        dict.

        Plan 06-02 wires this into `Envelope.for_table_list` / `for_rows` because
        those factories run inside Pydantic constructor scope and cannot await.
        """
        from mcp_zeeker import config

        if self._data is None:
            return ("", "")
        db_data = self._data.get(database, {})
        lic = db_data.get("license") or ""
        lurl = db_data.get("license_url") or ""
        if lic or lurl:
            return (lic, lurl)
        return config.LICENSES.get(database, ("", ""))
