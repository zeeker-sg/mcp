# Phase 5 — Client Verification Checklist (Transparent Fragment-Parent Joins)

Walk this checklist against the DEPLOYED instance at `https://mcp.zeeker.sg/mcp` (preferred,
once the Phase 5 image is rolled out) OR against a local server:

```
uv run uvicorn mcp_zeeker.app:app --host 127.0.0.1 --port 8080
```

The local path proves the handler logic end-to-end without depending on the deployment
schedule. The remote path proves DNS + TLS + Caddy + the docker-network sibling-container
path end-to-end. Walk BOTH if the Phase 5 image is live; the local path alone is acceptable
if the deployment has not yet been updated.

> **F-4 OBLIGATION — DRY-RUN OBLIGATORY before declaring this plan complete.**
> Per 01-LEARNINGS.md F-4 (ratified in Phase 2 + Phase 3 + Phase 4): every curl example in
> this document MUST be dry-run against the chosen target (live or local) before marking
> Phase 5 complete. See the F-4 Sign-off block at the bottom.

> **AUTO-MODE NOTICE.** The orchestrator-side checkpoint for this plan was auto-approved
> in AUTO chain mode (mirroring the Phase 3 / Phase 4 pattern). The auto-approval ONLY
> unblocks the orchestrator. Substantive human review against a real MCP target is still
> the standing obligation before any production cut-over. The F-4 Sign-off block at the
> bottom is intentionally UNSIGNED — a human verifier must fill it in.

## Scope

Phase 5 ships the transparent URL→parent_pk→fragment_fk join on `query_table`. When the
filter set on a `*_fragments` table contains exactly one `eq` filter on the parent's URL
column, the handler:

1. Resolves the URL to a parent PK via a single upstream lookup (Call 1, memoized in
   `ParentPKCache` with 30-min TTL).
2. Rewrites the filter to `parent_fk = <parent_pk>` and dispatches Call 2 against the
   fragments table sorted by paragraph order with `_nocount=1` injected.
3. Strips `id`, `judgment_id`, `item_id`, `parent_id`, and `parent_fk` from every row via
   the existing `HIDDEN_COLUMNS` gate (FRAG-02 — internal PK / FK never reach the LLM).
4. Paginates past Datasette's 1,000-row cap via a keyset cursor (`(qhash,
   last_order_by_value, last_id)` — Datasette's `_next` token shape preserved on the
   wire, FRAG-04 / FRAG-05).
5. Resolves multi-match parents deterministically via `_sort_desc=<parent_match_order_by>
   &_size=1` (per-table override in `FRAGMENT_PARENTS`) and emits a structured warning
   log binding `parent_url_hash=<16-hex>` instead of the URL value itself (FRAG-06 /
   INJ-05).

Three fragment-table pairs are wired in `config.FRAGMENT_PARENTS`:

| Fragment table                                | Parent table              | Parent URL column | order_by         |
|-----------------------------------------------|---------------------------|-------------------|------------------|
| `zeeker-judgements.judgments_fragments`       | `judgments`               | `source_url`      | `ordinal`        |
| `sglawwatch.about_singapore_law_fragments`    | `about_singapore_law`     | `item_url`        | `fragment_order` |
| `pdpc.enforcement_decisions_fragments`        | `enforcement_decisions`   | `decision_url`    | `sequence`       |

**Design properties the human verifier MUST understand BEFORE walking the scenarios** —
these are documented design choices, NOT bugs to file:

1. **Cold-cache fragment-join latency is ~5s; warm-cache is ~150ms.** First call after a
   cold deployment runs ~1.6s for Call 1 + ~3s for Call 2 against `judgments_fragments`
   (the largest fragment table — see RESEARCH §4.11). Subsequent calls within the 30-min
   ParentPKCache TTL skip Call 1 and complete in ~150ms. Fragment tools are EXCLUDED from
   the project p95<1.5s SLO per ROADMAP NFR-01. Do NOT file a cold-path latency as a bug.
2. **Multi-match parent warnings are stale-duplicate-import data-quality signals, NOT
   true URL→multi-parent collisions.** Every observed multi-match in current data shares
   the SAME parent `id` (e.g., `2001_SGHC_216` has 2 rows in `judgments`, both `id=
   6074e86bc12d`, different `created_at`). The `_sort_desc=created_at&_size=1` resolution
   is deterministic. The warning log is for operator triage, not LLM-visible.
3. **Public `query_table` signature accepts `le=200`; fragment-join path re-clamps to
   100.** This asymmetry is documented in the `query_table` tool description text (D5-09).
   The re-clamp emits the fixed-literal `"invalid_filter_op: limit exceeds fragment-join
   cap of 100"` — no `{limit}` interpolation echoing back the user-supplied value (D5-08
   / INJ-05).
