---
phase: 03-structured-retrieval-url-keyed-fetch
plan: 04
subsystem: retrieval-slice-c
tags: [mvp-slice-c, fetch, url-keyed, manual-checklist, phase3]
requires:
  - "src/mcp_zeeker/core/visibility.py (_resolve_table, _visible_columns, raise_unsupported_table_for_fetch, raise_not_found — Plan 03-01)"
  - "src/mcp_zeeker/core/datasette_client.py (DatasetteClient.get_table_rows, UpstreamCallFailed — Plan 03-01)"
  - "src/mcp_zeeker/core/envelope.py (Envelope.for_rows factory — Phase 1, Plan 03-03 Pagination extensions unused by fetch)"
  - "src/mcp_zeeker/config.py (URL_COLUMNS, HEAVY_COLUMNS, FRAGMENT_PARENTS, TOOL_TRAILER)"
  - "Plan 03-02 query_table — analog for @mcp.tool registration shape + ToolAnnotations + Annotated signature + TOOL_TRAILER trailer (no behavioral coupling)"
provides:
  - "core/config_lookup.url_column_for(database, table) -> str | None — SOLE reader of config.URL_COLUMNS"
  - "tools/retrieval.fetch — registered @mcp.tool for URL-keyed exact-match retrieval (FETCH-01..05)"
  - "tests/manual/PHASE3-CLIENT-VERIFY.md — D3-20 manual checklist with 6 scenarios + F-4 dry-run section + INJ-05 acceptance gate"
  - "8 GREEN tests in tests/tools/test_fetch.py (all 5 FETCH-XX REQs + D3-14 step 6 multi-match + unknown_database/unknown_table propagation)"
affects:
  - "src/mcp_zeeker/core/config_lookup.py (added url_column_for; module docstring extended)"
  - "src/mcp_zeeker/tools/retrieval.py (replaced fetch NotImplementedError stub with live handler + _FETCH_DESCRIPTION + visibility/config_lookup imports)"
  - "tests/tools/test_fetch.py (rewrote 6 Wave-0 stubs into 8 live tests; added _table_url_re regex matcher mirroring test_query_table)"
  - "tests/manual/PHASE3-CLIENT-VERIFY.md (created — Phase 3 user-facing acceptance gate)"
tech-stack:
  added: []
  patterns:
    - "Single call-site discipline for URL_COLUMNS (mirror of hidden_columns_for / D2-10 — url_column_for is the only reader)"
    - "Validation order D3-14: _resolve_table → url_column_for → upstream call with _size=2 → not_found short-circuit on zero rows → emit-column reshape — short-circuit guarantees no presence side-channel between unsupported and not_found"
    - "Multi-match warning uses structlog kwargs with NO `url=…` binding — INJ-05 / T-03-16; threat-model grep enforces absence"
    - "raise_not_found takes only (database, table) — never url — INJ-05 / T-03-15"
    - "fetch never emits retrieved_content (heavy columns stripped entirely from envelope) — distinct from query_table which routes heavies under retrieved_content (D3-05) on explicit opt-in"
    - "Defensive FK strip via FRAGMENT_PARENTS lookup (unreachable for current URL_COLUMNS but guards future config drift)"
    - "Test stdout-vs-stdlib capture: structlog's PrintLoggerFactory writes JSON to stdout, so the ambiguous-URL test uses capsys (not caplog); the not_found test filters httpx INFO records (httpx is infrastructure outside the INJ-05 contract)"
key-files:
  created:
    - "tests/manual/PHASE3-CLIENT-VERIFY.md"
  modified:
    - "src/mcp_zeeker/core/config_lookup.py"
    - "src/mcp_zeeker/tools/retrieval.py"
    - "tests/tools/test_fetch.py"
