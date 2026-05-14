---
phase: 05-transparent-fragment-parent-joins
plan: 04
status: complete
executed: 2026-05-14
mode: parallel (sub-agent worktree)
test_posture: "no source/test changes — automated suite posture unchanged from Plan 05-03 (263 passed, 2 skipped, 0 failed)"
subsystem: manual-uat / f-4-dry-run / phase-close
tags:
  - manual-uat
  - f-4-dry-run
  - client-verify
  - auto-mode-checkpoint
  - documentation-only
  - phase-close
dependency_graph:
  requires:
    - 05-01 (FRAGMENT_PARENTS.parent_match_order_by extension; cursor keyset encode/decode; fragment_join skeleton + ParentPKCache)
    - 05-02 (compile_filter body + query_table 5 micro-additions + app.py lifespan + D5-09 tool description note)
    - 05-03 (test-only hardening — 5-canary INJ-05 hostile-URL corpus + multi-match URL-hash assertion + 1500-frag synthetic walk + truncated honesty + ParentPKCache hit side-channel)
  provides:
    - tests/manual/PHASE5-CLIENT-VERIFY.md (8-scenario F-4 dry-run obligation document)
    - AUTO-mode Phase 5 close (orchestrator-side checkpoint auto-approved; sign-off block UNSIGNED for retro audit)
  affects: []
tech_stack:
  added: []
  patterns:
    - "Phase 1-4 PHASE{N}-CLIENT-VERIFY.md template carry-forward (verbatim — preconditions + scope + scenarios + F-4 sign-off block)"
    - "F-4 OBLIGATION discipline at TOP + F-4 Sign-off block at BOTTOM (01-LEARNINGS §F-4 → Phase 2/3/4 ratification → Phase 5 carry-forward)"
    - "AUTO-mode UNSIGNED placeholder in Signed-off-by field (mirrors Phase 3 / Phase 4 — orchestrator auto-approves to unblock close; human walk still required pre-production)"
    - "INJ-05 acceptance gates per-scenario (grep -F for canary substrings + URL/parent_pk literals + user-supplied values in error bodies)"
    - "Curl/JSON-RPC dry-run examples (A-E) for wire-level evidence"
    - "Known accepted gaps section carries forward Phase 4 lone-surrogate canary + Plan 05-03 empty-parent fall-through documentation"
key_files:
  created:
    - tests/manual/PHASE5-CLIENT-VERIFY.md
  modified: []
decisions:
  - "D-Plan-05-04-01: 10 F-4 Sign-off block checkboxes (pre-conditions + 8 scenarios + 1 findings line) — matches Phase 4 template verbatim. The plan spec stated 9; the analog template has 10 (the 'Findings captured' line is genuinely useful for retro audit). Followed the analog over the literal plan count since both spec lines (success_criteria + verification) refer to the analog 'Sign-off block' which has 10 items in PHASE4-CLIENT-VERIFY.md."
  - "D-Plan-05-04-02: F-4 Signed-off by field populated with '[AUTO MODE — UNSIGNED — recorded for retro audit]' per plan line 260. The 10 walk-through checkboxes remain unchecked — only the signature placeholder records the AUTO-mode auto-approval. A real human reviewer must tick the boxes + replace the placeholder before any production cut-over."
  - "D-Plan-05-04-03: Two atomic commits — Task 1 ships the checklist file; Task 2 records the AUTO-mode auto-approval as a 5-line edit to the Signed-off-by block. Following the 'Each task committed individually' parallel-executor rule from the orchestrator prompt. Task 2 has no source-code impact (it is a checkpoint:human-verify task whose normal mode is to PAUSE for the human); the file edit just makes the AUTO-mode decision auditable."
  - "D-Plan-05-04-04: SMART GLOVE judgment URL (judgment_id=66e73dfa5db4 from RESEARCH §3 — the 957-fragment parent) is described as '<SMART_GLOVE_URL>' template variable in Scenario 6's curl, with a pre-walk discovery step (curl against the judgments parent table by id__exact). The actual URL is not captured in this checklist because RESEARCH §3 references it by judgment_id, not source_url. The reviewer extracts the URL at walk time. Trade-off: less prescriptive curl, but the URL is data-set-dependent and could rotate."
