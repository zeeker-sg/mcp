# Changelog

## [Unreleased]

### Fixed — Search returned no results for any query that actually matched

`search()` was returning `data: []` with every `upstream_total_hits` entry at
zero, while the same terms matched real rows through Datasette directly. A
scoped search whose targets all had hits (e.g.
`search(query="data", databases=["pdpc"])`) was rejected outright as
`invalid_query: query syntax not supported`.

- **Root cause**: SQLite FTS5 auxiliary functions (`bm25`, `snippet`,
  `highlight`) are only callable while the fts5 cursor sits on the matched
  row. `build_bm25_sql` called `bm25(...)` in the same SELECT as
  `count(*) OVER ()`, and `build_maxp_sql` wrapped it in `MIN(...)`. Both
  force SQLite to buffer/sort rows first, tearing down that context and
  raising `unable to use function bm25 in the requested context` — HTTP 400
  from Datasette.
- **Why it looked healthy**: the failure is data-dependent. A query matching
  nothing never invokes `bm25`, so zero-hit tables returned a clean 200 while
  every table that had hits returned a 400. The envelope reported honest
  zeroes for the former and counted the latter in `failed_tables`; when ALL
  targets had hits, the all-400 → `invalid_query` promotion (D4-09 case (c))
  fired and a single common word was reported as bad syntax.
- **Fix**: both SQL builders now isolate `bm25()` in an innermost `_hits`
  SELECT that contains no window function, no aggregate, and no join, with
  `LIMIT -1` to block subquery flattening. Ranking, the full-match-set
  `count(*) OVER ()` total, and the join back to the content table all happen
  at outer levels. Only the top-`limit` rowids reach the content-table join,
  so the largest corpus materializes far less than before.
- **Regression cover**: `tests/core/test_search_sql_executes.py` runs the
  emitted SQL against a real SQLite FTS5 index for every configured searchable
  table and fragment source, asserting non-empty results for a matching term
  (and clean-empty for a non-matching one). The previous builder tests
  asserted on SQL substrings only and passed throughout the outage.
  `tests/test_live_golden_path.py` gains a live cross-check that fails only
  when MCP and Datasette disagree about whether a term matches.

### Fixed — Timed-out search targets were reported as zero hits

`zeeker-judgements.judgments` — the largest table (10,804 rows) and the most
relevant for most queries — never appeared in `upstream_total_hits`, with
`failed_tables: 0`, making "we never heard back" indistinguishable from
"upstream said zero".

- The 0.8 s fan-out budget was cancelling it, and cancelled tasks contributed
  nothing to the failure accounting by design.
- `fan_out_search` now reconciles cancelled targets after the budget expires:
  they stay absent from `upstream_total_hits` and are counted in
  `failed_tables` with a `None` status, so a timeout can never be misread as
  an FTS5 syntax error by the all-400 promotion. A
  `search_fan_out_budget_exceeded` warning is logged with the cancelled count.
- The budget moved to `config.SEARCH_FAN_OUT_TIMEOUT_S` (default 2.0 s,
  overridable via the `SEARCH_FAN_OUT_TIMEOUT_S` env var) so it is tunable
  without a code change.

### Changed — `search` query parameter description

The parameter claimed queries were "FTS5 phrase-wrapped server-side", which
stopped being true when issue #12 introduced term-level escaping. It now
states the actual contract: terms are AND-ed with adjacency not required, a
double-quoted query is an exact phrase, and FTS5 operators are escaped and
matched literally.

### Changed — Stateless MCP spec migration (partial)

Adopted the July 28, 2026 MCP spec revision in substance. The server was
already stateless (`stateless_http=True`, no session store, self-contained
qhash pagination cursors, TTL caches only); this release formalizes the
transition while maintaining full backwards compatibility with legacy clients.

- **Dependency bump**: `fastmcp` 3.2.4 → 3.4.7. All 579 tests pass. The
  middleware API (`Middleware`, `MiddlewareContext`, `add_middleware` FIFO
  ordering) and `http_app(path="/", stateless_http=True)` construction are
  unchanged. The FastMCP 4.x bump (which carries formal July-2026 spec
  support) is gated on its stable release — currently `4.0.0b2` on PyPI.
- **Session metric**: `SessionLogMiddleware` now emits two events:
  - `session_start` — on every MCP `initialize` handshake (legacy clients,
    unchanged from #5)
  - `first_request` — on the first non-`initialize` MCP request (new-spec
    clients that skip the handshake per the July 2026 revision)
  - `FIRST_REQUEST_FIELDS` locked field set added to `config.py`
- **Protocol string**: GET `/mcp/` status body now includes `"stateless":true`
  alongside the existing `"protocol":"2025-06-18"` advertisement
- **CORS headers**: `mcp-session-id` and `mcp-protocol-version` kept in the
  allow-list for legacy clients; removal note added with target date 2027-01
- **Documentation**: README "Stable server identifier" section rewritten for
  the new discovery mechanism; CLAUDE.md notes the targeted spec version and
  legacy-client compatibility stance

### Compatibility

- Old clients (pre-2026 spec): `initialize` handshake works as before;
  `session_start` events emitted
- New clients (July 2026 spec): no handshake needed; `first_request` events
  emitted on first tool call
- Container restart mid-conversation: no impact (stateless by construction
  since commit 4ce06d5; spec-guaranteed under the new revision)