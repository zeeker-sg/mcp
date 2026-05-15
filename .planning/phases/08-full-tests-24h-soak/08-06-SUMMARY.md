---
phase: "08"
plan: "06"
plan_id: "08-06"
subsystem: docs
tags: [nfr, readme, traceability, operator-docs]
dependency_graph:
  requires: ["08-01", "08-02", "08-03", "08-04", "08-05"]
  provides: ["NFR-05 closure", "Phase 8 traceability sweep"]
  affects: ["README.md", ".planning/REQUIREMENTS.md"]
tech_stack:
  added: []
  patterns: ["Operator-actionable README section", "Traceability table update"]
key_files:
  created: []
  modified:
    - README.md
    - .planning/REQUIREMENTS.md
decisions:
  - "Replaced Phase 1 forward-looking placeholder with operator-actionable 3-bullet Anthropic IP allowlist section referencing host Caddy layer, Anthropic operator contact, and quarterly re-verify cadence"
  - "Preserved existing single-worker constraint section at README.md lines 71-90 unchanged (NFR-05 second clause)"
  - "Flipped 11 Phase-8 traceability rows from Pending to Satisfied with per-row plan ID references and clarifying suffixes for TEST-04 and TEST-05"
metrics:
  duration: "~5 minutes"
  completed: "2026-05-15"
  tasks_completed: 2
  tasks_total: 2
  files_changed: 2
---

# Phase 8 Plan 06: NFR-05 README delta + traceability sweep Summary

**One-liner:** Operator-actionable Anthropic IP allowlist section replaces Phase 1 placeholder; 11 Phase-8 requirement rows flipped from Pending to Satisfied in traceability table.

## What Was Built

Two documentation-only file modifications — no source code, no test code, no new dependencies.

### Task 1: README.md section replacement (commit 35d5d01)

**Before (Phase 1 forward-looking placeholder, README.md lines 105-110):**
```
### Anthropic IP allowlist (forward-looking)

The deployed instance must accept connections from Anthropic's published egress IP ranges to
be reachable via Claude Desktop and Claude Code. Phase 1 ships without an explicit IP
allowlist; Phase 9 (registry submission) will add the operational note and any Caddy-level
`trusted_proxies` configuration needed.
```

**After (operator-actionable, ~12 lines):**
```
### Anthropic IP allowlist

The deployed instance must accept inbound connections from Anthropic's MCP egress IP ranges
to be reachable via Claude Desktop and Claude Code. Anthropic does not (as of 2026-05) publish
a stable, machine-readable list of MCP-egress IPs; operators should:

1. Consult Anthropic's operator-facing documentation or registry-onboarding contact for the
   current allowlist.
2. Apply the allowlist at the host Caddy layer (or upstream firewall), NOT in the MCP
   container — Caddy already owns ingress per `Caddyfile.prod`.
3. Re-verify the allowlist at Phase 9 (registry submission) and quarterly thereafter; the
   IPs change without notice.

Operators who allowlist by domain rather than IP can use Anthropic's published egress hostnames
where available; this trades a lookup hop for resilience to IP churn.
```

**NFR-05 verification commands (both pass):**
- `grep -q 'Anthropic IP' README.md` — exits 0
- `grep -q 'workers 1' README.md` — exits 0 (existing section preserved)

### Task 2: REQUIREMENTS.md traceability sweep (commit 39f1a29)

11 Phase-8 traceability rows updated:

| Requirement | Before Status | After Status |
|-------------|---------------|--------------|
| TEST-01 | Pending | Satisfied (08-02) |
| TEST-02 | Pending | Satisfied (08-04) |
| TEST-03 | Pending | Satisfied (08-03) |
| TEST-04 | Pending | Satisfied (08-03 docstring marker; test originated in Phase 5) |
| TEST-05 | Pending | Satisfied (08-05 smoke gate; full 24h via workflow_dispatch) |
| TEST-06 | Pending | Satisfied (08-03) |
| NFR-01 | Pending | Satisfied (08-05 report-gate) |
| NFR-02 | Pending | Satisfied (08-05 driver --concurrency 50) |
| NFR-03 | Pending | Satisfied (08-05 report-gate) |
| NFR-04 | Pending | Satisfied (08-01 dep-footprint test) |
| NFR-05 | Pending | Satisfied (08-06 README delta) |

**OBS rows unchanged (Phase 7 territory):**
- OBS-04: still `Satisfied (07-07 gap closure — CR-01)`
- OBS-02: still `Deferred to v2 (D7-05)`

**SUB rows unchanged (Phase 9 territory):**
- All 7 SUB-* rows still `Pending`

**Coverage / Per-phase counts unchanged:**
- `- v1 requirements: 75 total` — preserved
- `- Phase 8 (Full tests + 24h soak): 11` — preserved

## Deviations from Plan

None. Plan executed exactly as written.

Note: Pre-existing ruff linting errors in test files were observed during verification but are entirely out of scope — this plan modifies only non-Python documentation files (README.md and REQUIREMENTS.md). Zero Python source/test/scripts files were touched.

## Confirmation Checklist

- [x] No source / test / scripts files modified: `git diff --name-only -- src tests scripts` is empty
- [x] NFR-04 dependency-footprint test still passes: `uv run pytest tests/test_dependency_footprint.py -x` — 3 passed
- [x] Full unit suite GREEN: 439 passed, 14 skipped, 5 warnings
- [x] OBS rows preserved as Phase 7 territory
- [x] SUB rows preserved as Phase 9 territory
- [x] Coverage / Per-phase counts lines unchanged

## Known Stubs

None. This is a documentation-only plan — no data rendering, no UI components.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced. README.md and REQUIREMENTS.md are documentation files only.

## Self-Check

- [x] `README.md` modified: FOUND
- [x] `.planning/REQUIREMENTS.md` modified: FOUND
- [x] Commit 35d5d01 exists: FOUND
- [x] Commit 39f1a29 exists: FOUND

## Self-Check: PASSED

## Forward Pointer

Phase 8 plan set complete (6 plans across 5 waves). Ready for `/gsd-verify-work 8`. Phase 9 (registry submission) is the next phase per ROADMAP.md.