metrics:
  duration: "approx 20 min (in-worktree)"
  task_count: 2
  files_modified: 1
  commits: 2
---

# Phase 5 Plan 04: Manual UAT Checklist + F-4 Dry-Run Sign-off Summary

## One-liner

`tests/manual/PHASE5-CLIENT-VERIFY.md` ships — 8 scenarios (3 happy-path fragment-join
pairs + multi-match INJ-05 hash binding + cold/warm latency disclosure + 957-fragment
keyset walk + fall-through path + 2 error paths) + F-4 OBLIGATION block at top + F-4
Sign-off block at bottom (UNSIGNED placeholder for retro audit per AUTO-mode pattern).
Phase 5 orchestrator-side close is unblocked; real human dry-run remains the standing
obligation before production cut-over.

## What shipped

### Task 1 — `tests/manual/PHASE5-CLIENT-VERIFY.md` — committed `9e6f824`

Created the Phase 5 manual UAT checklist mirroring `tests/manual/PHASE4-CLIENT-VERIFY.md`
structure verbatim (preconditions + scope + scenarios + F-4 sign-off block). Content:

**Top of file (per plan spec line 144-147):**
- Title: `# Phase 5 — Client Verification Checklist (Transparent Fragment-Parent Joins)`.
- Local-server start command: `uv run uvicorn mcp_zeeker.app:app --host 127.0.0.1 --port 8080`.
- F-4 OBLIGATION blockquote citing 01-LEARNINGS.md F-4 (ratified Phase 2 + Phase 3 +
  Phase 4) + the requirement that every curl example MUST be dry-run before Phase 5 closes.
- AUTO-MODE NOTICE blockquote announcing the orchestrator-side auto-approval AND that
  substantive human review against a real MCP target is still standing obligation.

**Scope section:**
One-paragraph summary of the 5-step transparent join (URL resolution → ParentPKCache →
filter rewrite → fragments fetch → keyset pagination). Cross-reference table of the
three fragment-table pairs (judgments_fragments / about_singapore_law_fragments /
enforcement_decisions_fragments) with their parent tables, parent URL columns, and
order_by columns. Four "design properties the human verifier MUST understand BEFORE
walking the scenarios" surfaced explicitly:

1. Cold-cache ~5s / warm-cache ~150ms (RESEARCH §4.11; fragments EXCLUDED from p95<1.5s
   SLO per ROADMAP NFR-01) — DO NOT file as a bug.
2. Multi-match warnings are stale-duplicate-import signals (all observed shares same
   parent id) — data-quality, not true URL→multi-parent collision.
3. Public `query_table` signature `le=200`; fragment-join path re-clamps to 100. Asymmetry
   documented at description time per D5-08 / D5-09.
4. `pagination.upstream_total_hits` / `filtered_table_rows_count` is `null` on the
   fragment-join path (`_nocount=1` injection — RESEARCH §4.4 / Pitfall 2).
   `pagination.truncated` + `pagination.next_cursor` are the load-bearing pagination
   signals.

**Pre-conditions section (4 checkboxes):**
- Target reachable (`/healthz` → `{"status":"ok"}`).
- `tools/list` returns SIX tool names (Phase 5 adds NO new tool — `query_table` is
  extended in place).
- The `query_table` tool description contains BOTH `"*_fragments"` AND `"parent's URL
  column"` (D5-09 verification — full grep recipe provided).
- Upstream `data.zeeker.sg` reachable + captured probe URL still resolves.

**Scenarios section (8 D5-20 scenarios per plan spec line 163-172):**

