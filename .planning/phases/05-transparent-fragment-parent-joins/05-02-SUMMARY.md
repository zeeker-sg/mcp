---
phase: 05-transparent-fragment-parent-joins
plan: 02
status: complete
executed: 2026-05-14
mode: parallel (sub-agent worktree)
test_posture: "253 passed, 9 skipped (8 RED for Plan 05-03 + 1 metadata_cache live + 1 discovery legacy), 0 failed"
subsystem: retrieval / fragment-join
tags:
  - walking-slice
  - compile_filter-body
  - handler-extension
  - keyset-cursor-swap
  - allowed-extra-columns
  - nocount-workaround
  - tool-description-update
dependency_graph:
  requires:
    - 05-01 (FRAGMENT_PARENTS.parent_match_order_by extension; cursor.encode_keyset_cursor / decode_keyset_cursor; fragment_join.normalize_url + ParentPKCache + compile_filter skeleton; conftest._load_fragments_fixture + bound_parent_pk_cache + stub_fragment_join_two_step)
  provides:
    - fragment_join.compile_filter body (real orchestrator — trigger + Call 1 + cache + rewrite)
    - query_table fragment-join code path (visibility exemption + Step 3.5 delegation + Step 7.5 limit cap + keyset cursor encode/decode + _nocount=1 injection)
    - app.py ParentPKCache lifespan binding (singleton + contextvar ready at first request)
    - 7 GREEN handler-level tests (3-pair happy path + FRAG-02 snapshot + 957-fragment walk + 3 error paths + 1 side-channel counter-patch)
  affects:
    - tools/retrieval.py query_table — extended (Phase 3/4 regression intact)
    - app.py — lifespan teardown order ParentPKCache → DatasetteClient → MetadataCache
tech_stack:
  added: []
  patterns:
    - "Phase 4 search.py orchestrator template — module-header preamble + structured warning log binding"
    - "Phase 2 metadata_cache.py singleton+contextvar+anyio.Lock lifecycle (mirrored for ParentPKCache)"
    - "Phase 3 filter_compiler.py fixed-literal ToolError discipline (D3-09 / WR-02)"
    - "Datasette _next token wire shape <last_order_by_value>,<last_id> (RESEARCH §4.3; preserves the (order_by, id) tiebreak FRAG-03 requires)"
    - "INJ-05 hostile-input pattern: string-concat for Datasette param keys (NOT f-string interpolation) — preserves the grep-clean discipline across the module"
key_files:
  created: []
  modified:
    - src/mcp_zeeker/core/fragment_join.py
    - src/mcp_zeeker/tools/retrieval.py
    - src/mcp_zeeker/app.py
    - tests/core/test_fragment_join.py
    - tests/tools/test_retrieval_fragment_join.py
    - tests/tools/test_retrieval_fragment_join_errors.py
    - tests/tools/test_retrieval_fragment_join_side_channel.py
decisions:
  - "D-Plan-05-02-01: anyio.Lock is non-reentrant. ParentPKCache.get() / .set() each acquire self._lock internally; calling them from inside compile_filter's single-flight `async with cache._lock:` block deadlocks. Inlined the get/set bodies inside the locked critical section — same effective semantics, no nested-acquire."
  - "D-Plan-05-02-02 (Rule 1 / Rule 2 deviation): the orchestrator's Phase 5 INJ-05 grep `(f\"|f')[^\"']*\\{(url|parent_url|filter_value|normalized_url)` is too strict — it flags column-NAME interpolation `f\"{parent_url_col}__exact\"`. The pre-existing Phase 3 `fetch()` line at retrieval.py used the same `f\"{url_col}__exact\"` pattern. Converted both new fragment_join code and the pre-existing fetch line to string concatenation `parent_url_col + \"__exact\"` so the grep stays clean. Behavior identical; INJ-05 intent (no URL VALUE interpolation) preserved."
  - "D-Plan-05-02-03: compile_filters re-validates filter columns against visible_columns (filter_compiler.py:114) as a defense-in-depth check. The internal parent_fk filter that fragment_join.compile_filter injects is NOT in visible (HIDDEN_COLUMNS strips id/judgment_id/etc.). Augmented visible_for_compile with parent_fk on the join path so compile_filters lets the synthetic filter through. Visibility against user-facing column names still rejects arbitrary parent_fk filters because the user-facing visibility loop runs BEFORE the rewrite — see threat T-05-13."
  - "D-Plan-05-02-04: the 957-fragment walk test consumes 10 ordered httpx_mock responses. Pages 2-9 are synthesized at test-time from large_page1.json's row template (helper `_synth_intermediate_page(page_num, template_row)`); page 1 and page 10 use the captured fixtures verbatim. ParentPKCache.bind(ttl=0) means Call 1 fires on every walk step — registered the parent-lookup stub as `is_reusable=True` so 10 calls share one response."
  - "D-Plan-05-02-05: query_table's existing Step 9 / Step 10 / Step 12 are wrapped in `if fragment_join_active: ... else: <Phase 3 path>` branches. The non-fragment-table path (every Phase 1/2/3/4 caller) takes the `else` branch and is byte-identical to today's behavior — 50 regression tests pass unchanged."
