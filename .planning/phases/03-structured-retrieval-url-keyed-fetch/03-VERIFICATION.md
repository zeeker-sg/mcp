---
phase: 03-structured-retrieval-url-keyed-fetch
verified: 2026-05-13T17:32:02Z
human_verified_at: 2026-05-14
status: complete
score: 5/5 must-haves verified (automated); all 4 manual checkpoints resolved via 03-HUMAN-UAT.md
overrides_applied: 0
re_verification:
  previous_status: human_needed
  previous_score: 5/5 must-haves verified (automated); 1 manual checkpoint outstanding
  gaps_closed: ["all 4 human_verification items"]
  gaps_remaining: []
  regressions: []
human_verification_resolved:
  - test: "Walk tests/manual/PHASE3-CLIENT-VERIFY.md against Claude Desktop (6 scenarios)"
    expected: "All 6 scenarios pass; ☐ boxes ticked; sign-off line filled in"
    resolved_by: "03-HUMAN-UAT.md Test 1 — all 6 scenarios green 2026-05-14, sign-off line filled (operator: houfu)"
  - test: "Walk Claude Code parity for scenarios 1, 3, 4"
    expected: "Same behavior observed on Claude Code as Claude Desktop"
    resolved_by: "03-HUMAN-UAT.md Test 2 — byte-exact envelope parity confirmed on S1 (10 rows), S3 (content_text lengths 40331/61016/57476 match to the byte), S4 (single-row light-only fetch)"
  - test: "Confirm no INJ-05 leakage in transcripts (no canary or filter-value text in visible error messages)"
    expected: "No URL, filter value, or canary string appears in any user-facing error during the walkthrough"
    resolved_by: "03-HUMAN-UAT.md Test 3 — both error-path gates (S5 unsupported_table_for_fetch, S6 invalid_cursor) confirmed fixed-literal with no user-input echo"
  - test: "F-4 dry-run: execute at least 3 of 5 curl/JSON-RPC examples (A–E) against the live target"
    expected: "Wire-level responses match the documented expected response per example"
    resolved_by: "03-HUMAN-UAT.md Test 4 — 5/5 examples A–E executed wire-level against https://mcp.zeeker.sg/mcp/; INJ-05 grep audit confirms no example.com / NONEXISTENT_999 / 9999 / elitigation substrings in error bodies D + E"
gaps: []
deferred:
  - truth: "pagination.truncated upstream wiring (consumer side)"
    addressed_in: "Phase 5 (FRAG-04)"
    evidence: "Plan 03-04 SUMMARY 'Outstanding items deferred': 'pagination.truncated upstream wiring — Phase 5 FRAG-04. Phase 3 surfaces the field honestly from upstream (default False)'."
  - truth: "Per-DB license strings"
    addressed_in: "Phase 6 (ENV-03)"
    evidence: "Plan 03-04 SUMMARY 'Outstanding items deferred': 'Per-DB license strings — Phase 6 ENV-03'."
  - truth: "Comprehensive hostile-input corpus (URL-canary side)"
    addressed_in: "Phase 8 (TEST-06)"
    evidence: "Plan 03-04 SUMMARY 'Outstanding items deferred': 'Comprehensive hostile-input corpus — Phase 8 TEST-06.'"
---

# Phase 3: Structured Retrieval + URL-Keyed Fetch Verification Report

**Phase Goal:** An MCP client can retrieve rows from any non-hidden table using filters, sort, pagination, and an explicit column allow-list, and can fetch a single row by URL on URL-keyed tables — both with hostile-input-safe error handling.

**Verified:** 2026-05-13T17:32:02Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (mapped to ROADMAP Success Criteria)