| # | Title | D-IDs | FRAG-IDs | INJ-05 gate |
|---|-------|-------|----------|-------------|
| 1 | Judgments fragment-join via `source_url` (URL=`2026_SGFC_46`) | D5-01 / D5-02 | FRAG-01 / FRAG-02 / FRAG-03 | yes (no internal IDs in any row) |
| 2 | Sglawwatch fragment-join via `item_url` + cursor continuation | D5-01 / D5-05 | FRAG-01 / FRAG-02 | yes |
| 3 | PDPC fragment-join via `decision_url` + cross-pair single-helper confirmation | D5-01 | FRAG-01 / FRAG-02 | yes |
| 4 | Multi-match parent (URL=`2001_SGHC_216`) + INJ-05 hash binding | D5-04 / FRAG-06 | FRAG-06 | yes (T-05-24 / T-05-26) |
| 5 | Cold/warm latency disclosure (RESEARCH §4.11 — design property) | — | — | n/a (T-05-27 accept-documented) |
| 6 | 957-fragment full walk via keyset cursor (judgment_id=`66e73dfa5db4`) | D5-05 | FRAG-04 / FRAG-05 | n/a (T-05-25 row-loss probe) |
| 7 | Fall-through path (fragment table query without eq-parent-URL filter) | D5-03 | FRAG-02 | yes (no multi-match log; no parent-table call) |
| 8a | `limit=101` → fixed-literal `invalid_filter_op` | D5-08 | — | yes (T-05-28 — no `{limit}` echo) |
| 8b | Garbage cursor → fixed-literal `invalid_cursor` | D5-07 | — | yes (no garbage value echo) |

Each scenario has: prompt (single sentence) + expected tool call (literal `query_table(...)`
invocation with parameters spelled out) + expected envelope or expected error (exact
shape) + curl example (in the F-4 Dry-Run section).

**Claude Code parity section:** at least 3 of 8 scenarios re-verified (recommended:
Scenario 1, Scenario 4, Scenario 8a — basic + INJ-05 + fixed-literal).

**F-4 Dry-Run Section (5 curl/JSON-RPC dry-run examples):**
- A. Scenario 1 (judgments fragment-join).
- B. Scenario 4 (multi-match INJ-05 — pipes through `grep -E '2001_SGHC_216|6074e86bc12d'`).
- C. Scenario 6 (957-fragment walk first page).
- D. Scenario 8a (limit cap fixed literal — pipes through `grep -F "101"`).
- E. Scenario 8b (keyset cursor malformed fixed literal — pipes through
  `grep -F "not-base64"`).

**Acceptance checklist (12 items):** Pre-conditions, 8 scenarios, no INJ-05 leakage
observed, 3 of 8 re-verified via Claude Code, 3 of 5 curl dry-runs executed, findings
captured under `.planning/sessions/<date>/F-4-PHASE5.md`.

**Known accepted gaps section (2 items):**
- (1) Lone-surrogate `\udc80` UnicodeEncodeError repr leak — carry-forward from Phase 4
  / 04-03-SUMMARY and Plan 05-03 / D-Plan-05-03-02. Scenario 4 does NOT include a
  lone-surrogate canary; the human-loop check uses pure-ASCII URLs.
- (2) Empty-parent fall-through fetches whole fragments table — newly discovered in
  Plan 05-03 / D-Plan-05-03-04. When Call 1 returns 0 matching parent rows, the handler
  falls through to Phase 3 path with EMPTY filters — fetching the entire fragments
  table (50 random rows). Recommended Phase 5.5 hotfix or Phase 6 hardening. Reviewer
  guidance: if Scenario 1 returns "wrong fragments for a known-missing URL," treat as
  this documented gap, NOT a new bug.

**Troubleshooting section (4 specific failure modes mapped to grep recipes):**
- Empty data for known-good URL → check empty-parent fall-through gap.
- Multi-match warning leaks URL substring → INJ-05 regression (grep
  `src/mcp_zeeker/core/fragment_join.py` for raw `url=` bindings).
- 957-walk loses rows or sees duplicates → keyset cursor regression at D5-05 / D5-07.
- Error message contains user-supplied value → locked-literal regression at D5-07 /
  D5-08.

**F-4 Sign-off block at the bottom (10 checkboxes — 1 pre-conditions + 8 scenarios + 1
findings line; 3 fill-in fields: Dry-run target / Date / Signed-off by):**
- All 10 checkboxes remain unchecked.
- Dry-run target field unfilled.
- Date field unfilled.
- Signed-off by field populated with `[AUTO MODE — UNSIGNED — recorded for retro audit]`
  per plan spec line 260 (and Task 2 commit message).

### Task 2 — AUTO-mode auto-approval of human-verify checkpoint — committed `3c5083f`

