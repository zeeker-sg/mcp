---
status: partial
phase: 08-full-tests-24h-soak
source: [08-VERIFICATION.md]
started: 2026-05-15T06:58:12Z
updated: 2026-05-17T00:38:21Z
---

## Current Test

[awaiting human testing]

## Tests

### 1. Run the full 24h soak via workflow_dispatch against mcp.zeeker.sg
expected: p50 < 300ms, p95 < 1.5s, max RSS < 256 MB, zero PoolTimeout cascade entries in latency.csv, daily rollover observed, report.py exits 0
result: [pending]
prerequisites:
- Set `SOAK_BYPASS_TOKEN` as a GitHub Actions repo secret (`openssl rand -hex 32`).
- Set the SAME `SOAK_BYPASS_TOKEN` value in the production container's environment (docker-compose env_file or operator-managed secrets). Restart the container so the env is picked up.
- Confirm preflight: the workflow's first step calls `/healthz` and `/admin/metrics` with the token; will abort if either fails.
ops_note: |
  This is a real 50-concurrent load test against the live deployment. Coordinate
  with ops on the run window. After the soak finishes, unset SOAK_BYPASS_TOKEN
  on the production container and restart so the bypass cannot fire in
  steady-state operation.

  2026-05-16 12:59 UTC — most recent attempted dispatch (GitHub Actions run id
  25962546108) failed at the preflight step with HTTP 404 from
  `/admin/metrics`. Root cause: the `SOAK_BYPASS_TOKEN` value held by the
  GitHub Actions repo secret does not match the value baked into the
  production container's env, so the bypass middleware does not authorise the
  request and the metrics route (conditional on bypass) returns 404. Unblock:
  regenerate the token (or copy the existing GH secret value), set it in the
  prod container's env_file, restart the container, then re-trigger
  `soak.yml` via workflow_dispatch.

### 2. Run live integration tests against data.zeeker.sg
expected: All 6 live tests pass, including test_live_describe_table, test_live_list_tables, test_live_query_table, test_live_fetch, test_live_search, test_live_list_databases
result: passed
command: `ZEEKER_LIVE=1 uv run pytest -m live -v`
prerequisites: (none — CR-01 was fixed in commit 6654c71)
evidence:
  date: 2026-05-17
  command: `UPSTREAM_URL=https://data.zeeker.sg ZEEKER_LIVE=1 uv run pytest -m live -v`
  tests:
  - tests/test_heavy_column_upstream.py::test_mlaw_news_heavy_column_returns_content
  - tests/test_live_golden_path.py::test_live_list_databases
  - tests/test_live_golden_path.py::test_live_list_tables
  - tests/test_live_golden_path.py::test_live_describe_table
  - tests/test_live_golden_path.py::test_live_search
  - tests/test_live_golden_path.py::test_live_query_table
  - tests/test_live_golden_path.py::test_live_fetch
  - tests/test_metadata_cache.py::test_live_metadata_parseable[zeeker-judgements]
  - tests/test_metadata_cache.py::test_live_metadata_parseable[pdpc]
  - tests/test_metadata_cache.py::test_live_metadata_parseable[sg-gov-newsrooms]
  - tests/test_metadata_cache.py::test_live_metadata_parseable[sglawwatch]

## Summary

total: 2
passed: 1
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps

CR-01 and CR-02 (the two blockers identified at original verification) are
now resolved. The soak workflow now points at the production endpoint via
soak-token bypass (commits 46a3732, de581a2, 68ed782 in the soak-against-prod
work). Remaining items are operational, not code defects:

1. Generate `SOAK_BYPASS_TOKEN` (one time): `openssl rand -hex 32`
2. Set it in both places (GitHub Actions secret + prod container env)
3. Restart prod container so env is picked up
4. Trigger `soak.yml` via `workflow_dispatch` on the Actions UI; observe the
   `soak-results` artifact at the end (latency.csv, rss.csv, report.md)
5. After the run, unset the env on prod and restart to close the bypass surface
6. Run `ZEEKER_LIVE=1 uv run pytest -m live -v` locally OR wait for the next
   nightly run of `live-tests.yml`
7. `/gsd-verify-work 8` once both runs have evidence