decisions:
  - "Wave-0 stub for test_fetch_heavy_columns_under_retrieved_content asserted `retrieved_content` IN row keys, contradicting the plan's <behavior> step 7 and must_haves. Plan is authoritative — rewrote test as test_fetch_strips_heavy_and_fragment_columns asserting no HEAVY_COLUMNS at top level AND `retrieved_content` ABSENT. This codifies the query_table-vs-fetch contract: query_table opts INTO heavies under retrieved_content; fetch never emits heavies (use query_table on *_fragments table for paragraph content)."
  - "Structlog ambiguous-URL warning lands on stdout (PrintLoggerFactory + JSONRenderer), not via stdlib logging — caplog does not capture it. Switched the multi-match test to capsys.readouterr() and assert against captured.out. This is structlog-config-aware rather than fragile to format changes."
  - "The not_found INJ-05 check excludes httpx INFO records (`r.name.startswith('httpx')`) because httpx logs the GET URL at INFO/DEBUG as infrastructure. The INJ-05 contract protects OUR (mcp_zeeker) log emissions + error message bodies, not the third-party HTTP client log. Documented in test docstring + inline comment so a reviewer doesn't think we're loosening the contract."
  - "url_column_for(database, table) returns None on miss (no default, no walrus, no 'unknown' string) so the caller MUST handle the None case via raise_unsupported_table_for_fetch. Mirrors hidden_columns_for's set() default in spirit: both helpers force the caller through the intended single-emission helper."
  - "_size=2 (not 1) on the exact-match query so the multi-match branch fires when there are exactly 2+ matches. _size=1 would never let us detect ambiguity. Cost is exactly one extra upstream row per match — negligible vs the alternative of routing through a separate COUNT query."
  - "FRAGMENT_PARENTS FK strip is unreachable for the current URL_COLUMNS configuration (no fragments table has a URL column entry). Kept the strip code anyway as defense in depth — adding `judgments_fragments.judgment_url` to URL_COLUMNS later would silently leak the parent FK without this guard. Documented as 'defensive coverage' in the handler comment."
metrics:
  duration_min: ~8
  completed_date: 2026-05-14
  tasks: 2
  commits: 3
  files_created: 1
  files_modified: 3
---

# Phase 3 Plan 04: Slice C — fetch URL-keyed retrieval Summary

**One-liner:** `fetch(database, table, url)` lands as a registered MCP tool with exact-string-equality URL matching, single-emission unsupported / not_found / ambiguous distinction, and full INJ-05 discipline — the user-supplied URL never appears in any error message or our own log records. Closes Phase 3's MVP slice trio (A query / B cursor+heavy / C fetch) and delivers FETCH-01..05 as a real, citation-ready capability.

## What shipped

- **`core/config_lookup.url_column_for(database, table) -> str | None`** — single call-site
  for `config.URL_COLUMNS`. Plan 03-04 enforces the same D2-10 discipline that
  `hidden_columns_for` codified for `HIDDEN_COLUMNS`: no other module may read
  `URL_COLUMNS` directly. Callers handle `None` via `raise_unsupported_table_for_fetch`.
- **`tools/retrieval.fetch`** — replaces the Plan 01 `NotImplementedError` stub with the
  8-step D3-14 validation pipeline:
  1. `_resolve_table` (shared with `query_table` — `unknown_database` / `unknown_table`).
  2. `url_column_for` → `raise_unsupported_table_for_fetch` on `None` (FETCH-04; NO
     upstream call beyond the DB summary, asserted by the test).
  3. `DatasetteClient.get_table_rows(database, table, [(url_col+"__exact", url),
     ("_size","2")])` — `_size=2` is the multi-match detector.
  4. `len(rows) == 0` → `raise_not_found(database, table)` (FETCH-05; URL is NOT a
     parameter — T-03-15).
  5. Emit columns: `(visible − HEAVY_COLUMNS) − fragment_fk`. Heavies stripped entirely
     (FETCH-03 must_have line — no `content_text` inlined, no `retrieved_content` key).
  6. `len(rows) > 1` → `log.warning("fetch_ambiguous_url", database=…, table=…,
     match_count=…)` — NO `url` kwarg (T-03-16). First row returned.
  7. Single-row envelope via `Envelope.for_rows`.
- **`_FETCH_DESCRIPTION`** — verbatim per D3-16: includes the rate-limit literal
  (ANNO-03), "exact string equality (no normalization)" language (FETCH-02 advice for
  the LLM), and the canonical `config.TOOL_TRAILER` suffix (INJ-01 / ANNO-02). The
  registry-introspection contract tests (`test_envelope_contract.py`,
  `test_tool_trailer.py`) now automatically cover `fetch` alongside the four Phase 1/2
  tools.