| #   | Truth (ROADMAP SC) | Status     | Evidence |
| --- | ------------------ | ---------- | -------- |
| 1   | `query_table` returns rows filtered by all 13 operators (exact, not, contains, startswith, endswith, gt, gte, lt, lte, in, notin, isnull, notnull), sortable by non-hidden column, default `limit=50` max `limit=200` | VERIFIED | `FilterOp` Literal has all 13 strings (introspection confirmed); `tests/tools/test_query_table.py::test_thirteen_ops_end_to_end[...]` runs 13 parametrized cases all green; sort tests `test_sort_ascending`/`test_sort_descending_via_dash_prefix`; limit tests `test_limit_default_50_passed_to_upstream`, `test_limit_max_200_accepted`, `test_limit_201_rejected_pydantic_before_upstream` |
| 2   | `query_table` without `columns` returns only per-table light set; with `columns=['content_text']` returns heavy text under `retrieved_content`, never inlined at row top level | VERIFIED | `tests/tools/test_query_table.py::test_default_light_columns_only` (asserts `set(row.keys()) & HEAVY_COLUMNS == set()` and `"retrieved_content" not in row`); `test_heavy_columns_appear_under_retrieved_content` (D3-19 snapshot: top-level keys disjoint from HEAVY_COLUMNS AND `retrieved_content.keys() ⊆ HEAVY_COLUMNS`); `test_default_response_has_no_retrieved_content_key`; `test_light_only_columns_omit_retrieved_content`; row-reshape impl at retrieval.py:269-281 partitions light vs heavy with explicit subset check |
| 3   | `query_table` that filters/sorts on hidden column or names unknown/hidden column in `columns` returns `unknown_column`; AND no user-supplied filter value text appears in error message or log for any hostile-input fixture | VERIFIED | `tests/tools/test_query_table_errors.py` (8 tests: hidden + nonexistent on filter/sort/columns paths); `tests/tools/test_retrieval_side_channel.py` proves single-emission via counter-patch (counter == 2 per path); `tests/test_filter_value_safety.py` exercises 5 canaries × 2 error paths = 10 cases, asserts canary absent from `caplog`/`capsys.out`/`capsys.err`/error_text at DEBUG level; `grep -n 'f"invalid_filter_op:.*{' src/mcp_zeeker/...` returns 0 — no value interpolation |
| 4   | Reusing cursor with different `sort`/`filters`/`columns` shape returns `invalid_cursor` (qhash mismatch); walking cursor with stable shape returns unique rows with no gaps | VERIFIED | `tests/tools/test_query_table_errors.py::test_invalid_cursor_on_shape_mismatch`, `test_invalid_cursor_on_malformed`, `test_invalid_cursor_short_circuits_before_upstream` (asserts ZERO upstream calls on invalid cursor); `tests/tools/test_query_table.py::test_cursor_walk_round_trip` (page-1 emits next_cursor; page-2 with `_next=PAGE2_TOKEN` succeeds; page-2 terminates with `next_cursor=None`); `tests/test_cursor.py` 7 tests for canonical_shape_str/encode/decode + tilde-encoded real cursor + padding-safe round-trip |
| 5   | `fetch(database, table, url)` returns non-heavy, non-fragment columns for matching row; unmatched URL → `not_found`; non-URL-keyed table → `unsupported_table_for_fetch`; URL match is exact string equality with no silent normalization | VERIFIED | `tests/tools/test_fetch.py::test_fetch_known_judgment_returns_one_row` (FETCH-01); `test_fetch_strips_heavy_and_fragment_columns` (FETCH-03 — no HEAVY at top level, no `retrieved_content` key); `test_fetch_not_found_zero_rows` (FETCH-05 — URL absent from error AND log); `test_fetch_unsupported_table` (FETCH-04 — no upstream call beyond DB summary); `test_fetch_exact_match_only_no_normalization` (FETCH-02 — `?utm=...` variant returns not_found) |

**Score:** 5/5 truths VERIFIED (automated). 4 human verification items pending per D3-20 manual checkpoint.

### Deferred Items

