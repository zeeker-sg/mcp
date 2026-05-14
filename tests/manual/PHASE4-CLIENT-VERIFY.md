# Phase 4 — Client Verification Checklist (Cross-Database Search)

Walk this checklist against the DEPLOYED instance at `https://mcp.zeeker.sg/mcp` (preferred,
once the Phase 4 image is rolled out) OR against a local server:

```
uv run uvicorn mcp_zeeker.app:app --host 127.0.0.1 --port 8080
```

The local path proves the handler logic end-to-end without depending on the deployment
schedule. The remote path proves DNS + TLS + Caddy + the docker-network sibling-container
path end-to-end. Walk BOTH if the Phase 4 image is live; the local path alone is acceptable
if the deployment has not yet been updated.

> **F-4 OBLIGATION — DRY-RUN OBLIGATORY before declaring this plan complete.**
> Per 01-LEARNINGS.md F-4 (ratified in Phase 2 + Phase 3): every curl example in this
> document MUST be dry-run against the chosen target (live or local) before marking
> Phase 4 complete. See the F-4 Sign-off block at the bottom.

## Scope

Phase 4 ships ONE new tool: `search(query, databases?, limit?)`. The handler fans out
across the 12 currently-searchable Singapore legal tables on `data.zeeker.sg`. Searchable
tables are **auto-discovered** from upstream FTS metadata at request time (cached for
~30 minutes) — there is NO per-table allow-list to keep in sync. The four databases
auto-discovery operates over are the same four Phase 1/2/3 already gate on:

| Database            | FTS-having tables (approx.)               | Notes                                      |
|---------------------|-------------------------------------------|--------------------------------------------|
| `zeeker-judgements` | `judgments` (1 FTS table)                 | Heavy `content_text` always stripped       |
| `pdpc`              | **(none — no FTS index upstream)**        | Auto-discovery returns empty (Scenario 3)  |
| `sg-gov-newsrooms`  | 8 `*_news` tables (acra, mas, mlaw, ...)  | Round-robin biases toward this DB (D4-05)  |
| `sglawwatch`        | `headlines`, `commentaries`, `summaries`  | High-cardinality FTS (Scenario 7)          |

**Design properties the human verifier MUST understand BEFORE walking the scenarios** —
these are documented design choices, NOT bugs to file:

1. **`pdpc` returns empty results for every search.** Upstream has no FTS index on any
   pdpc table (`fts_table: None` in the metadata). Auto-discovery's FOUR-gate filter
   drops every pdpc table. This is D4-03 / Pitfall 3 — Scenario 3 confirms it end-to-end.
2. **Round-robin merge biases toward `sg-gov-newsrooms`.** With 8 searchable tables vs
   1-3 in the other DBs, sg-gov-newsrooms claims 8 of every 12 round-robin slots. This
   is D4-05 — Scenarios 1 and 7 surface it. Callers who want to scope can pass
   `databases=["zeeker-judgements"]` etc.
3. **First call after deployment may show `failed_tables > 0` and latency ≥ 0.8s.**
   The 0.8s `anyio.move_on_after` budget at D4-06 will cancel any per-table call that
   exceeds it. Cold-cache p99 per-table is 1.6s upstream. After 1-2 warm calls, latency
   drops to 100-200ms wall and `failed_tables` settles to 0. This is Pitfall 4 — Scenario
   8 confirms it; the operator runbook MUST reference this scenario before paging on
   cold-call latency.

## Pre-conditions

Phase 1 / Phase 2 / Phase 3 pre-conditions remain valid (DNS, TLS, `/healthz`,
trailing-slash preserving HTTPS, `initialize` handshake clean, the prior 5 tool names
register cleanly). Additionally for Phase 4:

- [ ] Target is reachable. For local:
  ```
  curl -sf http://127.0.0.1:8080/healthz
  ```
  For remote:
  ```
  curl -sf https://mcp.zeeker.sg/healthz
  ```
  Both must return HTTP 200 with body `{"status":"ok"}`.

