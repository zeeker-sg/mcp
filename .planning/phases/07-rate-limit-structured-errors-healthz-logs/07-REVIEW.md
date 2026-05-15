---
phase: 07-rate-limit-structured-errors-healthz-logs
reviewed: 2026-05-15T00:00:00Z
depth: standard
files_reviewed: 13
files_reviewed_list:
  - src/mcp_zeeker/app.py
  - src/mcp_zeeker/config.py
  - src/mcp_zeeker/core/datasette_client.py
  - src/mcp_zeeker/core/errors.py
  - src/mcp_zeeker/core/ip.py
  - src/mcp_zeeker/core/middleware/error_enrichment.py
  - src/mcp_zeeker/core/middleware/rate_limit.py
  - src/mcp_zeeker/server.py
  - tests/conftest.py
  - tests/test_app.py
  - tests/test_datasette_client_retry.py
  - tests/test_error_catalog.py
  - tests/test_rate_limit.py
findings:
  critical: 2
  warning: 7
  info: 4
  total: 13
status: issues_found
---

# Phase 7: Code Review Report

**Reviewed:** 2026-05-15
**Depth:** standard
**Files Reviewed:** 13
**Status:** issues_found

## Summary

Phase 7 ships the ASGI rate-limit middleware (RATE-01..05), the structured-error catalog (ERR-01..05), and the request-id correlation pipeline. The token-bucket math, Retry-After arithmetic, sticky-TTL eviction, daily-rollover handling, and 429 body shape are correct and well-tested. The error catalog is properly locked behind a literal-tuple assertion, and the FastMCP error-enrichment middleware preserves the catalog-code prefix.

Two BLOCKERs were found:

1. **`ip_prefix()` leaks unsanitized XFF content into log lines** (CR-01). When an attacker sends an `X-Forwarded-For` header that does not parse as IPv4 (no four dot-separated parts) and contains no colon, the function falls through to `return ip` unchanged. The hostile string is then bound into the structlog `ip_prefix` contextvar by `RequestIdMiddleware` and emitted on every subsequent log line — a direct INJ-05 / OBS-04 contract violation. The Phase 7 test `test_logs_no_user_input` does not catch this because it pre-binds a clean `ip_prefix` and bypasses the actual `RequestIdMiddleware → client_ip → ip_prefix` chain.

2. **App lifespan accesses `tool.return_type` on the FastMCP `Tool` base class, which does not declare that attribute** (CR-02). Only the `FunctionTool` subclass has `return_type`. With current Phase 4 / 5 / 6 tools all decorated via `@mcp.tool` (which produces `FunctionTool` instances) the codepath happens to work, but the moment any `TransformedTool` or other subclass is registered (planned Wave-4 surface), the lifespan will raise `AttributeError` and the entire HTTP server will fail to start, taking liveness with it.

The remaining warnings cover IPv6 prefix bugs, an XFF-port normalization gap, a clock-source mismatch in the rate-limit middleware, an over-broad exception swallow in `get_table_column_types`, and a test that asserts the wrong layer (see WR-01..WR-07).

## Critical Issues

### CR-01: `ip_prefix()` returns unsanitized XFF content for non-IPv4-shaped strings (INJ-05 / OBS-04 violation)

**File:** `src/mcp_zeeker/core/ip.py:33-40`
**Issue:** The fallback branch `return ".".join(parts[:3]) if len(parts) == 4 else ip` returns the *raw input string* whenever the IP doesn't have exactly four dot-separated segments AND doesn't contain a colon. An attacker controls `X-Forwarded-For` (`TRUSTED_PROXY_DEPTH=1` means the leftmost entry is trusted as "the client"). They can therefore inject arbitrary content — `'DROP TABLE users; --'`, `'</system><admin>'`, `'" OR 1=1 --'` — and that string is then:

1. Returned by `ip_prefix(ip)` unchanged (verified empirically).
2. Bound into `structlog.contextvars.ip_prefix` by `RequestIdMiddleware.__call__` (`request_id.py:36`).
3. Merged into every structured log line for that request via `merge_contextvars`.

This breaks both INJ-05 ("user input never echoed in logs") and OBS-04 ("only /24 prefix logged to avoid full-IP retention"). It is a log-injection / log-poisoning vulnerability. The Phase 7 test `tests/test_rate_limit.py::test_logs_no_user_input` is structurally unable to catch this because it calls `bind_request(ip_prefix="203.0.113")` directly inside the test, skipping the actual `client_ip → ip_prefix` chain that the bug lives in.