Items explicitly out of Phase 3 scope; addressed in later phases per ROADMAP/SUMMARY deferred lists.

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | pagination.truncated upstream wiring (consumer side) | Phase 5 (FRAG-04) | Plan 03-04 SUMMARY notes "Phase 3 surfaces the field honestly from upstream (default False); the consumer side lands when fragments slicing arrives" |
| 2 | Per-DB license strings | Phase 6 (ENV-03) | Plan 03-04 SUMMARY "Outstanding items deferred" — `config.LICENSES` placeholders are empty strings; Phase 6 ENV-03 wires MetadataCache |
| 3 | `query_timeout` error code mapping | Phase 7 (ERR-02) | Plan 03-04 SUMMARY — Phase 3 collapses upstream timeouts into `upstream_unavailable` |
| 4 | Wider URL-canary hostile-input corpus | Phase 8 (TEST-06) | Plan 03-04 SUMMARY — Plan 03-04 added only a single URL canary `SECRET-PATH-CANARY-9999`; full corpus is TEST-06 follow-up |
| 5 | Fragment-parent join transparency | Phase 5 (FRAG-01..06) | Plan 03-04 SUMMARY — Phase 3 keeps the parent-FK strip in fetch as defensive coverage; dedicated fragments tool lands in Phase 5 |

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `src/mcp_zeeker/config.py` | DEFAULT_QUERY_LIMIT=50, MAX_QUERY_LIMIT=200, HEAVY_COLUMNS frozenset (6 names) | VERIFIED | Lines 264-278; runtime check confirms `HEAVY_COLUMNS == frozenset({content_text, full_text, html_raw, footnote_text, figure_descriptions, text})` |
| `src/mcp_zeeker/core/visibility.py` | 5 raise_unknown_* helpers + _resolve_table + _visible_columns | VERIFIED | All 5 helpers present (lines 32-82); `_visible_columns` reads via `DatasetteClient.current().get_database()` and subtracts `hidden_columns_for` (single source of truth, Pitfall 1 avoided) |
| `src/mcp_zeeker/core/filter_compiler.py` | Filter pydantic model + 13-op FilterOp + compile_filters pure function | VERIFIED | FilterOp Literal has exactly 13 strings (introspection verified); compile_filters has zero IO, zero f-string-into-URL paths; all `invalid_filter_op:` messages are fixed literals; `from None` discipline applied on numeric coercion failure |
| `src/mcp_zeeker/core/cursor.py` | canonical_shape_str + encode_cursor + decode_cursor (qhash) | VERIFIED | Pure stdlib (base64+hashlib+json+fastmcp.exceptions); BLAKE2b digest_size=8 + url-safe b64 + `|` separator; `from None` on decode failure; two fixed-literal `invalid_cursor:` messages |
| `src/mcp_zeeker/core/envelope.py` | Pagination extended with next_cursor + truncated (D3-12) | VERIFIED | Lines 32-47; `extra="forbid"` preserved; pre-existing `total`/`next_offset` retained |
| `src/mcp_zeeker/core/datasette_client.py` | get_table_rows method (mirrors get_table_column_types) | VERIFIED | Line 154; prepends `("_shape", "objects")`; routes through `_request_with_retry` (retry-once-with-jitter on 502/503) |
| `src/mcp_zeeker/core/config_lookup.py` | url_column_for helper (single call-site for URL_COLUMNS) | VERIFIED (with warning) | Helper exists at line 38; **see WR-CR-01 below** — `tools/discovery.py:228` reads `config.URL_COLUMNS` directly, violating the documented single-source-of-truth invariant (but `fetch` itself correctly uses `url_column_for`) |
| `src/mcp_zeeker/tools/retrieval.py` | query_table + fetch as @mcp.tool with ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True) | VERIFIED | Both registered; introspection via `mcp.get_tool('query_table')` and `mcp.get_tool('fetch')` confirms annotations; both descriptions end with `config.TOOL_TRAILER`; QUERY-10 ("case-insensitive") + FETCH-02 ("exact string equality") documented |
| `tests/manual/PHASE3-CLIENT-VERIFY.md` | D3-20 checklist with 6 scenarios, F-4 dry-run, sign-off line | VERIFIED (artifact) — checklist NOT YET walked | File exists (386 lines); 6 scenarios + Claude Code parity + 5 F-4 dry-runs (A–E) + INJ-05 acceptance gate + sign-off; **sign-off line still reads "Verified on YYYY-MM-DD by `<user>`"** (manual walkthrough pending per D3-20) |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `tools/retrieval.py` | `core/visibility.py` | `from mcp_zeeker.core.visibility import _resolve_table, _visible_columns, raise_unknown_column, raise_unsupported_table_for_fetch, raise_not_found` | WIRED | Line 50-56 |
| `tools/retrieval.py` | `core/filter_compiler.py` | `from mcp_zeeker.core.filter_compiler import Filter, compile_filters` | WIRED | Line 49 |
| `tools/retrieval.py` | `core/datasette_client.py` | `DatasetteClient.current().get_table_rows(database, table, params)` | WIRED | Lines 265, 369 |
| `tools/retrieval.py` | `core/envelope.py` | `Envelope.for_rows(database, table, rows, pagination=Pagination(next_cursor=..., truncated=...))` | WIRED | Line 292-296 (query_table); line 414 (fetch — no pagination) |
| `tools/retrieval.py` | `core/cursor.py` | `from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor` | WIRED | Line 46 |
| `tools/retrieval.py` (fetch) | `core/config_lookup.py` | `url_column_for(database, table)` | WIRED | Line 45 + invocation at line 361 |
| `tools/retrieval.py` | `server.py` | `@mcp.tool(...)` on both `query_table` and `fetch` | WIRED | Lines 73-81, 313-321; runtime introspection: both tools register with `readOnlyHint=True, idempotentHint=True, openWorldHint=True` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| `query_table` envelope `.data` | `reshaped` | Real Datasette query via `DatasetteClient.get_table_rows` (httpx GET `/{db}/{table}.json?...`) — not hardcoded; 27 handler-level tests exercise the path with stubbed-but-realistic upstream payloads | Yes | FLOWING — handler issues real upstream call and reshapes the response rows; cursor decode + `_next` param propagation also tested |
| `query_table` envelope `.pagination.next_cursor` | `next_cursor` | `encode_cursor(canonical_shape, result["next"])` when upstream returns truthy `next`; else `None` | Yes | FLOWING — `test_cursor_walk_round_trip` confirms page-1→page-2 walk; cursor decode short-circuits before upstream call (`test_invalid_cursor_short_circuits_before_upstream`) |
| `query_table` row `["retrieved_content"]` | per-row heavy-column dict | reshape step copies from `upstream_row[c] for c in heavy_to_emit` when caller opts in | Yes | FLOWING — D3-19 snapshot test enforces `set(retrieved_content.keys()) ⊆ HEAVY_COLUMNS` |
| `fetch` envelope `.data[0]` | `row_dict` | `{c: first[c] for c in sorted(emit_cols) if c in first}` where `first = result["rows"][0]` from real Datasette query with `?{url_col}__exact={url}&_size=2` | Yes | FLOWING — `test_fetch_known_judgment_returns_one_row` confirms real-shaped row payload flows through; `test_fetch_strips_heavy_and_fragment_columns` confirms heavy/FK columns excluded |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| All 13 filter ops registered in FilterOp Literal | `python -c "import typing; from mcp_zeeker.core.filter_compiler import FilterOp; print(len(typing.get_args(FilterOp)))"` | 13 | PASS |
| HEAVY_COLUMNS frozenset has 6 documented columns | `python -c "from mcp_zeeker import config; print(config.HEAVY_COLUMNS == frozenset({'content_text','full_text','html_raw','footnote_text','figure_descriptions','text'}))"` | True | PASS |
| Cursor round-trip works (encode→decode returns input) | `python -c "from mcp_zeeker.core.cursor import canonical_shape_str, encode_cursor, decode_cursor; s = canonical_shape_str('pdpc','enforcement_decisions',None,[],None); assert decode_cursor(encode_cursor(s, 'abc'), s) == 'abc'"` | exit 0 | PASS |
| `url_column_for` returns mapped column for known tables | `python -c "from mcp_zeeker.core.config_lookup import url_column_for; assert url_column_for('zeeker-judgements','judgments') == 'source_url'; assert url_column_for('nope','table') is None"` | exit 0 | PASS |
| Both tools registered with ANNO-01 annotations | `python -c "import asyncio; from mcp_zeeker.server import mcp; ...await mcp.get_tool('query_table').annotations"` | readOnlyHint=True, idempotentHint=True, openWorldHint=True for both | PASS |
| QUERY-10 case-insensitivity documented in description | `"case-insensitive" in _QUERY_TABLE_DESCRIPTION.lower()` | True | PASS |
| FETCH-02 exact-match contract documented | `"exact string equality" in _FETCH_DESCRIPTION.lower()` | True | PASS |
| Both descriptions end with `config.TOOL_TRAILER` | Inspection of `_QUERY_TABLE_DESCRIPTION` and `_FETCH_DESCRIPTION` | True | PASS |
| No NotImplementedError stubs remain | `grep -n "raise NotImplementedError" src/mcp_zeeker/tools/retrieval.py` | 0 matches | PASS |
| Full test suite passes | `uv run pytest -q` | 166 passed, 2 skipped, 0 failures | PASS |
| Phase 3 test files all pass | `uv run pytest tests/tools/test_query_table.py tests/tools/test_query_table_errors.py tests/tools/test_fetch.py tests/test_cursor.py tests/test_filter_compiler.py tests/test_filter_value_safety.py tests/tools/test_retrieval_side_channel.py -v` | 84 passed | PASS |
| No scope-boundary markers remain in handler | `grep -c "Plan 03-03 will replace" src/mcp_zeeker/tools/retrieval.py` | 0 | PASS |
| No f-string interpolation of user values in error messages | `grep -n 'f"invalid_filter_op:.*{' src/mcp_zeeker/tools/retrieval.py src/mcp_zeeker/core/filter_compiler.py src/mcp_zeeker/core/cursor.py` | 0 matches | PASS |
| `fetch_ambiguous_url` warning has NO `url=` binding | `grep -B2 -A6 "fetch_ambiguous_url" src/mcp_zeeker/tools/retrieval.py` | Only `database=`, `table=`, `match_count=` bound — no `url=` | PASS |

