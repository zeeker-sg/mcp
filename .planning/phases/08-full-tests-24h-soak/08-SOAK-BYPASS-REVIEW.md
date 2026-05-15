---
phase: 08-soak-bypass-review
status: issues_found
severity_counts:
  critical: 2
  warning: 4
  info: 3
depth: deep
files_reviewed: 10
files_reviewed_list:
  - src/mcp_zeeker/core/soak_auth.py
  - src/mcp_zeeker/core/admin.py
  - src/mcp_zeeker/core/middleware/rate_limit.py
  - src/mcp_zeeker/core/middleware/origin.py
  - src/mcp_zeeker/core/middleware/request_id.py
  - src/mcp_zeeker/app.py
  - scripts/soak/run_soak.py
  - scripts/soak/rss_sampler.py
  - scripts/soak/report.py
  - .github/workflows/soak.yml
  - tests/test_soak_auth.py
  - tests/test_admin_metrics.py
  - tests/test_rate_limit.py
updated: 2026-05-15T00:00:00Z
---

# Phase 8 Soak-Bypass: Code Review Report

**Reviewed:** 2026-05-15
**Depth:** deep (cross-file, import graph, call chains)
**Files Reviewed:** 13 (10 primary + 3 supporting: origin.py, request_id.py, report.py)
**Status:** issues_found

## Summary

This changeset adds a UAT soak-bypass mechanism: a shared secret (`SOAK_BYPASS_TOKEN`) gates
both rate-limiter skipping and an `/admin/metrics` RSS read-out. The core design is sound —
single source of truth (`soak_auth.py`), constant-time comparison, default-safe (token absent
= feature off), token never echoed into logs or response bodies. The middleware ordering is
correct (RequestId → Origin → RateLimit, all wrapping the Starlette router uniformly).

Two blockers require a fix before this changeset ships:

1. **Production ModuleNotFoundError (CR-01):** `admin.py._read_rss_kb` imports from
   `scripts.soak.rss_sampler`, but the Docker image copies only `src/` — `scripts/` is
   absent at runtime. Every authenticated `/admin/metrics` call in production will crash
   with `ModuleNotFoundError`, returning a 500 rather than a 200.

2. **Silent false-pass on NFR-03 when RSS collection is broken (CR-02):** `report.py._load_rss`
   ingests the `-1` sentinel records that `run_soak.py._rss_sampler_loop` writes on remote
   failure. `max(rss_kb)` over an all-sentinel list equals `-1`, which converts to
   `-0.001 MB` — well under the 256 MB ceiling — so the NFR-03 gate passes silently even when
   all RSS measurements failed. A 24h soak run where the production `/admin/metrics` endpoint
   is broken will produce a green CI result with zero valid RSS samples.

The two issues are coupled: CR-01 guarantees that the production endpoint will always 500 on
the first soak run, all RSS samples will be -1 (CR-02), and the CI soak will report a false
green for NFR-03.

---

## Critical Issues

### CR-01: `admin.py` imports from `scripts.soak.rss_sampler` — module absent in production Docker image

**File:** `src/mcp_zeeker/core/admin.py:38`

**Issue:** `_read_rss_kb()` performs a lazy import:

```python
from scripts.soak.rss_sampler import rss_kb_from_proc, rss_kb_from_self
```

The `Dockerfile` (stage 2) copies only `COPY --from=builder /app/src /app/src` — the
`scripts/` directory is not present in the production image. `scripts/` has a
`scripts/soak/__init__.py` but no top-level `scripts/__init__.py`, and Hatch's wheel build
(`packages = ["src/mcp_zeeker"]`) also excludes `scripts/`. At runtime, the first
authenticated `GET /admin/metrics` will raise `ModuleNotFoundError: No module named 'scripts'`
and return HTTP 500, not 200.

The import is deferred ("lazily so unit tests can monkeypatch") but that only helps unit tests
which run with the project root in `sys.path`. Production uvicorn does not have the project
root in `sys.path` — only the installed package `mcp_zeeker` is importable.

Cross-file trace:
- `app.py:104` registers the route → `admin.py:47` (`admin_metrics`) → `admin.py:38` (lazy
  import at call time) → `ModuleNotFoundError` in production.
