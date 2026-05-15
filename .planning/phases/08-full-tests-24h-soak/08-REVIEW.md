---
phase: 08-full-tests-24h-soak
reviewed: 2026-05-15T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - src/mcp_zeeker/app.py
  - scripts/soak/__init__.py
  - scripts/soak/report.py
  - scripts/soak/rss_sampler.py
  - scripts/soak/run_soak.py
  - scripts/soak/workload.py
  - tests/_corpus/hostile_inputs.py
  - tests/_corpus/soak_workload.py
  - tests/test_app_lifespan_contract.py
  - tests/test_dependency_footprint.py
  - tests/test_envelope_snapshot.py
  - tests/test_error_catalog.py
  - tests/test_filter_compiler.py
  - tests/test_hidden_data_enforcement.py
  - tests/test_live_golden_path.py
  - tests/tools/test_retrieval_fragment_join.py
  - .github/workflows/live-tests.yml
  - .github/workflows/soak.yml
findings:
  critical: 2
  warning: 4
  info: 2
  total: 8
status: issues_found
---

# Phase 8: Code Review Report

**Reviewed:** 2026-05-15T00:00:00Z
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

Phase 8 delivers the full test suite (TEST-01..06) and 24h soak harness (NFR-01..05) plus
the CR-02 carryover fix (`getattr` defensive read in `app.py:59`). The CR-02 fix itself is
correct and surgical — no collateral behavior change. NFR-04 is clean: all imports in
`scripts/soak/` and `tests/_corpus/` are stdlib-only (argparse, asyncio, csv, json, os, pathlib,
random, re, resource, sys, time, typing) plus the already-pinned `httpx`. No new runtime or dev
dependencies were introduced.

Two blockers are present. The more urgent is a structural assertion bug in
`test_live_golden_path.py` that causes `test_live_describe_table` to always fail when run live —
the assertions treat `envelope.data` (a `list`) as a `dict`. The second blocker is in
`scripts/soak/report.py`: the main entry point crashes with an unhandled `FileNotFoundError`
when `latency.csv` is absent (which occurs whenever the server crashes before any requests
are served), rather than exiting with code 1.

Four warnings cover: an unbounded `latency_log` list in the soak driver that can OOM the
driver process at high 429-cascade rates; GitHub Actions actions pinned to mutable tags
instead of SHAs; asymmetric singleton teardown in `test_hidden_data_enforcement.py`;
and a stale `sleep 5` in `soak.yml` that the inline comment admits is the wrong pattern.

---

## Critical Issues

### CR-01: `test_live_describe_table` assertions treat list as dict — always fails live

**File:** `tests/test_live_golden_path.py:119-120`
**Issue:** `describe_table` returns `Envelope.for_rows(rows=[schema.model_dump()])`, which means
`envelope.data` is `list[dict]` (a one-element list). Lines 119-120 then assert:

```python
assert "columns" in envelope.data          # line 119 — checks if "columns" is an ELEMENT of the list; always False
assert len(envelope.data["columns"]) >= 1  # line 120 — subscripts a list with a string key; TypeError
```

`"columns" in envelope.data` performs a membership test on a list whose elements are dicts, not
strings — this is always `False`, so the assertion always fails whenever
`ZEEKER_LIVE=1 pytest -m live` is run. Line 120 would then raise `TypeError` (list subscripted
with a string). The bug is silent in normal CI because `@pytest.mark.live` tests are skipped
without `ZEEKER_LIVE=1`.

**Confirmed via:** `describe_table` at `src/mcp_zeeker/tools/discovery.py:279` returns
`Envelope.for_rows(..., rows=[schema.model_dump()])`. `Envelope.data` is typed `list[dict]`.
The correct check is `envelope.data[0]`.

**Fix:**
```python
# test_live_golden_path.py:117-120 — replace:
envelope = await describe_table("pdpc", "enforcement_decisions")

assert envelope.provenance.source == "data.zeeker.sg"
assert "columns" in envelope.data[0]          # envelope.data is list[dict]; access element 0
assert len(envelope.data[0]["columns"]) >= 1  # correct subscript
```

---

### CR-02: `scripts/soak/report.py` crashes with unhandled `FileNotFoundError` when `latency.csv` is absent

**File:** `scripts/soak/report.py:236`
**Issue:** `main()` opens `latency.csv` unconditionally via `_load_latency(latency_path)`.
If the uvicorn server crashes during startup (before any requests are served), `run_soak.py`
may exit before writing `latency.csv`. Calling `report.py` in that state raises an unhandled
`FileNotFoundError` rather than a clean exit-code-1 with a human-readable error. By contrast,
`rss.csv` IS checked for existence at line 237 (`if rss_path.exists() else []`). The
inconsistency is a bug: the CI gate step (`Report + gate` in `soak.yml`) would display a Python
traceback instead of a meaningful failure message, obscuring the root cause.

