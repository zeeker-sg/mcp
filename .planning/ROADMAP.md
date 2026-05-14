# Roadmap: Zeeker MCP Connector

## Overview

Nine phases mirroring PRD M1-M9, ordered to retire the highest-risk unknown first. Phase 1 stands up the streamable HTTP transport and the smallest end-to-end tool (`list_databases`) so DNS, TLS, FastMCP, upstream reachability, and envelope shape are all proven on day one. Each subsequent phase delivers a user-observable capability that an MCP client (Claude Desktop, Claude Code, or an Anthropic registry reviewer) can verify externally — discovery surface and denylists (Phase 2), structured retrieval and URL-keyed fetch (Phase 3), cross-database search (Phase 4), transparent fragment-parent joins (Phase 5), envelope hardening and injection-resistance labelling (Phase 6), rate limiting plus error catalog plus operational observability (Phase 7), full test suite plus 24h soak (Phase 8), and submission PR to `anthropics/claude-for-legal` (Phase 9).

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Skeleton transport + first tool** - Streamable HTTP `/mcp` endpoint live; `list_databases` returns the four Zeeker databases inside a stub provenance envelope
- [ ] **Phase 2: Discovery surface + denylists** - `list_tables` and `describe_table` enforce hidden-table/column denylists with no presence side-channel
- [x] **Phase 3: Structured retrieval + URL-keyed fetch** - `query_table` (11 filter operators, qhash cursor, light/heavy columns) and `fetch` (per-table URL mapping) work against real upstream (completed 2026-05-14)
- [ ] **Phase 4: Cross-database search** - `search` runs FTS across the four databases with hidden-table stripping and preview-only rows
- [ ] **Phase 5: Transparent fragment-parent joins** - `query_table` on `*_fragments` tables resolves URL→parent PK→fragment FK transparently and paginates past Datasette's 1k-row cap
- [ ] **Phase 6: Envelope hardening + injection-resistance labelling** - Single EnvelopeBuilder is the only emission path; every tool description ends with the fixed safety trailer
- [ ] **Phase 7: Rate limit + structured errors + healthz + logs** - 20-burst/60-min/5k-24h token bucket; locked error catalog; liveness-only `/healthz`; structured JSON access logs
- [ ] **Phase 8: Full tests + 24h soak** - Filter/envelope/hidden/fragment/rate-limit/error unit coverage; gated live tests against `data.zeeker.sg`; 24h soak validates p95, memory, log growth
- [ ] **Phase 9: Submission PR to anthropics/claude-for-legal** - Public docs, privacy policy, README with 3 use cases + injection-resistance writeup, `.mcp.json` entry mimicking an existing merged entry character-for-character

## Phase Details

### Phase 1: Skeleton transport + first tool
**Goal**: A live MCP server at `/mcp` that any MCP client can connect to and call `list_databases` against, receiving the four Zeeker databases wrapped in a stub provenance envelope.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: TRANSPORT-01, TRANSPORT-02, TRANSPORT-03, TRANSPORT-04, TRANSPORT-05, TRANSPORT-06, DISC-01, ANNO-01, ANNO-02, ANNO-03, ANNO-04, ENV-06, ENV-07, CFG-01, CFG-02, OBS-01, OBS-02, OBS-03, OBS-04, OBS-05
**Success Criteria** (what must be TRUE):
  1. Both Claude Desktop and Claude Code can complete an `initialize` handshake against `https://mcp.zeeker.sg/mcp` over streamable HTTP (POST+GET on a single endpoint), and `tools/list` returns `list_databases` with a flat `type: "object"` schema and `readOnlyHint/idempotentHint/openWorldHint` annotations.
  2. Calling `list_databases` returns the four configured databases (`zeeker-judgements`, `pdpc`, `sg-gov-newsrooms`, `sglawwatch`) each with `description` and `table_count`, wrapped in a `{data, provenance}` envelope emitted only by EnvelopeBuilder.
  3. Every tool description ends with the fixed safety trailer sentence; a startup CI assertion fails the build if any registered tool drifts.
  4. A request with a disallowed `Origin` header is rejected, and `httpx.AsyncClient` is a single process-lifetime instance with explicit `Limits` and `Timeout` set per research recommendations.
  5. Structured JSON logs include `request_id` (contextvar-bound), `tool`, `duration_ms`, `status`, and `ip_prefix` for every request, with the schema field set declared in `config.py`.