- [ ] `tools/list` returns SIX tool names: `list_databases`, `list_tables`,
  `describe_table`, `query_table`, `fetch`, **`search`**. Run `initialize` first
  (required by MCP spec), then `tools/list` in the same curl session. Because
  `stateless_http=True`, you do NOT need to capture an Mcp-Session-Id:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    <TARGET>/mcp/
  ```
  Expected: response body (in `data:` SSE event or JSON body) includes all six tool
  names. Crucially, `search` appears in addition to the Phase 1-3 five.

- [ ] Upstream `data.zeeker.sg` is reachable and at least one searchable table is
  populated. Quick smoke:
  ```
  curl -sf "https://data.zeeker.sg/zeeker-judgements/judgments.json?_search=privacy&_size=2&_shape=objects" \
    | python3 -m json.tool | head -30
  ```
  Expected: `"rows": [ … ]` non-empty AND `"filtered_table_rows_count": <integer>≥1`.
  If this returns 0 rows, swap "privacy" for "court" in Scenarios 1, 4, 5, 7 before
  proceeding (the canary query needs to actually return results upstream for those
  scenarios to be observable).

## Claude Desktop

1. Open `claude_desktop_config.json` (Settings → Developer → Edit Config).
2. Ensure `mcpServers` contains the zeeker entry:
   ```json
   {
     "zeeker": {
       "url": "https://mcp.zeeker.sg/mcp"
     }
   }
   ```
3. Restart Claude Desktop. Confirm zeeker appears as a connected MCP server. Confirm the
   tool list under the server name shows `search` alongside the Phase 1-3 five.

## Scenarios (D4-20)

### Scenario 1 — basic `search("privacy")`

- [ ] In a new chat, prompt:
  > "Use the zeeker MCP server to search across the Singapore legal corpus for the
  > word 'privacy'. Return the default page of results."
- [ ] Expected tool call: `search(query="privacy")` with default databases + default
  `limit=20`.
- [ ] Expected envelope shape:
  - `data` is a list of ≤ 20 preview rows.
  - **Every** row has exactly the keys `{title, date, summary, url, database, table}` —
    no `content_text`, no `id`, no fragment-FK columns (D4-12 normalization).
  - `provenance.database is None` and `provenance.table is None` (cross-DB scope —
    D4-16).
  - `provenance.license == "mixed"` (D4-16 `LICENSE_MIXED`).
  - `pagination.upstream_total_hits` is a dict with ~12 keys of the form
    `"<db>.<table>"` (D4-17). Pdpc tables MUST NOT appear in the key set.
  - `pagination.failed_tables == 0` on a warm system.
- [ ] Round-robin bias check: count how many of the 20 rows have `database ==
  "sg-gov-newsrooms"`. Expect ≥ 8 (8 tables × 1 round of the first round-robin
  pass). This is D4-05 — DO NOT file as a bug.
- [ ] Screenshot the response. Save to
  `evidence/04-search/scenario-01-basic-search.png`.

### Scenario 2 — `search("Section 5(a)", databases=["zeeker-judgements"])` (escape verification)

- [ ] Prompt:
  > "Search zeeker-judgements only for the exact phrase 'Section 5(a)'."
- [ ] Expected tool call: `search(query="Section 5(a)", databases=["zeeker-judgements"])`.
- [ ] Expected: the parentheses do NOT cause an FTS5 syntax error. `escape_fts5`
  (D4-08) wraps the query as `"Section 5(a)"` (with embedded double-quote pair around
  the whole phrase) before it reaches Datasette. Verify (if you have wire access to
  the server logs OR via the structlog DEBUG channel) that the dispatched upstream
  URL contains the URL-encoded form `%22Section+5%28a%29%22` (or
  `%22Section%205%28a%29%22`). Either way:
  - The envelope MUST contain rows (FTS5 ops like `(` `)` `*` `OR` `AND` `NEAR` no
    longer break the search).
  - No ToolError raised.
  - `failed_tables == 0`.
- [ ] Screenshot the response. Save to
  `evidence/04-search/scenario-02-escape-verification.png`.

### Scenario 3 — `search("privacy", databases=["pdpc"])` (empty-envelope path)

- [ ] Prompt:
  > "Search only the pdpc database for 'privacy'."
- [ ] Expected tool call: `search(query="privacy", databases=["pdpc"])`.
- [ ] Expected envelope:
  - `envelope.data == []` (empty list, NOT a ToolError).
  - `envelope.pagination.upstream_total_hits == {}` (empty dict — auto-discovery
    returned ZERO searchable tables for pdpc).
  - `envelope.pagination.failed_tables == 0` (zero tables dispatched, zero
    failed — not the same as "some tables failed").
  - `envelope.provenance.license == "mixed"` (multi-DB factory still emits the
    `for_search_results` envelope shape per D4-16).
- [ ] Expected LLM behavior: per the tool description (D4-15 — "databases without
  a full-text index upstream are silently skipped"), the LLM should explain to the
  user something like "the pdpc database does not have a full-text index, so this
  search returned no results. To browse pdpc enforcement decisions directly, use
  `list_tables` and `query_table` to filter by date or party." The LLM MUST NOT
  infer that search is broken.
- [ ] **Safety property:** confirm via the request log (if accessible) that NO
  `/pdpc/...?_search=` URL was dispatched to upstream. Pdpc-no-dispatch is a
  load-bearing safety property (Pitfall 3) — if pdpc rows ARE returned, the
  fts_table FOUR-gate failed in production: ESCALATE.
- [ ] Screenshot the response. Save to
  `evidence/04-search/scenario-03-pdpc-empty.png`.

### Scenario 4 — `search("privacy", limit=100)` deterministic ordering

- [ ] First call:
  > "Search for 'privacy' across all databases with limit 100. Show me the full
  > list of (database, table, title, date)."
- [ ] Expected tool call: `search(query="privacy", limit=100)`.
- [ ] Capture the response. Save the `.data` field to
  `/tmp/scenario-04-run-1.json`.
- [ ] Within 5 seconds (so the 30-min cache is still warm), repeat:
  > "Run that exact same search again with the same parameters."
- [ ] Expected tool call: identical `search(query="privacy", limit=100)`.
- [ ] Capture and save to `/tmp/scenario-04-run-2.json`.
- [ ] Compare:
  ```
  diff <(python3 -c "import json,sys; print(json.dumps([(r['database'], r['table'], r['title']) for r in json.load(open('/tmp/scenario-04-run-1.json'))], indent=2))") \
       <(python3 -c "import json,sys; print(json.dumps([(r['database'], r['table'], r['title']) for r in json.load(open('/tmp/scenario-04-run-2.json'))], indent=2))")
  ```
  Expected: ZERO output (identical). Round-robin merge is deterministic per D4-05
  (alphabetical DB iteration → upstream-metadata table order within DB → upstream
  FTS rank within table). If the diff is non-empty, either auto-discovery iteration
  order or the round-robin merge has a bug: ESCALATE.
- [ ] Screenshot both responses + the diff result. Save to
  `evidence/04-search/scenario-04-deterministic-ordering.png`.

### Scenario 5 — `search → fetch` chain

- [ ] Prompt:
  > "Search for 'court' and then fetch the full row of the first result by its url."
- [ ] Expected tool calls (in this order):
  1. `search(query="court")`.
  2. From the first row of the response, extract `database`, `table`, and `url`.
  3. `fetch(database=<that-db>, table=<that-table>, url=<that-url>)`.
- [ ] Expected:
  - The search result row has a non-null `url` field (URL_COLUMNS exists for the
    returned `(database, table)` — Phase 3 D3-13 FETCH-02).
  - The fetch call returns an envelope with `data` of EXACTLY ONE row containing
    the light columns + matching `source_url` (Phase 3 contract — no
    `content_text`, no `retrieved_content`, no `id`).
- [ ] If `fetch` raises `unsupported_table_for_fetch: <table>` for a table that
  search just returned a row from, ESCALATE — search and fetch's URL_COLUMNS
  contracts have drifted. The intersection of "searchable" and "fetchable" should
  be the set of FTS-having parent tables; if search returns a row that fetch
  rejects, the planner missed a URL_COLUMNS entry. Note: some search-returned
  tables are fragment tables (`*_fragments`) which are reached via
  `query_table(parent_fk)`, NOT fetch — auto-discovery already excludes those via
  the `SEARCH_DENYLIST_PATTERNS` denylist (Plan 04-01 / D4-04). If a fragment table
  appears in search results, ESCALATE — denylist regressed.
- [ ] Screenshot both tool calls. Save to
  `evidence/04-search/scenario-05-search-fetch-chain.png`.

### Scenario 6 — hostile query (canary) never echoed (INJ-05)

- [ ] Prompt:
  > "Run `search(query='ZEEKER_CANARY_42')` against the zeeker MCP server."
- [ ] Expected tool call: `search(query="ZEEKER_CANARY_42")`.
- [ ] Expected envelope shape: either
  - (a) `data == []` with non-zero `failed_tables` (if the canary is so weird that
    every per-table FTS dispatch returns 400 — extremely unlikely for a plain
    `[A-Za-z0-9_]+` token), OR
  - (b) `data == []` with `failed_tables == 0` and `upstream_total_hits` having
    every dispatched table at value `0` (the canary matched nothing — the expected
    path), OR
  - (c) a single `ToolError` with code prefix `invalid_query:` if the canary somehow
    tripped the strip()/escape contract (extremely unlikely for `ZEEKER_CANARY_42`,
    but the catalog is locked at D4-09).
- [ ] **INJ-05 acceptance gate (the load-bearing assertion):** the canary string
  `ZEEKER_CANARY_42` MUST NOT appear in:
  - The response JSON body (no envelope field carries it back).
  - Any ToolError message (FIXED-literal discipline at D4-09).
  - Any visible log line emitted by `mcp_zeeker.*` (debug level OK to inspect; the
    application-code log fields are restricted to `database`, `table`,
    `error_class` per D4-07 — NEVER `query=...`).
  - Any stderr output from the uvicorn process.

  Confirm by piping the entire transcript through `grep -F ZEEKER_CANARY_42` — that
  grep should return ONLY the original outgoing tool call line (the user's prompt
  echo). The canary token in the URL query-string going UP to upstream
  (`data.zeeker.sg/...?_search=ZEEKER_CANARY_42`) is by design and NOT a leak —
  D4-07's invariant is about MCP server emissions back to the LLM, not the wire to
  Datasette.
- [ ] Cross-reference: Plan 04-03's `tests/test_search_value_safety.py` already
  GREEN-tests this end-to-end with a 5-canary corpus. Scenario 6 is the human-loop
  sanity check at the OUTER deployment boundary — if the automated tests pass but
  the human walk-through sees the canary leak somewhere, the gap is between the
  test harness and the real deployment (e.g., a reverse-proxy access log not
  scoped by the tests). ESCALATE in that case.
- [ ] Screenshot the response + the `grep -F` result. Save to
  `evidence/04-search/scenario-06-canary-no-echo.png`.

### Scenario 7 — drill-down hint surfaces (`pagination.upstream_total_hits`)

- [ ] Prompt:
  > "Search for 'court' with default limit. Tell me how many TOTAL upstream hits
  > each table has, and which tables have more hits than the rows you returned."
- [ ] Expected tool call: `search(query="court", limit=20)`.
- [ ] Expected envelope:
  - `pagination.upstream_total_hits` is populated for EVERY dispatched (db, table)
    pair, INCLUDING tables with zero hits (Probe 6 invariant — Plan 04-03
    `test_zero_total_hits_table_still_in_upstream_total_hits`).
  - At least one value in `upstream_total_hits` exceeds the per-table row count
    actually returned (for a common word like "court", `sglawwatch.headlines`
    typically reports `filtered_table_rows_count` ≥ 100, but only 1-2 rows per
    table fit into the 20-row round-robin slice).
- [ ] Expected LLM behavior: per the tool description (D4-15 — "When
  pagination.upstream_total_hits exceeds returned counts, narrow the query or
  follow up with query_table to drill into a specific table"), the LLM should
  summarize:
  - Total hits per table.
  - At least one suggestion of the form "X has 225 hits but only Y rows in the
    result; narrow the query or call `query_table(database=X.db, table=X.table,
    ...)` to drill in."
- [ ] If the LLM does NOT pick up the drill-down hint, the description text in
  `_SEARCH_DESCRIPTION` (D4-15) is being trimmed by client middleware — verify the
  full description reaches the LLM via `tools/list` introspection. Cold-cache note:
  if `failed_tables > 0` on this call, the upstream_total_hits dict will be
  missing keys for the failed tables; that is expected (the partial-results
  envelope shape, per Pitfall 4 / Scenario 8). Run Scenario 7 a second time after
  Scenario 8 confirms the cache is warm.
- [ ] Screenshot the response + the LLM's drill-down acknowledgement. Save to
  `evidence/04-search/scenario-07-drill-down-hint.png`.

### Scenario 8 — cold-cache acceptable behavior (04-RESEARCH §3.3 / Pitfall 4)

> **Reviewer note:** This scenario documents EXPECTED behavior. It is NOT a bug to
> see `failed_tables > 0` on the first call after a deployment. Do not file an
> incident; do not page the operator. The 0.8s `anyio.move_on_after` budget at
> D4-06 is the documented SLO. See 04-RESEARCH §3.3 + Pitfall 4 for the rationale
> (briefly: cold-cache p99 per-table is 1.6s upstream; partial results are
> deliberately preferred over either (a) blowing the p95 budget or (b) failing the
> whole call). This scenario is the human-loop SLO acceptance gate, not a
> regression test.

- [ ] Restart the local uvicorn process (or wait for the deployed instance to
  cycle, OR wait ≥ 5 minutes since the last search call so the upstream's
  in-memory cache evicts).
- [ ] First call (the cold-cache call):
  > "Search for 'privacy' across all databases."
- [ ] Expected tool call: `search(query="privacy")`.
- [ ] Record the wall-clock latency (start to first response byte). Acceptable
  range for cold-cache: anywhere from 100ms (lucky warm upstream) to ~1.6s (worst
  observed in 04-RESEARCH §3.3 — Probe 4).
- [ ] Record `envelope.pagination.failed_tables`. Acceptable range: 0 to ~6
  (cold-cache failed_tables > 0 is by design; the 0.8s `move_on_after` budget will
  cancel any per-table call exceeding it; partial results are still envelope-shape
  valid and the LLM can interpret `failed_tables` honestly).
- [ ] Within 2 seconds, call the same search again (the warm-cache call):
  > "Run that same search again right now."
- [ ] Record latency + `failed_tables` again. Expected:
  - Latency: 100-200ms (warm path).
  - `failed_tables == 0`.
- [ ] **Acceptance gate (do NOT escalate if these hold):**
  - First call: any latency up to ~1.6s, any `failed_tables` value 0-6 → ACCEPT.
  - Second call: latency < 500ms AND `failed_tables == 0` → ACCEPT.
- [ ] **Escalation gate (DO escalate if):**
  - Second call ALSO shows latency > 500ms OR `failed_tables > 0` — this is not
    cold-cache; it's a steady-state issue (DB outage, upstream Datasette
    degradation, httpx pool exhausted, etc.). Page the operator.
- [ ] Screenshot both calls' timing + envelope. Save to
  `evidence/04-search/scenario-08-cold-cache.png`.

## Claude Code (parity)

1. From the project root in a terminal:
   ```
   claude mcp add zeeker https://mcp.zeeker.sg/mcp
   ```
2. Confirm registration:
   ```
   claude mcp list
   ```
   Must show `zeeker` with status `connected` and 6 tools.

Re-run AT LEAST 3 of Scenarios 1-8 via Claude Code with the same prompts. Confirm
identical envelope shapes (no Desktop-vs-Code drift). Recommended subset:
Scenario 1 (basic), Scenario 3 (pdpc empty — the design property reviewers most
often question), Scenario 6 (canary INJ-05 sanity).

- [ ] Scenario 1 — Claude Code parity. Screenshot →
  `evidence/04-search/scenario-01-claude-code.png`.
- [ ] Scenario 3 — Claude Code parity. Screenshot →
  `evidence/04-search/scenario-03-claude-code.png`.
- [ ] Scenario 6 — Claude Code parity (canary INJ-05). Screenshot →
  `evidence/04-search/scenario-06-claude-code.png`.

## F-4 Dry-Run Section (curl / JSON-RPC payloads)

The MCP protocol over streamable HTTP accepts a single JSON-RPC envelope per HTTP POST.
For `tools/call`, the params object embeds the tool name and arguments dict. Run these
BEFORE the Claude Desktop / Code walkthrough to confirm wire-level behavior.

> All examples use `<TARGET>` as a placeholder. Replace with
> `https://mcp.zeeker.sg` (deployed) or `http://127.0.0.1:8080` (local). The protocol
> path is always `/mcp/` with the trailing slash.

