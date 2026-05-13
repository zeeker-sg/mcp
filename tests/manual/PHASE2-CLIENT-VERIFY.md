# Phase 2 Manual Client Verification — DISC-02/03/04/05

Walk this checklist against the DEPLOYED instance at `https://mcp.zeeker.sg/mcp`. Do NOT
use `localhost` — the point is to prove DNS + TLS + Caddy + the docker-network
sibling-container path all work end-to-end with the Phase 2 tools.

> **F-4 OBLIGATION — DRY-RUN OBLIGATORY before declaring plan complete.**
> Per 01-LEARNINGS.md F-4: every curl example in this document MUST be dry-run against
> the live deployed instance before marking Phase 2 complete. See the F-4 sign-off block
> at the bottom.

## Pre-conditions

All Phase 1 pre-conditions remain valid. Additionally:

- [x] `mcp.zeeker.sg` resolves to the operator's host
- [x] `https://mcp.zeeker.sg/healthz` returns HTTP 200 with body `{"status":"ok"}`:
  ```
  curl -sf https://mcp.zeeker.sg/healthz
  ```
- [ ] Trailing-slash redirect preserves HTTPS (F-1 regression check — commit 349a739):
  ```
  curl -sI -X POST https://mcp.zeeker.sg/mcp | grep -i ^location
  ```
  Must return `location: https://mcp.zeeker.sg/mcp/` — NOT `http://`.
  Verified 2026-05-13 13:39Z: response header `location: https://mcp.zeeker.sg/mcp/`.

- [x] `initialize` handshake completes and returns Mcp-Session-Id (stateful path, if any) OR
  completes cleanly without one (stateless path, per commit 4ce06d5). With `stateless_http=True`,
  the response header `mcp-session-id` should be ABSENT:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual-curl","version":"0.1"}}}' \
    https://mcp.zeeker.sg/mcp/ -D -
  ```
  Expected: HTTP 200, no `mcp-session-id:` response header (F-3 invariant).
  Verified 2026-05-13 13:39Z: HTTP 200, no `mcp-session-id:` in response headers.

- [x] `tools/list` returns exactly 3 tool names: `list_databases`, `list_tables`, `describe_table`.
  Run `initialize` first (required by MCP spec), then run `tools/list` in the same curl session.
  Because `stateless_http=True`, you do NOT need to capture an Mcp-Session-Id — each request
  is independent:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    https://mcp.zeeker.sg/mcp/
  ```
  Expected: response body (in `data:` SSE event or JSON body) includes all three tool names:
  `list_databases`, `list_tables`, `describe_table`.
  Verified 2026-05-13 13:39Z: all three tool names returned.

## Claude Desktop

1. Open `claude_desktop_config.json` (Settings → Developer → Edit Config)
2. Ensure `mcpServers` contains the zeeker entry:
   ```json
   {
     "zeeker": {
       "url": "https://mcp.zeeker.sg/mcp"
     }
   }
   ```
3. Restart Claude Desktop. Confirm zeeker appears as a connected MCP server.

### list_tables

- [x] Open a new chat, type:
  > "What tables are available in the zeeker-judgements database?"
- [x] Claude should call `list_tables(database="zeeker-judgements")` and present visible tables.
- [x] Expected result: the response includes `judgments` and `judgments_fragments` table names.
  It must NOT include any table name beginning with `_zeeker` (hidden platform tables per DISC-03).
  Verified 2026-05-13 13:43Z: `judgments` (10,556 rows) and `judgments_fragments` (71,827 rows), no `_zeeker_*` leakage.
- [x] Screenshot the full window. Save to `evidence/02-discovery/claude-desktop-list-tables.png`.

### describe_table

- [x] In the same or a new chat, type:
  > "Describe the schema of the judgments table in zeeker-judgements."
- [x] Claude should call `describe_table(database="zeeker-judgements", table="judgments")`.
- [x] Expected result: response includes `light_columns`, `available_columns`, `url_keyed: true`,
  and `supports_fragments: true` for the judgments table.
  Verified 2026-05-13 13:45Z: exact 8-field shape, no FK/idx/triggers, light_columns (9) ⊂ available_columns (17), heavy text (`content_text`, `court_summary`) only in available, `url_keyed: true`, `supports_fragments: true`.
- [x] Screenshot the full window. Save to `evidence/02-discovery/claude-desktop-describe-table.png`.

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

### list_tables via Claude Code

- [ ] Open Claude Code, type:
  > "Call list_tables for zeeker-judgements and show me the result."
- [ ] Claude should call `list_tables(database="zeeker-judgements")` and present the tables.
- [ ] Verify no `_zeeker`-prefixed tables appear in the output.
- [ ] Screenshot the response. Save to `evidence/02-discovery/claude-code-list-tables.png`.

### describe_table via Claude Code

- [ ] In Claude Code, type:
  > "Call describe_table for the judgments table in zeeker-judgements."
- [ ] Claude should call `describe_table(database="zeeker-judgements", table="judgments")`.
- [ ] Verify `light_columns`, `available_columns`, `url_keyed`, `supports_fragments` are present.
- [ ] Screenshot the response. Save to `evidence/02-discovery/claude-code-describe-table.png`.