4. **`pagination.upstream_total_hits` / `filtered_table_rows_count` is `null` on the
   fragment-join path.** Phase 5 injects `_nocount=1` against the fragments table to dodge
   the `sql_time_limit_ms` ceiling on the implicit `COUNT(*)` over the join (RESEARCH
   §4.4 / Pitfall 2 — `judgments_fragments` would otherwise return HTTP 400). The
   load-bearing pagination signals are `pagination.truncated` (must be `false` on every
   page of a clean walk) and `pagination.next_cursor` (the keyset continuation token —
   null on the terminal page). Do NOT expect a total-count field on this path.

## Pre-conditions

Phase 1 / Phase 2 / Phase 3 / Phase 4 pre-conditions remain valid (DNS, TLS, `/healthz`,
trailing-slash preserving HTTPS, `initialize` handshake clean, the prior 6 tool names
register cleanly). Additionally for Phase 5:

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
  `describe_table`, `query_table`, `fetch`, `search`. Phase 5 introduces NO new tool —
  `query_table` is extended in place. Run `initialize` first (required by MCP spec), then
  `tools/list` in the same curl session. Because `stateless_http=True`, you do NOT need
  to capture an Mcp-Session-Id:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    <TARGET>/mcp/
  ```
  Expected: response body (in `data:` SSE event or JSON body) includes all six tool
  names — `query_table` is one of them.

- [ ] The `query_table` tool description contains BOTH substrings `"*_fragments"` AND
  `"parent's URL column"` (D5-09 — verifies the fragment-table note shipped to the LLM
  via `tools/list` introspection). Inspect via:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}' \
    <TARGET>/mcp/ \
    | tr -d '\n' | grep -oE '"name":"query_table"[^}]*"description":"[^"]*"' \
    | grep -E "\\*_fragments.*parent's URL column"
  ```
  Expected: the grep emits a non-empty match (both substrings present in the description
  field). If empty, the D5-09 note did not ship — ESCALATE before walking any scenario.

- [ ] Upstream `data.zeeker.sg` is reachable and the captured probe URLs still resolve:
  ```
  curl -sf "https://data.zeeker.sg/zeeker-judgements/judgments.json?source_url__exact=https%3A%2F%2Fwww.elitigation.sg%2Fgd%2Fs%2F2026_SGFC_46&_size=1&_shape=objects" \
    | python3 -m json.tool | head -20
  ```
  Expected: `"rows": [ { … "source_url": "https://www.elitigation.sg/gd/s/2026_SGFC_46"
  … "id": "..." } ]` with `filtered_table_rows_count: 1`. If this returns 0 rows, the
  fixture data has rotated upstream — swap the URL in Scenarios 1, 4, 5, 8 for a known-
  good judgment URL before proceeding (and update this file in a follow-up commit).

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
   tool list under the server name shows the six Phase 1-4 tools (Phase 5 introduces no
   new tool surface — `query_table` is extended in place).

## Scenarios (D5-20)

### Scenario 1 — Judgments fragment-join via `source_url` (FRAG-01 / FRAG-02 / FRAG-03)

- [ ] In a new chat, prompt:
  > "Use the zeeker MCP server to fetch all fragments of the judgment at
  > `https://www.elitigation.sg/gd/s/2026_SGFC_46` from `zeeker-judgements`."
- [ ] Expected tool call:
  ```
  query_table(
      database="zeeker-judgements",
      table="judgments_fragments",
      filters=[{"column": "source_url", "op": "exact",
                "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}],
  )
  ```
- [ ] Expected envelope shape:
  - `data` is a list of ~10 fragment rows ordered by `ordinal` ascending starting at 0.
  - **Every** row has at most the keys `{ordinal, text, chunk_text, source_url, ...}` —
    no `id`, no `judgment_id`, no `parent_id` (D5-02 / FRAG-02 — HIDDEN_COLUMNS strip).
  - `pagination.truncated == false`.
  - `pagination.next_cursor` is null (the parent has ~10 fragments, well under the
    default `limit=50`).
  - `pagination.upstream_total_hits` is null (the `_nocount=1` injection — RESEARCH §4.4).
  - `provenance.database == "zeeker-judgements"` and `provenance.table ==
    "judgments_fragments"`.
- [ ] FRAG-02 spot-check (use `jq`):
  ```
  jq '.data[] | keys | inside(["id","judgment_id","item_id","parent_id"]) | not' \
     <response.json> | grep -c true
  ```
  Expected output: a count equal to the row count (every row passes the no-internal-IDs
  check). If any row fails, ESCALATE — FRAG-02 regression.
- [ ] Screenshot the response. Save to
  `evidence/05-fragment-join/scenario-01-judgments.png`.

### Scenario 2 — Sglawwatch fragment-join via `item_url`

- [ ] Prompt:
  > "Fetch the first 10 fragments of the SingaporeLawWatch article at
  > `https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01-the-singapore-legal-system`."
- [ ] Expected tool call:
  ```
  query_table(
      database="sglawwatch",
      table="about_singapore_law_fragments",
      filters=[{"column": "item_url", "op": "exact",
                "value": "https://www.singaporelawwatch.sg/About-Singapore-Law/Overview/ch-01-the-singapore-legal-system"}],
      limit=10,
  )
  ```
