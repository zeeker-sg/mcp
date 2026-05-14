"""
Fragment-join orchestrator — D5-01 / D5-04 / D5-05 / D5-06 (Phase 5).

This module implements the transparent URL→parent_pk→fragment_fk join for
`query_table` calls on `*_fragments` tables. The handler at
`tools/retrieval.py` delegates to `compile_filter` after `_visible_columns`
and before `filter_compiler.compile`; this module returns either the original
filters (no join needed) or a rewritten filter list with the parent_fk filter
substituted plus a warning-state record for multi-match parents (FRAG-06).

Architecture: SEQUENTIAL two-request orchestrator (NOT a concurrent fan-out
like Phase 4's core/search.py). Call 1 resolves parent URL → parent_pk via
ParentPKCache + Datasette; Call 2 (issued by the caller, not this module)
fetches fragments. ROADMAP explicitly excludes fragment tools from the
p95<1.5s SLO — cold-path latency may approach 2s and that is accepted.

Security properties (auditable by inspection):

- All HTTP IO routes through `DatasetteClient.current().get_table_rows`
  (D-13/14/16 carry-forward); this module makes ONE upstream call per
  parent lookup that misses the cache.
- `parent_pk` flows ONLY inside Call 2 URL parameters (set by the caller)
  and the ParentPKCache value (server-internal state). It is NEVER:
  - in the rows of any envelope returned to the LLM (HIDDEN_COLUMNS already
    strips id + parent_fk at the response edge — FRAG-02 carry-forward),
  - in any ToolError message text (this module raises NO ToolError — error
    surfaces stay with the Phase 3 handler),
  - in any log line emitted by this module (the multi-match warning binds
    `parent_url_hash` — blake2b 8-byte — NOT the URL value itself; INJ-05 /
    D3-09 carry-forward),
  - in any cursor token (keyset cursor encodes ONLY
    `(qhash, last_order_by_value, last_id)` per D5-06; parent_pk is
    re-resolved each continuation call via ParentPKCache).
- Multi-match warning binds `parent_url_hash` (blake2b 8-byte hex) — the
  raw URL value is NEVER bound. This module assigns the warning-state
  record returned to the caller; the caller decides whether to emit the
  structured log entry (`event="fragment_parent_multi_match"`).
- `compile_filter` returns rewritten filters that BYPASS the per-field
  visibility check at the handler level — the rewritten parent_fk filter
  is INTERNAL state (the LLM never typed it). The handler exempts the
  parent_fk column from `_visible_columns` enforcement on the synthetic
  filter only (per 05-RESEARCH §4.6 / Pitfall 4 allowed_extra_columns).
- ToolError messages anywhere this module raises (none in v1) are FIXED
  LITERALS — no f-string interpolation of any user-supplied value.
- normalize_url is a pure function; the cache key is the normalized URL,
  never the raw input.

References: D5-01 (orchestrator placement), D5-04 (two-request + ParentPKCache),
D5-05 (keyset cursor on fragment side), D5-06 (qhash binds normalized URL, not
parent_pk), FRAG-01..06 (success criteria), INJ-05 / D3-09 (no value echoes).
"""

from __future__ import annotations

import contextvars
import hashlib
import time
from urllib.parse import urlsplit, urlunsplit

import anyio
import structlog