**Plans**: 6 plans
Plans:
- [x] 01-01-PLAN.md — Bootstrap: pyproject.toml + package skeleton + Wave-0 test stubs
- [x] 01-02-PLAN.md — config.py (D-21 source of truth) + Envelope/Provenance/Pagination Pydantic models
- [x] 01-03-PLAN.md — Transport stack: FastMCP server + Starlette app + middleware (request_id/origin/access_log) + structlog + httpx lifecycle
- [x] 01-04-PLAN.md — DatasetteClient with retry + list_databases tool + 6 Pydantic input model drafts + NotImplementedError stubs
- [x] 01-05-PLAN.md — Contract & smoke suite: registry-introspection, in-memory MCP smoke, uvicorn random-port smoke, Origin matrix, structlog OBS-01..05
- [x] 01-06-PLAN.md — Deploy: Dockerfile + docker-compose + README "Deployment" prose + manual TRANSPORT-05 checklist for Claude Desktop + Claude Code
**UI hint**: no
**Research flag**: standard patterns — `/gsd-research-phase` optional

### Phase 2: Discovery surface + denylists
**Goal**: An MCP client can enumerate the non-hidden tables in each database and inspect their schemas, with hidden tables and columns indistinguishable from genuinely nonexistent ones.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: DISC-02, DISC-03, DISC-04, DISC-05
**Success Criteria** (what must be TRUE):
  1. `list_tables(database)` returns the non-hidden tables in each of the four databases with row counts and one-line descriptions; `sglawwatch.metadata` and `sglawwatch.schema_versions` never appear in any response.
  2. `describe_table(database, table)` returns exactly `{name, columns, light_columns, available_columns, url_keyed, supports_fragments, row_count, description}` with no `foreign_keys`, `indexes`, or `triggers` forwarded from upstream Datasette.
  3. A request for a hidden table and a request for a genuinely nonexistent table return `unknown_table` with identical message text and indistinguishable timing buckets — no presence side-channel.
  4. `describe_table` distinguishes the default `light_columns` set from the full `available_columns` so a caller knows exactly which columns the `columns` parameter on `query_table` will accept.
  5. `config.HIDDEN_TABLES` and `config.HIDDEN_COLUMNS` are populated for all four databases and reviewable in a single audit pass.
**Plans**: 3 plans
Plans:
- [x] 02-01-PLAN.md — Foundation: config.py extensions + MetadataCache + config_lookup + DatasetteClient column-types + lifespan binding + Wave-0 test stubs + conftest fixtures
- [x] 02-02-PLAN.md — Tools: list_tables + describe_table @mcp.tool implementations + shared _visible_tables/_resolve_table/raise_unknown_* helpers + Envelope.for_table_list + TableSchema + DISC-05 side-channel test
- [x] 02-03-PLAN.md — Tail: F-1 proxy-headers regression test + F-3 stateless_http session regression tests + tests/manual/PHASE2-CLIENT-VERIFY.md with F-4 dry-run obligation
**UI hint**: no
**Research flag**: standard patterns — `/gsd-research-phase` optional

### Phase 3: Structured retrieval + URL-keyed fetch
**Goal**: An MCP client can retrieve rows from any non-hidden table using filters, sort, pagination, and an explicit column allow-list, and can fetch a single row by URL on URL-keyed tables — both with hostile-input-safe error handling.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: QUERY-01, QUERY-02, QUERY-03, QUERY-04, QUERY-05, QUERY-06, QUERY-07, QUERY-08, QUERY-09, QUERY-10, FETCH-01, FETCH-02, FETCH-03, FETCH-04, FETCH-05
**Success Criteria** (what must be TRUE):
  1. `query_table` returns rows filtered by all 11 operators (`exact`, `not`, `contains`, `startswith`, `endswith`, `gt`, `gte`, `lt`, `lte`, `in`, `notin`, `isnull`, `notnull`), sorted by any non-hidden column, with default `limit=50` and maximum `limit=200`.
  2. A `query_table` call without `columns` returns only the per-table light column set; the same call with `columns=["content_text"]` returns the heavy text under each row's `retrieved_content` key, never inlined at the row top level.
  3. A `query_table` call that filters or sorts on a hidden column, or names an unknown/hidden column in `columns`, returns `unknown_column` — and no user-supplied filter value text appears in the error message or any log line for any hostile-input fixture in the test corpus.
  4. Reusing a cursor with a different `sort`/`filters`/`columns` shape returns `invalid_cursor` (qhash mismatch), and walking the cursor with a stable shape returns unique rows with no gaps across the full result set.
  5. `fetch(database, table, url)` returns the non-heavy, non-fragment columns for the matching row on URL-keyed tables; an unmatched URL returns `not_found`, and a `fetch` on a table without URL mapping returns `unsupported_table_for_fetch`. URL match is exact string equality with no silent normalization.