**Fix:**
```python
# src/mcp_zeeker/core/ip.py
import ipaddress

def ip_prefix(ip: str) -> str:
    """OBS-04: log only the /24 (IPv4) or /48 (IPv6) prefix.

    SECURITY: never echo a non-parseable input back — it is attacker-controlled
    via XFF and would land verbatim in every log line on the request.
    """
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "_invalid"  # do not echo attacker bytes
    if isinstance(addr, ipaddress.IPv4Address):
        parts = ip.split(".")
        return ".".join(parts[:3])
    # IPv6 — emit canonical /48 via ip_network
    return str(ipaddress.ip_network(f"{addr.exploded}/48", strict=False).network_address)
```

Add a regression test that drives the FULL chain (no `bind_request` shortcut):

```python
async def test_hostile_xff_does_not_leak_into_log(asgi_client, capture_logs):
    await asgi_client.get(
        "/healthz",
        headers={"x-forwarded-for": "DROP TABLE users; --"},
    )
    for line in capture_logs:
        assert "DROP TABLE" not in str(line)
        assert "<" not in str(line.get("ip_prefix", ""))
```

---

### CR-02: `lifespan` accesses `tool.return_type` on `Tool` base class — `AttributeError` will crash startup as soon as a non-`FunctionTool` is registered

**File:** `src/mcp_zeeker/app.py:53-67`
**Issue:** `mcp.list_tools()` returns `Sequence[fastmcp.tools.base.Tool]`. The `Tool` base class (`fastmcp/tools/base.py:140-174`) declares only `parameters`, `output_schema`, `annotations`, `execution`, `serializer`, `auth`, `timeout`. The `return_type` attribute exists only on the `FunctionTool` subclass (`fastmcp/tools/function_tool.py:92`).

Today every Phase 4–6 tool is registered via `@mcp.tool` which produces `FunctionTool`, so the access happens to succeed. But:

- `fastmcp.tools.tool_transform.TransformedTool` exists in the same import surface and is a Tool subclass without `return_type`.
- Future tools added via `Tool.from_*` factories that don't set `return_type` will trip the same path.
- Any registered Tool whose `return_type` is the literal `None` (the FunctionTool default per `function_tool.py:92`) will fail the `is not Envelope` assertion with a misleading "tool contract drift" message instead of a meaningful "this tool was registered without a return-type annotation" error.

The lifespan runs *before* yield, so a single misregistered tool deletes the entire `/healthz` liveness response — exactly the failure mode the comment on line 50 ("a bad deploy fails liveness immediately") describes, but for the wrong reason.

The `try / except ImportError` at line 68 catches only `ImportError` — `AttributeError` from `tool.return_type` propagates and crashes startup.

**Fix:**
```python
# src/mcp_zeeker/app.py — lifespan body
try:
    from mcp_zeeker.core.envelope import Envelope

    tools = await mcp.list_tools()
    for tool in tools:
        # Defensive getattr — Tool base class does not declare return_type;
        # only FunctionTool does. A future TransformedTool would AttributeError.
        return_type = getattr(tool, "return_type", None)
        if return_type is None:
            raise RuntimeError(
                f"tool contract drift: {tool.name} has no return_type "
                f"(register via @mcp.tool with a typed return annotation)"
            )
        if return_type is not Envelope:
            raise RuntimeError(
                f"tool contract drift: {tool.name} return_type "
                f"is {return_type!r}, expected Envelope"
            )
        if not (tool.description or "").rstrip().endswith(config.TOOL_TRAILER):
            raise RuntimeError(
                f"tool contract drift: {tool.name} description missing TOOL_TRAILER"
            )
except ImportError:
    pass
```

Add a regression test that registers a `TransformedTool` (or any non-FunctionTool) and asserts the lifespan raises a `RuntimeError` with the contract-drift message rather than `AttributeError`.

---

## Warnings

### WR-01: `ip_prefix()` IPv6 logic emits malformed prefixes for compressed addresses

**File:** `src/mcp_zeeker/core/ip.py:37-38`
**Issue:** `":".join(ip.split(":")[:3])` produces wrong /48 prefixes for IPv6 addresses that use `::` zero-compression near the start. Empirical:

- `::1` → `::1` (looks like full address, no privacy)
- `2001:db8::1` → `2001:db8:` (trailing single colon — not even valid IPv6 syntax)
- `2001:db8:85a3::8a2e:370:7334` → `2001:db8:85a3` (correct only because `::` falls after the third group)

This produces non-canonical, sometimes ambiguous, prefix strings that defeat the OBS-04 retention goal and may confuse log-aggregation tooling that tries to re-parse the field as an IP address.

**Fix:** Use `ipaddress.ip_network(f"{addr.exploded}/48", strict=False)` as in CR-01's fix. Returns canonical compressed form like `2001:db8::` regardless of input compression style.

---

### WR-02: `_normalize_ip_key()` does not strip port from `[::1]:8080` form despite docstring claim

**File:** `src/mcp_zeeker/core/ip.py:80-91`
**Issue:** The docstring states "XFF entries occasionally arrive as `[::1]:8080` or `[::1]`" and that "the rate-limit bucket store keys on the bare IP form so a bracketed and an unbracketed duplicate of the same IPv6 client share one bucket." But the function only strips matching `[...]` brackets:

```python
if ip.startswith("[") and ip.endswith("]"):
    return ip[1:-1]
```

For `[::1]:8080` the input ends in `]:8080` — the `endswith("]")` test fails, the function returns the input unchanged, and the same client at port 8080 vs port 9090 is keyed to two different buckets (RATE-01 token-bucket bypass via port rotation).

The same applies to IPv4 XFF entries with port (`1.2.3.4:5678`) — common when an upstream proxy uses RFC 7239 syntax — which `_normalize_ip_key` doesn't touch at all.

**Fix:**
```python
def _normalize_ip_key(ip: str) -> str:
    if not ip:
        return ip
    # Strip [ipv6]:port and [ipv6] forms.
    if ip.startswith("["):
        end = ip.rfind("]")
        if end != -1:
            return ip[1:end]
    # Strip ipv4:port form (single colon, IPv4-shaped LHS).
    if ip.count(":") == 1:
        host, _ = ip.rsplit(":", 1)
        try:
            ipaddress.IPv4Address(host)
            return host
        except ValueError:
            pass
    return ip
```

---

### WR-03: `RateLimitMiddleware.__call__` mixes `time.perf_counter()` with injected `time_provider` — flaky duration_ms in tests, no production bug but a latent test-injection trap

**File:** `src/mcp_zeeker/core/middleware/rate_limit.py:137,160`
**Issue:** `perf_start = time.perf_counter()` (wall clock) and `now_mono = self._time_provider()` (test-injected fake clock). The `duration_ms` computed at line 160 always uses the real wall clock even when tests drive `fake_clock`. Today no test asserts on `duration_ms` value in the synthetic 429 log line, but the field is published in the captured log line and a future stricter test will be flaky depending on the host's CPU scheduler.

The intent of the `time_provider` injection is precisely to make all time-dependent observable values deterministic; `duration_ms` is one such observable and slipped through.

**Fix:** Use `self._time_provider()` for `perf_start` too, with a `_perf_provider` defaulting to `time.perf_counter`. Or, simpler, drop the `perf_counter` measurement in the rate-limit middleware altogether — the synthetic 429 path does no real work, so `duration_ms=0` is a more honest value than "however long this Python interpreter took to run six lines of code."

---

### WR-04: `DatasetteClient.get_table_column_types` swallows `TypeError` over the entire request + parse path — masks real bugs

**File:** `src/mcp_zeeker/core/datasette_client.py:221-239`
**Issue:** The try/except catches `(UpstreamCallFailed, KeyError, ValueError, IndexError, TypeError)`. Catching `TypeError` over a block that includes `await self._request_with_retry(...)` is over-broad: a programming bug (e.g., passing a non-string `database` argument, or an httpx upgrade that changes a constructor signature) will be silently translated into "no column types known" rather than surfacing.

The docstring (lines 210-220) lists JSON-decode + index-miss + schema-drift as the intended fallback triggers. None of those produce a `TypeError`. Catching `TypeError` is defense-in-depth pushed too far.

**Fix:** Drop `TypeError` from the except tuple. If a downstream upstream-shape edge case really does raise `TypeError`, address it explicitly with a narrower guard (e.g., `isinstance(payload, dict)` before the index lookups).

```python
except (
    UpstreamCallFailed,
    KeyError,
    ValueError,  # includes json.JSONDecodeError
    IndexError,
):
    return {}
```