- **`tests/manual/PHASE3-CLIENT-VERIFY.md`** — D3-20 checklist mirroring
  `PHASE2-CLIENT-VERIFY.md`'s structure: pre-conditions → 6 Claude Desktop scenarios →
  Claude Code parity (3 of 6 scenarios) → F-4 dry-run section with 5 curl/JSON-RPC
  payloads → acceptance checklist + sign-off. Covers the user-visible slice end-to-end:
  filter-by-date, cursor walk, opt-in heavy, fetch known URL, unsupported fetch,
  cursor shape-mismatch.

## Tests turned GREEN

The Plan 03-01 Wave-0 stubs in `tests/tools/test_fetch.py` were 6 function-body-import
stubs that raised `NotImplementedError` (no assertions). Plan 03-04 rewrote them as 8
live tests:

| Test                                                | REQ-ID / D-ID         |
|-----------------------------------------------------|-----------------------|
| `test_fetch_known_judgment_returns_one_row`         | FETCH-01              |
| `test_fetch_exact_match_only_no_normalization`      | FETCH-02              |
| `test_fetch_unsupported_table`                      | FETCH-04 + no-upstream-call assertion |
| `test_fetch_not_found_zero_rows`                    | FETCH-05 / INJ-05 (URL-not-echoed in error AND log) |
| `test_fetch_strips_heavy_and_fragment_columns`      | FETCH-03 (no heavies, no retrieved_content, no hidden id) |
| `test_fetch_ambiguous_url_returns_first_and_warns`  | D3-14 step 6 + INJ-05 / T-03-16 |
| `test_fetch_unknown_database`                       | _resolve_table shared with query_table |
| `test_fetch_unknown_table`                          | _resolve_table shared with query_table |

All 8 pass. Full suite (166 tests, 2 skipped) passes with no regression in Phase 1 / 2 /
Slice A / Slice B tests.

## D-IDs implemented

- **D3-13** — `@mcp.tool(name="fetch", ...)` decorator + `ToolAnnotations(readOnlyHint=
  True, idempotentHint=True, openWorldHint=True)` + Annotated signature for
  database/table/url.
- **D3-14** — Validation order: `_resolve_table` → `url_column_for` →
  `get_table_rows(_size=2)` → zero-row not_found → emit-column reshape → ambiguous warning
  → envelope.
- **D3-15** — Single-row envelope via `Envelope.for_rows(database, table, rows=[row_dict])`
  with no `Pagination`.
- **D3-16** — `_FETCH_DESCRIPTION` verbatim with TOOL_TRAILER + rate-limit literal +
  exact-string-equality language.
- **D3-20** — `tests/manual/PHASE3-CLIENT-VERIFY.md` written with the 6 scenarios + F-4
  dry-run; the manual checkpoint task in this plan is the user-walked gate.

## REQ-IDs satisfied

- **FETCH-01** — happy path returns a single-row envelope for a known judgment URL
  (test_fetch_known_judgment_returns_one_row).
- **FETCH-02** — `?utm=...` URL variant is treated as different (no silent
  normalization) → `not_found` (test_fetch_exact_match_only_no_normalization).
- **FETCH-03** — non-heavy, non-fragment column projection; `retrieved_content` key
  never emitted; globally-hidden `id` stripped via `_visible_columns`
  (test_fetch_strips_heavy_and_fragment_columns).
- **FETCH-04** — `unsupported_table_for_fetch` for tables absent from `URL_COLUMNS`,
  with NO upstream table-row request issued (test_fetch_unsupported_table).
- **FETCH-05** — `not_found` for zero-row upstream responses, with the URL absent from
  both the error message body AND our log records (test_fetch_not_found_zero_rows).

## Phase 3 close

Plans 03-01 through 03-04 together close Phase 3:

| Plan  | Slice              | REQ-IDs delivered |
|-------|--------------------|-------------------|
| 03-01 | Foundation (Wave 0) | n/a (shared helpers) |
| 03-02 | Slice A — query_table light MVP | QUERY-01..06, QUERY-09, QUERY-10 |
| 03-03 | Slice B — heavy_columns + qhash cursor | QUERY-07, QUERY-08 |
| 03-04 | Slice C — fetch URL-keyed | FETCH-01..05 |