- [ ] Expected envelope:
  - `data` is exactly 10 rows ordered by `fragment_order` ascending starting at 0.
  - No `id`, `item_id`, or `parent_id` in any row (FRAG-02).
  - `pagination.truncated == false`.
  - `pagination.next_cursor` is **non-null** (this parent has ~114 fragments per
    RESEARCH §2; the first page returns 10 of 114).
- [ ] Follow-up: paste the returned cursor back and call again to verify keyset
  continuation. Confirm:
  - Pages 2 returns 10 fresh rows with `fragment_order ≥ 10`.
  - No overlap in `(fragment_order, id)` tuples between page 1 and page 2.
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-02-sglawwatch.png`.

### Scenario 3 — PDPC fragment-join via `decision_url`

- [ ] Prompt:
  > "Fetch all fragments of the PDPC decision at
  > `https://www.pdpc.gov.sg/organisations/regulations-decisions/enforcement-decisions/breach-of-the-protection-obligation-by-sesami-singapore-pte-ltd-and-abecha-pte-ltd`."
- [ ] Expected tool call:
  ```
  query_table(
      database="pdpc",
      table="enforcement_decisions_fragments",
      filters=[{"column": "decision_url", "op": "exact",
                "value": "https://www.pdpc.gov.sg/organisations/regulations-decisions/enforcement-decisions/breach-of-the-protection-obligation-by-sesami-singapore-pte-ltd-and-abecha-pte-ltd"}],
  )
  ```
- [ ] Expected envelope:
  - `data` is ~18 rows ordered by `sequence` ascending starting at 0.
  - No `id` / `decision_id` / `parent_id` in any row (FRAG-02).
  - `pagination.truncated == false`.
  - `pagination.next_cursor` is null (single-page response).
- [ ] **Cross-pair confirmation:** Scenarios 1, 2, and 3 use the SAME `query_table` tool
  with the SAME filter shape (`{column: <parent_url_col>, op: "exact", value: <url>}`).
  This confirms D5-01 single-helper routing — there is no per-pair tool surface; the
  fragment_join orchestrator handles all three pairs through one auditable code path.
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-03-pdpc.png`.

### Scenario 4 — Multi-match parent + INJ-05 / FRAG-06 / D5-04

- [ ] Prompt:
  > "Fetch fragments of the judgment at
  > `https://www.elitigation.sg/gd/s/2001_SGHC_216` from zeeker-judgements."
- [ ] Expected tool call:
  ```
  query_table(
      database="zeeker-judgements",
      table="judgments_fragments",
      filters=[{"column": "source_url", "op": "exact",
                "value": "https://www.elitigation.sg/gd/s/2001_SGHC_216"}],
  )
  ```
- [ ] Expected envelope: returns fragments successfully (deterministic resolution — the
  newer `created_at` row wins via `_sort_desc=created_at&_size=1`). FRAG-02 invariants
  hold.