### A. basic `search("privacy")`

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 20, "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {
        "query": "privacy"
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; envelope body has `data` (≤ 20 preview rows), `provenance`
(`database: null`, `table: null`, `license: "mixed"`), `pagination` with
`upstream_total_hits` (~12 keys, NO `pdpc.*` keys) and `failed_tables: 0` (on warm).

### B. escape verification `search("Section 5(a)", databases=["zeeker-judgements"])`

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 21, "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {
        "query": "Section 5(a)",
        "databases": ["zeeker-judgements"]
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; envelope `data` non-empty (no FTS5 syntax error from the parens).
`failed_tables == 0`. `upstream_total_hits` has a `zeeker-judgements.judgments` key.

### C. pdpc empty path `search("privacy", databases=["pdpc"])`

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 22, "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {
        "query": "privacy",
        "databases": ["pdpc"]
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; envelope `data == []`, `upstream_total_hits == {}`,
`failed_tables == 0`. Confirm by piping through:
```
... | grep -E '"data":\s*\[\]|"upstream_total_hits":\s*\{\}|"failed_tables":\s*0'
```
All three patterns should match.

### D. canary INJ-05 `search("ZEEKER_CANARY_42")`

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 23, "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {
        "query": "ZEEKER_CANARY_42"
      }
    }
  }' \
  <TARGET>/mcp/ \
  > /tmp/scenario-d-response.json