**Plans**: 4 plans
Plans:
**Wave 1**
- [x] 03-01-PLAN.md — Foundation: config.py constants, core/visibility.py, core/filter_compiler.py (13 ops), DatasetteClient.get_table_rows, conftest extension, Wave 0 test stubs

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 03-02-PLAN.md — Slice A: query_table handler with light-column projection (QUERY-01/02/05/06/07/09/10); filter-value safety canary corpus

**Wave 3** *(blocked on Wave 2 completion)*
- [x] 03-03-PLAN.md — Slice B: core/cursor.py + Pagination extension + retrieved_content reshape + qhash cursor walk (QUERY-03/04/08)

**Wave 4** *(blocked on Wave 3 completion)*
- [x] 03-04-PLAN.md — Slice C: fetch handler (FETCH-01..05) + url_column_for helper + tests/manual/PHASE3-CLIENT-VERIFY.md (D3-20)
**UI hint**: no
**Research flag**: needs phase research — filter compiler design + Datasette fixture capture from `data.zeeker.sg`; recommend `/gsd-research-phase` before planning

### Phase 4: Cross-database search
**Goal**: An MCP client can issue a single full-text query across the four Singapore legal databases and receive preview-only result rows with provenance, with hidden-table results stripped and FTS syntax in user input safely escaped.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: SEARCH-01, SEARCH-02, SEARCH-03, SEARCH-04, SEARCH-05, SEARCH-06
**Success Criteria** (what must be TRUE):
  1. `search(query)` with no `databases` parameter searches exactly the four configured databases (explicit list from `config.py`, not "all") via Datasette's `/-/search.json`; default `limit=20`, maximum `limit=100`.
  2. Each result row contains preview fields only — title, date, summary (where present), URL, database, table — with no heavy text columns inlined; rows originating from hidden tables are filtered out before the envelope is built.
  3. User input containing FTS5 special characters (`"`, `(`, `*`, `:`, `OR`, `AND`, `NEAR`) is quote-escaped (wrapped in double quotes, internal quotes doubled), so a query like `Section 5(a)` succeeds as phrase search.
  4. A malformed query that survives escaping and triggers an upstream FTS5 syntax error returns `invalid_query` from the locked error catalog — never `upstream_unavailable` or a pass-through 400.
**Plans**: 4 plans
Plans:
- [x] 04-01-PLAN.md — Foundation: config.py 3 globals + Pagination/Envelope extension + raise_invalid_query + TableSummary.fts_table + UpstreamCallFailed.status + core/fts_escape.py + core/search.py skeleton + tests/conftest.py consolidation + Wave 0 stubs
- [x] 04-02-PLAN.md — Walking slice: core/search.py body (searchable_tables_for + fan_out_search) + tools/search.py @mcp.tool handler body + GREEN tests for happy paths / errors / side-channel / orchestrator
- [x] 04-03-PLAN.md — Hardening: auto-discovery FOUR-gate filter tests + INJ-05 hostile-input corpus (5 canaries × 2 paths)
- [ ] 04-04-PLAN.md — Manual UAT: tests/manual/PHASE4-CLIENT-VERIFY.md (8 scenarios + F-4 dry-run sign-off)
**UI hint**: no
**Research flag**: standard patterns — `/gsd-research-phase` optional

### Phase 5: Transparent fragment-parent joins
**Goal**: An MCP client can paginate over the full set of paragraph-level fragments of any parent record by filtering a `*_fragments` table with the parent's URL — without ever seeing an internal PK or FK, and without losing rows to Datasette's 1,000-row truncation cap.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: FRAG-01, FRAG-02, FRAG-03, FRAG-04, FRAG-05, FRAG-06
**Success Criteria** (what must be TRUE):
  1. A `query_table` call on `judgments_fragments` (or any `*_fragments` table in `FRAGMENT_PARENTS`) with a URL-style filter on the parent's URL column returns ordered fragments for that parent; no `id`, `judgment_id`, `item_id`, or `parent_id` appears anywhere in any response.
  2. A synthetic 1,500-fragment parent paginates fully via repeated `query_table` calls — every fragment returned, no silent loss to the 1,000-row cap, and `pagination.truncated` is surfaced honestly (true when relevant, false otherwise).
  3. Fragments are sorted by the per-table `order_by` column with a deterministic `(order_by, id)` tiebreaker and numeric coercion where applicable; consecutive identical calls return rows in identical order.
  4. When the parent URL maps to multiple parent rows, the join resolves with `ORDER BY updated_at DESC, id ASC LIMIT 1` and emits a structured warning log entry — no non-determinism, no `ambiguous_parent` error in v1.