- [ ] **Capture the server-side structured logs** during the call (set
  `STRUCTLOG_LEVEL=DEBUG` if local, or pull from the deployment's log aggregator). Look
  for the `event="fragment_parent_multi_match"` record. Required bindings on that log
  line:

  | Binding                       | Expected value                                     |
  |-------------------------------|----------------------------------------------------|
  | `event`                       | `"fragment_parent_multi_match"`                    |
  | `database`                    | `"zeeker-judgements"`                              |
  | `fragment_table`              | `"judgments_fragments"`                            |
  | `parent_table`                | `"judgments"`                                      |
  | `parent_match_count`          | `2`                                                |
  | `parent_url_hash`             | exactly 16 lowercase hex chars (`^[0-9a-f]{16}$`)  |
  | `selected_parent_match_value` | ISO timestamp (e.g., `"2026-04-22T21:07:09.426849"`) |

- [ ] **INJ-05 acceptance gate (the load-bearing assertion):**
  - The substring `"2001_SGHC_216"` MUST NOT appear in the log line, the envelope, any
    error message, stdout, or stderr.
  - The literal parent `id` (e.g., `"6074e86bc12d"`) MUST NOT appear in the log line OR
    the envelope (FRAG-02 / T-05-19).
  - The bindings `url=`, `parent_url=`, `value=`, and `parent_pk=` MUST NOT appear
    anywhere on the warning log line.
- [ ] Confirm by piping the captured log line through:
  ```
  grep -E '2001_SGHC_216|6074e86bc12d|parent_pk=|parent_url=' <captured-log>
  ```
  Expected: ZERO output. If `grep` prints anything, INJ-05 has been violated — ESCALATE
  before signing off.
- [ ] Paste the redacted multi-match warning log line into the findings file as evidence.
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-04-multi-match.png`.

### Scenario 5 — Cold/warm latency disclosure (RESEARCH §4.11 — design property)

> **Reviewer note:** This scenario documents EXPECTED behavior. It is NOT a bug to see
> ~5s wall-clock latency on the first fragment-join call after a deployment. Do not file
> an incident; do not page the operator. Per ROADMAP NFR-01, fragment tools are
> EXCLUDED from the project p95<1.5s SLO. This scenario is the human-loop SLO acceptance
> gate, not a regression test.

- [ ] Restart the local uvicorn process (or wait for the deployed instance to cycle, OR
  wait ≥ 30 minutes since the last fragment-join call so the ParentPKCache expires).
- [ ] First call (the cold-cache call) — run Scenario 1's curl example wrapped in
  `time`:
  ```
  time curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":50,"method":"tools/call","params":{"name":"query_table","arguments":{"database":"zeeker-judgements","table":"judgments_fragments","filters":[{"column":"source_url","op":"exact","value":"https://www.elitigation.sg/gd/s/2026_SGFC_46"}]}}}' \
    <TARGET>/mcp/ > /tmp/scenario-05-cold.json
  ```
  Acceptable cold-path latency range: 100ms (lucky warm upstream) to ~5s (Call 1 cold
  ~1.6s + Call 2 cold ~3s).
- [ ] Second call (within 5 seconds of the first — the warm-cache call). Repeat the same
  curl with `id=51`. Acceptable warm-path latency range: ~50-200ms (ParentPKCache
  positive hit + warm httpx pool — Call 1 is skipped entirely on cache hit).
- [ ] **Acceptance gate (do NOT escalate if these hold):**
  - First call: any latency up to ~5s → ACCEPT.
  - Second call: latency < 500ms → ACCEPT.
- [ ] **Escalation gate (DO escalate if):**
  - Second call ALSO shows latency > 1s — this is not cold-cache; the ParentPKCache
    isn't binding correctly OR there's a steady-state issue (DB outage, upstream
    Datasette degradation, httpx pool exhausted). Page the operator.
- [ ] Record both wall-clock times in the findings file.
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-05-cold-warm-latency.png`.

### Scenario 6 — 957-fragment full walk via keyset cursor (FRAG-05)

> **Reviewer note:** 957 is the LARGEST current parent's fragment count in production
> data (`judgment_id=66e73dfa5db4` — the SMART GLOVE INTERNATIONAL judgment per RESEARCH
> §3). There is no 1,500-fragment parent in live data; the FRAG-04 success criterion is
> covered by Plan 05-03's `test_1500_fragment_walk_synthetic` test (synthetic via
> httpx_mock). This scenario is the live integration cover for FRAG-05.

- [ ] First, identify the SMART GLOVE source URL by hitting the parent table:
  ```
  curl -sS "https://data.zeeker.sg/zeeker-judgements/judgments.json?id__exact=66e73dfa5db4&_size=1&_shape=objects" \
    | python3 -m json.tool | grep source_url
  ```
  Record this URL — call it `<SMART_GLOVE_URL>` below.
- [ ] First page:
  ```
  curl -sN -X POST \
    -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":60,\"method\":\"tools/call\",\"params\":{\"name\":\"query_table\",\"arguments\":{\"database\":\"zeeker-judgements\",\"table\":\"judgments_fragments\",\"filters\":[{\"column\":\"source_url\",\"op\":\"exact\",\"value\":\"<SMART_GLOVE_URL>\"}],\"limit\":100}}}" \
    <TARGET>/mcp/ > /tmp/scenario-06-page-01.json
  ```
  Expected page-1 envelope: `data` has 100 rows; `ordinal` values are 0..99; FRAG-02
  invariants hold; `pagination.truncated == false`; `pagination.next_cursor` is non-null.
- [ ] Walk pages 2..10 by feeding each response's `pagination.next_cursor` back into the
  next call's `arguments.cursor`. Capture each page to `/tmp/scenario-06-page-NN.json`.
  Expected per page:
  - Pages 2-9: each returns 100 rows; ordinals continue contiguously (page 2 = 100..199,
    ..., page 9 = 800..899).
  - Page 10: returns 57 rows (957 = 9 × 100 + 57); ordinals 900..956;
    `pagination.next_cursor` is null (terminal page).
  - Every page: `pagination.truncated == false`.
- [ ] Post-walk validation:
  ```
  jq -s 'add | .[].data[].ordinal' /tmp/scenario-06-page-*.json | sort -n | uniq -c | awk '{print $1}' | sort -u
  ```
  Expected output: exactly `1` (every ordinal seen exactly once — no duplicates).
  ```
  jq -s 'add | .[].data[].ordinal' /tmp/scenario-06-page-*.json | wc -l
  ```
  Expected output: `957`.
  ```
  jq -s 'add | [.[].data[].ordinal] | max' /tmp/scenario-06-page-*.json
  ```
  Expected output: `956`.
- [ ] If any page returned fewer than 100 rows except page 10, OR if the deduplicated
  ordinal count ≠ 957, OR if `max(ordinal) ≠ 956`, ESCALATE — this is a keyset-cursor
  row-loss bug.
