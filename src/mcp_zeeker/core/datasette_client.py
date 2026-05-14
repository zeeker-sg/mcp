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
    """Raised when the upstream Datasette request fails after retry policy.

    Phase 4 (D4-09 / 04-RESEARCH §3.7 / Pitfall 5): exposes the HTTP status
    code when known so the search orchestrator can distinguish per-table FTS5
    syntax errors (status 400 — mapped to `invalid_query` when ALL tables
    fail) from generic upstream unavailability (status 5xx or transport
    failure — mapped to `upstream_unavailable`).

    Existing raise sites that don't pass `status=` (transport errors at the
    httpx.RequestError boundary and the retry-exhausted post-loop raise) keep
    `status=None` via the default — backward-compatible with Phase 1/2/3 callers
    that read the exception via `str(exc)` only.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TableSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")  # D-13: tolerant read of upstream JSON

    name: str
    # Phase 2 optional fields (D2-09: hidden flag; D2-13: row_count passes through honestly)
    # Defaults ensure Phase 1 fixtures still work with only {"name": ...} payloads.
    hidden: bool = False
    count: int | None = None
    columns: list[str] = []
    primary_keys: list[str] = []
    # Phase 4 (D4-02 / 04-RESEARCH §3.1 / Pitfall 1 / Pitfall 3): FTS5 virtual
    # table name when upstream has built an FTS index for this table's content;
    # None otherwise. core.search.searchable_tables_for uses
    # `fts_table is not None` as the LOAD-BEARING safety gate — Datasette
    # silently ignores `_search=` on non-FTS tables and would return
    # rowid-ordered rows as fake "search results" without this gate.
    # Default None preserves backward compat with Phase 2 fixtures that don't
    # set the key (Pydantic extra="ignore" tolerates missing fields per D-13).
    fts_table: str | None = None


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
                # D4-09 / 04-RESEARCH §3.7: pass status so the search
                # orchestrator can distinguish 5xx (upstream_unavailable)
                # from 400 (per-table FTS5 syntax error → invalid_query when
                # all tables fail).
                raise UpstreamCallFailed(f"upstream 504 on {url}", status=504)
            if 200 <= resp.status_code < 300:
                return resp
            # D4-09 / 04-RESEARCH §3.7: same — pass through resp.status_code.
            # The transport-error raise (httpx.RequestError above) keeps
            # status=None via default since no HTTP response was parsed.
            raise UpstreamCallFailed(
                f"upstream {resp.status_code} on {url}", status=resp.status_code
            )
        raise UpstreamCallFailed(f"upstream retry exhausted on {url}")

    async def get_database(self, name: str) -> DatabaseSummary:
        """Fetch database metadata from /{name}.json and return a typed DatabaseSummary."""
        resp = await self._request_with_retry("GET", f"/{name}.json")
        return DatabaseSummary.model_validate(resp.json())

    async def get_table_column_types(self, database: str) -> dict[str, dict[str, str]]:
        """Fetch column type map from /{database}/_zeeker_schemas.json.

        Returns {table_name: {column_name: sql_type}} for all tables in the DB.
        Falls back to empty dict if the table is absent, the upstream call
        fails, OR the response shape is malformed (HTTP 200 with unexpected
        JSON during a partial outage / schema drift). The caller is expected
        to merge with config.COLUMN_TYPES as a fallback.

        Response shape: {"columns": [...], "rows": [[resource_name, ..., column_definitions, ...]]}
        """
        # WR-06: wrap BOTH the upstream call AND the JSON-parsing path so any
        # of UpstreamCallFailed / JSONDecodeError / KeyError / ValueError /
        # IndexError / TypeError maps to the documented empty-dict fallback.
        # Previously, a HTTP 200 with a malformed JSON shape (partial outage
        # or upstream schema drift) propagated an un-mapped exception that
        # bypassed FastMCP's ToolError mapping and surfaced as a 500-class
        # error with a Python traceback in the envelope — leaking
        # implementation details and breaking the D3-12 locked catalog.
        # json.JSONDecodeError is a subclass of ValueError, so the explicit
        # list catches it without an extra entry; `.index()` ValueErrors are
        # the same class and intentionally swept into the fallback.
        try:
            resp = await self._request_with_retry("GET", f"/{database}/_zeeker_schemas.json")
            payload = resp.json()
            col_idx = payload["columns"].index("resource_name")
            defn_idx = payload["columns"].index("column_definitions")
            result: dict[str, dict[str, str]] = {}
            for row in payload.get("rows", []):
                table_name = row[col_idx]
                raw_defn = row[defn_idx]
                result[table_name] = json.loads(raw_defn) if isinstance(raw_defn, str) else {}
            return result
        except (
            UpstreamCallFailed,
            KeyError,
            ValueError,  # includes json.JSONDecodeError and .index() misses
            IndexError,
            TypeError,
        ):
            return {}

    async def get_table_rows(
        self,
        database: str,
        table: str,
        params: list[tuple[str, str]],
    ) -> dict:
        """Fetch rows from /{database}/{table}.json with the given query params (D3-14).

        Always prepends `_shape=objects` so the response `rows` field is a list of
        dicts (not the column-array shape Datasette defaults to). This is the
        upstream HTTP path consumed by Phase 3's query_table and fetch handlers.

        Retry semantics inherited from `_request_with_retry`: one retry with
        jitter on 502/503; immediate UpstreamCallFailed on 504, transport error,
        or non-2xx after retry (D-16). The handler maps the exception to
        `upstream_unavailable` in the envelope.

        Returns the raw parsed JSON dict — keys include `rows`, `columns`,
        `next`, `truncated`, `filtered_table_rows_count`. The handler unpacks
        and re-shapes per D3-04 (heavy columns under `retrieved_content`).
        """
        resp = await self._request_with_retry(
            "GET",
            f"/{database}/{table}.json",
            params=[("_shape", "objects"), *params],
        )
        return resp.json()
