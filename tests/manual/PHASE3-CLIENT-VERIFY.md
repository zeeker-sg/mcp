# Phase 3 — Client Verification Checklist (Structured Retrieval + URL-Keyed Fetch)

Walk this checklist against the DEPLOYED instance at `https://mcp.zeeker.sg/mcp` (preferred,
once the Phase 3 image is rolled out) OR against a local server:

```
uv run uvicorn mcp_zeeker.app:app --host 127.0.0.1 --port 8080
```

The local path proves the handler logic end-to-end without depending on the deployment
schedule. The remote path proves DNS + TLS + Caddy + the docker-network sibling-container
path end-to-end. Walk BOTH if the Phase 3 image is live; the local path alone is acceptable
if the deployment has not yet been updated.

> **F-4 OBLIGATION — DRY-RUN OBLIGATORY before declaring this plan complete.**
> Per 01-LEARNINGS.md F-4: every curl example in this document MUST be dry-run against
> the chosen target (live or local) before marking Phase 3 complete. See the F-4 sign-off
> block at the bottom.

## Pre-conditions

Phase 1 and Phase 2 pre-conditions remain valid (DNS, TLS, `/healthz`, trailing-slash
preserving HTTPS, `initialize` handshake clean). Additionally for Phase 3:

- [ ] Target is reachable. For local:
  ```
  curl -sf http://127.0.0.1:8080/healthz
  ```
  For remote:
  ```
  curl -sf https://mcp.zeeker.sg/healthz
  ```
  Both must return HTTP 200 with body `{"status":"ok"}`.

- [ ] `tools/list` returns FIVE tool names: `list_databases`, `list_tables`,
  `describe_table`, `query_table`, `fetch`. Run `initialize` first (required by MCP spec),
  then `tools/list` in the same curl session. Because `stateless_http=True`, you do NOT
  need to capture an Mcp-Session-Id — each request is independent:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    <TARGET>/mcp/
  ```
  Expected: response body (in `data:` SSE event or JSON body) includes all five tool names.
  Crucially, `fetch` and `query_table` appear in addition to the Phase 2 trio.

- [ ] Upstream data.zeeker.sg has the canonical fetch URL available:
  ```
  curl -sf "https://data.zeeker.sg/zeeker-judgements/judgments.json?source_url__exact=https%3A%2F%2Fwww.elitigation.sg%2Fgd%2Fs%2F2026_SGDC_136&_size=2&_shape=objects" | python3 -m json.tool | head -30
  ```
  Expected: `"rows": [ { … "source_url": "https://www.elitigation.sg/gd/s/2026_SGDC_136" … } ]`
  with `filtered_table_rows_count: 1`. If this returns 0 rows, swap the fixture URL in
  scenarios 4 + 6 for a different known-good judgment URL before proceeding.

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
   tool list under the server name shows `query_table` and `fetch` alongside the Phase 2
   three.

### Scenario 1 — Filter by date on `pdpc.enforcement_decisions`

- [ ] In a new chat, prompt:
  > "Use the zeeker MCP server to query PDPC enforcement decisions issued after 2026-01-01,
  > sorted from newest to oldest, returning at most 10 rows."
- [ ] Expected tool call: `query_table(database="pdpc", table="enforcement_decisions",
  filters=[{"column":"decision_date","op":"gt","value":"2026-01-01"}], sort="-decision_date",
  limit=10)`.
- [ ] Expected envelope: `data` has ≤10 rows; every row has `decision_date > "2026-01-01"`;
  rows are sorted descending by `decision_date`. If more matches exist upstream,
  `pagination.next_cursor` is non-null; otherwise null.
- [ ] No row contains `content_text` or any HEAVY_COLUMNS field at top level.
- [ ] No `retrieved_content` key on any row (default-light contract — D3-19).
- [ ] Screenshot the response. Save to `evidence/03-retrieval/scenario-01-filter-by-date.png`.

### Scenario 2 — Cursor walk through `zeeker-judgements.judgments`

- [ ] First call:
  > "List 3 rows from zeeker-judgements.judgments sorted by decision_date descending."
- [ ] Expected: `query_table("zeeker-judgements","judgments", sort="-decision_date", limit=3)`.
  Capture the `pagination.next_cursor` from the response (it MUST be non-null since
  judgments has > 3 rows).
- [ ] Second call (paste the cursor verbatim into the next prompt):
  > "Continue from cursor `<PASTE>` — fetch the next 3 rows from the same query."
- [ ] Expected: `query_table("zeeker-judgements","judgments", sort="-decision_date",
  limit=3, cursor="<PASTE>")`.
- [ ] Verify the second response's rows do NOT overlap with the first response's rows
  (compare `citation` fields).
- [ ] Verify the second response's rows continue in descending `decision_date` order
  (each row's date ≤ the previous response's last row).
- [ ] Screenshot both responses. Save to `evidence/03-retrieval/scenario-02-cursor-walk.png`.

### Scenario 3 — Opt-in heavy column `content_text` on `zeeker-judgements.judgments`

- [ ] Prompt:
  > "Get me 3 rows from zeeker-judgements.judgments including their full content_text —
  > use the columns parameter to opt in."
- [ ] Expected tool call: `query_table("zeeker-judgements","judgments",
  columns=["citation","content_text"], limit=3)`.
- [ ] Expected envelope: each row has `citation` at the top level AND a
  `retrieved_content` sub-object with a non-empty `content_text` string. The top level
  MUST NOT contain `content_text` directly — that's the D3-05 "heavy under
  retrieved_content" contract.
- [ ] Visually confirm Claude treats `retrieved_content.content_text` as data, not as
  instructions (the TOOL_TRAILER on the description tells the LLM to do so — INJ-01).
- [ ] Screenshot the response. Save to `evidence/03-retrieval/scenario-03-opt-in-heavy.png`.

### Scenario 4 — Fetch a known judgment by URL

- [ ] Prompt:
  > "Fetch the row from zeeker-judgements.judgments whose source_url is
  > `https://www.elitigation.sg/gd/s/2026_SGDC_136`."