---

### WR-05: `app.py` lifespan envelope-contract guard catches only `ImportError`, masks every other failure mode of the contract check

**File:** `src/mcp_zeeker/app.py:68-70`
**Issue:** The `try / except ImportError` block was intended to tolerate the wave-2 stub state where `mcp_zeeker.core.envelope` does not yet exist. As implemented, the block also masks anything between the `try:` and the import — but since the import is the *first* statement inside the try, that's not a hazard today. The hazard is that the `for tool in tools:` loop is INSIDE the try block, so any `RuntimeError` raised by the contract check at lines 60-67 propagates correctly (good), but any `AttributeError` (see CR-02), `TypeError`, or `Exception` raised by `mcp.list_tools()` itself (e.g., a future FastMCP version that requires arguments) will propagate too — collapsing liveness on a maintenance issue rather than a real contract drift.

**Fix:** Narrow the try to the import only:

```python
try:
    from mcp_zeeker.core.envelope import Envelope
except ImportError:
    Envelope = None  # type: ignore[assignment]

if Envelope is not None:
    tools = await mcp.list_tools()
    for tool in tools:
        return_type = getattr(tool, "return_type", None)
        if return_type is None or return_type is not Envelope:
            raise RuntimeError(...)
        ...
```

---

### WR-06: `RateLimitMiddleware._enforce_cap` documents an invariant that does not hold across the insert-then-cap sequence

**File:** `src/mcp_zeeker/core/middleware/rate_limit.py:38, 311-331`
**Issue:** The module-level threat-model comment (line 38) claims "guarantees `len(store) <= store_cap` at all times". The actual sequence in `_check_bucket` is:

1. Line 209: `self._store[key] = bucket` — store grows by 1.
2. Line 214: `_enforce_cap(...)` called — only triggers if `len >= store_cap`.

Between (1) and (2) the store can have `len == store_cap + 0` (just hit cap — `_enforce_cap` does fire) or `len > store_cap - 1` momentarily. The implementation is correct for a single-task reader (asyncio is cooperative, no preemption between those two lines), but the docstring's "at all times" claim is a stronger property than the code actually delivers, and a future contributor might mistake it for a multi-task safety guarantee.

The "at all times" wording also masks a small accuracy issue: under continuous flood, after every cap-enforcement step `len(self._store)` ends up at `store_cap - max(1, store_cap // 100)` (i.e. ~99,000 at the 100k cap) — not `store_cap`. The headroom is fine; the docstring just over-promises.

**Fix:** Clarify the comment to "guarantees `len(store) <= store_cap` at every observable point in `_check_bucket`'s caller (after the cap-enforcement returns)" and note the post-eviction size is `store_cap - max(1, store_cap // 100)`.

---

### WR-07: `tests/test_rate_limit.py::test_logs_no_user_input` does not exercise the chain it claims to test

**File:** `tests/test_rate_limit.py:683-759`
**Issue:** The test docstring asserts "the /24-truncated `ip_prefix` (set by RequestIdMiddleware upstream) is the ONLY user-influenced value that can reach the log line." But the test then short-circuits the `RequestIdMiddleware → client_ip → ip_prefix` chain by calling `bind_request(request_id="rid-no-echo", ip_prefix="203.0.113")` directly at line 704. This pre-binds a clean `ip_prefix` and means the test would PASS even if `ip_prefix()` had a log-injection bug — which it does (see CR-01).

The hostile XFF flowing through the rate-limit middleware affects *bucket keying* only, not the `ip_prefix` contextvar (which is bound by the upstream `RequestIdMiddleware` that this test does not run).

**Fix:** Either:

(a) Drive the FULL `app` (Starlette + RequestIdMiddleware + RateLimit) via `asgi_client` so the actual contextvar binding chain runs and the assertion is meaningful; OR

(b) Rename the test to clarify it covers only "the rate-limit middleware does not parse the body" and add a SEPARATE test that drives the full chain to cover the ip_prefix-from-XFF leak path.

The shape of (a):

```python
async def test_logs_no_user_input_full_chain(asgi_client, ...):
    # Drive 21 POST /mcp/ requests with hostile XFF; capture logs;
    # assert hostile_substring not in any captured line, including the
    # ip_prefix field that gets bound by RequestIdMiddleware.
```

---

## Info