- `tests/test_admin_metrics.py:65` (`test_returns_200_with_rss_kb_when_authenticated`) passes
  locally because `pytest` adds the project root to `sys.path`, making `scripts.soak.rss_sampler`
  importable. CI will also pass for the same reason. The bug is invisible until a production
  deployment is tested.

**Fix:** Move the RSS reading logic into `src/mcp_zeeker/` or make the import resilient. The
cleanest option is to inline the two small functions (`rss_kb_from_proc` / `rss_kb_from_self`)
directly into `admin.py` (they are 30 LOC of stdlib-only code):

```python
# src/mcp_zeeker/core/admin.py  — replace _read_rss_kb entirely

import os
import re
from pathlib import Path

def _read_rss_kb() -> int:
    """Read resident-set-size in KB. Linux fast-path; macOS fallback."""
    pid = os.getpid()
    # Linux: /proc/{pid}/status VmRSS field
    try:
        text = Path(f"/proc/{pid}/status").read_text()
        m = re.search(r"^VmRSS:\s+(\d+)\s*kB", text, re.MULTILINE)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    # macOS / non-Linux fallback
    import resource
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    if os.uname().sysname == "Darwin":
        return rusage.ru_maxrss // 1024
    return rusage.ru_maxrss
```

Alternatively, relocate `rss_sampler.py` to `src/mcp_zeeker/core/rss_sampler.py` (fixes the
import path) and update both `admin.py` and `scripts/soak/run_soak.py` imports accordingly.

---

### CR-02: `-1` sentinel records from failed remote RSS sampling cause silent false-pass on NFR-03

**File:** `scripts/soak/report.py:157,248` (cross-file: `scripts/soak/run_soak.py:183`)

**Issue:** When the remote `/admin/metrics` call fails (returns None from `rss_kb_from_remote`),
`run_soak.py:183` writes a `-1` sentinel to `rss_log`:

```python
rss_log.append((time.time(), -1))
```

`report.py._load_rss` (line 82) converts every value with `int(float(row[1]))`, so `-1` is
loaded as-is into the `values` list. The NFR-03 gate then evaluates:

```python
max_rss_mb = max(rss_kb) / 1024.0 if rss_kb else 0.0  # report.py:248
if max_rss_mb > args.max_rss_mb:  # report.py:259
    breaches.append(...)
```

When all 1440 samples are `-1`, `max(rss_kb) = -1`, `max_rss_mb = -0.001`, the breach check
fails to fire, and the soak report claims zero RSS pressure — a false green. The test result
will state "max: -0.0 MB" in the markdown summary and not breach the 256 MB threshold.

This is compounded by CR-01: the production endpoint will return 500 for every
`rss_kb_from_remote` call (all None → all -1), so the entire 24h soak run will produce a
false-green NFR-03 result.

**Fix:** Filter or flag sentinel values in `_load_rss`, and add an explicit "insufficient RSS
samples" breach gate:

```python
# report.py — updated _load_rss and gate

def _load_rss(path: Path) -> list[int]:
    """Parse rss.csv, filtering -1 sentinel records."""
    values: list[int] = []
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue
            if len(row) < 2:
                continue
            try:
                v = int(float(row[1]))
            except (ValueError, IndexError):
                continue
            if v >= 0:       # skip -1 sentinels
                values.append(v)
    return values

# In main(), after loading rss_kb:
# Require at least 50% of expected samples to be valid.
expected_samples = args.duration // int(args.rss_sample_interval)
if len(rss_kb) < expected_samples * 0.5:
    breaches.append(
        f"rss_samples={len(rss_kb)} < 50% of expected {expected_samples} "
        f"(remote /admin/metrics may be unreachable)"
    )
```

---

## Warnings

### WR-01: `configured.encode("latin-1")` raises `UnicodeEncodeError` if `SOAK_BYPASS_TOKEN` contains non-latin-1 characters

**File:** `src/mcp_zeeker/core/soak_auth.py:80`