All 15 Phase 3 REQ-IDs (QUERY-01..10 + FETCH-01..05) are shipped. The manual checkpoint
in this plan (Task 3) is the phase-ending user-walked acceptance gate.

## Deviations from Plan

**1. [Rule 1 — Bug] test_fetch_heavy_columns_under_retrieved_content rewrite**

- **Found during:** Task 1 (test rewrite).
- **Issue:** The Plan 03-01 Wave-0 stub asserted `"retrieved_content" in row` and
  `set(row["retrieved_content"].keys()) <= config.HEAVY_COLUMNS`, contradicting Plan
  03-04's `<behavior>` step 7 ("emit_cols = (visible − HEAVY_COLUMNS) − fk_to_exclude")
  and the must_have line "no content_text inlined, no `id`/`judgment_id`/`parent_id`
  leaked." The plan also explicitly states "fetch never emits retrieved_content per
  FETCH-03."
- **Fix:** Renamed the test to `test_fetch_strips_heavy_and_fragment_columns` and
  flipped the assertions: HEAVY columns absent from top level AND `retrieved_content`
  key absent from the row. The plan is authoritative — query_table is the path for
  heavies (under retrieved_content); fetch is the path for the single canonical row
  without heavy text.
- **Files modified:** tests/tools/test_fetch.py
- **Commit:** 143e7b0 (RED) + c30a982 (GREEN — the live impl assertions now match).

**2. [Rule 3 — Blocking] httpx URL appears in caplog at INFO/DEBUG**

- **Found during:** Task 1 (test_fetch_not_found_zero_rows).
- **Issue:** `caplog.at_level(logging.DEBUG)` captured httpx's request log line
  containing the URL-encoded canary substring (`SECRET-PATH-CANARY-9999`). The INJ-05
  contract protects `mcp_zeeker`'s log emissions + error messages, not the third-party
  HTTP client's infrastructure log.
- **Fix:** Switched caplog level to WARNING (httpx logs at INFO, so excluded by level)
  AND added a defensive filter `[r for r in caplog.records if not r.name.startswith(
  "httpx")]` for documentation. Test docstring + inline comment explain the rationale
  so a reviewer doesn't mistake this for a contract loosening.
- **Files modified:** tests/tools/test_fetch.py
- **Commit:** c30a982 (rolled into the GREEN commit since this happened inside the RED→
  GREEN debug loop).

**3. [Rule 3 — Blocking] structlog warning lands on stdout, not via stdlib logging**

- **Found during:** Task 1 (test_fetch_ambiguous_url_returns_first_and_warns).
- **Issue:** `mcp_zeeker.core.logging.configure_logging` configures structlog with the
  default `PrintLoggerFactory` (no explicit `logger_factory=…` argument) + `JSONRenderer`,
  so warning lines are written to stdout as JSON. `caplog` is wired to stdlib `logging`
  loggers and does not see these records.
- **Fix:** Switched the test fixture from `caplog` to `capsys`; assert against
  `captured.out + captured.err`. This is structlog-config-aware and resilient to log
  format changes (we look for `fetch_ambiguous_url` literal + `"match_count": 2`).
- **Files modified:** tests/tools/test_fetch.py
- **Commit:** c30a982.

**4. [Rule 3 — Blocking] pytest_httpx full-URL matcher vs. query-string params**

- **Found during:** First GREEN run after fetch was implemented.
- **Issue:** `httpx_mock.add_response(url=str)` matches the full URL including query
  string. fetch always issues `?_shape=objects&<url_col>__exact=…&_size=2`, so a bare
  `_table_url("zeeker-judgements","judgments")` mock never matched.
- **Fix:** Added a `_table_url_re(database, table)` regex helper that mirrors
  `tests/tools/test_query_table._table_url_re` and matches the path regardless of query
  string. Replaced all 5 `add_response` calls in test_fetch.py.
- **Files modified:** tests/tools/test_fetch.py
- **Commit:** c30a982.

**5. [Rule 3 — Cleanup] removed unused `_zeeker_schemas` mocks from test_fetch.py**

