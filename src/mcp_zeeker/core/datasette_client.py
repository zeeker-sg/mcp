"""
Typed DatasetteClient wrapping httpx.AsyncClient with retry-once-with-jitter.

Implements D-13 (typed wrapper), D-14 (httpx lifecycle via contextvar),
D-16 (retry-once-with-jitter on 502/503, immediate 504 surface),
D-17 (Phase 1 error mapping via UpstreamCallFailed).
"""

from __future__ import annotations

import asyncio
import contextvars
import random

import httpx
from pydantic import BaseModel, ConfigDict


class UpstreamCallFailed(Exception):
    """Raised when the upstream Datasette request fails after retry policy."""


class TableSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")  # D-13: tolerant read of upstream JSON

    name: str


class DatabaseSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tables: list[TableSummary]


_current: contextvars.ContextVar[DatasetteClient | None] = contextvars.ContextVar(
    "datasette_client", default=None
)


class DatasetteClient:
    """Typed async wrapper around httpx.AsyncClient for upstream Datasette calls."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @classmethod
    def current(cls) -> DatasetteClient:
        """Return the DatasetteClient bound to the current context.

        Raises RuntimeError if called outside a bind() scope.
        """
        client = _current.get()
        if client is None:
            raise RuntimeError("DatasetteClient.current() called outside a bound scope")
        return client

    @classmethod
    def bind(cls, client: DatasetteClient) -> contextvars.Token:
        """Bind a DatasetteClient to the current context. Returns a Token for reset()."""
        return _current.set(client)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        """Restore the previous binding (LIFO). Call with the Token from bind()."""
        _current.reset(token)

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