### Probe Execution

| Probe | Command | Result | Status |
| ----- | ------- | ------ | ------ |
| _Project has no convention-pathed probe scripts (`scripts/*/tests/probe-*.sh`)_ | _N/A_ | _N/A_ | SKIPPED (no probe scripts declared in PLAN/SUMMARY for this phase; pytest test suites function as the executable verification surface here) |

### Requirements Coverage

All 15 Phase 3 requirement IDs accounted for across the 4 plans. Cross-referenced against REQUIREMENTS.md descriptions.

| Requirement | Source Plan(s) | Description | Status | Evidence |
| ----------- | -------------- | ----------- | ------ | -------- |
| QUERY-01 | 03-02 | `query_table` returns rows filtered, sorted, paginated | SATISFIED | retrieval.py:82 handler signature + filter→sort→limit→upstream→envelope pipeline; tests cover filter/sort/columns/limit paths |
| QUERY-02 | 03-02 | Default light column set; heavy text only when explicitly listed in `columns` | SATISFIED | retrieval.py:207-216 branch; test_default_light_columns_only |
| QUERY-03 | 03-03 | Heavy columns returned under `retrieved_content` key | SATISFIED | retrieval.py:277-280 reshape; test_heavy_columns_appear_under_retrieved_content (D3-19 snapshot) |
| QUERY-04 | 03-02, 03-03 | 13 filter operators (REQ description says "11" but lists 13; treated as 13) | SATISFIED | FilterOp Literal has all 13; test_thirteen_ops_end_to_end parametrizes all 13 |
| QUERY-05 | 03-02 | Filters/sort on hidden columns → unknown_column (no presence side-channel) | SATISFIED | test_filter_on_hidden_column_raises_unknown_column + test_sort_on_hidden_column_raises_unknown_column + counter-patch in test_retrieval_side_channel.py |
| QUERY-06 | 03-02 | `columns` validated against schema; unknown/hidden → unknown_column | SATISFIED | test_columns_with_hidden_raises_unknown_column + test_columns_with_nonexistent_raises_unknown_column |
| QUERY-07 | 03-02 | Default limit 50, maximum 200, enforced | SATISFIED | Pydantic Field(ge=1, le=200) at retrieval.py:104-112 + handler clamp at retrieval.py:156-157; test_limit_201_rejected_pydantic_before_upstream |
| QUERY-08 | 03-03 | Pagination cursor is qhash-bound; mismatched cursor → invalid_cursor | SATISFIED | core/cursor.py: BLAKE2b digest_size=8 over canonical_shape_str; test_invalid_cursor_on_shape_mismatch |
| QUERY-09 | 03-02 | User-supplied filter values NEVER echoed in errors/logs/LLM-readable strings | SATISFIED | test_filter_value_never_echoed_in_error_or_log (5 canaries × 2 paths = 10 cases all green); grep returns 0 f-string interpolations of `{value}` |
| QUERY-10 | 03-02 | `contains` case-sensitivity documented in tool description | SATISFIED | _QUERY_TABLE_DESCRIPTION contains "case-insensitive"; test_description_documents_case_insensitive_contains |
| FETCH-01 | 03-04 | `fetch(database, table, url)` returns the row at the given URL for URL-keyed tables | SATISFIED | test_fetch_known_judgment_returns_one_row |
| FETCH-02 | 03-04 | URL match is exact string equality — no silent normalization | SATISFIED | test_fetch_exact_match_only_no_normalization |
| FETCH-03 | 03-04 | Returns non-heavy, non-fragment columns; heavy and fragments require follow-up query_table | SATISFIED | retrieval.py:392 emit_cols = (visible - HEAVY_COLUMNS) - fk_to_exclude; test_fetch_strips_heavy_and_fragment_columns |
| FETCH-04 | 03-04 | Non-URL-keyed table → unsupported_table_for_fetch | SATISFIED | retrieval.py:362 url_col is None branch; test_fetch_unsupported_table also asserts no upstream call |
| FETCH-05 | 03-04 | Zero rows → not_found | SATISFIED | retrieval.py:378 raise_not_found; test_fetch_not_found_zero_rows (also asserts URL absent from error + log) |