**Fix:**
```python
# report.py main() — add existence check before _load_latency:
if not latency_path.exists():
    print(f"ERROR: latency.csv not found at {latency_path}; did the soak driver run?", file=sys.stderr)
    return 1

durations_ms, errors = _load_latency(latency_path)
```

---

## Warnings

### WR-01: Unbounded `latency_log` list in soak driver may OOM the driver process at high 429 rates

**File:** `scripts/soak/run_soak.py:157, 120`
**Issue:** `latency_log` is an in-memory list that accumulates one tuple per request for the
entire soak duration. With `concurrency=50` and a pool timeout of 5 s, each `asyncio.gather`
batch of 50 tasks can complete in milliseconds when most responses are HTTP 429
(near-instant from the rate limiter). At even conservative rates (10,000 req/min at peak
429-cascade), the list grows to ~864M entries over 24h. Python tuple + list object overhead
is ~200 bytes/entry — potentially several GB. The soak driver is a separate process from the
server (NFR-03 measures server RSS via `--server-pid-file`), but driver OOM would abort the
entire soak run.

This does not affect NFR-03 accuracy when the server PID is specified; it only risks
terminating the soak early.

**Fix:** Stream latency rows directly to `latency.csv` using a `csv.writer` opened at the
start of the soak, rather than buffering all rows in memory. The `rss_log` list faces the
same risk but is sampled at 60 s intervals so it grows to at most 1440 entries — acceptable.

```python
# Instead of latency_log: list = []:
with (out_dir / "latency.csv").open("w", newline="") as lat_f:
    lat_writer = csv.writer(lat_f)
    lat_writer.writerow(["wall_ts", "status", "duration_seconds", "error_class"])
    # pass lat_writer to _one_request and write rows directly
```

---

### WR-02: GitHub Actions steps pinned to mutable version tags, not commit SHAs

**Files:** `.github/workflows/live-tests.yml:18,20`, `.github/workflows/soak.yml:26,28,58`
**Issue:** All three workflows use mutable tag references for third-party actions:

```yaml
- uses: actions/checkout@v4           # mutable
- uses: astral-sh/setup-uv@v3         # mutable
- uses: actions/upload-artifact@v4    # mutable (soak.yml only)
```

Mutable tags can be moved to a different commit (intentionally or via supply-chain
compromise). The project's security posture — "small, audited dependency footprint" — applies
to Python deps but not to CI actions. For a read-only server this is lower risk than for a
write-enabled service, but the registry submission target (`claude-for-legal`) may require
SHA-pinned actions.

**Fix:** Replace with pinned commit SHAs (verify on github.com/actions/checkout/releases etc.):
```yaml
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
- uses: astral-sh/setup-uv@f0ec1fc3b38f5e7cd731bb6ce540c53b2de5cd8  # v3.0.0 (example)
- uses: actions/upload-artifact@6f51ac03b9356f520e9adb1b1b7802705f340c2  # v4.4.3
```

---

### WR-03: Asymmetric singleton teardown in `test_hidden_data_enforcement.py` — `datasette_client` fixture omits `clear_singleton()`

**File:** `tests/test_hidden_data_enforcement.py:116-122`
**Issue:** The `metadata_cache` fixture (lines 125-136) calls
`MetadataCache.clear_singleton()` on teardown, but the `datasette_client` fixture (lines
116-122) only calls `DatasetteClient.reset(token)` — it does NOT call
`DatasetteClient.clear_singleton()`. Per `datasette_client.py:125`, `bind()` sets BOTH the
contextvar AND the class-level `_singleton`. After teardown, the contextvar is restored via
`reset()`, but `_singleton` still holds a reference to the now-closed `httpx.AsyncClient`.
If any subsequent test (in a different file) invokes `DatasetteClient.current()` without a
bound contextvar, it will receive the stale/closed client and produce confusing errors.

The risk is low because all other tests in the suite bind their own contextvar, but the
missing `clear_singleton()` call is a latent trap for future tests that rely on the fallback
singleton path.

**Fix:**
```python
@pytest.fixture
async def datasette_client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        yield dc
        DatasetteClient.reset(token)
        DatasetteClient.clear_singleton()  # add this — mirrors metadata_cache fixture
```

---

### WR-04: `soak.yml` uses `sleep 5` instead of a readiness probe — acknowledged but not resolved