```
Then INJ-05 acceptance gate:
```
grep -F "ZEEKER_CANARY_42" /tmp/scenario-d-response.json
```
Expected: the canary string MUST NOT appear in the response file. If `grep` prints
any lines containing the canary, INJ-05 has been violated end-to-end at the
deployment boundary — ESCALATE before signing off.

### E. unknown_database `search("court", databases=["does_not_exist"])`

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 24, "method": "tools/call",
    "params": {
      "name": "search",
      "arguments": {
        "query": "court",
        "databases": ["does_not_exist"]
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200 (JSON-RPC carries errors in the body, not in HTTP status); body
contains a tool-error result with message prefix `unknown_database:`. The string
`does_not_exist` MUST NOT appear in the error message body — that's the INJ-05
acceptance gate carried forward from Phase 2/3.

## Acceptance

- [ ] Scenario 1 (basic search) passes on Claude Desktop.
- [ ] Scenario 2 (escape verification) passes on Claude Desktop.
- [ ] Scenario 3 (pdpc empty path) passes on Claude Desktop.
- [ ] Scenario 4 (deterministic ordering across two runs) passes on Claude Desktop.
- [ ] Scenario 5 (search → fetch chain) passes on Claude Desktop.
- [ ] Scenario 6 (canary INJ-05 never echoed) passes on Claude Desktop.
- [ ] Scenario 7 (drill-down hint surfaces) passes on Claude Desktop.
- [ ] Scenario 8 (cold-cache acceptable behavior) passes on Claude Desktop —
  first-call latency + `failed_tables` documented; second-call clean.
- [ ] No INJ-05 leakage observed in any transcript: no canary token (`ZEEKER_CANARY_42`,
  `does_not_exist`, etc.) appears in any error message or visible log line during the
  walkthrough.
- [ ] At least 3 of 8 scenarios re-verified via Claude Code (parity check).
- [ ] At least 3 of 5 curl/JSON-RPC dry-run examples (A–E) executed and matched the
  documented expected response (F-4 wire-level evidence).
- [ ] Findings captured under `.planning/sessions/<YYYY-MM-DD>/F-4-PHASE4.md` — one
  entry per scenario, including PASS / FAIL / ESCALATE status + latency + a
  PII-stripped excerpt of the response envelope.

## Known accepted gaps (carry-forward from Plan 04-03 + earlier)

These are documented gaps where automated tests have local compensating controls
but the underlying source contract is NOT fully tight. They do NOT block Phase 4
close, but a reviewer signing off below should be aware:

1. **`core/search.py::_one_table` "NEVER raises" contract is not fully held.**
   Plan 04-03's lone-surrogate canary `"\udc80"` surfaced that `_one_table`'s
   `try/except UpstreamCallFailed` does not catch generic Python exceptions like
   `UnicodeEncodeError` raised by httpx during URL construction. The exception
   propagates through the `anyio` task group as an `ExceptionGroup` and bubbles
   out of `fan_out_search`, contradicting its docstring promise that "Failures
   are aggregated; the orchestrator returns the 4-tuple regardless."

   - **Test-level compensation (in place):** `tests/test_search_value_safety.py`
     catches `ExceptionGroup` / `UnicodeEncodeError` for the surrogate canary
     specifically and runs the INJ-05 leak scan against the error text. The
     canary still never leaks (it cannot be URL-encoded so it never reaches the
     wire), so the INJ-05 invariant is preserved end-to-end.
   - **Production exposure (none observed):** for a hostile canary like
     `ZEEKER_CANARY_42` in Scenario 6 (pure ASCII, URL-encodable), this gap is
     NOT reachable. The gap is only reachable via lone surrogates, which a
     legitimate LLM caller will essentially never produce.
   - **Recommended source fix (Phase 5 hardening or a 04-05 follow-up plan):**
     one-line `except Exception` addition in `src/mcp_zeeker/core/search.py`
     `_one_table`:
     ```python
     except UpstreamCallFailed as exc:
         failures.append(exc)
         log.warning("search_table_failed", database=db, table=table, error_class=type(exc).__name__)
         return
     except Exception as exc:  # NEW — defense-in-depth for the "NEVER raises" contract
         failures.append(UpstreamCallFailed("unexpected error", status=None))
         log.warning("search_table_failed", database=db, table=table, error_class=type(exc).__name__)
         return
     ```
     With this fix, the surrogate-canary path naturally promotes to
     `upstream_unavailable` (all targets failed with `status=None`) and Plan
     04-03's failure-path locked-error-code assertion can be re-enabled. See
     04-03-SUMMARY.md "Deviations from Plan" #2 for the full carry-forward.

   - **Scenario 6 does NOT include a lone-surrogate canary.** The Plan 04-04
     human-loop check uses `ZEEKER_CANARY_42` (pure ASCII) which is fully
     URL-encodable and exercises the standard envelope path. The lone-surrogate
     defense-in-depth gap is left as a documented-not-fixed item per the
     reasoning above.

## Troubleshooting

- **`search` returns empty for every query** (not just pdpc): check that
  `TableSummary.fts_table` is populated by `MetadataCache.get_database` —
  Pitfall 1 (the `fts_table: str | None = None` field on `TableSummary` was
  silently stripped by `extra="ignore"` before Plan 04-01's fix). Verify with:
  ```
  curl -sf "https://data.zeeker.sg/zeeker-judgements.json" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print([t.get('fts_table') for t in d['tables']])"
  ```
  Expected: at least one non-null value (e.g. `'judgments_fts'`).
- **`search` returns rows for pdpc**: Pitfall 3 regression. The FOUR-gate filter
  failed in production. Verify
  `src/mcp_zeeker/core/search.py::searchable_tables_for` still checks
  `summary.fts_table is not None`. If yes, check upstream — maybe pdpc gained an
  FTS index (in which case Plan 04-04 needs revision, NOT a bug — auto-discovery
  picks it up at the next 30-min cache refresh).
- **First call after deployment has `failed_tables > 6` (more than half failed)**:
  Pitfall 4 is mild; this is severe. Check upstream `data.zeeker.sg`
  responsiveness directly with `curl -w "%{time_total}"`. If upstream is fine, the
  MCP server's httpx pool may be misconfigured (check `core/http_client.py`
  Limits). Operators: page.
- **Canary `ZEEKER_CANARY_42` appears in the response or in a log line**:
  INJ-05 regression. The locked-literal discipline at D4-09 / D4-07 was broken
  somewhere (a new f-string into a `raise_*` helper, or a new structlog binding
  that includes `query=`). Audit with:
  ```
  grep -rE "(f\"|f')[^\"']*\{(query|search)" src/mcp_zeeker/
  ```
  Expected: empty. Plan 04-03's value-safety corpus has 5 canaries × 4 surfaces
  = 20+ assertions; if production leaks but tests pass, the leak is at the
  reverse-proxy / access-log layer NOT covered by the test harness — see
  Scenario 6's escalation note.

---

## F-4 Sign-off

Per 01-LEARNINGS.md F-4 (carried forward through Phase 2 + Phase 3): every curl
example and CLI command in this checklist MUST be dry-run against the chosen
target before marking this plan complete.

- [ ] Pre-conditions block dry-run results recorded (/healthz + tools/list + upstream
  smoke).
- [ ] Scenario 1 (basic search) walked on Claude Desktop or Claude Code.
- [ ] Scenario 2 (escape verification) walked.
- [ ] Scenario 3 (pdpc empty path) walked AND pdpc-no-dispatch confirmed.
- [ ] Scenario 4 (deterministic ordering) walked — `diff` of two runs shown to be
  empty.
- [ ] Scenario 5 (search → fetch chain) walked.
- [ ] Scenario 6 (canary INJ-05) walked — `grep -F ZEEKER_CANARY_42` confirmed empty
  in response body + logs.
- [ ] Scenario 7 (drill-down hint) walked — LLM acknowledged drill-down on at least
  one over-the-limit table.
- [ ] Scenario 8 (cold-cache acceptable) walked — first-call latency + `failed_tables`
  documented; second-call clean.
- [ ] Findings captured under `.planning/sessions/<YYYY-MM-DD>/F-4-PHASE4.md`.

**Dry-run target:** `__________________________________________________`
  *(e.g. `https://mcp.zeeker.sg` or `http://127.0.0.1:8080`)*

**Date:** `____________________`

**Signed-off by:** `____________________________________________`

> This task is a `checkpoint:human-verify`. The automated agent has written this
> checklist and committed it; the actual walk-through requires a human at a keyboard
> with access to Claude Desktop and Claude Code, and a target deployment of the
> Phase 4 server image. To resume the orchestrator after a real walk, tick each
> checkbox above, fill in the three fields, and reply "approved" to the spawning
> chat. To escalate, describe the failing scenario(s) so the verification loop can
> route into Plan 04-XX-revision before Phase 4 closes.