- [ ] Record total wall-clock time for the 10-call walk in the findings file (expect
  ~1-2s end-to-end given the parent_pk is warm after page 1).
- [ ] Screenshot the page-1 and page-10 responses. Save to
  `evidence/05-fragment-join/scenario-06-957-walk.png`.

### Scenario 7 — Fall-through path (D5-03 — fragment table without eq-parent-URL filter)

- [ ] Prompt:
  > "Query `judgments_fragments` for rows where `ordinal > 5`, limit 10. Don't filter by
  > parent URL — I want a flat sample across documents."
- [ ] Expected tool call:
  ```
  query_table(
      database="zeeker-judgements",
      table="judgments_fragments",
      filters=[{"column": "ordinal", "op": "gt", "value": 5}],
      limit=10,
  )
  ```
- [ ] Expected envelope:
  - `data` is 10 rows where `ordinal > 5`, spanning multiple parent documents (each row
    has a different `source_url`).
  - FRAG-02 invariants hold (`id`, `judgment_id`, `parent_id` stripped — HIDDEN_COLUMNS
    still applies on the fall-through path).
  - `pagination.next_cursor` may be non-null (the fragments table has many rows
    matching).
  - Phase 3's `le=200` limit cap applies on this path (the `le=100` fragment-join cap is
    NOT engaged because the join is not active). Verify by sending `limit=200` — should
    succeed without `invalid_filter_op`.
- [ ] **Negative assertion:** confirm via the deployment's structured logs that NO
  `event="fragment_parent_multi_match"` record was emitted for this call (fall-through
  path skips Call 1 entirely — there is no parent lookup to multi-match against).
- [ ] **Negative assertion:** confirm via the wire-level access logs (or by counting
  upstream requests) that the parent table `judgments` was NOT queried during this call
  — only `judgments_fragments` is hit (D5-03 — no Call 1 on the fall-through path).
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-07-fallthrough.png`.

### Scenario 8 — Error paths (D5-07 / D5-08 — fixed-literal discipline)

#### 8a — `limit=101` on the fragment-join path

- [ ] Prompt:
  > "Fetch fragments of the judgment at
  > `https://www.elitigation.sg/gd/s/2026_SGFC_46` from zeeker-judgements with a limit of
  > 101."
- [ ] Expected tool call:
  ```
  query_table(
      database="zeeker-judgements",
      table="judgments_fragments",
      filters=[{"column": "source_url", "op": "exact",
                "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}],
      limit=101,
  )
  ```
- [ ] Expected: `ToolError` with EXACT message text
  ```
  invalid_filter_op: limit exceeds fragment-join cap of 100
  ```
- [ ] **INJ-05 acceptance gate:** the digit `101` (or any user-supplied limit value)
  MUST NOT appear in the error message. The literal is fixed at description time per
  D5-08 — no `{limit}` f-string interpolation. Confirm:
  ```
  grep -F "101" <error-message>
  ```
  Expected: empty. If `grep` finds `101`, INJ-05 / T-05-28 has been violated — ESCALATE.
- [ ] Note: `limit=200` on the same call WITHOUT the parent-URL filter (fall-through
  path) succeeds — that confirms the asymmetry is gate-bound to the join, not the
  signature. The Phase 3 `le=200` signature still applies normally on the fall-through
  path.
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-08a-limit-cap.png`.

#### 8b — Garbage `cursor` value (keyset cursor malformed)

- [ ] Prompt:
  > "Fetch fragments of the judgment at
  > `https://www.elitigation.sg/gd/s/2026_SGFC_46` from zeeker-judgements with cursor
  > `!!!not-base64!!!`."
- [ ] Expected tool call:
  ```
  query_table(
      database="zeeker-judgements",
      table="judgments_fragments",
      filters=[{"column": "source_url", "op": "exact",
                "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}],
      cursor="!!!not-base64!!!",
  )
  ```
- [ ] Expected: `ToolError` with EXACT message text
  ```
  invalid_cursor: keyset cursor is malformed
  ```
- [ ] **INJ-05 acceptance gate:** the garbage value `"!!!not-base64!!!"` MUST NOT appear
  in the error message body (D5-07 fixed-literal discipline). Confirm:
  ```
  grep -F "not-base64" <error-message>
  ```
  Expected: empty. If `grep` finds the garbage value, INJ-05 has been violated —
  ESCALATE.
- [ ] Screenshot. Save to
  `evidence/05-fragment-join/scenario-08b-cursor-malformed.png`.

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
Scenario 1 (basic judgments fragment-join), Scenario 4 (multi-match INJ-05 sanity),
Scenario 8a (limit-cap fixed-literal discipline).

- [ ] Scenario 1 — Claude Code parity. Screenshot →
  `evidence/05-fragment-join/scenario-01-claude-code.png`.