**File:** `.github/workflows/soak.yml:38`
**Issue:** The inline comment reads `# poll-until-ready preferred — see live_server pattern in conftest.py`
but the implementation uses `sleep 5`. On a loaded GitHub runner, 5 seconds may be
insufficient for uvicorn to bind and process the first request; on a fast runner it wastes
time. If uvicorn fails to start (import error, port conflict), the soak driver will connect
immediately with `ConnectionRefused` errors, log them as `error_class="ConnectionRefusedError"`,
and the run will produce a 24h latency CSV full of errors — which `report.py` will pass
(p50/p95/RSS are zero for error-only runs since `durations_ms` only includes timed rows).

The comment correctly identifies the problem but doesn't fix it.

**Fix:** Replace `sleep 5` with a readiness loop:
```bash
# Poll /healthz until ready (max 30s)
for i in $(seq 1 30); do
  curl -sf http://127.0.0.1:8000/healthz && break || sleep 1
done
curl -sf http://127.0.0.1:8000/healthz || { echo "Server failed to start"; exit 1; }
```

---

## Info

### IN-01: `_surfaces_contain` double-reports plain ASCII canaries via the `_repr` suffix

**File:** `tests/_corpus/hostile_inputs.py:52-54`
**Issue:** For printable ASCII canaries like `"ZEEKER_CANARY_42"`, `repr(canary).strip("'\"")` 
evaluates to `"ZEEKER_CANARY_42"` — identical to the canary itself. When such a canary appears
in a surface, the function appends both `"stdout"` AND `"stdout_repr"` to `leaks`, producing a
double-count. The intent of the repr check is to catch backslash-escape leakage of
unprintable canaries (e.g. `"\udc80"` appearing as `"\\udc80"` in output), where
`repr(canary).strip("'\"")` differs from `canary`. The double-reporting is a false positive
for plain-text canaries — no actual information is lost (leaks are still correctly detected)
but the surface list is misleading for diagnostic output.

**Fix:** Guard the repr check so it only fires when the stripped repr differs from the canary:
```python
stripped_repr = repr(canary).strip("'\"")
if stripped_repr != canary and stripped_repr in surface_text and repr(canary) != repr(""):
    leaks.append(f"{surface_name}_repr")
```

---

### IN-02: `test_dependency_footprint.py` does not assert pinning discipline for dev deps

**File:** `tests/test_dependency_footprint.py:82-95`
**Issue:** `test_pinning_discipline_runtime()` (lines 82-95) asserts that every entry in
`[project.dependencies]` carries a version operator. There is no equivalent
`test_pinning_discipline_dev()` for `[dependency-groups.dev]`. An unpinned dev dep (e.g.
`pytest` without a version) would pass the footprint test but silently allow any version,
undermining NFR-04's lock discipline for the dev tool set.

**Fix:** Add a parallel test:
```python
def test_pinning_discipline_dev() -> None:
    """NFR-04: every [dependency-groups.dev] entry must carry a version operator."""
    pyproject = tomllib.loads((_project_root() / "pyproject.toml").read_text())
    REQUIRED_OPERATORS = ("~=", ">=", "==")
    for entry in pyproject["dependency-groups"]["dev"]:
        has_pin = any(op in entry for op in REQUIRED_OPERATORS)
        assert has_pin, (
            f"dev dep missing version operator (expected one of {REQUIRED_OPERATORS}): "
            f"{entry!r}"
        )
```

---

## NFR-04 Import Verification

All `scripts/soak/` and `tests/_corpus/` imports verified against the locked set:

| File | Third-party imports | Verdict |
|------|---------------------|---------|
| `scripts/soak/__init__.py` | none | PASS |
| `scripts/soak/report.py` | stdlib only (argparse, collections, csv, sys, datetime, pathlib) | PASS |
| `scripts/soak/rss_sampler.py` | stdlib only (os, re, pathlib, resource) | PASS |
| `scripts/soak/run_soak.py` | `httpx` (already pinned ~=0.28) | PASS |
| `scripts/soak/workload.py` | stdlib only (sys, pathlib) | PASS |
| `tests/_corpus/hostile_inputs.py` | none | PASS |
| `tests/_corpus/soak_workload.py` | stdlib only (typing) | PASS |

No new runtime or dev dependencies introduced. NFR-04 invariant holds.

## CR-02 Fix Verification

`src/mcp_zeeker/app.py:59` change is confirmed surgical:

```python
# Before (broken): tool.return_type
# After (correct):
if getattr(tool, "return_type", None) is not Envelope:
```

The change is a single attribute access substitution. No other behavior in `app.py` was
modified. The regression test in `test_app_lifespan_contract.py` correctly exercises the
`SimpleNamespace` stand-in path and will fail on the pre-fix code, pass on the fixed code.

---

_Reviewed: 2026-05-15T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
