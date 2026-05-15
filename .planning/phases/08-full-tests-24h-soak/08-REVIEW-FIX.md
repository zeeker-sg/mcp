---
phase: 08-full-tests-24h-soak
fixed_at: 2026-05-15T00:00:00Z
review_path: .planning/phases/08-full-tests-24h-soak/08-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 8: Code Review Fix Report

**Fixed at:** 2026-05-15T00:00:00Z
**Source review:** .planning/phases/08-full-tests-24h-soak/08-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (2 critical, 4 warning; 2 info findings skipped per scope)
- Fixed: 6
- Skipped: 0

## Fixed Issues

### CR-01: fix list-as-dict assertion in test_live_describe_table

**Files modified:** `tests/test_live_golden_path.py`
**Commit:** 6654c71
**Applied fix:** Changed lines 119-120 from `"columns" in envelope.data` and
`envelope.data["columns"]` to `envelope.data[0]` — `envelope.data` is `list[dict]`
(Envelope.for_rows wraps the schema in a 1-element list), so the membership test
and subscript must operate on element 0 of the list, not the list itself.

### CR-02: guard latency.csv existence before loading in report.py

**Files modified:** `scripts/soak/report.py`
**Commit:** 6997d1b
**Applied fix:** Added an `if not latency_path.exists()` check before calling
`_load_latency(latency_path)` in `main()`. If `latency.csv` is absent, prints a
human-readable error to stderr and returns exit code 1, mirroring the existing
`rss.csv` guard pattern at the next line.

### WR-01: stream latency rows to CSV to prevent OOM in soak driver

**Files modified:** `scripts/soak/run_soak.py`
**Commit:** 61dcc7e
**Applied fix:** Replaced the unbounded in-memory `latency_log` list with a
`csv.writer` opened at the start of `run_soak()`. The `latency.csv` file is
opened before the HTTP client context and `_one_request` now accepts `lat_writer`
instead of `latency_log`, writing each row directly to disk via
`lat_writer.writerow(...)`. Memory usage is now O(1) regardless of soak duration
or 429 cascade rate. The RSS log remains in-memory (bounded ~1440 entries over
24h at 60s intervals).

### WR-02: pin GitHub Actions steps to verified commit SHAs

**Files modified:** `.github/workflows/live-tests.yml`, `.github/workflows/soak.yml`
**Commit:** e856534
**Applied fix:** Replaced all mutable tag references with verified commit SHAs
(confirmed via `gh api repos/{owner}/{repo}/git/refs/tags/{tag}`):
- `actions/checkout@v4` → `@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2`
- `astral-sh/setup-uv@v3` → `@caf0cab7a618c569241d31dcd442f54681755d39  # v3` (peeled from annotated tag)
- `actions/upload-artifact@v4` → `@b4b15b8c7c6ac21ea08fcf65892d2ee8f75cf882  # v4.4.3`

### WR-03: add clear_singleton() teardown to datasette_client fixture

**Files modified:** `tests/test_hidden_data_enforcement.py`
**Commit:** 0022aab
**Applied fix:** Added `DatasetteClient.clear_singleton()` after `DatasetteClient.reset(token)`
in the `datasette_client` fixture teardown. `DatasetteClient.clear_singleton()` already
existed in the class (datasette_client.py:136). This mirrors the `metadata_cache` fixture's
symmetric teardown and prevents future tests from receiving a stale/closed httpx.AsyncClient
via the singleton fallback path.

### WR-04: replace sleep 5 with /healthz readiness probe loop in soak.yml

**Files modified:** `.github/workflows/soak.yml`
**Commit:** 08fec44
**Applied fix:** Replaced `sleep 5` with a `curl -fsS` loop polling
`http://127.0.0.1:8000/healthz` for up to 60 seconds, followed by a final
check that exits 1 with a diagnostic message if the server never responds.
Added `set -euo pipefail` for strict error handling in the step.

## Skipped Issues

None — all 6 in-scope findings were fixed.

## Post-Fix Test Results

Full pytest suite run after all fixes: **439 passed, 14 skipped, 0 failed** (6.60s).
No regressions introduced. The 14 skips are pre-existing (live tests require
`ZEEKER_LIVE=1`, surrogate canary carry-forward, Phase 2 placeholder skips).

---

_Fixed: 2026-05-15T00:00:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