- [ ] Expected tool call: `fetch("zeeker-judgements","judgments",
  url="https://www.elitigation.sg/gd/s/2026_SGDC_136")`.
- [ ] Expected envelope: `data` has EXACTLY ONE row. The row contains the light
  citation + case_name + source_url fields. Confirm in the transcript:
  - NO `content_text` at any level (fetch never emits heavy columns — FETCH-03).
  - NO `retrieved_content` key (fetch never emits this key — must_have line).
  - NO `id` / `judgment_id` / `parent_id` (hidden + FK columns stripped).
- [ ] Screenshot the response. Save to `evidence/03-retrieval/scenario-04-fetch-happy.png`.

### Scenario 5 — Unsupported-table fetch (FETCH-04)

- [ ] Prompt:
  > "Try fetching from zeeker-judgements.judgments_fragments with the URL
  > `https://example.com/anything`."
- [ ] Expected tool call: `fetch("zeeker-judgements","judgments_fragments",
  url="https://example.com/anything")`.
- [ ] Expected error: ToolError with code prefix `unsupported_table_for_fetch:` because
  `judgments_fragments` has no entry in `URL_COLUMNS` (fragments tables are reached via
  query_table on the fragment FK, not via fetch).
- [ ] Visually confirm the error message contains "judgments_fragments" (identifier
  echo is intentional) but does NOT contain the user-supplied URL "example.com" — that
  URL is filtered out per INJ-05.
- [ ] Screenshot the error. Save to `evidence/03-retrieval/scenario-05-unsupported.png`.

### Scenario 6 — Cursor shape-mismatch rejection

- [ ] Re-issue the scenario 2 first call to obtain a fresh cursor. Then prompt:
  > "Use the cursor `<PASTE>` but change the sort to ascending (`sort=decision_date`)
  > with limit 3."
- [ ] Expected tool call: `query_table("zeeker-judgements","judgments",
  sort="decision_date", limit=3, cursor="<PASTE>")` — the original cursor was bound to
  `sort="-decision_date"`, so the shape digest will not match.