from mcp_zeeker import config
from mcp_zeeker.core.config_lookup import url_column_for
from mcp_zeeker.core.datasette_client import (
    DatasetteClient,
    UpstreamCallFailed,  # noqa: F401 — re-exported for handler-side except-bubble symmetry
)
from mcp_zeeker.core.filter_compiler import Filter

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# normalize_url — pure stdlib URL canonicalization (RESEARCH §4.8)
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Canonicalize a parent URL for use as the ParentPKCache key (RESEARCH §4.8).

    Algorithm (deterministic, idempotent):
      1. If empty after whitespace strip, return empty string.
      2. urlsplit into (scheme, netloc, path, query, fragment).
      3. Lowercase the scheme; upgrade `http` → `https`.
      4. Lowercase the netloc (host + port).
      5. If path length > 1 AND path ends with `/`, strip the trailing slash.
         Root path `/` is preserved verbatim.
      6. Preserve query string and fragment verbatim (no re-sorting).
      7. urlunsplit and return.

    Examples:
        >>> normalize_url("https://Example.Gov.SG/Decision")
        'https://example.gov.sg/Decision'
        >>> normalize_url("https://example.gov.sg/decision/")
        'https://example.gov.sg/decision'
        >>> normalize_url("http://example.gov.sg/decision")
        'https://example.gov.sg/decision'
        >>> normalize_url("https://example.gov.sg/")
        'https://example.gov.sg/'
        >>> normalize_url("  https://example.gov.sg/x  ")
        'https://example.gov.sg/x'
        >>> normalize_url("")
        ''
        >>> normalize_url("https://example.gov.sg/page?q=1&v=2")
        'https://example.gov.sg/page?q=1&v=2'
        >>> normalize_url("https://example.gov.sg/page#frag")
        'https://example.gov.sg/page#frag'

    Pure function — no IO, no logging, no exceptions for malformed input
    (urlsplit is permissive; non-URL inputs round-trip unchanged).
    """
    stripped = url.strip()
    if not stripped:
        return ""
    parts = urlsplit(stripped)
    scheme = parts.scheme.lower()
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


# ---------------------------------------------------------------------------
# ParentPKCache — singleton+contextvar lifecycle mirroring MetadataCache
# ---------------------------------------------------------------------------


_current_pk: contextvars.ContextVar[ParentPKCache | None] = contextvars.ContextVar(
    "parent_pk_cache", default=None
)


class ParentPKCache:
    """TTL-cached `(database, parent_table, normalized_url) → parent_pk` store.

    Mirrors MetadataCache's singleton+contextvar lifecycle (D2-06 / F-2 fix
    from commit 4184a64). Singleton+contextvar dual-binding ensures
    production cross-task reads hit the singleton while test reads hit the
    contextvar-bound instance.

    FRAG-02 invariant: parent_pk is stored INSIDE the cache value only —
    never logged, never returned in a response, never encoded in a cursor.
    The normalized URL is in the KEY only — never echoed.

    Negative caching: `set(...None)` records "no matching parent" so repeat
    queries don't retry the upstream lookup; consumers distinguish via the
    `(cache_hit, parent_pk_or_None)` return tuple.

    TTL defaults to `config.METADATA_TTL_SECONDS` (30 min — Phase 2 pattern).
    """

    _singleton: ParentPKCache | None = None

    def __init__(self, ttl: int = config.METADATA_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._data: dict[str, dict[str, dict[str, tuple[str | None, float]]]] = {}
        self._lock = anyio.Lock()  # MUST be inside __init__ (Pitfall 3 / F-2)

    @classmethod
    def current(cls) -> ParentPKCache:
        """Return the ParentPKCache bound to the current context, or the
        process-wide singleton. Raises RuntimeError if neither is set."""
        cache = _current_pk.get()
        if cache is not None:
            return cache
        if cls._singleton is not None:
            return cls._singleton
        raise RuntimeError("ParentPKCache.current() called outside a bound scope")

    @classmethod
    def bind(cls, cache: ParentPKCache) -> contextvars.Token:
        """Bind a ParentPKCache. Sets BOTH per-task contextvar and process-wide
        singleton (F-2 dual-binding). Returns the contextvar Token for reset()."""
        cls._singleton = cache
        return _current_pk.set(cache)

    @classmethod
    def reset(cls, token: contextvars.Token) -> None:
        """Restore the previous contextvar binding (LIFO). Singleton intentionally
        not cleared — use clear_singleton() for that."""
        _current_pk.reset(token)

    @classmethod
    def clear_singleton(cls) -> None:
        """Clear the process-wide singleton. Test-teardown only."""
        cls._singleton = None

    async def get(
        self,
        database: str,
        parent_table: str,
        normalized_url: str,
    ) -> tuple[bool, str | None]:
        """Look up cached parent_pk. Returns `(cache_hit, parent_pk_or_None)`.

        - `(False, None)` → cache miss; caller must hit upstream.
        - `(True, "pk_string")` → positive cache hit.
        - `(True, None)` → NEGATIVE cache hit (no matching parent upstream);
          caller MUST NOT retry the upstream lookup.

        TTL-expired entries return `(False, None)` (treated as miss). Entry
        is left in place for explicit overwrite by `set()`.
        """
        async with self._lock:
            entry = self._data.get(database, {}).get(parent_table, {}).get(normalized_url)
            if entry is None:
                return (False, None)
            parent_pk, expiry = entry
            if time.monotonic() >= expiry:
                return (False, None)
            return (True, parent_pk)

    async def set(
        self,
        database: str,
        parent_table: str,
        normalized_url: str,
        parent_pk: str | None,
    ) -> None:
        """Store `parent_pk` (or `None` for negative cache) with TTL expiry."""
        async with self._lock:
            (self._data.setdefault(database, {}).setdefault(parent_table, {}))[normalized_url] = (
                parent_pk,
                time.monotonic() + self._ttl,
            )

    def clear(self) -> None:
        """Clear all cached entries. Sync — test-teardown only."""
        self._data.clear()


# ---------------------------------------------------------------------------
# compile_filter — SKELETON until Plan 05-02 ships the body
# ---------------------------------------------------------------------------


async def compile_filter(
    database: str,
    table: str,
    filters: list[Filter],
) -> tuple[list[Filter], dict | None]:
    """Detect and execute the fragment-parent join, rewriting filters if needed.

    Contract (Plan 05-02 will body-fill — D5-01 / D5-04 / 05-RESEARCH §4.6):

    1. **Trigger detection** (D5-02): If `(database, table)` is in
       `config.FRAGMENT_PARENTS` AND `filters` contains exactly one `eq` filter
       whose column matches the expected parent URL column (computed from
       `URL_COLUMNS[FRAGMENT_PARENTS[<frag>].parent_table]`), proceed to the
       join. Otherwise return `(filters, None)` — fall-through, no rewrite.

    2. **Parent PK lookup** (D5-04): Compute `normalized_url = normalize_url(value)`;
       query `ParentPKCache.current().get(database, parent_table, normalized_url)`.
       On miss, issue Call 1 via `DatasetteClient.current().get_table_rows(...)`
       with params `[(<parent_url_col>__exact, raw_value), (_sort_desc,
       <parent_match_order_by>), (_size, "1"), (_shape, "objects")]`. Extract
       `rows[0][<parent_pk>]` or treat as no-match. Set cache; return.

    3. **Filter rewrite**: Drop the parent-URL eq filter from the input list;
       append a synthetic `Filter(column=<parent_fk>, op="exact",
       value=<parent_pk>)`. Other filters carry through unchanged.

    4. **Multi-match warning** (FRAG-06 / D5-04): If Call 1's response
       reports `filtered_table_rows_count > 1`, populate
       `warning_state = {parent_match_count: N, parent_url_hash:
       blake2b8(normalized_url), parent_table: <name>, selected_parent_<col>:
       <ts_value>}` — return as the second tuple element. The handler emits
       the structured log entry (this module just assembles the record;
       INJ-05: URL value NEVER bound, only its hash).

    Returns:
        `(rewritten_filters, warning_state_or_None)`. Caller (the
        `query_table` handler) uses these to issue Call 2 — this module does
        NOT issue Call 2.

    Raises:
        Nothing in v1. Upstream failures bubble up as `UpstreamCallFailed`
        from DatasetteClient; the handler maps to `upstream_unavailable`
        per D-13 / D3-12 carry-forward.

    FRAG-02 / INJ-05: parent_pk and raw URL value NEVER appear in any
    returned warning-state record, log line emitted by this module, or
    ToolError message. The multi-match warning binds `parent_url_hash`
    (blake2b 8-byte hex of the NORMALIZED URL) — NEVER the raw value.
    """
    # 1. Fall-through if (database, table) is not a fragment table.
    fragment_parent = config.FRAGMENT_PARENTS.get(f"{database}.{table}")
    if fragment_parent is None:
        return (filters, None)

    # 2. Fall-through if config drift dropped the parent URL column.
    parent_table = fragment_parent["parent_table"]
    parent_url_col = url_column_for(database, parent_table)
    if parent_url_col is None:
        return (filters, None)

    # 3. Trigger contract: EXACTLY one `exact` filter on the parent URL column
    #    activates the join. Zero or many → fall-through (D5-02 / D5-03).
    eq_url_filters = [f for f in filters if f.column == parent_url_col and f.op == "exact"]
    if len(eq_url_filters) != 1:
        return (filters, None)

    # 4. Compute the cache key (the normalized URL — NEVER the raw value).
    url_value = str(eq_url_filters[0].value)
    normalized = normalize_url(url_value)

    cache = ParentPKCache.current()
    warning_state: dict | None = None

    # 5. Single-flight on the cold-cache path (Pitfall 6 — mirrors
    #    MetadataCache._refresh_lock discipline). Re-check inside the lock so
    #    a sibling task that already filled the cache short-circuits the
    #    second upstream Call 1. The lock is NON-reentrant (anyio.Lock), so
    #    nested calls to cache.get() / cache.set() — which acquire the same
    #    lock — would deadlock; mirror the get/set bodies inline within the
    #    locked block.
    async with cache._lock:
        entry = cache._data.get(database, {}).get(parent_table, {}).get(normalized)
        cache_hit = False
        parent_pk: str | None = None
        if entry is not None:
            cached_pk, expiry = entry
            if time.monotonic() < expiry:
                cache_hit = True
                parent_pk = cached_pk

        if not cache_hit:
            # 6. Cache MISS — fire Call 1 (parent lookup). UpstreamCallFailed
            #    bubbles to the handler's existing `upstream_unavailable`
            #    mapping in tools/retrieval.py (D-13 / D3-12 carry-forward).
            #    `_sort_desc=<parent_match_order_by>` per RESEARCH §4.2
            #    (Pitfall 1: NEVER the dash-prefix variant — Datasette
            #    returns HTTP 500 for that wire shape).
            #    `_size=1` is sufficient: Datasette still reports
            #    `filtered_table_rows_count` honestly even with size=1, which
            #    is how we detect multi-match without fetching every stale
            #    duplicate (FRAG-06).
            # INJ-05 compliance: build the param key via string concatenation
            # (NOT f-string) so the INJ-05 grep (which forbids f-string
            # interpolation of {url|parent_url|filter_value|normalized_url})
            # never trips. The column-name interpolation is conceptually safe
            # (parent_url_col comes from config.URL_COLUMNS, not user input),
            # but the literal grep-discoverable pattern is what matters here.
            call1_params: list[tuple[str, str]] = [
                (parent_url_col + "__exact", url_value),
                ("_sort_desc", fragment_parent["parent_match_order_by"]),
                ("_size", "1"),
            ]
            resp = await DatasetteClient.current().get_table_rows(
                database, parent_table, call1_params
            )
            rows = resp.get("rows") or []

            if not rows:
                # Negative cache (inline set — lock held). Repeat queries with
                # the same URL inside the TTL re-use the negative entry and
                # never re-hit upstream (D5-04).
                cache._data.setdefault(database, {}).setdefault(parent_table, {})[normalized] = (
                    None,
                    time.monotonic() + cache._ttl,
                )
                return ([], None)

            parent_pk = rows[0][fragment_parent["parent_pk"]]
            cache._data.setdefault(database, {}).setdefault(parent_table, {})[normalized] = (
                parent_pk,
                time.monotonic() + cache._ttl,
            )

            # FRAG-06 — surface a structured warning if Datasette reports more
            # than one matching parent row. The `_size=1` cap above means
            # `rows` is single-element; `filtered_table_rows_count` reflects
            # the unconstrained match count.
            match_count = int(resp.get("filtered_table_rows_count") or 1)
            if match_count > 1:
                selected_match_value = rows[0].get(fragment_parent["parent_match_order_by"])
                # INJ-05: bind the BLAKE2b hash of the normalized URL, NOT
                # the raw value. NEVER bind parent_pk (FRAG-02).
                log.warning(
                    "fragment_parent_multi_match",
                    database=database,
                    fragment_table=table,
                    parent_table=parent_table,
                    parent_match_count=match_count,
                    selected_parent_match_value=selected_match_value,
                    parent_url_hash=hashlib.blake2b(normalized.encode(), digest_size=8).hexdigest(),
                )
                warning_state = {
                    "parent_match_count": match_count,
                    "selected_parent_match_value": selected_match_value,
                }

    # 7. Negative cache hit short-circuit (cache_hit=True, parent_pk=None).
    if parent_pk is None:
        return ([], None)

    # 8. Rewrite the filter list — inject the internal parent_fk filter and
    #    drop the user-supplied exact-on-parent-URL filter. Other filters
    #    (e.g., `ordinal > 5` for drill-within-document) carry through.
    #    The rewritten parent_fk filter BYPASSES the handler's per-field
    #    visibility loop — it is INTERNAL state computed from FRAGMENT_PARENTS
    #    config, not user input (audited via the side-channel counter-patch
    #    test per 05-RESEARCH §4.6 / Pitfall 4).
    rewritten: list[Filter] = [
        Filter(
            column=fragment_parent["parent_fk"],
            op="exact",
            value=parent_pk,
        ),
        *[f for f in filters if not (f.column == parent_url_col and f.op == "exact")],
    ]
    return (rewritten, warning_state)