metrics:
  duration: "approx 35 min (in-worktree)"
  task_count: 3
  files_modified: 7
  commits: 3
---

# Phase 5 Plan 02: Walking Slice — Fragment-Join Live End-to-End Summary

## One-liner

`fragment_join.compile_filter` body ships (trigger + Call 1 with `_sort_desc=<parent_match_order_by>&_size=1` + ParentPKCache single-flight + filter rewrite + multi-match warning) and `query_table` extends with 5 micro-additions (`allowed_extra_columns` visibility exemption + Step 3.5 delegation + Step 7.5 limit re-clamp to 100 + Step 9/12 keyset cursor swap binding qhash to the NORMALIZED URL not parent_pk + Step 10 `_nocount=1` injection); `app.py` binds `ParentPKCache` once per process; 7 RED handler-level test stubs flip to GREEN.

## Walking-slice proof

A real MCP client can now call:

```python
query_table(
    database="zeeker-judgements",
    table="judgments_fragments",
    filters=[{"column": "source_url", "op": "exact", "value": "https://www.elitigation.sg/gd/s/2026_SGFC_46"}],
)
```

and receive ordered fragments back. The LLM filters by the parent table's URL column (the same column it knows from `describe_table` on `judgments`) and never sees the internal `judgment_id` PK or FK. The 957-fragment paginated walk completes cleanly across 10 pages with `truncated=False` on every page and `next_cursor=None` on the terminal page.

## Tasks executed

### Task 1 — `core/fragment_join.py::compile_filter` body — committed `9869836`

- Trigger detection (D5-02): `(database, table) in FRAGMENT_PARENTS` AND exactly-one-exact filter on the parent URL column. Anything else returns the filters unchanged (fall-through per D5-03).
- Call 1 parent lookup (D5-04): `DatasetteClient.current().get_table_rows(database, parent_table, [(parent_url_col + "__exact", url_value), ("_sort_desc", fragment_parent["parent_match_order_by"]), ("_size", "1")])` per RESEARCH §4.2. The dash-prefix sort syntax (`_sort=-<col>`) returns HTTP 500 — never used.
- `ParentPKCache.current()` single-flight via `async with cache._lock:` — re-checks the cache inside the lock so a sibling task that already filled the entry short-circuits the second upstream call. The get/set bodies are inlined inside the locked block (anyio.Lock is non-reentrant — D-Plan-05-02-01).
- Filter rewrite: drops the user-supplied exact-on-parent-URL filter and injects `Filter(column=fragment_parent["parent_fk"], op="exact", value=parent_pk)`. Other filters carry through (e.g., `ordinal > 5` for drill-within-document).
- Multi-match warning (FRAG-06): when Call 1 reports `filtered_table_rows_count > 1`, emits `log.warning("fragment_parent_multi_match", ..., parent_url_hash=blake2b(normalized.encode(), digest_size=8).hexdigest())`. NEVER binds the raw URL or parent_pk.
- Negative cache: empty parent-lookup result is cached as `None` so repeat queries within the TTL don't re-hit upstream.
- Tests: deleted `test_compile_filter_skeleton_raises_not_implemented`; added 4 GREEN tests (trigger, two fall-through variants, negative cache short-circuit). 12 unit tests pass.