- [ ] Scenario 4 — Claude Code parity (multi-match INJ-05). Screenshot →
  `evidence/05-fragment-join/scenario-04-claude-code.png`.
- [ ] Scenario 8a — Claude Code parity (limit-cap fixed literal). Screenshot →
  `evidence/05-fragment-join/scenario-08a-claude-code.png`.

## F-4 Dry-Run Section (curl / JSON-RPC payloads)

The MCP protocol over streamable HTTP accepts a single JSON-RPC envelope per HTTP POST.
For `tools/call`, the params object embeds the tool name and arguments dict. Run these
BEFORE the Claude Desktop / Code walkthrough to confirm wire-level behavior.

> All examples use `<TARGET>` as a placeholder. Replace with
> `https://mcp.zeeker.sg` (deployed) or `http://127.0.0.1:8080` (local). The protocol
> path is always `/mcp/` with the trailing slash.

### A. Scenario 1 — Judgments fragment-join

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 30, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments_fragments",
        "filters": [
          {"column": "source_url", "op": "exact",
           "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}
        ]
      }
    }
  }' \
  <TARGET>/mcp/
```
Expected: HTTP 200; envelope body has `data` (~10 fragment rows ordered by `ordinal`
ascending starting at 0), no `id` / `judgment_id` / `parent_id` keys on any row,
`pagination.truncated: false`, `pagination.next_cursor: null`.

### B. Scenario 4 — Multi-match parent + INJ-05

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 31, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments_fragments",
        "filters": [
          {"column": "source_url", "op": "exact",
           "value": "https://www.elitigation.sg/gd/s/2001_SGHC_216"}
        ]
      }
    }
  }' \
  <TARGET>/mcp/ \
  > /tmp/scenario-b-response.json
```
Then INJ-05 acceptance gate against the response body:
```
grep -E '2001_SGHC_216|6074e86bc12d' /tmp/scenario-b-response.json
```
Expected: empty. The response envelope MUST NOT contain the URL substring (filter values
are not echoed back) nor the parent `id` literal (FRAG-02 stripping). The URL in the
outgoing JSON-RPC `arguments` is BY DESIGN — that's the user's own input echoed in their
own shell, not the server's response.

### C. Scenario 6 — 957-fragment walk (first page)

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 32, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments_fragments",
        "filters": [
          {"column": "source_url", "op": "exact",
           "value": "<SMART_GLOVE_URL>"}
        ],
        "limit": 100
      }
    }
  }' \
  <TARGET>/mcp/ \
  > /tmp/scenario-c-page-01.json
```
Then continue pages 2..10 by extracting `pagination.next_cursor` from each response and
feeding it back into the next request's `arguments.cursor` field. After 10 calls, all
957 ordinals (0..956) should appear exactly once across the concatenated responses.

### D. Scenario 8a — Limit cap fixed literal

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 33, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments_fragments",
        "filters": [
          {"column": "source_url", "op": "exact",
           "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}
        ],
        "limit": 101
      }
    }
  }' \
  <TARGET>/mcp/ \
  > /tmp/scenario-d-response.json
```
Then F-4 INJ-05 acceptance gate:
```
grep -F "101" /tmp/scenario-d-response.json
```
Expected: empty (other than the JSON-RPC `id: 33` value if your `id` happens to collide
— rename if needed). The error message body must be exactly:
```
invalid_filter_op: limit exceeds fragment-join cap of 100
```
No user-supplied value (`101`) appears anywhere in the error text.

### E. Scenario 8b — Keyset cursor malformed fixed literal

```
curl -sN -X POST \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0", "id": 34, "method": "tools/call",
    "params": {
      "name": "query_table",
      "arguments": {
        "database": "zeeker-judgements",
        "table": "judgments_fragments",
        "filters": [
          {"column": "source_url", "op": "exact",
           "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}
        ],
        "cursor": "!!!not-base64!!!"
      }
    }
  }' \
  <TARGET>/mcp/ \
  > /tmp/scenario-e-response.json
```
Then F-4 INJ-05 acceptance gate:
```
grep -F "not-base64" /tmp/scenario-e-response.json
```
Expected: empty. The error message must be exactly:
```
invalid_cursor: keyset cursor is malformed
```

## Acceptance

- [ ] Pre-conditions block dry-run results recorded (`/healthz` + `tools/list` + D5-09
  description grep + upstream smoke).
- [ ] Scenario 1 (judgments fragment-join) passes on Claude Desktop.
- [ ] Scenario 2 (sglawwatch fragment-join + cursor continuation) passes on Claude
  Desktop.
- [ ] Scenario 3 (pdpc fragment-join + cross-pair single-helper confirmation) passes on
  Claude Desktop.
- [ ] Scenario 4 (multi-match parent + INJ-05 hash binding) passes on Claude Desktop —
  log line captured, URL substring + parent_pk literal both absent from response, log,
  stdout, stderr.
- [ ] Scenario 5 (cold/warm latency disclosure) walked — first-call wall-clock recorded;
  second-call < 500ms.