**Issue:** `hmac.compare_digest(configured.encode("latin-1"), presented.encode("latin-1"))` — the
comment at line 78–80 correctly explains why bytes are used. However, if `SOAK_BYPASS_TOKEN` is
set to a value containing a Unicode character above U+00FF (e.g., a token generated by some
secret managers that include emoji or non-ASCII characters), `configured.encode("latin-1")` will
raise `UnicodeEncodeError`. This exception would propagate uncaught through
`is_soak_authenticated` → `RateLimitMiddleware.__call__` → unhandled, producing a 500 on every
request.

The presented header value is also encoded with `latin-1`, but headers arriving from the network
are already bytes and are decoded from latin-1 at line 54 — only high-byte sequences above U+00FF
can come from the env var.

The code comment at line 77–79 does not acknowledge this edge case for the configured token.

**Fix:** Encode the configured token with `utf-8` (or use `errors="replace"`) or add a guard
in `_get_configured_token` to reject tokens that cannot be safely encoded:

```python
def _get_configured_token() -> str | None:
    token = os.environ.get("SOAK_BYPASS_TOKEN", "")
    if not token:
        return None
    try:
        token.encode("latin-1")  # validate encodability before storing
    except UnicodeEncodeError:
        return None  # treat misconfigured token as absent (safe default)
    return token
```

Alternatively, compare as UTF-8 bytes throughout, which handles all Unicode without error.

---

### WR-02: `X-Soak-Bypass` absent from CORS `access-control-allow-headers` — cross-origin browser clients cannot use the bypass

**File:** `src/mcp_zeeker/core/middleware/origin.py:44-46`

**Issue:** The CORS preflight response lists:

```python
"access-control-allow-headers": "content-type, mcp-session-id, mcp-protocol-version"
```

`X-Soak-Bypass` is not included. Under the CORS spec, browsers require `X-Soak-Bypass` to be
listed in `Access-Control-Allow-Headers` before they will send it cross-origin. This means a
browser-based soak driver (or any future browser client) from an allowed origin would have its
preflight rejected, and the `X-Soak-Bypass` header would be stripped.

In practice the soak driver is a server-side Python process (GHA runner) that is not
browser-bound, so CORS does not apply. However, the `OriginAllowlistMiddleware` explicitly
handles preflights for future browser debug clients (docstring line 8). If such a client ever
needs to reach `/admin/metrics`, the bypass will silently fail without a browser error that
mentions the missing header.

**Fix:** Add `x-soak-bypass` to the allowed headers list:

```python
"access-control-allow-headers": (
    "content-type, mcp-session-id, mcp-protocol-version, x-soak-bypass"
),
```

Note: this only matters for browser-origin clients, which are not the soak driver. The risk is
low but the fix is a one-line change.

---

### WR-03: Multiple `X-Soak-Bypass` headers — `_extract_header` returns first match without rejecting ambiguity

**File:** `src/mcp_zeeker/core/soak_auth.py:51-56`

**Issue:** The ASGI spec allows duplicate headers. `_extract_header` returns the first matching
`x-soak-bypass` value and ignores subsequent ones. If a proxy or an adversarial client sends:

```
X-Soak-Bypass: <valid-token>
X-Soak-Bypass: attacker-value
```

The function authenticates on the first (valid) header and ignores the second. This is not a
bypass vulnerability — the first value matches and auth succeeds as intended. However, the
semantics are unexplained: a reader might expect that duplicate security headers cause
rejection (as a defense-in-depth measure). The current behavior is safe but the choice is not
documented.

**Fix:** Add a docstring note, or defensively reject duplicate bypass headers:

```python
def _extract_header(scope: Scope) -> str | None:
    """Pull the X-Soak-Bypass header value. Returns None if absent or duplicated."""
    if scope.get("type") != "http":
        return None
    found: str | None = None
    for name, value in scope.get("headers", ()):
        if name == _HEADER_NAME:
            if found is not None:
                return None  # reject duplicate bypass headers
            try:
                found = value.decode("latin-1")
            except UnicodeDecodeError:
                return None
    return found
```

---

### WR-04: `rss_kb_from_remote` passes an absolute URL to a client that already has `base_url` set — behavior is correct but fragile

**File:** `scripts/soak/rss_sampler.py:81` (cross-file: `scripts/soak/run_soak.py:242-254`)

**Issue:** `run_soak.py` creates:

```python
async with httpx.AsyncClient(
    base_url=args.target_url,   # e.g. "https://mcp.zeeker.sg"
    ...
) as client:
    ...
    metrics_client=client,
    metrics_base_url=args.target_url,
```

`rss_kb_from_remote` then calls:

```python
resp = await client.get(
    f"{base_url.rstrip('/')}/admin/metrics",  # absolute URL
    ...
)
```

httpx resolves the URL correctly: when a full URL is passed to `client.get()`, the client's
`base_url` is ignored for that request and the absolute URL is used directly. The behavior is
currently correct (confirmed against httpx 0.28.1).

The fragility is this: `rss_kb_from_remote` accepts a `client` with an already-bound
`base_url` and then constructs its own absolute URL from a separately-passed `base_url` string.
If `metrics_base_url` and the client's `base_url` ever diverge (e.g., a caller passes a
differently-configured client), the function silently uses its own `base_url` parameter. The
`client` parameter's `base_url` is ignored — the function only needs the client for its
connection pool and timeout settings, not its base URL. This is a latent coupling bug.

**Fix:** Change `rss_kb_from_remote` to accept a relative path and rely on the caller's client
`base_url`, OR document explicitly that the `client.base_url` is intentionally ignored and
only the `base_url` string parameter governs the URL:

```python
async def rss_kb_from_remote(client, base_url: str, token: str) -> int | None:
    """...
    NOTE: `client.base_url` (if set) is intentionally ignored.
    The request URL is constructed entirely from the `base_url` string parameter.
    ...
    """
```

---

## Info

### IN-01: `test_header_with_undecodable_bytes_returns_false` comment is misleading

**File:** `tests/test_soak_auth.py:76-79`

**Issue:** The test comment reads "latin-1 decodes any byte sequence, so this won't
UnicodeDecodeError". This is correct for `value.decode("latin-1")` (header bytes → str), but
the same reasoning does NOT apply to `configured.encode("latin-1")` (env str → bytes), which
CAN raise `UnicodeEncodeError` for non-latin-1 chars (see WR-01). The comment, read in
isolation, may lead a future developer to incorrectly conclude that latin-1 is always safe in
both directions.

**Fix:** Clarify the comment scope:

```python
# latin-1 decodes any *byte sequence* to str without error (every byte 0x00–0xFF maps).
# Note: the reverse (str → latin-1 bytes) can still fail for chars > U+00FF.
```

---

### IN-02: `test_returns_200_with_rss_kb_when_authenticated` asserts `body["rss_kb"] > 0` — fragile on low-memory environments

**File:** `tests/test_admin_metrics.py:73`

**Issue:** The test asserts `body["rss_kb"] > 0`. This is correct in practice because any live
Python process has non-zero RSS. However, the assertion fails to account for a platform where
`rss_kb_from_proc` returns `None` AND `rss_kb_from_self()` somehow returns `0`. More
importantly, the test currently works because `scripts.soak.rss_sampler` is importable during
test runs (project root is on `sys.path`) — this is the same condition that masks CR-01.

**Fix:** Mock `_read_rss_kb` in this test to return a known value (e.g., `1024`), removing the
implicit dependency on the environment and the implicit dependency on `scripts/` being
importable:

```python
async def test_returns_200_with_rss_kb_when_authenticated(client, monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    monkeypatch.setattr("mcp_zeeker.core.admin._read_rss_kb", lambda: 1024)
    async with client as c:
        resp = await c.get("/admin/metrics", headers={"X-Soak-Bypass": "expected-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"rss_kb": 1024}
```

This also removes the `assert body["rss_kb"] > 0` fragility.

---

### IN-03: Timing oracle between token-mismatch 404 and unrouted 404 is documented threat but test coverage is absent

**File:** `src/mcp_zeeker/core/admin.py:56-58`

**Issue:** As noted in the scope's concern #3, a request to `/admin/metrics` with a wrong
token goes through `scope["headers"]` walk + `hmac.compare_digest` (when token is configured),
while a request to an unregistered path `/does-not-exist` short-circuits at Starlette's router
level. The latencies are observably different, leaking whether `/admin/metrics` exists.

The `admin.py` docstring acknowledges the 404-for-obscurity design but does not document this
timing difference. No test verifies that the timing oracle is considered acceptable within the
stated threat model.