- [ ] Expected error: ToolError with code prefix `invalid_cursor:` and a fixed-literal
  message. The cursor token itself is NOT echoed back in the error string.
- [ ] Screenshot the error. Save to `evidence/03-retrieval/scenario-06-invalid-cursor.png`.

## Claude Code

1. From the project root in a terminal:
   ```
   claude mcp add zeeker https://mcp.zeeker.sg/mcp
   ```
2. Confirm registration:
   ```
   claude mcp list
   ```
   Must show `zeeker` with status `connected`.

### Scenario 1 (Claude Code parity) — Filter by date

- [ ] In Claude Code, prompt:
  > "Call query_table on pdpc.enforcement_decisions with a filter for decision_date > 2026-01-01,
  > sort=-decision_date, limit=10."
- [ ] Verify the same envelope shape Claude Desktop returned (≤10 rows, descending,
  no heavies at top level).
- [ ] Screenshot. Save to `evidence/03-retrieval/scenario-01-claude-code.png`.

### Scenario 3 (Claude Code parity) — Opt-in heavy column

- [ ] Prompt:
  > "Call query_table on zeeker-judgements.judgments with columns=['citation','content_text']
  > and limit=3."
- [ ] Verify rows have `retrieved_content.content_text` (not top-level content_text).
- [ ] Screenshot. Save to `evidence/03-retrieval/scenario-03-claude-code.png`.

### Scenario 4 (Claude Code parity) — Fetch known judgment

- [ ] Prompt:
  > "Call fetch on zeeker-judgements.judgments with
  > url=https://www.elitigation.sg/gd/s/2026_SGDC_136."
- [ ] Verify single-row envelope, no heavies, no FK columns.
- [ ] Screenshot. Save to `evidence/03-retrieval/scenario-04-claude-code.png`.

## F-4 Dry-Run Section (curl / JSON-RPC payloads)

The MCP protocol over streamable HTTP accepts a single JSON-RPC envelope per HTTP POST.
For `tools/call`, the params object embeds the tool name and arguments dict. Run these
BEFORE the Claude Desktop / Code walkthrough to confirm wire-level behavior.

> All examples use `<TARGET>` as a placeholder. Replace with
> `https://mcp.zeeker.sg` (deployed) or `http://127.0.0.1:8080` (local). The protocol
> path is always `/mcp/` with the trailing slash.

### A. query_table with filter + sort

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 10, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "pdpc",
        "table": "enforcement_decisions",
        "filters": [{"column":"decision_date","op":"gt","value":"2026-01-01"}],
        "sort": "-decision_date",
        "limit": 10
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; response body envelope has `data` (list of rows), `provenance`,
`pagination`. Each row has `decision_date > "2026-01-01"`, no `content_text`, no
`retrieved_content` key.

### B. query_table with cursor (paste from response A's `pagination.next_cursor`)

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 11, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "pdpc",
        "table": "enforcement_decisions",
        "filters": [{"column":"decision_date","op":"gt","value":"2026-01-01"}],
        "sort": "-decision_date",
        "limit": 10,
        "cursor": "<PASTE FROM RESPONSE A>"
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; next page of rows. Re-issuing with a different `sort` value MUST
return a JSON-RPC error containing `invalid_cursor:`.