### Task 2 — `tools/retrieval.py::query_table` extension + `app.py` lifespan binding — committed `a663cc4`

Five micro-additions inserted into the existing 15-step `query_table` body (Phase 3 path preserved; non-fragment-table queries take the existing `else` branches):

1. **Visibility exemption (Step 4)** — compute `allowed_extra_columns = {parent_url_col}` for fragment tables in `FRAGMENT_PARENTS`; the per-field visibility loop accepts the parent URL column on top of `visible` (RESEARCH §4.6 Resolution A / Pitfall 4). Also captures `parent_url_for_qhash = normalize_url(user_url_value)` before the rewrite so the keyset cursor can bind to the normalized URL (D5-06).
2. **Fragment-join delegation (Step 3.5)** — `await fragment_join.compile_filter(database, table, normalized_filters)` between the visibility loop and the column-types fetch. `UpstreamCallFailed` from Call 1 maps to the existing `upstream_unavailable` fixed-literal. `fragment_join_active` is detected by presence of the internal `parent_fk` filter in the rewritten list.
3. **Limit re-clamp (Step 7.5)** — fixed-literal `ToolError("invalid_filter_op: limit exceeds fragment-join cap of 100")` when `fragment_join_active and limit > 100`. No `{limit}` interpolation (D5-08 / INJ-05).
4. **Keyset cursor decode (Step 9)** — on the join path, builds a synthetic filter list with `Filter(column=parent_url_col, op="exact", value=parent_url_for_qhash)` in place of the rewritten `parent_fk` filter, then computes `canonical_shape_str(database, table, None, synthetic, columns)` so qhash binds the NORMALIZED URL not parent_pk (D5-06). Cursor decodes via `decode_keyset_cursor(cursor, canonical_shape) → (last_ord, last_id)` → `datasette_next = f"{last_ord},{last_id}"` per RESEARCH §4.3 wire format.
5. **`_nocount=1` injection (Step 10)** — `params.append(("_nocount", "1"))` when `fragment_join_active`. judgments_fragments is the largest table; without this Datasette's COUNT(*) over the filtered subquery exceeds `sql_time_limit_ms` and returns HTTP 400 (RESEARCH §4.4 / Pitfall 2).
6. **Keyset cursor encode (Step 12)** — when `fragment_join_active and result.get("next")`, splits the upstream `next` token on `,` and routes through `encode_keyset_cursor(canonical_shape, last_ord, last_id)` so the (order_by, id) tiebreak survives across pages (FRAG-03).

Also:
- **D5-09 tool-description note** — one-liner "On *_fragments tables, an `exact` filter on the parent's URL column triggers a transparent join — fragments are returned sorted by paragraph order with `limit` capped at 100 per call." inserted BEFORE the rate-limits sentence; `config.TOOL_TRAILER` remains the LAST suffix (INJ-01 / ANNO-02 preserved).
- **app.py lifespan** — adds `ParentPKCache.bind(ParentPKCache())` after the existing `MetadataCache` + `DatasetteClient` binds. LIFO teardown: `ParentPKCache.reset → DatasetteClient.reset → MetadataCache.reset` (mirrors existing pattern).
- **`visible_for_compile`** — augments the `visible_columns` set passed to `compile_filters` with the parent_fk column when `fragment_join_active` so filter_compiler's defense-in-depth check accepts the internal rewritten filter (D-Plan-05-02-03).

### Task 3 — flip 4 handler-level RED stubs to GREEN — committed `8448aec`

- `tests/tools/test_retrieval_fragment_join.py` — 3 of 4 stubs flipped GREEN: `test_three_pairs_happy_path` (3 parametrized pairs), `test_no_internal_ids_in_response`, `test_957_fragment_walk`. The 1500-frag synthetic stays `pytest.skip` (Plan 05-03 owns).
- `tests/tools/test_retrieval_fragment_join_errors.py` — 3 of 3 stubs flipped GREEN with anchored regex matches against the EXACT fixed literals (D5-07 / D5-08 / D5-03).
- `tests/tools/test_retrieval_fragment_join_side_channel.py` — 1 of 1 stub flipped GREEN; counter-patches `mcp_zeeker.tools.retrieval.fragment_join.compile_filter` and asserts the counter == 3 after invoking `query_table` once per fragment-table pair (D5-01 single auditable code path).