This is within the documented threat model (token leakage = only rate-limit bypass + RSS
read-out; no hidden data disclosed) and is accepted-by-design, but the acceptance should be
recorded explicitly in the docstring rather than inferred.

**Fix:** Add a one-sentence note to `admin.py` docstring:

```python
# Timing note: a wrong-token 404 takes slightly longer than a true unrouted 404
# (header walk + hmac.compare_digest). This is an accepted timing side-channel:
# it leaks "this path is handled" but not the token value or any data.
```

---

## 13-Concern Verdict Table

| # | Concern | Verdict |
|---|---------|---------|
| 1 | Constant-time comparison correctness | No leak — `compare_digest(bytes, bytes)` is correct; empty-token guard fires before compare. UnicodeEncodeError on configured token if env contains non-latin-1 chars → see WR-01. |
| 2 | Header extraction: ASGI header normalization, duplicate headers, trailing whitespace | ASGI normalizes to lowercase (Starlette/h11/httptools all lowercase header names). Trailing whitespace: compare_digest is strict — no bypass. Duplicate headers: first-match-wins, not documented → see WR-03. |
| 3 | Information disclosure via `/admin/metrics` 404 | No leak of content. Timing oracle exists (documented in IN-03) — accepted within stated threat model. |
| 4 | Rate-limit bypass placement | Correct. Bypass fires after non-HTTP scope check, before bucket creation and sweep. No bucket pollution, no LRU pressure from soak. `perf_start` skipped for bypass requests (intentional micro-optimization, documented). |
| 5 | CSRF / unauthenticated abuse | No browser CSRF risk — custom header requires preflight rejected by OriginAllowlistMiddleware for non-allowed origins. Server-to-server with leaked token: documented boundary. CORS preflight does not include `X-Soak-Bypass` in allowed headers → see WR-02. |
| 6 | `/admin/metrics` middleware coverage | All middleware applies uniformly. No path around RateLimit to the handler. Confirmed by Starlette middleware-wraps-all-routes semantics. |
| 7 | Driver — token in process environment | No exposure beyond standard CI model. Token not in CLI args, not logged. httpx DEBUG not enabled. |
| 8 | Workflow — token exposure | GHA secrets masking active. `set -euo pipefail` present (no `set -x`). Empty-token preflight guard correct. |
| 9 | `rss_kb_from_remote` error handling | Broad `except Exception` is necessary for soak-loop stability. `-1` sentinel is written on failure → ingested by `report.py._load_rss` without filtering → false-pass on NFR-03 → see CR-02. |
| 10 | Test isolation | `monkeypatch` fixtures properly scoped. Conftest has no global `SOAK_BYPASS_TOKEN` clear, but tests that depend on env-unset explicitly call `delenv`. Not a current bug. |
| 11 | `_get_configured_token` lazy re-read | Correct design. O(1) dict lookup. Allows token rotation via restart. |
| 12 | Daily LRU eviction + bypass interaction | No interaction. Soak-authenticated requests create no buckets. No contribution to bucket store pressure during soak window. |
| 13 | Should bypass skip access log? | 50 concurrent over 24h at ~50 req/s ceiling = up to ~4.3M log lines. Within scope for NFR-05 consideration but not a correctness bug. Accept or mitigate in v2. |

---

## Final Summary

**Overall verdict: FIX-FIRST before pushing to production.**

Two blockers must be resolved:

- **CR-01** will cause every authenticated `/admin/metrics` call to 500 in production (the
  Docker image excludes `scripts/`), making the entire remote RSS measurement path non-functional.
- **CR-02** will cause the NFR-03 soak gate to produce a false green when all RSS samples fail
  (which they will, due to CR-01), silently certifying compliance with the 256 MB memory
  constraint without actually measuring it.

The four warnings (WR-01 through WR-04) should be addressed before the next production
deployment cycle but do not block the immediate soak run fix. The three info items are
documentation and test-robustness improvements.

The security design of the bypass (single source of truth, constant-time comparison,
default-safe, middleware ordering correct, token non-echoed) is sound and does not require
changes.

---

_Reviewed: 2026-05-15_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
