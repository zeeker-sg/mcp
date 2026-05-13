---
status: partial
phase: 03-structured-retrieval-url-keyed-fetch
source: [03-VERIFICATION.md, tests/manual/PHASE3-CLIENT-VERIFY.md]
started: 2026-05-14T00:30:00Z
updated: 2026-05-14T00:30:00Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Claude Desktop — 6-scenario walkthrough
expected: Walk all 6 scenarios in `tests/manual/PHASE3-CLIENT-VERIFY.md` against Claude Desktop. Each ☐ box ticked after observing documented expected behavior. Sign-off line filled in.
result: [pending]

### 2. Claude Code — parity check on scenarios 1, 3, 4
expected: Same behavior observed on Claude Code as on Claude Desktop for filter-by-date (1), opt-in heavy (3), and fetch-known-URL (4) — confirms two-client parity.
result: [pending]

### 3. INJ-05 transcript audit — no canary or value leakage
expected: No user-supplied URL, filter value, or hostile-input canary string appears in any user-facing error message across the walkthrough transcripts.
result: [pending]

### 4. F-4 dry-run — at least 3 of 5 curl/JSON-RPC examples (A–E)
expected: Wire-level responses match the documented expected response per example in `PHASE3-CLIENT-VERIFY.md` § F-4 Dry-Run Section.
result: [pending]

## Summary

total: 4
passed: 0
issues: 0
pending: 4
skipped: 0
blocked: 0

## Gaps