- **Found during:** First GREEN run after fetch was implemented.
- **Issue:** The Wave-0 stub registered `/_zeeker_schemas.json` mocks on the assumption
  that fetch would call `get_table_column_types` (query_table does). It doesn't — fetch
  has no compile_filters step. pytest_httpx's default `assert_all_responses_were_requested`
  failed.
- **Fix:** Removed the 5 `_zeeker_schemas` `add_response` blocks and the
  `_zeeker_schemas_url` + `_empty_schema_payload` helpers since both are no longer
  referenced. Marked the metadata_cache fixture's metadata URL mock as `is_optional=True`
  since fetch doesn't actually call MetadataCache either.
- **Files modified:** tests/tools/test_fetch.py
- **Commit:** c30a982.

None of these are architectural changes — Rules 1 and 3 only. No Rule 4 checkpoint
needed during execution. (The plan's Task 3 manual checkpoint IS the phase-ending gate
but is not a deviation.)

## Threat Flags

None. The plan's `<threat_model>` covers T-03-15 (raise_not_found), T-03-16
(fetch_ambiguous_url log), T-03-17 (heavy/FK strip), T-03-18 (URL normalization
side-channel), T-03-19 (unsupported vs unknown_table — accepted). The implementation
matches each disposition, the threat-model greps return 0 matches (T-03-15) and the
URL-not-bound contract holds on the multi-match log line (T-03-16, verified by
test_fetch_ambiguous_url + the inline comment in the handler).

No new security-relevant surface was introduced beyond what the plan's threat register
anticipated.

## Manual checkpoint outcome

**Status:** pending-human-action. Task 3 (D3-20 walkthrough) is the phase-ending gate;
the user walks `tests/manual/PHASE3-CLIENT-VERIFY.md` against Claude Desktop and Claude
Code to confirm all 6 scenarios + INJ-05 acceptance + F-4 dry-run. The executor returns
a checkpoint payload to the orchestrator for this step; the user types `approved` when
all 6 scenarios pass, or describes the failing scenario to trigger a gap-closure plan
via `/gsd-plan-phase 3 --gaps`.

To be filled in after the walkthrough:

```
Sign-off: <name>, YYYY-MM-DD — <approved / partial / blocked + reason>
```

## Outstanding items deferred

These are explicitly not Phase 3 scope; the plan flagged each:

- **`pagination.truncated` upstream wiring** — Phase 5 FRAG-04. Phase 3 surfaces the
  field honestly from upstream (default False) but no current Datasette response sets
  it; the consumer side lands when fragments slicing arrives.
- **Per-DB license strings** — Phase 6 ENV-03. `config.LICENSES` placeholders are empty
  strings; `Envelope.for_rows` passes them through unchanged.
- **`query_timeout` error code mapping** — Phase 7 ERR-02. Phase 3 collapses upstream
  timeouts into `upstream_unavailable`.
- **Comprehensive hostile-input corpus** — Phase 8 TEST-06. Plan 03-02 shipped the
  filter-value canary corpus; Plan 03-04 only adds the URL canary `SECRET-PATH-CANARY-
  9999` to the not_found test. A wider URL-canary corpus (zero-width spaces, embedded
  newlines, JSON-like payloads) is the TEST-06 follow-up.
- **Fragment-parent join transparency** — Phase 5 FRAG-01..06. Phase 3 leaves the
  parent-FK strip in the fetch handler as defensive coverage; the dedicated `fetch_with_
  fragments` (or equivalent) tool that actually exposes fragments lands in Phase 5.

## Self-Check: PASSED

Files verified present:
- `src/mcp_zeeker/core/config_lookup.py` (modified — `url_column_for` added)
- `src/mcp_zeeker/tools/retrieval.py` (modified — `fetch` handler live)
- `tests/tools/test_fetch.py` (modified — 8 GREEN tests)
- `tests/manual/PHASE3-CLIENT-VERIFY.md` (created — D3-20 checklist)
- `.planning/phases/03-structured-retrieval-url-keyed-fetch/03-04-SUMMARY.md`

Commits verified present:
- `143e7b0` — RED tests for fetch handler
- `c30a982` — fetch handler + url_column_for implementation
- `723565e` — PHASE3-CLIENT-VERIFY manual checklist

Full suite re-run: 166 passed, 2 skipped, 0 failures.
Lint/format checks: clean on the 3 modified source files.