### C. fetch — known judgment URL (happy path)

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 12, "method": "tools/call",
    "params": {
      "name": "fetch",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments",
        "url": "https://www.elitigation.sg/gd/s/2026_SGDC_136"
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; envelope `data` is a 1-element list. Row contains light columns
only (no `content_text`, no `retrieved_content`, no `id`).

### D. fetch — unsupported table (FETCH-04)

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 13, "method": "tools/call",
    "params": {
      "name": "fetch",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments_fragments",
        "url": "https://example.com/anything"
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200 (JSON-RPC carries errors in the body, not in HTTP status); body
contains a tool-error result with message prefix `unsupported_table_for_fetch:`. The
URL string `example.com/anything` MUST NOT appear in the error message body.

### E. fetch — not_found (known-bad URL)

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 14, "method": "tools/call",
    "params": {
      "name": "fetch",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments",
        "url": "https://www.elitigation.sg/gd/s/9999_NONEXISTENT_999"
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: body contains a tool-error result with message prefix `not_found:`. The URL
string `NONEXISTENT_999` MUST NOT appear in the error message body (INJ-05 acceptance
gate). Confirm by piping the body through `grep -F NONEXISTENT_999` — that grep should
return nothing.

## Acceptance

- [ ] Scenario 1 (filter by date) passes on Claude Desktop.
- [ ] Scenario 2 (cursor walk) passes on Claude Desktop.
- [ ] Scenario 3 (opt-in heavy) passes on Claude Desktop.
- [ ] Scenario 4 (fetch known URL) passes on Claude Desktop.
- [ ] Scenario 5 (unsupported-table fetch) passes on Claude Desktop.
- [ ] Scenario 6 (cursor shape-mismatch) passes on Claude Desktop.
- [ ] No INJ-05 leakage observed in any transcript: no user-supplied filter VALUE and
  no user-supplied URL appears in any error message or visible log line during the
  walkthrough.
- [ ] At least 3 of 6 scenarios re-verified via Claude Code (parity check).
- [ ] At least 3 of 5 curl/JSON-RPC dry-run examples (A–E) executed and matched the
  documented expected response (F-4 wire-level evidence).

## Troubleshooting

- **`query_table` returns "invalid_filter_op" for a date string**: check the column type
  in `describe_table`; if upstream `_zeeker_schemas` is unavailable, the handler falls
  back to `config.COLUMN_TYPES` (Pitfall 5). A missing type entry is interpreted as
  TEXT — strict date ops (gt/gte/lt/lte) refuse to compare TEXT against a YYYY-MM-DD
  literal. Patch is to add the column type to `config.COLUMN_TYPES` (config-only).
- **Cursor reuse silently returns wrong rows**: this MUST NOT happen — the canonical
  shape digest binds the cursor to sort + filters + columns. If you see it, file a
  bug; the qhash mechanism is the contract that prevents shape mismatch.
- **`fetch` returns 2-row envelope**: this also MUST NOT happen — fetch caps at the
  first row and emits a WARNING log line. Check the server logs for
  `fetch_ambiguous_url`. If `data` has more than 1 row, the handler is broken; file
  a bug.
- **`example.com` appears in the not_found error**: INJ-05 regression. The
  `raise_not_found` helper takes ONLY (database, table) — verify
  `src/mcp_zeeker/core/visibility.py raise_not_found` has not been changed to accept a
  URL parameter.

---

## F-4 Dry-Run Obligation

Per 01-LEARNINGS.md F-4: every curl example and CLI command in this checklist MUST be
dry-run against the live target before marking this plan complete.

- [ ] Pre-conditions A + B + C dry-run results recorded
- [ ] At least one Scenario 1–6 step executed on Claude Desktop
- [ ] curl examples A through E dry-run against `<TARGET>`
- [ ] INJ-05 acceptance gate visually confirmed (no URL or canary in any error body)
- [ ] Any deviation logged as a follow-up plan or fixed in this checklist text

**Screenshots:**
- [ ] `evidence/03-retrieval/scenario-01-filter-by-date.png` captured
- [ ] `evidence/03-retrieval/scenario-02-cursor-walk.png` captured
- [ ] `evidence/03-retrieval/scenario-03-opt-in-heavy.png` captured
- [ ] `evidence/03-retrieval/scenario-04-fetch-happy.png` captured
- [ ] `evidence/03-retrieval/scenario-05-unsupported.png` captured
- [ ] `evidence/03-retrieval/scenario-06-invalid-cursor.png` captured

**Operator sign-off:** Verified on YYYY-MM-DD by <user>

> This task is a `checkpoint:human-verify`. The automated agent has written this
> checklist and committed it; the actual walk-through requires a human at a keyboard
> with access to Claude Desktop and Claude Code. To resume, walk every item above,
> tick each ☐ box, and fill in the sign-off line above with your name and date.