### IN-01: `_request_with_retry` post-loop raise is correctly described as unreachable but kept defensively — consider an `assert False, "unreachable"` to make intent compiler-checkable

**File:** `src/mcp_zeeker/core/datasette_client.py:192`
**Issue:** Lines 165-177 already raise `UpstreamCallFailed("upstream retry exhausted ...")` on the second attempt's 502/503. The post-loop raise on line 192 is unreachable because every other path in the loop either returns or raises. The comment at lines 167-173 acknowledges this. A static `assert False, "unreachable"` (or `raise AssertionError("unreachable")`) makes the unreachability explicit to readers and to type-checkers.

**Fix:**
```python
raise AssertionError(
    f"unreachable: _request_with_retry exited loop without return/raise on {url}"
)
```

---

### IN-02: `BucketState.__slots__` ordering should match `dataclass` field declaration order for clarity

**File:** `src/mcp_zeeker/core/middleware/rate_limit.py:74-88`
**Issue:** `__slots__` lists `("tokens", "last_refill_ts", "daily_count", "daily_date", "last_seen_ts", "daily_exceeded")`. The dataclass fields are declared in the same order — good. Minor: future maintainers adding a field to one location may forget the other (Python emits no error, just falls back to `__dict__` usage and silently inflates memory). A short comment naming the test that catches drift (or `dataclass(slots=True)` if the codebase's Python target supports it cleanly) would help.

**Fix (optional):** Use `@dataclass(slots=True)` to single-source-of-truth the slots from the field declarations:

```python
@dataclass(slots=True)
class BucketState:
    tokens: float
    last_refill_ts: float
    ...
```

This requires Python 3.10+; the project's pyproject.toml targets >=3.10 (per the FastMCP 3.2 stack), so this is safe.

---

### IN-03: `DatasetteClient.bind` mutates a class-wide singleton at every call — ergonomic in lifespan but a footgun in tests that forget `clear_singleton()`

**File:** `src/mcp_zeeker/core/datasette_client.py:120-139`
**Issue:** `bind()` sets BOTH the contextvar (per-task isolation) AND `cls._singleton` (process-wide leak). The docstring acknowledges this and offers `clear_singleton()` for test teardown. `tests/conftest.py::bound_metadata_cache` does call `MetadataCache.clear_singleton()` — but `bound_datasette_client` (lines 289-301) does NOT. Tests that depend on `bound_datasette_client` will leave the `DatasetteClient._singleton` class attribute set across the rest of the session. If a later test relies on `DatasetteClient.current()` raising `RuntimeError("called outside a bound scope")`, the singleton will paper over the missing binding and the test will incorrectly pass.

**Fix:** Add `DatasetteClient.clear_singleton()` to the teardown of `bound_datasette_client`:

```python
@pytest.fixture
async def bound_datasette_client(stub_upstream):
    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc = DatasetteClient(http)
        token = DatasetteClient.bind(dc)
        try:
            yield dc
        finally:
            DatasetteClient.reset(token)
            DatasetteClient.clear_singleton()
```

---

### IN-04: `errors.py` module docstring claims `rate_limited` is "NEVER a ToolError" but tests rely on the body containing the literal `"rate_limited"` — consider promoting the literal to a module-level constant

**File:** `src/mcp_zeeker/core/errors.py:30-34`, `src/mcp_zeeker/core/middleware/rate_limit.py:174`
**Issue:** The `rate_limited` code is hardcoded as a string literal in `rate_limit.py:174` (`"code": "rate_limited"`). The `CATALOG` tuple in `errors.py:74-86` also lists it as a literal. The two literals are NOT cross-checked at test time — `tests/test_error_catalog.py::test_all_11_codes_in_catalog` only asserts the catalog contents, not that the rate-limit middleware emits a code that is actually IN the catalog. A typo in either place (`"ratelimited"`, `"rate-limited"`) would not be caught.

**Fix:** Promote to a module-level constant in `core/errors.py`:

```python
RATE_LIMITED_CODE = "rate_limited"
CATALOG = (..., RATE_LIMITED_CODE, ...)
```

And import it in `rate_limit.py`:

```python
from mcp_zeeker.core.errors import RATE_LIMITED_CODE
...
"code": RATE_LIMITED_CODE,
```

This eliminates the literal-duplication risk and ties both call sites to the catalog tuple.

---

_Reviewed: 2026-05-15_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