- [ ] Scenario 6 (957-fragment walk) walked — all 957 ordinals seen exactly once;
  `max(ordinal) == 956`; `truncated: false` on every page; terminal `next_cursor: null`.
- [ ] Scenario 7 (fall-through path) walked — fragment-table query without parent-URL
  filter succeeds; no `fragment_parent_multi_match` log emitted; parent table not
  queried upstream.
- [ ] Scenario 8a (limit cap fixed literal) walked — error message exactly matches
  `invalid_filter_op: limit exceeds fragment-join cap of 100`; `101` not in error body.
- [ ] Scenario 8b (keyset cursor malformed fixed literal) walked — error message
  exactly matches `invalid_cursor: keyset cursor is malformed`; garbage value not in
  error body.
- [ ] No INJ-05 leakage observed in any transcript: no URL substring
  (`"2001_SGHC_216"`), no parent_pk literal (`"6074e86bc12d"`), no garbage user input
  (`"!!!not-base64!!!"`), no user-supplied limit value (`"101"`) appears in any error
  message or visible log line during the walkthrough.
- [ ] At least 3 of 8 scenarios re-verified via Claude Code (parity check).
- [ ] At least 3 of 5 curl/JSON-RPC dry-run examples (A–E) executed and matched the
  documented expected response (F-4 wire-level evidence).
- [ ] Findings captured under `.planning/sessions/<YYYY-MM-DD>/F-4-PHASE5.md` — one
  entry per scenario, including PASS / FAIL / ESCALATE status + latency (Scenarios 5 +
  6) + a PII-stripped excerpt of the response envelope.

## Known accepted gaps (carry-forward from earlier phases + Plan 05-03)

These are documented gaps where automated tests have local compensating controls but the
underlying source contract is NOT fully tight. They do NOT block Phase 5 close, but a
reviewer signing off below should be aware:

1. **Lone-surrogate canary `"\udc80"` UnicodeEncodeError repr leak** (carry-forward
   from Phase 4 / 04-03-SUMMARY and Plan 05-03 / D-Plan-05-03-02). Python's
   `UnicodeEncodeError.__str__()` includes the offending character's `\udc80` repr by
   design. The exception escapes the `anyio` task group as an `ExceptionGroup`;
   `str(eg)` includes the surrogate's `\udc80` repr by Python's machinery. The INJ-05
   invariant for this canary is narrowed to channels Zeeker controls (envelope, stdout,
   stderr, mcp_zeeker caplog) — the `error.__str__` surface is permitted to include
   `\udc80` repr because `ToolError` doesn't propagate `UnicodeEncodeError` in
   production (handler maps it to `upstream_unavailable`). Scenario 4 does NOT include a
   lone-surrogate canary; the human-loop check uses pure-ASCII URLs which are fully
   URL-encodable and exercise the standard envelope path. **Recommended source fix
   (Phase 6 hardening or a 05-XX follow-up):** one-line `except Exception` addition in
   `src/mcp_zeeker/core/fragment_join.py` `compile_filter` mirroring the Plan 04-03
   proposed fix for `core/search.py::_one_table`.