Task 2 is a `checkpoint:human-verify` task that normally pauses for a human verifier.
AUTO chain mode is active; per the orchestrator-side workflow's auto-mode checkpoint
handling, the checkpoint is auto-approved with the structured "Auto-approved" log line
and execution continues.

Per the plan spec (05-04-PLAN.md line 260): the F-4 sign-off is recorded as
`Signed-off by: [AUTO MODE — UNSIGNED — recorded for retro audit]` and the developer is
expected to manually run the curls offline before the next milestone close. This commit
records the 5-line edit to the Signed-off-by field that makes the AUTO-mode decision
auditable in git history.

The 10 walk-through checkboxes remain UNCHECKED so a real human reviewer still has to
tick them when they perform the actual dry-run. The Dry-run target and Date fields
remain blank.

## D-IDs surfaced (mapping scenarios to phase decisions)

- **D5-01** (single auditable join orchestrator) — Scenarios 1, 2, 3 happy paths use
  the SAME `query_table` tool with the SAME filter shape across all 3 pairs.
- **D5-02** (single-eq-on-parent-URL trigger) — Scenarios 1, 2, 3, 4 all use the
  `op: "exact"` filter shape on the parent URL column.
- **D5-03** (fall-through philosophy) — Scenario 7 confirms fragment table query
  without parent-URL filter goes through standard `query_table` (no fragment_join
  invocation; no parent-table call; no multi-match log).
- **D5-04** (two-request shape + multi-match warning) — Scenario 4 captures the
  `event="fragment_parent_multi_match"` warning log and verifies the
  `parent_url_hash=<16-hex>` binding shape.
- **D5-05** (keyset cursor) — Scenario 6 walks 10 pages × ≤100 rows; Scenario 2 walks
  page-1 → page-2 cursor continuation.
- **D5-07** (decode_keyset_cursor fixed-literal `"invalid_cursor: keyset cursor is
  malformed"`) — Scenario 8b's expected error shape.
- **D5-08** (limit re-clamp to 100 with fixed-literal `"invalid_filter_op: limit
  exceeds fragment-join cap of 100"`) — Scenario 8a's expected error shape.
- **D5-09** (tool description one-line fragment-table note) — pre-conditions checkbox
  greps the description for `"*_fragments"` AND `"parent's URL column"`.

## F-4 dry-run target

`<UNSET — to be filled in by the real human verifier in the F-4 Sign-off block>`

## F-4 findings location

`.planning/sessions/<YYYY-MM-DD>/F-4-PHASE5.md` (to be created by the real human
verifier at walk time, NOT by AUTO mode).

## Outstanding items

None at the orchestrator-side close. The two known gaps documented in the checklist
("Known accepted gaps" section) are NOT Phase 5 blockers — both have local compensating
controls (test-side exemption for lone surrogate; production rate-limit mitigation for
empty-parent fall-through) and explicit recommended source fixes for Phase 6 hardening
or a Plan 05-XX follow-up.

The standing obligation for a real human walk-through before any production cut-over
remains in force. AUTO-mode auto-approval here ONLY unblocks the orchestrator-side
Phase 5 close.

## Conftest status

`tests/conftest.py` **UNMODIFIED across Plans 05-02 / 05-03 / 05-04** — Plan 05-01
owned the single consolidated edit; consolidation discipline successful across the full
phase. Verified by `git log --oneline tests/conftest.py` showing the most recent touch
was `eb2aa7a test(05-01): conftest extension + 4 Wave-0 stub test files`.

## Source-code touched

**None.** Plan 05-04 is pure documentation. `git diff --stat src/` between the merge-
base `3c9aaed` and the post-Plan-05-04 worktree HEAD returns empty.

## Phase 5 close — automated test totals

Full Phase 1–5 automated suite (unchanged from Plan 05-03's close — Plan 05-04 ships
no test changes):

```
uv run pytest tests/ -x -q --ignore=tests/manual
# 263 passed, 2 skipped, 0 failed
```

Phase 5 contributes 37 GREEN tests across `core/`, `tools/`, and `tests/tools/` (per
Plan 05-03 SUMMARY breakdown):