## DISC-05 Side-Channel Acceptance (manual reinforcement)

The automated tests in Plan 02 cover the code-path identity of hidden-vs-nonexistent table
error messages. This section provides the UX-layer check: confirm the error messages the
operator (or Claude) sees are indistinguishable between the two cases.

- [x] In Claude Desktop or Claude Code, invoke:
  > "Describe the schema of the metadata table in sglawwatch."
  (This is a hidden platform table — exists in upstream Datasette but must be denied.)
  Expected: error message containing `unknown_table: Table not found: sglawwatch.metadata`
  Verified 2026-05-13 13:47Z (Claude Desktop): exact string `unknown_table: Table not found: sglawwatch.metadata`.

- [x] Immediately after, invoke:
  > "Describe the schema of the totally_fictitious_table table in sglawwatch."
  (This table does not exist at all.)
  Expected: error message containing `unknown_table: Table not found: sglawwatch.totally_fictitious_table`
  Verified 2026-05-13 13:47Z (Claude Desktop): exact string `unknown_table: Table not found: sglawwatch.totally_fictitious_table`.

- [x] Visual confirmation: BOTH error message strings have the identical prefix
  `unknown_table: Table not found:` and identical structure — only the table identifier differs.
  This ensures clients cannot distinguish hidden from nonexistent tables via the error message.
  Verified 2026-05-13 13:47Z: Claude itself articulated the property in chat — "Looks like the API doesn't distinguish between 'table exists but is hidden' vs 'table genuinely doesn't exist.'" The DISC-05 contract is invisible at the protocol layer.

## Acceptance

- Four tools are reachable from Claude clients: `list_databases`, `list_tables`, `describe_table`
  (three listed; `list_databases` covered in Phase 1).
- `list_tables(zeeker-judgements)` shows only non-platform tables (no `_zeeker` prefix).
- `describe_table(zeeker-judgements, judgments)` shows all expected metadata fields.
- DISC-05: hidden table error message is structurally identical to nonexistent table error.
- Four screenshots committed under `evidence/02-discovery/` (or deferral logged below).

## Troubleshooting

- **`list_tables` returns 0 tables or errors**: Check upstream Datasette is reachable.
  Run `curl -sf https://data.zeeker.sg/zeeker-judgements.json | python3 -m json.tool | head -20`
  to confirm table list.
- **Hidden tables appear in `list_tables` output**: DISC-03 denylist in
  `config.HIDDEN_TABLE_PREFIXES` is missing or not applied. Check `src/mcp_zeeker/tools/discovery.py`.
- **`describe_table` on hidden table returns data instead of error**: DISC-05 enforcement is
  broken. The tool should call `_check_hidden` before fetching column metadata.
- **`initialize` returns `mcp-session-id` header**: `stateless_http=True` has been removed from
  `mcp.http_app()` in `src/mcp_zeeker/app.py`. Check commit 4ce06d5 is still in the deployed image.
- **`tools/list` shows only 1 or 2 tools**: Phase 2 tools not registered — check
  `src/mcp_zeeker/server.py` for `list_tables` and `describe_table` tool definitions.

---

## F-4 Dry-Run Obligation

Per 01-LEARNINGS.md F-4: every curl example and CLI command in this checklist MUST be
dry-run against the live `https://mcp.zeeker.sg/mcp` instance BEFORE marking this plan
complete. Specifically:

- [x] Every curl example in "Pre-conditions" has been executed against the live host
- [x] The `tools/list` pre-check curl confirmed exactly 3 tool names
- [ ] Every CLI command in "Claude Code" has been executed end-to-end (deferred — Claude Desktop acceptance sufficient; Claude Code is the same three calls through a different transport)
- [x] Every "expected response" assertion was hand-verified against the actual response
- [x] The DISC-05 side-channel check was visually confirmed (error message identity)
- [x] Any deviation was either (a) fixed in the checklist text, or (b) logged as a follow-up

**Screenshots:**
- [x] `evidence/02-discovery/claude-desktop-list-tables.png` captured
- [x] `evidence/02-discovery/claude-desktop-describe-table.png` captured
- [x] `evidence/02-discovery/claude-desktop-disc05-side-channel.png` captured (covers DISC-05 visual proof)
- [ ] `evidence/02-discovery/claude-code-list-tables.png` (deferred with Claude Code walkthrough)
- [ ] `evidence/02-discovery/claude-code-describe-table.png` (deferred with Claude Code walkthrough)

**Operator sign-off:** houfu, 2026-05-13 — Claude Desktop walkthrough complete; DISC-02/03/04/05 all confirmed end-to-end against `https://mcp.zeeker.sg/mcp`. Claude Code section deferred (same three calls through a different MCP client transport; not load-bearing for acceptance).

> This task is a `checkpoint:human-action`. The automated agent has written this checklist
> and committed it; the actual walk-through against the live deployment requires a human
> at a keyboard with access to Claude Desktop and Claude Code. The SUMMARY.md records this
> as pending-human-action. To resume, walk every item above and fill in the sign-off line:
>
> `Operator sign-off: <name, date>`
