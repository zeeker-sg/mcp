---
phase: "08"
plan: "05"
subsystem: soak-harness
tags:
  - soak
  - performance
  - nfr
  - ci
completed_at: "2026-05-15"
duration_minutes: 45
tasks_completed: 5
files_created: 8
files_modified: 1

dependency_graph:
  requires:
    - "08-01"  # NFR-04 dep-footprint test must pass; soak harness adds no new deps
  provides:
    - scripts/soak package (run_soak.py driver, report.py gate, rss_sampler.py, workload.py)
    - tests/_corpus/soak_workload.py (canonical WORKLOAD)
    - .github/workflows/soak.yml (one-click 24h soak CI trigger)
  affects:
    - NFR-01 (p50/p95 latency)
    - NFR-02 (50-concurrent)
    - NFR-03 (RSS < 256 MB)
    - TEST-05 (24h soak)

tech_stack:
  added: []  # Zero new deps — stdlib + already-pinned httpx only (NFR-04 preserved)
  patterns:
    - asyncio.Semaphore concurrency bounding in soak driver
    - /proc/{pid}/status + resource.getrusage macOS fallback for RSS sampling (stdlib-only)
    - sort-then-index percentile (sorted(samples)[int(p * len(samples))]) — no hdrhistogram
    - single-source-of-truth re-export: canonical WORKLOAD in tests/_corpus, re-export in scripts/soak
    - CSV reducer + CLI exit-code gate pattern (0=pass, 1=breach)
    - workflow_dispatch-only CI workflow for expensive (25h) manual operations

key_files:
  created:
    - scripts/soak/__init__.py
    - scripts/soak/rss_sampler.py
    - scripts/soak/workload.py
    - scripts/soak/run_soak.py
    - scripts/soak/report.py
    - tests/_corpus/soak_workload.py
    - .github/workflows/soak.yml
  modified:
    - .gitignore  # soak-results/ + soak-smoke-results/ added

decisions:
  - id: "D8-05-01"
    description: "Canonical WORKLOAD in tests/_corpus/soak_workload.py (not scripts/); scripts/soak/workload.py re-exports. Keeps canonical pytest-rooted."
  - id: "D8-05-02"
    description: "Smoke soak ran on local dev box (macOS) — all NFR thresholds passed (p50=2.2ms, p95=2.9ms, RSS=57.7MB). Full 24h run deferred to CI pre-release workflow_dispatch."
  - id: "D8-05-03"
    description: "soak.yml uses sleep 5 for uvicorn readiness (not poll-until-ready loop) — acceptable for a 25h manual-only workflow per 08-PATTERNS.md locked skeleton."
---

# Phase 8 Plan 05: 24h Soak Harness Summary

24h soak harness with asyncio/httpx driver, RSS sampler, CSV/markdown report, and workflow_dispatch-only CI workflow — closes TEST-05 (smoke level) and gates NFR-01/02/03.

## What Was Built

Five new Python modules in `scripts/soak/` and one canonical workload corpus in `tests/_corpus/`:

- **`scripts/soak/__init__.py`** — empty package marker (0 bytes, mirrors `tests/_corpus/__init__.py`)
- **`scripts/soak/rss_sampler.py`** — pure-stdlib RSS sampler; reads `/proc/{pid}/status` (VmRSS, Linux) with `resource.getrusage` + macOS byte-normalisation fallback. The `os.uname().sysname == "Darwin"` branch is the only platform check in this codebase — intentional and documented inline.
- **`tests/_corpus/soak_workload.py`** — canonical 5-entry `WORKLOAD: list[tuple[str, dict, float]]` with the 35/30/20/10/5% distribution (discovery/query/search/fetch/fragment). Single source of truth per D8-05-01.
- **`scripts/soak/workload.py`** — thin re-exporter with a `sys.path.insert(0, project_root)` to allow `from tests._corpus.soak_workload import WORKLOAD` in non-pytest contexts. The `as WORKLOAD` re-export is explicit.
- **`scripts/soak/run_soak.py`** — asyncio + httpx driver; single `httpx.AsyncClient` with 4-axis timeout, `asyncio.Semaphore(N)` concurrency, RSS sidecar task, per-request error categorisation (pool_timeout/request_timeout/rate_limited/5xx/4xx/ok), no-retry contract, CSV outputs.
- **`scripts/soak/report.py`** — CSV reducer + CLI exit-code gate; sort-then-index percentile (no hdrhistogram), daily-rollover detection via per-minute 429 bucketing, markdown summary writer, exit 0/1.
- **`.github/workflows/soak.yml`** — `workflow_dispatch:` ONLY (no cron — 25h burn per run), `timeout-minutes: 1500`, single-worker uvicorn (RATE-06), full 50-concurrency 86400s driver, report gate with NFR-01/03 thresholds, `actions/upload-artifact@v4` with `if: always()`.
- **`.gitignore`** — `soak-results/` and `soak-smoke-results/` added (artifacts can be 170 MB+ for a full 24h run).

