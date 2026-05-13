"""
Typed DatasetteClient wrapping httpx.AsyncClient with retry-once-with-jitter.

Implements D-13 (typed wrapper), D-14 (httpx lifecycle via contextvar),
D-16 (retry-once-with-jitter on 502/503, immediate 504 surface),
D-17 (Phase 1 error mapping via UpstreamCallFailed).

Phase 2 additions:
- TableSummary extended with hidden/count/columns/primary_keys optional fields (D2-09/D2-13)
- get_table_column_types(database) fetches /{db}/_zeeker_schemas.json for column types
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import random

import httpx
from pydantic import BaseModel, ConfigDict


class UpstreamCallFailed(Exception):
    """Raised when the upstream Datasette request fails after retry policy."""


class TableSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")  # D-13: tolerant read of upstream JSON

    name: str
    # Phase 2 optional fields (D2-09: hidden flag; D2-13: row_count passes through honestly)
    # Defaults ensure Phase 1 fixtures still work with only {"name": ...} payloads.
    hidden: bool = False
    count: int | None = None
    columns: list[str] = []
    primary_keys: list[str] = []


class DatabaseSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tables: list[TableSummary]


_current: contextvars.ContextVar[DatasetteClient | None] = contextvars.ContextVar(
    "datasette_client", default=None
)


class DatasetteClient:
    """Typed async wrapper around httpx.AsyncClient for upstream Datasette calls.

    Two-tier resolution (test-friendly contextvar AND production-correct singleton):
    - Tests: `DatasetteClient.bind(...)` sets a contextvar token — scoped to the
      current async task so parallel pytest cases stay isolated.
    - Production: `bind()` ALSO stores the client in a process-wide class attribute.
      The Starlette lifespan binds once at startup; per-request handler tasks read
      via `current()` which falls back to the class attribute when the contextvar
      is empty (it is, because contextvars don't propagate from the lifespan task
      to request tasks). Matches D-14 "single process-lifetime client" semantics.
    """

    _singleton: DatasetteClient | None = None

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @classmethod
    def current(cls) -> DatasetteClient:
        """Return the DatasetteClient bound to the current context, or the
        process-wide singleton. Raises RuntimeError if neither is set."""
        client = _current.get()
        if client is not None:
            return client
        if cls._singleton is not None:
            return cls._singleton
        raise RuntimeError("DatasetteClient.current() called outside a bound scope")

    @classmethod
    def bind(cls, client: DatasetteClient) -> contextvars.Token:
        """Bind a DatasetteClient. Sets BOTH the per-task contextvar (test isolation)
        and the process-wide singleton (production cross-task reads). Returns the
        contextvar Token for `reset()`."""
        cls._singleton = client
        return _current.set(client)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        """Restore the previous contextvar binding (LIFO). The process-wide
        singleton is intentionally NOT cleared — clearing it would require a
        separate `clear_singleton()` call (used in test teardown if needed)."""
        _current.reset(token)

    @classmethod
    def clear_singleton(cls) -> None:
        """Clear the process-wide singleton. Test-teardown only — production
        relies on the singleton living for the full process lifetime."""
        cls._singleton = None

    async def _request_with_retry(self, method: str, url: str, **kw) -> httpx.Response:
        """Execute HTTP request with retry-once-with-jitter on 502/503 (D-16).

        - 502/503 on attempt 0: sleep 0.25 + uniform(0, 0.25)s, retry once.
        - 504: raise UpstreamCallFailed immediately (no retry per D-16).
        - httpx.RequestError: raise UpstreamCallFailed (no retry in Phase 1).
        - 2xx: return response.
        - Other status: raise UpstreamCallFailed.
        """
        for attempt in (0, 1):
            try:
                resp = await self._http.request(method, url, **kw)
            except httpx.RequestError as exc:
                # D-16: no retry on transport errors in Phase 1
                raise UpstreamCallFailed(str(exc)) from exc
            if resp.status_code in (502, 503) and attempt == 0:
                await asyncio.sleep(0.25 + random.random() * 0.25)
                continue
            if resp.status_code == 504:
                raise UpstreamCallFailed(f"upstream 504 on {url}")
            if 200 <= resp.status_code < 300:
                return resp
            raise UpstreamCallFailed(f"upstream {resp.status_code} on {url}")
        raise UpstreamCallFailed(f"upstream retry exhausted on {url}")

    async def get_database(self, name: str) -> DatabaseSummary:
        """Fetch database metadata from /{name}.json and return a typed DatabaseSummary."""
        resp = await self._request_with_retry("GET", f"/{name}.json")
        return DatabaseSummary.model_validate(resp.json())

    async def get_table_column_types(self, database: str) -> dict[str, dict[str, str]]:
        """Fetch column type map from /{database}/_zeeker_schemas.json.

        Returns {table_name: {column_name: sql_type}} for all tables in the DB.
        Falls back to empty dict if the table is absent or the upstream call fails;
        caller is expected to merge with config.COLUMN_TYPES as a fallback.

        Response shape: {"columns": [...], "rows": [[resource_name, ..., column_definitions, ...]]}
        """
        try:
            resp = await self._request_with_retry("GET", f"/{database}/_zeeker_schemas.json")
        except UpstreamCallFailed:
            return {}
        payload = resp.json()
        col_idx = payload["columns"].index("resource_name")
        defn_idx = payload["columns"].index("column_definitions")
        result: dict[str, dict[str, str]] = {}
        for row in payload.get("rows", []):
            table_name = row[col_idx]
            raw_defn = row[defn_idx]
            result[table_name] = json.loads(raw_defn) if isinstance(raw_defn, str) else {}
        return result