- `tests/core/test_fragment_join.py` — 12 tests (normalize_url 8 + compile_filter 4)
- `tests/core/test_parent_pk_cache.py` — 3 tests (positive / negative / TTL-expiry)
- `tests/core/test_cursor_keyset.py` — 3 tests (round-trip / malformed / shape-mismatch)
- `tests/core/test_fragment_join_value_safety.py` — 6 tests (5 canaries + 1 multi-match)
- `tests/tools/test_retrieval_fragment_join.py` — 9 tests (3 happy + 1 FRAG-02 + 1
  957-walk + 1 1500-walk + 2 truncated honesty + 1 cache-hit)
- `tests/tools/test_retrieval_fragment_join_errors.py` — 3 tests (cursor malformed +
  limit cap + fall-through)
- `tests/tools/test_retrieval_fragment_join_side_channel.py` — 1 test (counter-patch on
  compile_filter)

All 6 FRAG-XX requirements satisfied with automated coverage:
- **FRAG-01** — 3-pair happy path (Plan 05-02).
- **FRAG-02** — no internal ids (Plan 05-02 + Plan 05-03).
- **FRAG-03** — Datasette _next tiebreak (Plan 05-01 + Plan 05-03).
- **FRAG-04** — 1500-fragment synthetic regression (Plan 05-03).
- **FRAG-05** — 957-fragment walk (Plan 05-02 + Scenario 6 live integration cover
  shipped here).
- **FRAG-06** — multi-match warning (Plan 05-02 source + Plan 05-03 hash assertion +
  Scenario 4 human-loop INJ-05 cover shipped here).

INJ-05 protected end-to-end across:
- Automated: 5-canary hostile-URL corpus (Plan 05-03).
- Manual: Scenarios 4 (multi-match log binding) + 8a/b (fixed-literal error messages).

## Deviations from Plan

### Auto-fixed Issues

None. Plan 05-04 is documentation-only — no source code, no tests touched. The
single judgment call was D-Plan-05-04-01 (10 sign-off checkboxes per the analog
template vs. 9 per the literal plan count); resolved by following the analog over the
literal count since both spec lines reference the analog's "Sign-off block" without
specifying a count match.

### Auth gates

None.

## Self-Check: PASSED

- [x] 2 tasks executed (Task 1: write checklist; Task 2: AUTO-mode auto-approval).
- [x] Each task committed individually:
  - Task 1: `9e6f824 docs(05-04): Phase 5 manual UAT checklist — 8 scenarios + F-4 sign-off (UNSIGNED)`
  - Task 2: `3c5083f docs(05-04): record AUTO-mode auto-approval of F-4 Task 2 checkpoint (UNSIGNED placeholder)`
- [x] SUMMARY.md created at `.planning/phases/05-transparent-fragment-parent-joins/05-04-SUMMARY.md`.
- [x] `tests/manual/PHASE5-CLIENT-VERIFY.md` created with all 8 scenarios + F-4 sign-off
  block (UNSIGNED) + Known accepted gaps section (lone-surrogate canary + empty-parent
  fall-through).
- [x] All Task 1 automated verify greps pass: `grep -c "### Scenario "` = 8;
  `grep -c "F-4 "` = 10 (>= 3); `grep -c "parent_url_hash"` = 3 (>= 1);
  `grep -c "invalid_cursor: keyset cursor is malformed"` = 3 (>= 1);
  `grep -c "invalid_filter_op: limit exceeds fragment-join cap of 100"` = 3 (>= 1);
  `grep -c "957"` = 11 (>= 1); `grep -cE "cold-cache|warm-cache|cold/warm"` = 6 (>= 1);
  `grep -q "F-4 OBLIGATION"` OK; `grep -q "F-4 Sign-off"` OK; probe URLs
  (2026_SGFC_46 / 2001_SGHC_216) = 14 mentions.
- [x] Task 2 automated verify passes: `UNSIGNED` and `AUTO MODE` markers present in
  PHASE5-CLIENT-VERIFY.md.
- [x] No modifications to STATE.md, ROADMAP.md, or any `src/` / `tests/*.py` file.
- [x] `tests/conftest.py` UNMODIFIED across Plans 05-02 / 05-03 / 05-04
  (consolidation discipline preserved).