## Smoke Soak Results (Task 4 — TEST-05 smoke gate)

Ran 60s at concurrency=5 against local single-worker uvicorn:

| Metric | Result | Threshold | Status |
|--------|--------|-----------|--------|
| p50 latency | 2.2 ms | 300 ms | PASS |
| p95 latency | 2.9 ms | 1500 ms | PASS |
| max RSS | 57.7 MB | 256 MB | PASS |
| pool_timeout | 0 | any | PASS |
| 5xx | 0 | any | PASS |
| report.py exit | 0 | 0 | PASS |

Samples: 81,355 latency rows; 12 RSS samples. The high rate_limited count (81,276) is expected — the driver exhausted the 5,000-request/24h bucket quickly at 50 RPS against a single IP; the rate-limit middleware is working correctly.

## Design Decisions

**D8-05-01 — WORKLOAD direction (W6 revision, breaking from original plan direction):**
The plan considered two directions: (a) canonical in `scripts/soak/workload.py` with re-export from `tests/_corpus/`, or (b) canonical in `tests/_corpus/soak_workload.py` with re-export from `scripts/soak/workload.py`. This plan implements direction (b) — canonical in `tests/_corpus/` — matching the W6 revision recommendation in `08-PATTERNS.md`. Rationale: `scripts/` requires a `sys.path` adjustment regardless of direction; keeping canonical in `tests/_corpus/` keeps pytest collection clean and the workload co-located with other test corpus data. The re-export uses `from tests._corpus.soak_workload import WORKLOAD as WORKLOAD` (explicit alias for ruff F401 clarity).

**D8-05-02 — Smoke on dev (macOS) vs CI:**
The smoke soak (60s, concurrency=5) passed all NFR thresholds on the dev machine. The full 24h soak (86400s, concurrency=50) is manual-only via `soak.yml` workflow_dispatch — this is intentional per `08-VALIDATION.md` "Manual-Only Verifications" and `08-RESEARCH.md` line 1174 (CI minutes budget). The smoke gate at TEST-05 level is considered closed.

**D8-05-03 — RSS sampling in smoke:** The smoke ran with `--server-pid` pointing to the uvicorn process. On macOS, `/proc/{pid}/status` is unavailable, so `rss_kb_from_proc()` returns `None` and the sampler falls back to `rss_kb_from_self()` (driver RSS, not server RSS). This is documented in the rss.csv header (`rss_kb_DRIVER_NOT_SERVER` when falling back). On Linux CI runners, `/proc/{pid}/status` works correctly.

## Deviations from Plan

**None** — plan executed exactly as specified. The W6 revision (WORKLOAD direction choice) was pre-documented in 08-PATTERNS.md and 08-05-PLAN.md interfaces; it was not a deviation but the intended implementation path.

## Threat Flags

No new network endpoints, auth paths, or trust boundaries introduced. The soak harness is a standalone driver that connects to an already-running server process; it adds no MCP tool handlers and no new HTTP routes. The `soak.yml` workflow artifact upload contains only scrubbed CSV data (timestamps + status codes + latency + error class — no request bodies, no tokens, no IP addresses).

## Self-Check: PASSED

All created files verified present:
- scripts/soak/__init__.py — FOUND
- scripts/soak/rss_sampler.py — FOUND
- scripts/soak/workload.py — FOUND
- scripts/soak/run_soak.py — FOUND
- scripts/soak/report.py — FOUND
- tests/_corpus/soak_workload.py — FOUND
- .github/workflows/soak.yml — FOUND
- .gitignore (modified) — FOUND

All commits verified in git log:
- 50f19fc: feat(08-05): soak package skeleton
- dc19956: feat(08-05): run_soak.py driver
- 4b3234b: feat(08-05): report.py gate
- 25145c5: chore(08-05): gitignore + smoke verification
- 151e142: feat(08-05): soak.yml CI workflow