**Coverage:** 15/15 requirements SATISFIED via automated evidence. No orphaned requirements (every Phase-3-mapped REQ in REQUIREMENTS.md is claimed by at least one plan's frontmatter).

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| _None in Phase 3-modified files_ | — | — | — | `grep -E "TBD\|FIXME\|XXX"` and `grep -E "TODO\|HACK\|PLACEHOLDER"` on the 5 core Phase 3 files (retrieval.py, visibility.py, filter_compiler.py, cursor.py, config_lookup.py) return 0 matches |
| `src/mcp_zeeker/tools/discovery.py:228` | 228 | Direct read of `config.URL_COLUMNS` bypasses the documented single call-site discipline (`url_column_for`) | WARNING (carried forward from 03-REVIEW.md CR-01) | Pre-existing Phase 2 module; **NOT in any Phase 3 plan's `files_modified`** (verified against 03-01..04 PLAN frontmatter). The Phase 3 `fetch` handler itself correctly routes through `url_column_for`. Surfaced here for traceability — this is a code-hygiene gap in a Phase 2 file that does not affect Phase 3 goal achievement. |

### Phase 3 Code Review Carry-forward

The 03-REVIEW.md (depth: standard, status: issues_found) identified 1 critical + 6 warnings + 4 info. None affect the Phase 3 goal as written in ROADMAP success criteria — all of the ROADMAP-success-criterion-mapped behaviors are correctly implemented and tested. Carrying forward as informational notes for the next planning cycle:

- **CR-01** (Phase 2 file `discovery.py` reads `config.URL_COLUMNS` directly) — does not affect `fetch` correctness; flagged as a single-source-of-truth hygiene gap in `describe_table`. Phase 3 fetch handler uses `url_column_for` correctly.
- **WR-01** (silent float-to-int truncation for INTEGER columns in `compile_filters`) — would surface subtly-wrong row counts on `gt=3.99` against INTEGER columns. Not a security boundary; affects correctness but every Phase 3 ROADMAP success criterion still holds in the tested paths.
- **WR-02** (limit clamp uses `invalid_filter_op` rather than a distinct `invalid_limit` code) — error categorization concern; the LOCKED error catalog (per Phase 3 D3-12) does not include `invalid_limit`. Status quo is consistent with the catalog.
- **WR-03** (LIGHT_COLUMNS config drift could leak heavy columns at top level) — current config DOES NOT trigger this (verified: all `LIGHT_COLUMNS` entries are disjoint from `HEAVY_COLUMNS`). Defense-in-depth recommendation.
- **WR-04** (`raise_*` helpers should be typed `NoReturn`) — runtime behavior correct; type-checker hint improvement.
- **WR-05** (`canonical_shape_str` conflates `columns=[]` with `columns=None`) — minor docstring/impl drift; not security-relevant.
- **WR-06** (`get_table_column_types` does not defend against malformed upstream JSON) — operational robustness gap; would surface a 500-class internal error rather than the locked-catalog `upstream_unavailable` code on schema drift.

The review found these as code-quality opportunities, not goal failures. Each is appropriate fodder for a follow-on hardening plan but does not block Phase 3 acceptance.

### Human Verification Required

The Phase 3 plan structure is explicit: Plan 03-04 is `autonomous: false` and Task 3 is a `checkpoint:human-verify` (per the PLAN frontmatter and the resume-signal block). The automated portion (Tasks 1–2 of Plan 03-04) is complete; the manual D3-20 walkthrough is the phase-ending acceptance gate.

#### 1. Manual D3-20 walkthrough on Claude Desktop

**Test:** Open `tests/manual/PHASE3-CLIENT-VERIFY.md`, run the 6 scenarios against a Claude Desktop instance pointed at the Zeeker MCP server (local `uv run uvicorn mcp_zeeker.app:app --port 8080` or live `mcp.zeeker.sg/mcp`).
**Expected:** All 6 ☐ acceptance boxes tick; sign-off line gets the operator's name + date; no INJ-05 leakage observed (no filter VALUE or URL in any user-facing error).
**Why human:** Real MCP client UX is the contract; only a human can confirm Claude Desktop renders the envelope correctly, that the cursor walk feels seamless, and that no canary/value text surfaces in the transcript. The structural correctness is already proven by 84 GREEN tests in the automated suite.

#### 2. Claude Code parity for scenarios 1, 3, 4

**Test:** Repeat scenarios 1 (filter), 3 (opt-in heavy), 4 (fetch known URL) from the checklist using Claude Code.
**Expected:** Same behavior, same envelope shape, same INJ-05 hygiene observed across both clients.
**Why human:** Two-client parity surfaces transport-layer or rendering differences that the unit-test harness cannot model. Required by D3-20.

#### 3. INJ-05 final acceptance — no canary/value text in any transcript

**Test:** While walking the checklist, inspect each LLM-visible error message for the canary substrings and the user-supplied URL.
**Expected:** No leakage. Per the troubleshooting block in PHASE3-CLIENT-VERIFY.md, an `example.com` substring in any not_found message is a regression to file as a bug.
**Why human:** Programmatic coverage handles caplog/capsys; only a human can audit what the LLM observer at the other end of the wire actually sees.

#### 4. F-4 dry-run obligation — execute at least 3 of 5 curl/JSON-RPC examples

**Test:** Run examples A (query_table with filter+sort), C (fetch happy path), D (fetch unsupported) at minimum, from PHASE3-CLIENT-VERIFY.md section "F-4 Dry-Run Section".
**Expected:** Wire-level responses match the documented expected response per example.
**Why human:** Wire-level evidence is the F-4 obligation per Phase 1 LEARNINGS — required before considering the UI walkthrough complete.

### Gaps Summary

Phase 3 has no automated-verifiable gaps. All 5 ROADMAP success criteria are observably true in the codebase with direct test evidence (84 GREEN tests across 7 test files covering the Phase 3 surface; 166/166 in the full regression suite). All 15 requirement IDs (QUERY-01..10, FETCH-01..05) are SATISFIED.

The single outstanding item is the D3-20 / Plan 03-04 Task 3 manual checkpoint — a `checkpoint:human-verify` gate that requires a human to walk `tests/manual/PHASE3-CLIENT-VERIFY.md` against Claude Desktop and Claude Code. The plan frontmatter explicitly marks Plan 03-04 as `autonomous: false`, and the SUMMARY notes "Manual checkpoint outcome: pending-human-action."

Code-review carry-forward items (1 CR + 6 WRs from 03-REVIEW.md) are non-blocking for the phase goal but should inform the next planning cycle:
- CR-01 (URL_COLUMNS direct read in `discovery.py`) is in a **Phase 2 file not modified by Phase 3** — flagged for traceability only.
- WR-01..06 are correctness/hygiene improvements; none invalidates any of the 5 ROADMAP success criteria as currently tested.

---

_Verified: 2026-05-13T17:32:02Z_
_Verifier: Claude (gsd-verifier)_