**Plans**: TBD
**UI hint**: no
**Research flag**: needs phase research — fragment-join orchestration, multi-match parent semantics, truncation handling; recommend `/gsd-research-phase` before planning

### Phase 6: Envelope hardening + injection-resistance labelling
**Goal**: Every successful response across every tool emits an identical, audit-ready provenance envelope; heavy text is structurally impossible to leak outside `retrieved_content`; and every tool description carries the fixed safety trailer that labels retrieved text as data, not instructions.
**Mode:** mvp
**Depends on**: Phase 5
**Requirements**: ENV-01, ENV-02, ENV-03, ENV-04, ENV-05, INJ-01, INJ-02, INJ-03, INJ-04, INJ-05
**Success Criteria** (what must be TRUE):
  1. Every successful response from every tool is wrapped in `{data, provenance, pagination?}` with provenance fields `source` (`data.zeeker.sg`), `database`, `table`, `retrieved_at` (ISO 8601 UTC set at the start of the tool call), `license` (looked up per-database from `config.LICENSES`, not hardcoded `CC-BY-4.0`), and `attribution`.
  2. For tables without a native `citation` column (e.g., PDPC enforcement decisions), the provenance includes a citation string synthesized from URL + date at envelope-build time.
  3. A CI lint asserts that no tool handler returns a raw dict — every emission path runs through `EnvelopeBuilder`; a snapshot test per tool asserts `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` and `set(row["retrieved_content"].keys()) ⊆ HEAVY_COLUMNS`.
  4. A CI assertion verifies that every registered tool description ends with the exact `TOOL_TRAILER` constant; build fails on drift.
  5. A hostile-input test corpus (canary tokens, `</system>`, FTS5 operators, 5 KB random strings, malformed UTF-8) passes through `query_table`, `search`, and `fetch` without any user-supplied filter value appearing in any error message, log line, or LLM-readable metadata field.
**Plans**: TBD
**UI hint**: no
**Research flag**: standard patterns — `/gsd-research-phase` optional

### Phase 7: Rate limit + structured errors + healthz + logs
**Goal**: The server enforces anonymous-tier rate limits with correct `Retry-After` semantics, returns every error from the locked catalog with stable codes plus request ID, exposes liveness on `/healthz` without leaking upstream status, and emits structured JSON access logs that never echo user input.
**Mode:** mvp
**Depends on**: Phase 6
**Requirements**: RATE-01, RATE-02, RATE-03, RATE-04, RATE-05, RATE-06, ERR-01, ERR-02, ERR-03, ERR-04, ERR-05
**Success Criteria** (what must be TRUE):
  1. The token bucket (burst 20, sustained 1 token/second, daily ceiling 5,000 per IP per 24h) is implemented as ASGI middleware so 429s short-circuit before JSON-RPC parsing; on exhaustion the response is HTTP 429 with integer-seconds `Retry-After` header AND `retry_after_seconds` in the structured error payload.
  2. Client IP is derived from `X-Forwarded-For` parsed right-to-left with configurable `TRUSTED_PROXY_DEPTH` (default 1); a test that floods with 10,000 distinct spoofed XFF values from one TCP peer leaves the bucket store size bounded (≤100k keys, LRU eviction, TTL-evict idle buckets).
  3. Every error response carries a stable `code` drawn from the locked catalog (`unknown_database`, `unknown_table`, `unknown_column`, `invalid_filter_op`, `invalid_cursor`, `invalid_query`, `unsupported_table_for_fetch`, `not_found`, `query_timeout`, `rate_limited`, `upstream_unavailable`), a request ID for incident correlation, and never echoes upstream Datasette message bodies.
  4. Upstream 502/503 (not 504) is retried exactly once with 250ms + uniform(0, 250ms) jitter and surfaced as `upstream_unavailable` if the retry fails; 504 surfaces immediately without retry.
  5. `GET /healthz` returns 200 with a minimal payload without consulting `data.zeeker.sg`; upstream-health diagnostics are exposed on a separate operator-only path (e.g., `/internal/upstream-status`), not in the public surface.
  6. Structured JSON access logs emit only the locked field set (`request_id`, `tool`, `database`, `table`, `duration_ms`, `status`, `ip_prefix`, `error_code`); no row contents and no filter values appear in any log line regardless of input size.