2. **Empty-parent fall-through fetches the whole fragments table** (newly discovered in
   Plan 05-03 — D-Plan-05-03-04). When Call 1 returns 0 matching parent rows for a
   user-supplied URL, `core/fragment_join.py::compile_filter` returns `([], None)`. The
   handler at `tools/retrieval.py:254-261` then detects `fragment_join_active=False` (no
   `parent_fk` filter in the rewritten list) and falls through to the Phase 3 code path
   with an EMPTY filter list — fetching the entire fragments table (capped at the
   default `limit=50` rows, but those 50 rows come from across all judgments, not the
   user's requested URL). A user querying with an unknown parent URL would receive 50
   random fragment rows from across all judgments — a privacy / scope-bounded-response
   violation. Mitigated in production by the 60 RPM rate limit (PRD §2) but should be a
   hard short-circuit. **Recommended source fix (Phase 5.5 hotfix or Phase 6
   hardening):** in `tools/retrieval.py`, after the `compile_filter` call, when
   `fragment_parent_meta is not None AND parent_url_for_qhash is not None AND len(
   normalized_filters) == 0` (empty filter list returned, signaling negative parent
   lookup), return an empty envelope directly without dispatching Call 2. Add a
   regression test asserting that when Call 1 returns 0 rows, no Call 2 fires
   (`httpx_mock.get_requests` count == 1 for the parent-table endpoint, 0 for the
   fragments-table endpoint). See 05-03-SUMMARY.md "Deviations" #3 for the full
   carry-forward context.

   **Scope of impact on this checklist:** if a reviewer accidentally walks Scenario 1's
   curl with a URL that's not actually present in `judgments` (e.g., a typo), the
   response will contain ~50 unrelated fragment rows rather than the expected
   single-document fragments OR an empty envelope. Verifier should treat a "wrong
   fragments returned for a known-missing URL" observation as the documented gap above,
   NOT a new bug.

## Troubleshooting

- **Scenario 1-3 returns empty data for a known-good URL**: check that the URL exists
  upstream by hitting the parent table directly with `?<parent_url_col>__exact=<url>`.
  If parent lookup returns 0 rows, you've hit the documented empty-parent fall-through
  gap (see Known accepted gaps #2). If parent lookup returns ≥1 row but the
  fragment-join returns empty, ESCALATE — Call 2 is failing despite a valid parent_pk.
- **Scenario 4 emits the multi-match warning but the URL substring appears in the log
  line**: INJ-05 regression. The locked-binding-set discipline at D5-04 / FRAG-06 was
  broken — a new structlog binding is leaking the URL value. Audit:
  ```
  grep -rE "log\.(warning|info|error)\([^)]*url" src/mcp_zeeker/core/fragment_join.py
  ```
  Expected: only `parent_url_hash=...` bindings, never raw `url=`, `parent_url=`, or
  `value=`. ESCALATE before Phase 5 close.
- **Scenario 6's 957-walk loses rows or sees duplicates**: keyset cursor regression at
  D5-05 / D5-07. Inspect:
  ```
  grep -rE "encode_keyset_cursor|decode_keyset_cursor" src/mcp_zeeker/core/cursor.py
  ```
  Verify the `(qhash, last_order_by_value, last_id)` tuple is preserved across encode /
  decode. ESCALATE.
- **Scenario 8a or 8b error message contains the user-supplied value**: locked-literal
  regression at D5-07 / D5-08. Audit:
  ```
  grep -rE "(f\"|f')[^\"']*\{(limit|cursor|value|filter_value)" src/mcp_zeeker/
  ```
  Expected: empty for these specific error-message construction sites. ESCALATE.
- **Cold-cache latency >> 5s on Scenario 5**: upstream `data.zeeker.sg` degradation OR
  httpx pool exhaustion. Check upstream directly with `curl -w "%{time_total}"
  https://data.zeeker.sg/zeeker-judgements/judgments_fragments.json?_nocount=1&_size=1`.
  If upstream is fine, Page the operator.

---

## F-4 Sign-off

Per 01-LEARNINGS.md F-4 (carried forward through Phase 2 + Phase 3 + Phase 4): every
curl example and CLI command in this checklist MUST be dry-run against the chosen target
before marking this plan complete.

- [ ] Pre-conditions block dry-run results recorded (/healthz + tools/list + D5-09
  description grep + upstream smoke).
- [ ] Scenario 1 (judgments fragment-join) walked on Claude Desktop or Claude Code.
- [ ] Scenario 2 (sglawwatch fragment-join + cursor continuation) walked.
- [ ] Scenario 3 (pdpc fragment-join + cross-pair confirmation) walked.
- [ ] Scenario 4 (multi-match + INJ-05 hash binding) walked — `grep` confirmed empty for
  URL substring + parent_pk literal.
- [ ] Scenario 5 (cold/warm latency disclosure) walked — first-call latency + warm-call
  latency documented.
- [ ] Scenario 6 (957-fragment walk via keyset cursor) walked — all 957 ordinals seen
  exactly once; `max(ordinal) == 956`; `truncated: false` on every page.
- [ ] Scenario 7 (fall-through path) walked — no `fragment_parent_multi_match` emitted;
  parent table not queried.
- [ ] Scenario 8 (error paths — limit cap + cursor malformed) walked — both fixed
  literals matched exactly; user-supplied values absent from error bodies.
- [ ] Findings captured under `.planning/sessions/<YYYY-MM-DD>/F-4-PHASE5.md`.

**Dry-run target:** `__________________________________________________`
  *(e.g. `https://mcp.zeeker.sg` or `http://127.0.0.1:8080`)*

**Date:** `____________________`

**Signed-off by:** `[AUTO MODE — UNSIGNED — recorded for retro audit]`
  *(Orchestrator AUTO-chain auto-approved the Task 2 checkpoint on 2026-05-14
  to unblock Phase 5 close. A human reviewer MUST still walk all 8 scenarios
  against a real target and replace this placeholder with a real signature
  before any production cut-over. Mirrors Phase 3 / Phase 4 pattern.)*

> This task is a `checkpoint:human-verify`. The automated agent has written this
> checklist and committed it; the actual walk-through requires a human at a keyboard
> with access to Claude Desktop and Claude Code, and a target deployment of the Phase 5
> server image. In AUTO chain mode the orchestrator auto-approved the checkpoint with
> the sign-off block left UNSIGNED — a human verifier MUST tick each checkbox above,
> fill in the three fields, and capture findings under
> `.planning/sessions/<YYYY-MM-DD>/F-4-PHASE5.md` before any production cut-over. To
> escalate during a real walk, describe the failing scenario(s) so the verification loop
> can route into Plan 05-XX-revision before Phase 5 closes.