## D-IDs implemented

- **D5-01** — single auditable join orchestrator surface — counter-patch GREEN.
- **D5-02** — exactly-one-eq-on-parent-URL trigger contract — happy-path + 2 fall-through tests GREEN.
- **D5-03** — fall-through philosophy — `test_fragment_table_without_eq_filter_falls_through` asserts no parent-lookup call when fragment_join inactive.
- **D5-04** — two-request shape + ParentPKCache + multi-match warning (warning body shipped here; the INJ-05 hash safety assertion is Plan 05-03's `test_multi_match_warning_hashes_url`).
- **D5-05** — keyset cursor via `(qhash, last_order_by_value, last_id)` — Step 9 + 12 swap GREEN.
- **D5-06** — qhash binds the NORMALIZED URL (not parent_pk) — captured `parent_url_for_qhash` before the rewrite; synthetic filter list reconstructs the URL-keyed shape for cursor purposes.
- **D5-07** — `decode_keyset_cursor` wired into the handler with the EXACT fixed literal `invalid_cursor: keyset cursor is malformed` — error test GREEN.
- **D5-08** — handler-level limit re-clamp to 100 — error test GREEN.
- **D5-09** — tool description gains the one-line fragment-table note BEFORE `config.TOOL_TRAILER` — preserved trailer-suffix invariant verified by automated check.

## RESEARCH corrections folded

- **§4.2** — `_sort_desc=<col>` syntax — used in `compile_filter` Call 1 params.
- **§4.3** — Datasette `_next` token wire format — used in Step 9 + 12 keyset swap.
- **§4.4** — `_nocount=1` injection — Step 10 universal on fragment-join Call 2.
- **§4.6** — `allowed_extra_columns` visibility exemption (Resolution A) — Step 4 extension.
- **§4.12** — tool description prose — inserted before `config.TOOL_TRAILER`.

(R1 / R2 / R4 were folded by Plan 05-01.)

## Files for Plan 05-03 to consume

Remaining RED stubs (Plan 05-03 deliverables):

- `tests/core/test_fragment_join_value_safety.py::test_url_value_never_echoed` (5 parametrized canaries) — INJ-05 hostile-URL corpus across stdout / stderr / caplog channels.
- `tests/core/test_fragment_join_value_safety.py::test_multi_match_warning_hashes_url` — FRAG-06: assert the warning log line contains `parent_url_hash=<16-hex>` and NOT the URL substring.
- `tests/tools/test_retrieval_fragment_join.py::test_1500_fragment_walk_synthetic` — FRAG-04: 15-page synthetic regression beyond Datasette's 1000-row cap.

The walking-slice infrastructure (compile_filter body + handler delegation + tool description + lifespan binding) is in place; Plan 05-03 adds the hardening tests against the SAME code paths.

## Conftest status

`tests/conftest.py` **UNTOUCHED IN PLAN 05-02** — single-touch consolidation discipline preserved (Plan 05-01 owns the Phase 5 conftest extension). Verified by `git diff --stat tests/conftest.py` returning empty.

## Deviations

### Auto-fixed Issues

**1. [Rule 1 — Bug] Orchestrator INJ-05 grep flags pre-existing `f"{url_col}__exact"` in `fetch()` at retrieval.py:521**

- **Found during:** Task 2 verification (`grep -rE "(f\"|f')[^\"']*\{(url|parent_url|filter_value|normalized_url)" src/mcp_zeeker/tools/retrieval.py` returned 1 match)
- **Issue:** Phase 3 commit c30a982 introduced `(f"{url_col}__exact", url)` in the `fetch` handler. `url_col` is a column NAME (e.g., "source_url") sourced from `config.URL_COLUMNS` — innocent interpolation, but the regex matches any f-string starting with `{url`. The grep was added as a Phase 5 acceptance criterion, so this pre-existing line tripped the gate.
- **Fix:** Converted to string concatenation `(url_col + "__exact", url)`. Same wire shape, same behavior, satisfies the literal grep. Mirrors the same discipline I applied in `fragment_join.compile_filter` for `(parent_url_col + "__exact", url_value)`. Added a comment explaining the choice.
- **Files modified:** `src/mcp_zeeker/tools/retrieval.py`
- **Commit:** `a663cc4`

**2. [Rule 1 — Bug] `compile_filter`'s comment `_sort=-<col>` tripped the inspect-based acceptance check**

- **Found during:** Task 1 verification (`assert '_sort=-' not in src`)
- **Issue:** The plan's automated verify command checks `_sort=-` is absent from the inspect-source string. My docstring comment said "NEVER `_sort=-<col>`" which matched. The acceptance criterion at the file level uses `grep -v '^#'` (excludes comment-only lines), so the comment would have passed that check — but the inspect-source check matches the full body including in-function comments.
- **Fix:** Reworded the comment to "NEVER the dash-prefix variant" without literal `_sort=-`.
- **Files modified:** `src/mcp_zeeker/core/fragment_join.py`
- **Commit:** `9869836`

**3. [Rule 2 — Missing critical functionality] `compile_filters`' defense-in-depth visibility re-check rejected the internal `parent_fk` filter**

- **Found during:** First Task 2 test run (regression sweep)
- **Issue:** `filter_compiler.compile_filters` at line 114 re-validates each filter's column against `visible_columns` even though the handler's per-field loop already did the primary check. The internal `parent_fk` filter that `fragment_join.compile_filter` injects is NOT in `visible` because HIDDEN_COLUMNS strips it. Without an augmentation, compile_filters raised `unknown_column: judgment_id` and the walking slice broke before any upstream call.
- **Fix:** Augmented `visible_for_compile = visible | allowed_extra_columns | {fragment_parent_meta["parent_fk"]} if fragment_join_active`. The user-facing visibility loop is unchanged — arbitrary user-supplied `judgment_id` filters still trip `unknown_column` because the loop runs BEFORE the rewrite (threat T-05-13 mitigation preserved).
- **Files modified:** `src/mcp_zeeker/tools/retrieval.py`
- **Commit:** `a663cc4`

**4. [Rule 1 — Bug] `anyio.Lock` is non-reentrant; `ParentPKCache.get()/set()` from inside `async with cache._lock:` deadlocked**

- **Found during:** First Task 1 test run
- **Issue:** The plan's pseudocode says "enter `async with cache._lock:` block ... re-check `await cache.get(...)` inside the lock ... `await cache.set(...)` inside the lock". But both `get()` and `set()` themselves acquire `cache._lock`, and anyio.Lock raises `RuntimeError("Attempted to acquire an already held Lock")` on nested acquire.
- **Fix:** Inlined the get/set body inside the `compile_filter` single-flight block (direct manipulation of `cache._data` + `time.monotonic()`). Same semantics — the lock is held throughout the cold-cache critical section, sibling tasks block on the same lock, no race window.
- **Files modified:** `src/mcp_zeeker/core/fragment_join.py`
- **Commit:** `9869836`

## Tests touched

- `tests/core/test_fragment_join.py` — REWRITTEN (skeleton-sentinel test removed; 4 GREEN compile_filter tests + 8 normalize_url parametrize cases — 12 tests pass).
- `tests/tools/test_retrieval_fragment_join.py` — RED stubs flipped: 3 happy-path parametrized + FRAG-02 snapshot + 957-frag walk = 5 GREEN; 1500-frag synth stays RED for Plan 05-03.
- `tests/tools/test_retrieval_fragment_join_errors.py` — 3 RED stubs flipped GREEN (anchored regex matches against fixed literals).
- `tests/tools/test_retrieval_fragment_join_side_channel.py` — 1 RED stub flipped GREEN (counter-patch on `compile_filter`).
- `tests/conftest.py` — **NOT modified.**

Full suite: **253 passed, 9 skipped (8 Plan 05-03 RED + 1 metadata_cache live + 1 discovery legacy), 0 failed.**

## Phase 1-4 regression posture

- `tests/tools/test_query_table.py` — 25 tests pass unchanged.
- `tests/tools/test_describe_table.py` + `test_list_tables.py` + `test_fetch.py` — all pass.
- `tests/tools/test_search.py` (Phase 4) — all pass.
- `tests/tools/test_retrieval_side_channel.py` — counter-patch on `raise_unknown_column` still GREEN (the new allowed_extra_columns exemption does NOT alter the loop's single-emission identity for unknown columns — only the success-set is wider).
- `tests/test_config_lookup_single_source.py` — passes (uses `url_column_for` in both retrieval.py and fragment_join.py).
- `tests/test_envelope_contract.py` — passes (no envelope-shape change).
- `tests/test_cursor.py` + `tests/core/test_cursor_keyset.py` — both pass.

## INJ-05 audit

Phase 5 INJ-05 grep over the two modified source files returns no matches:

```bash
grep -rE "(f\"|f')[^\"']*\{(url|parent_url|filter_value|normalized_url)" \
  src/mcp_zeeker/core/fragment_join.py \
  src/mcp_zeeker/tools/retrieval.py
# (no output)
```

All log bindings in `compile_filter` use `parent_url_hash=blake2b(normalized.encode(), digest_size=8).hexdigest()` for URL — never the raw value. The only `url_value` references in `compile_filter` are (a) the `compile_filters`-side Datasette param value (httpx URL-encodes) and (b) the function-body computation `normalized = normalize_url(url_value)` — neither bound to any LLM-readable surface.

## Threat surface scan

No new threats discovered. All threats T-05-09..T-05-16 from the plan's threat model are addressed by Plan 05-02:

- **T-05-09** (parent_pk leaking via cursor) — mitigated: keyset cursor encodes `(qhash, last_ord, last_id)` only; qhash is computed over a synthetic filter list with the normalized URL substituted back in.
- **T-05-10** (cross-table cursor reuse) — mitigated: canonical_shape includes `(database, table)`, so cross-table cursor reuse trips the existing `cursor does not match current request shape` literal.
- **T-05-11** (limit re-clamp value echo) — mitigated: fixed-literal message, INJ-05 grep clean on the limit re-clamp line.
- **T-05-12** (HTTP 400 SQL Interrupted body echo) — mitigated: `_nocount=1` unconditional on the join path; the existing `upstream_unavailable` Phase 3 mapping handles transient failures without echoing body text.
- **T-05-13** (smuggled judgment_id filter) — mitigated: `allowed_extra_columns` is scoped to the SPECIFIC parent URL column (not a wildcard); a filter like `{column: "judgment_id", op: "exact", value: "x"}` still trips `unknown_column` at the per-field loop before fragment_join runs.
- **T-05-14** (TTL boundary load amplification) — accepted per plan (Phase 7 rate limiter is primary defense).
- **T-05-15** (silent app.py bind failure) — mitigated: `ParentPKCache.current()` raises `RuntimeError("ParentPKCache.current() called outside a bound scope")` so a missing bind fails loudly on the first fragment-join request.
- **T-05-16** (Phase 3/4 regression) — mitigated: all 5 insertions conditional on `fragment_join_active`; non-fragment queries take the existing Phase 3 `else` branches. 50-test Phase 3 + Phase 4 regression GREEN.

## Self-Check: PASSED

- [x] All 3 tasks executed
- [x] Each task committed individually (3 commits: 9869836, a663cc4, 8448aec)
- [x] SUMMARY.md created at `.planning/phases/05-transparent-fragment-parent-joins/05-02-SUMMARY.md`
- [x] `core/fragment_join.py::compile_filter` has a real body (no NotImplementedError)
- [x] `tools/retrieval.py::query_table` has the 5 micro-additions per RESEARCH §4.6 + PATTERNS.md
- [x] D5-09 tool description note appended; `config.TOOL_TRAILER` still last
- [x] 7 RED stub tests now GREEN (3 happy + 1 snapshot + 1 957-walk + 3 error + 1 side-channel)
- [x] 8 stub tests stay RED (5 INJ-05 canaries + 1 multi-match hash + 1 synth-1500 walk + skeleton-sentinel DELETED)
- [x] No modifications to STATE.md, ROADMAP.md, tests/conftest.py
- [x] INJ-05 grep clean on both modified source files
- [x] `uv run pytest tests/ -x -q --ignore=tests/manual` passes (253 pass / 9 skip / 0 fail)
- [x] `uv run ruff format --check` + `uv run ruff check` both pass on all modified files

## Next steps

Plan 05-03 ships the 3 remaining RED stubs (INJ-05 hostile-URL corpus + multi-match URL-hash assertion + 1500-frag synthetic regression). Plan 05-04 ships the manual UAT.
