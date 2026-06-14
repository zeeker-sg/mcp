# Issue #6 — `search` ~13s per-call structural cost: approaches & recommendation

**Researched:** 2026-06-14
**Issue:** [#6 — `search` tool is uniformly ~13s in production — investigate per-call structural cost](https://github.com/zeeker-sg/mcp/issues/6)
**Status:** Research only — no code changed. Grounds the issue's "measure first, then decide what to fix" plan in the actual call graph.
**Confidence:** HIGH on the call-graph / root-cause (read from source); the absolute millisecond split is an estimate pending the Stage-1 instrumentation.

---

## 1. Problem restatement

`search` p50 ≈ p90 ≈ max ≈ 13s — a flat latency profile that says *fixed per-call structural cost*, not data-dependent query variance. The issue hypothesizes the cost is in auto-discovery and grows with (DBs × tables). The code confirms it and pinpoints the exact mechanism.

---

## 2. Root cause (grounded in code)

### 2.1 The hot call: `get_database()` is uncached
`DatasetteClient.get_database(name)` issues a fresh upstream `GET /{name}.json` **every call, with no caching** (`core/datasette_client.py:206-209`). The `MetadataCache` (1800s TTL) caches `/-/metadata.json` only; `searchable_tables_for` *deliberately* bypasses it because `/-/metadata.json` lacks the `fts_table` field (`core/search.py:131-133`). So the per-DB table/column/FTS summary is re-fetched from upstream on every use.

### 2.2 How many times per `search`
`/{db}.json` is the single most-repeated upstream call in a search. Per database, in **sequential awaits**:

| Call site | Source | `get_database` calls |
|---|---|---|
| `searchable_tables_for(db)` direct | `core/search.py:145` | 1 |
| → `_visible_tables(db)` inside it | `core/search.py:146` → `visibility.py:128` | 1 |
| handler Step 5 `_visible_columns(db, table)`, **per discovered table** | `tools/search.py:170` → `visibility.py:167` | 1 × N_tables |
| Step 10 post-filter `_visible_tables(db_for_row)` (handler-cached per DB) | `tools/search.py:222` | ≤1 |

For the default 4-DB search (~12 searchable tables: ~1 judgments, 0 pdpc-no-FTS, ~8 newsrooms, ~3 sglawwatch):

| DB | tables (N) | `get_database` calls (2 + N) |
|---|---|---|
| zeeker-judgements | 1 | 3 |
| pdpc | 0 | 2 |
| sg-gov-newsrooms | 8 | 10 |
| sglawwatch | 3 | 5 |
| **total** | **12** | **~20** (+ up to 4 post-filter) |

~20 serial, uncached `/{db}.json` fetches. `/{db}.json` is heavier than a row query (Datasette enumerates tables + columns + counts), and cold-path latency in the hundreds of ms × 20 sequential ≈ the observed ~13s. The count is fixed by schema, not query → the flat p50≈p90≈max profile. **This is the structural cost.**

### 2.3 Why it's a scaling cliff
Cost ≈ `2·D + T` sequential round-trips (D databases, T searchable tables). Every database added in Phase 7+ adds its tables linearly to a **serial** path. The `searchable_tables_for` "add a DB, no code change" feature trades code edits for per-call latency that compounds with catalogue growth — exactly the issue's concern.

### 2.4 Corroborating signal
The `list_databases` probe in #5 took **5,782ms** cold. `list_databases` also fans `get_database` across the 4 DBs (for table counts) — 4 uncached serial fetches ≈ 5.8s. Same root cause, smaller multiplier. Caching `get_database` speeds up `list_tables` / `describe_table` / `list_databases` too, not just `search`.

---

## 3. Approaches

Ordered to honor the issue's "measure first" mandate, then fix the dominant cost.

### Stage 1 — Instrument (the issue's first checkbox) ⭐ do first
Split `search`'s `duration_ms` into `discovery_ms` / `fan_out_ms` / `post_filter_ms` and bind them to the `tool_call` log line for `search`. Wrap the three handler regions with `time.perf_counter()` and bind via `structlog.contextvars.bind_contextvars` inside the handler (the `StructuredLogMiddleware` line picks them up — same mechanism `database`/`table` already use). **Expected:** `discovery_ms` ≈ 95%+ of the total, confirming §2 before we touch behavior. Low effort, zero risk, and it's the evidence the issue asks for.

### Stage 2 — Kill the redundant `get_database` calls (the dominant fix)
Two complementary levers, smallest-blast-radius first:

- **2a. Request-scoped memoization (safe, immediate).** Within one `search`, `get_database(db)` returns identical data ~5× per DB. Memoize per (request, db) so each DB is fetched **once** → ~20 calls collapse to ~4 per search, **zero staleness risk** (same request = same snapshot, which is also what the post-filter wants). Implementable as a small per-call dict threaded through discovery, or an `anyio`/contextvar request cache.
- **2b. Cross-request TTL cache for `get_database` (the big win).** A `DatabaseSummaryCache` keyed by db name, TTL ~300–1800s, mirroring `MetadataCache`'s shape. Table/column/FTS structure changes rarely; serving it from memory amortizes discovery to ~0 upstream calls in steady state and removes the scaling cliff. **Must be single-flight per key** (an `anyio.Lock`/in-flight map) so the 8-parallel-search burst from the issue doesn't stampede 8× misses into 8× upstream fetches — the cache has to *help* exactly the burst case, not just the steady case.

2a alone is a ~5× cut with no staleness; 2b makes discovery effectively free. Recommend both: 2a as the correctness-preserving floor, 2b for the steady-state win.

### Stage 3 — Remove the per-table re-resolution (altitude / structural)
Handler Step 5 re-fetches columns via `_visible_columns(db, table)` **per table** (`tools/search.py:170`) even though `searchable_tables_for` *already* had `TableSummary.columns` in hand and already resolved the preview (`core/search.py:161-162`). Thread the resolved `(table, preview, columns)` out of discovery so the handler doesn't re-resolve — this deletes the `T`-sized inner loop of `get_database` calls at the source, independent of caching. Cleanest depth-fix; pairs well with Stage 2 but stands alone.

### Parallelize discovery (subsumed, note only)
`gather`-ing `get_database` across the 4 DBs instead of sequential awaits would cut DB-level fetches to ~1 RTT, but it doesn't fix the per-table repetition and is made moot by Stage 2's caching. Not worth its own change.

### Summary table

| Approach | Effort | Latency win | Staleness risk | Recommend |
|---|---|---|---|---|
| **S1 instrument** | low | none (measures) | none | **yes, first** |
| **S2a request memoization** | low | ~5× (20→~4 calls) | none | **yes** |
| **S2b TTL cache + single-flight** | medium | ~steady-state ~0 | bounded by TTL | **yes** |
| S3 thread columns from discovery | medium | removes T-sized loop | none | yes (with S2) |
| parallelize discovery only | low | DB-level only | none | skip (subsumed) |

---

## 4. Caching design notes (for Stage 2b)

- **TTL value:** start at the `MetadataCache` default (1800s) or lower (300s) for fresher FTS-index discovery. Config-driven (`config.py`), like `METADATA_TTL_SECONDS`.
- **Single-flight:** per-key in-flight lock so concurrent misses share one upstream fetch (the 8-parallel-search burst is the design target, not an afterthought).
- **Invalidation:** TTL-only (no write path exists; upstream changes rarely). Document that a newly added upstream table/FTS index appears after ≤TTL.
- **Interaction with the Step 10 post-filter:** that post-filter is a "rare race" guard for a table vanishing between dispatch and response (`tools/search.py:213-225`). Under request-scoped caching it reads the same snapshot as discovery, so the race window collapses and the guard becomes near-vacuous — acceptable (visibility correctness still holds against the cached snapshot), but call it out so nobody thinks the guard still does work it no longer does.
- **Memory:** 4 small `DatabaseSummary` objects — negligible against the <256MB budget.

---

## 5. Recommendation

1. **Stage 1 instrument** and confirm `discovery_ms` dominates (evidence the issue asks for; ship + read prod once).
2. **Stage 2a request-scoped memoization** — immediate ~5× cut, no staleness, low risk.
3. **Stage 2b TTL cache + single-flight** — steady-state ~0 discovery cost; validate against a reproduced 8-parallel-search burst load test (the issue's last checkbox).
4. **Stage 3** thread columns out of discovery to delete the redundant per-table loop at the source.
5. **Document** the per-DB `search` cost expectation in CONTRIBUTING / the search tool description so future DB additions are eyes-open (issue's third checkbox).

### What this touches (for the eventual implementation)
- `core/datasette_client.py` or a new `core/database_summary_cache.py` — the cache + single-flight.
- `core/search.py` / `tools/search.py` — memoization wiring; thread columns through discovery (Stage 3).
- `core/middleware/access_log.py` consumers — sub-timing contextvars for `search` (Stage 1).
- `config.py` — `DATABASE_SUMMARY_TTL_SECONDS`.
- `tests/` — cache hit/miss + single-flight under concurrency; a burst load test; assert `tool_call` sub-timing fields for `search`.

## 6. Out of scope (per the issue)
- Replacing Datasette as the FTS backend.
- Cross-database query planning (skipping DBs whose indexes contain none of the query terms).
- Changing the round-robin merge in `fan_out_search`.

## 7. Open questions
1. **Stage-1 split — separate fields vs one event?** `search` sub-timings (`discovery_ms`/`fan_out_ms`/`post_filter_ms`) are search-only; binding them onto the shared `tool_call` line means other tools log nulls for them. Decide: extra optional fields on `tool_call`, or a separate `search_timing` event. (Mirrors the #5 `LOG_FIELDS` locked-tuple tension — there's a test asserting the `tool_call` field set.)
2. **TTL value** — 1800s (match MetadataCache) vs shorter for fresher FTS discovery. Operational call.
3. **Cache scope** — extend `MetadataCache` to also hold `/{db}.json`, or a dedicated `DatabaseSummaryCache`? Dedicated is cleaner (different endpoint, different shape); decide before coding.

---

## 8. Update 2026-06-14 — expanded levers, Lever-4 feasibility, subissue split

After a brainstorm, the approaches group into six levers: **do less** (memoize, thread columns, collapse the two discovery passes), **reuse** (TTL cache, stale-while-revalidate, startup warm), **parallelize** (concurrent discovery), **cheaper-per-call** (count-free / SQL discovery — "Lever 4"), **move off hot path** (background-refreshed catalogue, or a generated static manifest), and **bound the damage** (a discovery deadline; today only fan-out is budgeted).

### Lever 4 feasibility — does Datasette support a cheaper discovery call?
**The software supports it (confirmed via Datasette docs).** Instance enablement on `data.zeeker.sg` is the only remaining unknown.

- **Arbitrary SQL over HTTP, JSON out — supported.** `/{db}/-/query.json?sql=…`, gated by the `execute-sql` permission (on by default in vanilla Datasette). One `select name, sql from sqlite_master where type='table'` per DB returns table names + DDL (columns derivable; FTS detectable via the `USING fts5` clause) in a single cheap query, bypassing the `/{db}.json` table-enumeration / `count(*)` cost.
- **`?_nocount=1` — supported** (documented to disable full result counts); `?_size=` controls page size. Attacks the `count(*)` cost directly.
- **Hard constraint:** whatever replaces `/{db}.json` must still yield `fts_table` + `columns` + `hidden`, which `DatabaseSummary` parses. The `sqlite_master` route reconstructs these but is real work.
- **Cannot be verified from the dev sandbox:** the environment's egress allowlist blocks `data.zeeker.sg`, and the anonymous tier 403s the catalogue endpoints anyway — the probe needs the owner token + network, i.e. the prod host / MCP container.
- **Net:** Lever 4 went from "is it even possible?" (yes) to "is `execute-sql` enabled for our token, and is it actually faster here?" — a ~15-minute measurement, not a design unknown. Sources: docs.datasette.io JSON API / Running SQL queries / Pages and API endpoints.

Probe runbook (run on the prod host, which has network + `ZEEKER_FULL_ACCESS_TOKEN`):
```bash
B=https://data.zeeker.sg ; H="Authorization: Bearer $ZEEKER_FULL_ACCESS_TOKEN"
for i in 1 2 3; do curl -s -H "$H" -o /dev/null -w "base t=%{time_total}s sz=%{size_download}\n" "$B/sg-gov-newsrooms.json"; done
curl -s -H "$H" -o /dev/null -w "nocount t=%{time_total}s\n" "$B/sg-gov-newsrooms.json?_nocount=1"
curl -s -H "$H" -w "\nsql t=%{time_total}s\n" "$B/sg-gov-newsrooms/-/query.json?sql=select+name,sql+from+sqlite_master+where+type='table'" | head -c 400
```

### Subissue split (created 2026-06-14)
#6 is now a tracking parent with three subissues on the path to green:
- **#8 — [#6a]** instrument search sub-timings (`discovery_ms`/`fan_out_ms`/`post_filter_ms`) — ships first, no deps.
- **#9 — [#6b]** eliminate redundant `get_database` calls (request memoization + thread columns).
- **#10 — [#6c]** cache `get_database` (single-flight TTL or background catalogue).

Lever 4 (cheaper-per-call) is held as a deferred **measurement spike** — not filed — because #6c may make discovery cost ~0 regardless. The discovery-deadline "bound the damage" idea is also unfiled; worth adding as insurance whenever #6c lands.