**Plans**: TBD
**UI hint**: no
**Research flag**: needs phase research — token-bucket math (burst + sustained + daily simultaneously), XFF semantics, eviction policy; recommend `/gsd-research-phase` before planning

### Phase 8: Full tests + 24h soak
**Goal**: A complete test suite covers every contract surface (filter mapping, envelope shape, hidden-data enforcement, fragment joins, rate-limit windows, error mapping, cursor binding, hostile inputs); gated live tests pass against `data.zeeker.sg`; and a 24h soak validates p50/p95 latency, concurrency, memory, and pool stability.
**Mode:** mvp
**Depends on**: Phase 7
**Requirements**: TEST-01, TEST-02, TEST-03, TEST-04, TEST-05, TEST-06, NFR-01, NFR-02, NFR-03, NFR-04, NFR-05
**Success Criteria** (what must be TRUE):
  1. Unit tests cover all 11 filter operators, envelope shape per tool, hidden-table/column rejection on both edges, fragment-parent join behavior (including multi-match and 1,500-fragment regression), rate-limit burst+sustained+daily windows, cursor qhash mismatch rejection, and the full error code catalog.
  2. Snapshot tests per tool assert `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` and `set(row["retrieved_content"].keys()) ⊆ HEAVY_COLUMNS`; a hostile-input corpus exercises filter-value-echo paths across error messages, logs, and metadata fields.
  3. Live integration tests gated by `ZEEKER_LIVE=1` pass against the real `data.zeeker.sg` deployment (nightly + pre-release schedule documented in CI).
  4. A 24h soak under synthetic load shows p50 < 300ms and p95 < 1.5s for non-fragment tools, stable resident memory < 256 MB, 50 concurrent requests handled without saturation, no `PoolTimeout` cascade, bounded log growth, and correct daily rate-limit rollover.
  5. Runtime dependency footprint is exactly six packages (`fastmcp`, `httpx`, `starlette`, `uvicorn`, `pydantic`, `structlog`) plus four dev packages (`pytest`, `pytest-asyncio`, `pytest-httpx`, `ruff`); deployment README documents the Anthropic IP-allowlist requirement and the single-worker Uvicorn constraint.
**Plans**: TBD
**UI hint**: no
**Research flag**: standard patterns — `/gsd-research-phase` optional

### Phase 9: Submission PR to anthropics/claude-for-legal
**Goal**: A PR is open against `anthropics/claude-for-legal` adding the Zeeker connector to at least one plugin's default `.mcp.json` (target: `regulatory-legal` first), with a README, public docs at `mcp.zeeker.sg/docs`, a privacy policy at a stable URL, three concrete LLM use cases, an injection-resistance writeup, and live tests passing within the last 7 days.
**Mode:** mvp
**Depends on**: Phase 8
**Requirements**: SUB-01, SUB-02, SUB-03, SUB-04, SUB-05, SUB-06, SUB-07
**Success Criteria** (what must be TRUE):
  1. Public docs are live at `mcp.zeeker.sg/docs` covering all six tools, their parameters, the locked error catalog, rate-limit semantics, the provenance envelope shape, and the injection-resistance posture; privacy policy is published at a stable URL.
  2. README in the connector repo includes three concrete LLM use cases tied directly to the target plugin's stated purpose (recommended starting plugin: `regulatory-legal`) plus an injection-resistance writeup section.
  3. A PR is open against `anthropics/claude-for-legal` adding a `.mcp.json` entry to at least one plugin; the entry is formatted character-for-character against an existing merged plugin entry (key casing, field order, tagline length, description length).
  4. Live integration tests against `data.zeeker.sg` have passed within the last 7 days of PR submission, with the latest run linked from the PR description.
**Plans**: TBD
**UI hint**: no
**Research flag**: needs phase research — mimic existing merged `.mcp.json` character-for-character; survey claude-for-legal merged entries; recommend `/gsd-research-phase` before planning

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Skeleton transport + first tool | 0/6 | Not started | - |
| 2. Discovery surface + denylists | 0/3 | Not started | - |
| 3. Structured retrieval + URL-keyed fetch | 4/4 | Complete    | 2026-05-14 |
| 4. Cross-database search | 0/4 | Not started | - |
| 5. Transparent fragment-parent joins | 0/TBD | Not started | - |
| 6. Envelope hardening + injection-resistance labelling | 0/TBD | Not started | - |
| 7. Rate limit + structured errors + healthz + logs | 0/TBD | Not started | - |
| 8. Full tests + 24h soak | 0/TBD | Not started | - |
| 9. Submission PR to anthropics/claude-for-legal | 0/TBD | Not started | - |
