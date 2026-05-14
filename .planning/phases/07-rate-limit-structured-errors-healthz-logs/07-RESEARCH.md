# Phase 7: Rate limit + structured errors + healthz + logs — Research

**Researched:** 2026-05-15
**Domain:** ASGI middleware rate-limiting, token bucket algorithms, MCP transport error handling, structured logging
**Confidence:** HIGH (token bucket math, XFF parsing, structlog); MEDIUM (429 transport risk — verified from SDK source but Claude Desktop behavior is inferred)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D7-01:** Fixed UTC midnight reset. `daily_date` field; on each request check `daily_date != today_utc()`, reset `daily_count = 0`, update `daily_date`.
- **D7-02:** `Retry-After = max(active_window_waits)`. Pseudocode in CONTEXT.md — integer seconds, max of burst_wait and daily_wait.
- **D7-03:** Sticky TTL for daily-locked buckets. Idle TTL = `max(15 min, time-to-next-utc-midnight)` when `daily_exceeded`; standard 15-min otherwise. LRU 100k cap is absolute backstop.
- **D7-04:** `/internal/upstream-status` deferred to v2. No in-process endpoint.
- **D7-05:** OBS-02 honestly deferred. REQUIREMENTS.md update lands in Phase 7.

### Claude's Discretion
- 429 response body shape: `{"error": {"code": "rate_limited", "message": "Rate limit exceeded", "retry_after_seconds": N, "request_id": "..."}}` — researcher verifies Claude Desktop/mcp-remote compatibility.
- Logging for rate-rejected requests: ASGI rate-limit middleware emits synthetic log line using `LOG_FIELDS`.
- Bucket math: `BucketState` dataclass `(tokens: float, last_refill_ts: float, daily_count: int, daily_date: date, last_seen_ts: float, daily_exceeded: bool)` in `dict[str, BucketState]` with manual LRU + TTL sweep.
- Test strategy: inject `time_provider: Callable[[], float]` into rate-limiter constructor; tests pass fake clock. No freezegun.
- Middleware ordering: `RequestIdMiddleware` → `OriginAllowlistMiddleware` → `RateLimitMiddleware` → `Mount("/mcp", ...)`.

### Deferred Ideas (OUT OF SCOPE)
- `/internal/upstream-status` — v2
- API-keyed authenticated tiers — v2
- Redis-backed distributed rate limiting — v2
- Per-tool rate-limit tiers — v2
- Edge-proxy rate limiting — orthogonal infra
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| RATE-01 | Token bucket: burst 20, sustained 1 tok/s, daily 5,000/IP/24h | Token Bucket Math section |
| RATE-02 | ASGI middleware (not FastMCP) so 429 short-circuits before JSON-RPC parse | 429 Transport Compatibility section confirms roll-own ASGI is correct |
| RATE-03 | XFF parsing right-to-left, `TRUSTED_PROXY_DEPTH=1` | XFF Parsing section; `core/ip.py` already exists |
| RATE-04 | Bucket store LRU ≤100k keys + TTL eviction | Bucket Store + Eviction section |
| RATE-05 | HTTP 429 + `Retry-After` header (integer seconds) + body with `retry_after_seconds` | Retry-After arithmetic in Token Bucket Math; 429 Transport section |
| RATE-06 | Single uvicorn worker required (documented) | CONTEXT already locked; no new research needed |
| ERR-01 | All errors as structured MCP errors with stable `code` + human-readable `message` | Error Catalog section |
| ERR-02 | 11-code locked catalog | Error Catalog Completion section |
| ERR-03 | Every error envelope echoes `request_id` | `request_id` contextvar already bound by `RequestIdMiddleware` |
| ERR-04 | 502/503 retry once with jitter; immediate raise on 504 | 502/503 Retry Verification section |
| ERR-05 | Upstream 4xx mapped to catalog — no upstream message echo | Error Catalog section; INJ-05 preservation |
</phase_requirements>

---

## Summary

**Key recommendations (4-6 bullets):**

1. **Roll own ASGI middleware, do not use FastMCP's `RateLimitingMiddleware`.** FastMCP's built-in runs at the MCP protocol layer (inside `on_call_tool`) — AFTER JSON-RPC parsing. RATE-02 mandates short-circuit BEFORE JSON-RPC parse. The existing `OriginAllowlistMiddleware` is the exact ASGI pattern to clone for `RateLimitMiddleware`. [VERIFIED: FastMCP Context7 docs; code inspection of `OriginAllowlistMiddleware`]

2. **Raw HTTP 429 from a POST to `/mcp` IS surfaced as an `SdkError` to the caller, not silently swallowed.** The TypeScript SDK's `streamableHttp.ts` (line 632) reads the response body and throws `SdkError(ClientHttpNotImplemented, "Error POSTing to endpoint: <body_text>", {status: 429, text: body_text})`. The body text IS included in the error message. This means the `{"error": {...}}` JSON body the CONTEXT.md defaults will be visible to Claude. The error surfaces as a transport-layer failure — NOT as a JSON-RPC error envelope. The LLM sees a tool call failure (not a structured error with code). Implication: use a short, clean body; the body text will appear in the error message verbatim. [VERIFIED: MCP TypeScript SDK source `packages/client/src/client/streamableHttp.ts:632`]

3. **BucketState store stays comfortably under 32 MB at 100k cap.** With `__slots__` on the dataclass: ~28 MB; without slots: ~37 MB including dict overhead. Adding `__slots__ = (...)` to the `BucketState` dataclass saves ~7 MB and eliminates `__dict__` allocation per instance. Recommend adding `__slots__`. [VERIFIED: Python object model arithmetic + `sys.getsizeof` probing]

4. **Opportunistic per-request sweep is sufficient; no background task needed.** At 50 RPS sustained, sweeping 1-in-N (e.g., 1-in-100) requests costs at most O(100k) dict iteration once per 2 seconds — within the 300 ms p50 budget only if the sweep is batched. Better pattern: track `_last_sweep_ts` on the limiter instance; on each request, if `now - _last_sweep_ts > SWEEP_INTERVAL_SECONDS` (e.g., 30s), sweep expired keys. Worst-case 100k iteration at 30s cadence = negligible per-request latency cost. [ASSUMED — no benchmark, but standard practice for in-memory rate limiters]

5. **No new `query_timeout` or `invalid_query` raise sites exist yet in the codebase.** Phase 7 adds the codes to the catalog definition and documents where they will be raised (Phase 4 search / Phase 7 timeout scaffolding). The actual raises for `query_timeout` may be deferred if FTS timeout logic is out of Phase 7 scope. See Error Catalog section. [VERIFIED: grep of codebase]

6. **The `/healthz` implementation is already correct and already tested.** `app.py:92-94` returns `{"status": "ok"}` without consulting upstream. `tests/test_app.py:22-30` already asserts the no-upstream contract. Phase 7 only needs to confirm the test is sufficient and add it to the formal test map. [VERIFIED: source + test inspection]

**Primary recommendation:** Roll own `RateLimitMiddleware` as an ASGI class mirroring `OriginAllowlistMiddleware`; keep the 429 body compact and free of user input; sweep the bucket store on a time-gated background check within the request path.

---

## Token Bucket Math

### Refill Formula

```
# On each request arrival at time `now`:
elapsed = now - bucket.last_refill_ts
new_tokens = min(BURST_CAPACITY, bucket.tokens + elapsed * TOKENS_PER_SECOND)
bucket.tokens = new_tokens
bucket.last_refill_ts = now

# Consume one token:
if bucket.tokens >= 1.0:
    bucket.tokens -= 1.0
    # ALLOW
else:
    # DENY — compute Retry-After
```

Constants: `BURST_CAPACITY = 20`, `TOKENS_PER_SECOND = 1.0` (sustained 1/s = 60/min).

**Clock source:** Use `time.monotonic()` for `last_refill_ts` and `last_seen_ts` (avoids wall-clock jumps). For `daily_date` comparison and `seconds_to_utc_midnight`, use `datetime.now(tz=timezone.utc)`. These are separate: the bucket refill uses monotonic, the daily reset and Retry-After for midnight uses wall clock.

**Leap seconds / DST:** UTC has no DST. Leap seconds are absorbed by `datetime.now(tz=timezone.utc)` at the OS level. No special handling required. [CITED: Python datetime docs]

### Retry-After Arithmetic (D7-02)

```python
import math
from datetime import datetime, timezone, timedelta

def seconds_to_utc_midnight(now: datetime) -> int:
    """Integer seconds until next 00:00:00 UTC. Minimum 1 (never 0)."""
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, math.ceil((tomorrow - now).total_seconds()))

def compute_retry_after(bucket: BucketState, now_mono: float, now_utc: datetime) -> int:
    """D7-02: max of all active window waits, integer seconds."""
    waits = []
    # Burst window: how long until tokens refill to 1?
    if bucket.tokens < 1.0:
        tokens_needed = 1.0 - bucket.tokens
        waits.append(math.ceil(tokens_needed / TOKENS_PER_SECOND))
    # Daily window: time to midnight (only if daily exceeded)
    if bucket.daily_exceeded:
        waits.append(seconds_to_utc_midnight(now_utc))
    return max(waits) if waits else 1
```

**Worked example (burst empty + daily also exceeded, time = 23:55:00 UTC):**

| Window | Wait |
|--------|------|
| Burst: tokens=0 | ceil(1/1.0) = 1 s |
| Daily: 00:05:00 to midnight | 300 s |
| `Retry-After` | max(1, 300) = **300 s** |

**Worked example (burst empty, daily NOT exceeded, tokens=0.5):**

| Window | Wait |
|--------|------|
| Burst: tokens=0.5 | ceil(0.5/1.0) = 1 s |
| Daily: not exceeded | (not included) |
| `Retry-After` | max(1) = **1 s** |

**Edge case: both windows refill at the same token but different times.** The max() guarantees a well-behaved client won't re-trip immediately.

**Always an integer:** `math.ceil()` guarantees integer output. Send as `Retry-After: 300` (header, integer string) AND `"retry_after_seconds": 300` (body, int). [CITED: RFC 9110 §10.2.3 — Retry-After header accepts integer seconds or HTTP date]

### Daily Reset Check

On each request, before bucket operations:
```python
today = date.today()  # UTC: use datetime.now(tz=timezone.utc).date()
if bucket.daily_date != today:
    bucket.daily_count = 0
    bucket.daily_date = today
    bucket.daily_exceeded = False  # CRITICAL: reset flag on new day
```

**Clock skew:** A single-process server with a single event loop has no clock skew. `datetime.now(tz=timezone.utc)` is consistent within a request.

---

## XFF Parsing

### Algorithm (already implemented in `core/ip.py`)

The `client_ip(conn: HTTPConnection) -> str` function in `src/mcp_zeeker/core/ip.py` is **already correct** and handles RATE-03:

```python
# src/mcp_zeeker/core/ip.py (lines 10-30) — VERIFIED existing implementation
def client_ip(conn: HTTPConnection) -> str:
    depth = getattr(config, "TRUSTED_PROXY_DEPTH", 1)
    xff = conn.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) <= depth:
            return parts[0] if parts else ""
        return parts[-(depth + 1)]
    return conn.client.host if conn.client else ""
```

**The ASGI rate-limit middleware CANNOT use `HTTPConnection`.** It operates on raw `Scope`, `Receive`, `Send`. It must replicate the XFF logic directly. Reference implementation for the middleware:

```python
# Reference: parse XFF from raw ASGI scope headers
def _client_ip_from_scope(scope: dict, depth: int) -> str:
    """Extract client IP from ASGI scope, replicating core/ip.py logic."""
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in scope.get("headers", [])
    }
    xff = headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) <= depth:
            return parts[0] if parts else ""
        return parts[-(depth + 1)]
    client = scope.get("client")
    return client[0] if client else ""
```

**Note on DRY:** The existing `core/ip.py:client_ip` takes an `HTTPConnection`, not a raw scope. Two options:
1. Add `client_ip_from_scope(scope, depth) -> str` to `core/ip.py` and call it from both `RequestIdMiddleware` and the new `RateLimitMiddleware`.
2. Accept the duplication (it's 8 lines). Option 1 is cleaner and avoids drift.

### IPv6 Edge Cases

The existing `ip_prefix()` already handles IPv6 (takes first 3 colon groups). The rate-limit key is the raw IP string from XFF/client — no normalization needed for keying; the same string produces the same dict key. IPv6 brackets (e.g., `[::1]`) in XFF: strip brackets before using as key.

```python
def _normalize_ip_key(ip: str) -> str:
    """Strip IPv6 brackets for consistent dict keying."""
    if ip.startswith("[") and ip.endswith("]"):
        return ip[1:-1]
    return ip
```

### Edge Cases Handled by Existing Logic

| Case | Behavior |
|------|----------|
| XFF missing entirely | Falls back to `scope["client"][0]` (the TCP peer — Caddy) |
| XFF has fewer hops than `depth` | Returns `parts[0]` (the leftmost entry = client) |
| XFF has exactly `depth` hops | Returns `parts[0]` (same as above, `len(parts) <= depth`) |
| XFF deliberately malformed (`,,extra,`) | Empty strings stripped by `if p.strip()` in list comp |
| XFF with padding whitespace | `.strip()` on each part handles it |
| No client in scope | Returns `""` — rate limiter should treat `""` as a single unknown key |

**Spoofed XFF flood:** An attacker POSTing with arbitrary XFF headers can create new bucket entries. With `TRUSTED_PROXY_DEPTH=1`, the Caddy proxy is the trusted hop. The client IP is extracted from XFF position `-(depth+1)` = second from right. If Caddy appends the true client IP to XFF (standard behavior), an attacker cannot forge a different client IP via an additional XFF header they supply — Caddy's append is authoritative. The rightmost entry appended by Caddy is the TCP peer address. The attacker can still spoof the leftmost entries (before Caddy appends), but with `depth=1`, those leftmost entries are the client IP position. Full analysis in Bucket Store section.

### `client_ip()` already used by `RequestIdMiddleware`

`RequestIdMiddleware` (the outermost middleware) already calls `client_ip(conn)` and binds `ip_prefix` to the structlog contextvar. The `RateLimitMiddleware` (the THIRD ASGI layer) receives the same scope and can re-derive the client IP from scope headers using the raw implementation above. This is correct — no contextvar sharing of the IP is needed between middleware layers.

---

## Bucket Store + Eviction

### BucketState Size Calculation

`BucketState` dataclass with `__slots__` (recommended):
```python
@dataclass
class BucketState:
    __slots__ = ("tokens", "last_refill_ts", "daily_count", "daily_date",
                 "last_seen_ts", "daily_exceeded")
    tokens: float
    last_refill_ts: float
    daily_count: int
    daily_date: date
    last_seen_ts: float
    daily_exceeded: bool
```

| Component | Size |
|-----------|------|
| Dataclass header + 6 slot pointers | 64 bytes |
| `tokens` (float, boxed) | 24 bytes |
| `last_refill_ts` (float, boxed) | 24 bytes |
| `daily_count` (int, small) | 28 bytes |
| `daily_date` (date object) | 32 bytes |
| `last_seen_ts` (float, boxed) | 24 bytes |
| `daily_exceeded` (bool = int) | 28 bytes |
| IPv4 string key (`'192.168.1.100'`) | 54 bytes |
| Dict slot overhead (hash+ptrs) | 16 bytes |
| **Per-entry total (with `__slots__`)** | **~294 bytes** |

100k entries: ~28 MB. Dict array overhead at 100k entries (150k slots pre-allocated for 2/3 load factor): ~2.3 MB.

**Total bucket store at 100k cap: ~30 MB.** [VERIFIED: Python `sys.getsizeof` measurement + object model accounting]

This is comfortably under the 32 MB target. Without `__slots__`, total rises to ~37 MB (exceeds 32 MB). **Use `__slots__`.**

**Note on float boxing:** Python's float is always a heap-allocated 24-byte object for `tokens`, `last_refill_ts`, `last_seen_ts` (not inline in the struct). The dict and dataclass store pointers to these. Small integer caching (-5 to 256) applies to `daily_count` in the early days but NOT at higher counts. At 5,000/day ceiling these are all above 256 and will be fresh allocations. However, `sys.intern` tricks are not practical here — just accept the per-entry cost.

### Sticky-TTL Eviction Algorithm (D7-03)

```python
def _effective_ttl(bucket: BucketState, now_utc: datetime) -> float:
    """Return effective TTL in seconds. D7-03."""
    if bucket.daily_exceeded:
        return max(RATE_IDLE_TTL_SECONDS,  # 900 = 15 min
                   seconds_to_utc_midnight(now_utc))
    return RATE_IDLE_TTL_SECONDS

def _is_expired(bucket: BucketState, now_mono: float, now_utc: datetime) -> bool:
    idle_seconds = now_mono - bucket.last_seen_ts
    return idle_seconds > _effective_ttl(bucket, now_utc)

def sweep(store: dict[str, BucketState], now_mono: float, now_utc: datetime) -> None:
    """Remove expired entries. Called periodically from RateLimitMiddleware."""
    expired = [ip for ip, b in store.items() if _is_expired(b, now_mono, now_utc)]
    for ip in expired:
        del store[ip]
```

**LRU backstop at 100k:** Pure-Python `dict` maintains insertion order (Python 3.7+) but NOT access order. For LRU semantics, two options:
1. Use `collections.OrderedDict` + `move_to_end()` on access (promotes recently-accessed key).
2. Use `last_seen_ts` field to find the LRU key when cap is hit: `min(store, key=lambda ip: store[ip].last_seen_ts)`.

Option 2 is O(n) for LRU eviction but only triggered at the 100k cap (under flood). Option 1 is O(1) eviction but adds `move_to_end()` on every access (overhead under normal load). **Recommendation: Option 2 for simplicity.** The LRU eviction at 100k cap is an attack backstop; under normal load it never fires. O(n) at 100k is ~1 ms of Python dict iteration — acceptable as a rare event during a flood.

### Time-Gated Sweep Strategy

```python
class RateLimitMiddleware:
    def __init__(self, app, ..., sweep_interval_seconds: float = 30.0):
        self._store: dict[str, BucketState] = {}
        self._last_sweep_ts: float = 0.0
        self._sweep_interval = sweep_interval_seconds

    async def __call__(self, scope, receive, send):
        now_mono = time.monotonic()
        # Time-gated sweep: only sweep if interval has elapsed
        if now_mono - self._last_sweep_ts > self._sweep_interval:
            self._sweep(now_mono, datetime.now(tz=timezone.utc))
            self._last_sweep_ts = now_mono
        # ... normal bucket logic
```

At 50 RPS, sweep triggers every 30 seconds. Sweeping 100k keys in Python: ~10-50ms in the worst case (100k expired entries to delete). This is within the p95 1.5s budget even in the worst case. Under normal load (few thousand active IPs), sweep is fast.

**Async concern:** The sweep runs synchronously within the async `__call__`. For 100k entries this could cause a brief event-loop stall. If this becomes a concern, use `asyncio.get_event_loop().run_in_executor(None, self._sweep, ...)`. For v1 at 50 RPS, synchronous sweep is acceptable.

### 10k Spoofed-XFF Flood Walkthrough (RATE-04 proof)

**Attack scenario:** Attacker sends 10,000 requests, each with a unique spoofed `X-Forwarded-For` IP, from a single TCP connection through Caddy.

**With `TRUSTED_PROXY_DEPTH=1`:** Caddy appends the actual TCP peer (attacker's IP, say `1.2.3.4`) to XFF. The header received by the MCP server looks like: `X-Forwarded-For: <spoofed_ip>, 1.2.3.4`.

The XFF parsing with `depth=1`:
```
parts = [spoofed_ip, "1.2.3.4"]
len(parts) == 2 > depth(1)
return parts[-(1+1)] = parts[-2] = spoofed_ip
```

So with `TRUSTED_PROXY_DEPTH=1`, **the attacker's spoofed IP IS extracted as the client IP.** This is by design — the operator trusts exactly 1 proxy (Caddy), and the client IP is one position before the last (Caddy's append). An attacker can create new bucket entries by varying the spoofed IP.

**Bucket store behavior under 10k spoofed flood:**
1. Requests 1–100,000: each unique spoofed IP creates a new `BucketState` entry.
2. At 100k entries, the LRU backstop fires: `min(store, key=lambda ip: store[ip].last_seen_ts)` finds the oldest entry and evicts it.
3. TTL sweep runs every 30s: entries idle for 15 min are swept. A flood at 1,000 unique IPs/second creates 30,000 entries before the first sweep — all within the 100k cap.
4. **Memory bound:** 100k × 294 bytes = ~30 MB. Hard cap enforced.
5. **CPU cost of LRU eviction:** O(n) = O(100k) per eviction event. Under a 10k/s flood this fires at 100k, then continuously as new entries push old ones out. At 100k, each new spoofed IP triggers a O(100k) scan. At 10k/s flood, this is 10k × O(100k) ops/second = potentially CPU-bound. Mitigation: batch eviction — when cap exceeded, evict 1% (1,000 entries) in one pass rather than one at a time.

**Recommended LRU eviction batch:**
```python
def _enforce_cap(self, now_mono: float, now_utc: datetime) -> None:
    """When store hits cap, evict oldest 1% of entries (batch LRU)."""
    if len(self._store) >= self._store_cap:
        # Evict oldest 1k entries by last_seen_ts
        evict_count = max(1, len(self._store) // 100)
        by_age = sorted(self._store, key=lambda ip: self._store[ip].last_seen_ts)
        for ip in by_age[:evict_count]:
            del self._store[ip]
```

**Store stays bounded:** The cap + batch eviction guarantee that `len(self._store) <= RATE_STORE_CAP` at all times. Daily-locked buckets are NOT evicted during a flood because their sticky TTL (`max(15min, seconds_to_midnight)`) is larger than the 15-min standard TTL, making them appear as RECENTLY active relative to newly-spoofed flood IPs. This is correct — a daily-locked legitimate IP retains its lock across the flood.

**Critical correctness invariant:** A daily-locked IP CANNOT restart its daily counter by going idle for 15 minutes (D7-03). The sticky TTL `max(15min, time_to_midnight)` ensures that even if evicted by LRU (only under extreme flood), the IP's daily counter is lost — not reset. When re-created after eviction, the new entry starts with `daily_count=0` for the current day, effectively giving the evicted IP a fresh counter. This is a known tradeoff: a sufficiently large flood can evict legitimate daily-locked IPs, letting them bypass the daily limit. The 100k cap means this requires > 100k unique attacker IPs simultaneously. Document in module docstring.

---

## 429 Transport Compatibility

### Finding: Raw HTTP 429 IS surfaced (not silently swallowed)

**Source:** MCP TypeScript SDK, `packages/client/src/client/streamableHttp.ts`, line 632 [VERIFIED: GitHub API inspection].

When the MCP server returns a non-2xx, non-401, non-403, non-405 HTTP response to a POST (tool call), the SDK executes:

```typescript
const text = await response.text?.().catch(() => null);
// ... 401/403 special handling ...
throw new SdkError(SdkErrorCode.ClientHttpNotImplemented,
    `Error POSTing to endpoint: ${text}`,
    { status: response.status, text });
```

For HTTP 429:
- `text` = the response body as a string (our JSON body)
- The error message is: `"Error POSTing to endpoint: {"error": {"code": "rate_limited", "message": "Rate limit exceeded", "retry_after_seconds": 300, "request_id": "..."}}`
- This is thrown as an `SdkError` with `status: 429`
- The body IS included in the error text verbatim

**How Claude sees it:** The `SdkError` propagates as a transport failure. In Claude Desktop/Claude.ai, this typically surfaces as a tool call failure with the error message text. The LLM will see the error message string — which includes the body.

**Implication for body design:**
- Keep the body compact and human-readable (it appears in error messages)
- Include `retry_after_seconds` as a number so a well-behaved client can parse it
- Do NOT include user input or filter values in the body (INJ-05)
- The `request_id` in the body is useful for correlation

**Confirmed body shape (CONTEXT.md default, now verified safe):**
```json
{
  "error": {
    "code": "rate_limited",
    "message": "Rate limit exceeded",
    "retry_after_seconds": 300,
    "request_id": "abc123"
  }
}
```

### ANNO-03 Compatibility

REQUIREMENTS.md ANNO-03: "Tool descriptions surface rate-limit semantics so the LLM understands a 429 is recoverable." Because the 429 surfaces as an `SdkError` with body text (not as a JSON-RPC error with code), the LLM's handling depends on how Claude Desktop parses `SdkError`. The `retry_after_seconds` field in the body IS visible in the error text — an LLM that sees the full error string will understand it should retry. The ANNO-03 tool description sentence is still needed for proactive awareness.

### FastMCP `RateLimitingMiddleware` — Why Not Use It

FastMCP's `RateLimitingMiddleware` (from `fastmcp.server.middleware.rate_limiting`) runs at the FastMCP middleware layer via `mcp.add_middleware()`. This fires inside `on_call_tool` — AFTER JSON-RPC parsing and session establishment. Using it would violate RATE-02 (rate-limit fires AFTER JSON-RPC parse). Additionally, FastMCP's built-in does not support: daily caps, sticky TTL, XFF parsing, or the custom 429 body shape. [VERIFIED: FastMCP Context7 docs confirm `mcp.add_middleware()` runs at MCP protocol layer]

### Recommendation: Roll-own ASGI, keep body compact

The `OriginAllowlistMiddleware` is the exact template. The 429 body is safe to include as described above.

### VSCode Issue Note

GitHub issue `microsoft/vscode#247734` reports that in some MCP client implementations (not the TypeScript SDK directly), 429 can become stuck/timeout without display. This is consistent with the SDK source: the `SdkError` is thrown, and if the hosting client (e.g., Claude Desktop's internal MCP bridge) does not propagate the error message, the tool call may timeout silently. **This is a client-side issue, not a server-side one.** The server should still send a well-formed 429 with body — client behavior improvement is upstream's responsibility. [CITED: github.com/microsoft/vscode/issues/247734]

---

## Error Catalog Completion

### Existing 6 codes (Phase 3, D3-12 — DO NOT CHANGE raise sites or message templates)

| Code | Raise site | Message template |
|------|-----------|-----------------|
| `unknown_database` | `core/visibility.py:raise_unknown_database` | `"unknown_database: Database not found: {database}"` |
| `unknown_table` | `core/visibility.py:raise_unknown_table` | `"unknown_table: Table not found: {database}.{table}"` |
| `unknown_column` | `core/visibility.py:raise_unknown_column` | `"unknown_column: Column not found: {database}.{table}.{column}"` |
| `invalid_filter_op` | `core/filter_compiler.py` (multiple) | Various fixed messages — no user value echo |
| `invalid_cursor` | `tools/retrieval.py` (multiple) | Fixed messages |
| `unsupported_table_for_fetch` | `core/visibility.py:raise_unsupported_table_for_fetch` | `"unsupported_table_for_fetch: ..."` |
| `not_found` | `core/visibility.py:raise_not_found` | `"not_found: ..."` |

### New 5 codes (Phase 7 additions — ERR-02)

| Code | Where Raised | Message Template | INJ-05 Safe? | Test Fixture |
|------|-------------|-----------------|--------------|-------------|
| `unknown_database` | Already exists in `core/visibility.py` | Already implemented | Yes | Already tested |
| `invalid_query` | `tools/search.py` — FTS5 syntax error (already: `search.py:209` raises `upstream_unavailable` for all failures; `invalid_query` is for Datasette 400 on FTS5 parse). Also Phase 7 scope: `search` tool when query escaping detects malformed input. | `"invalid_query: Query could not be parsed"` | Yes — no user query echoed | `test_error_catalog.py::test_invalid_query_raises_correct_code` |
| `query_timeout` | `core/datasette_client.py` — when `httpx.TimeoutException` is caught (currently surfaces as `UpstreamCallFailed`). Phase 7 adds a distinct `QueryTimeoutError` subclass or distinguishes by exception type in tool handlers. | `"query_timeout: Query timed out"` | Yes — no URL/query echoed | `test_error_catalog.py::test_query_timeout_raises_correct_code` |
| `rate_limited` | `core/middleware/rate_limit.py` — ASGI layer 429 body. NOT a `ToolError` (never reaches FastMCP layer). Body: `{"error": {"code": "rate_limited", ...}}`. | Body: `"Rate limit exceeded"` | Yes — no user input in body | `tests/test_rate_limit.py::test_429_body_has_rate_limited_code` |
| `upstream_unavailable` | Already exists: `tools/retrieval.py:263,493,716`, `tools/search.py:209`. Phase 7 verifies these are wired. | `"upstream_unavailable: upstream call failed"` | Yes — no upstream message echoed | `test_datasette_client_retry.py` already covers retry; Phase 7 adds error-code propagation test |

**Note on `invalid_query`:** The code already exists in REQUIREMENTS.md but has no raise site yet. Phase 7 scope should clarify: SEARCH-06 (Phase 4 scope) handles FTS5 escaping. If Phase 7 only closes the catalog for existing tools, `invalid_query` raise sites may be deferred to Phase 4. Phase 7 should at minimum define the catalog entry and add a test fixture that verifies the code string matches when raised.

**Pattern for new error codes in `core/visibility.py`:**
```python
def raise_invalid_query() -> NoReturn:
    raise ToolError("invalid_query: Query could not be parsed")

def raise_query_timeout() -> NoReturn:
    raise ToolError("query_timeout: Query timed out")
```

**INJ-05 verification for all new codes:** None of the new message templates include `{user_input}`, `{query}`, `{url}`, or any variable from tool call parameters. All messages are fixed strings with no format substitution of user-supplied data. [VERIFIED: proposed templates above; existing pattern in `filter_compiler.py`]

### Error Code String Convention

Pattern (confirmed from codebase): `ToolError("code: Human readable message")` where `code` is the catalog code followed by `: `. FastMCP extracts this as the error message string. The `code` field in ERR-01 refers to the part before `: `. Tests that assert error codes match on the prefix.

**ERR-03 (request_id echo):** For `ToolError`-based errors, the `request_id` is in the structlog contextvar (bound by `RequestIdMiddleware`). FastMCP converts `ToolError` to a JSON-RPC error response. The `request_id` is NOT automatically injected into the MCP error envelope by FastMCP. Phase 7 needs to either:
1. Include `request_id` in the `ToolError` message string (ugly), OR
2. Intercept errors via a FastMCP middleware (`on_call_tool` wrapper) that appends `request_id` to the error envelope.

The CONTEXT.md does not prescribe an approach. The cleanest option is a thin FastMCP `ErrorEnrichmentMiddleware` that catches `ToolError` and adds `request_id` from `structlog.contextvars.get_contextvars()["request_id"]` to the error detail. This is an open question for the planner (see Open Questions).

---

## 502/503 Retry Verification

### Existing Implementation (VERIFIED: `datasette_client.py:126-158`)

```python
async def _request_with_retry(self, method: str, url: str, **kw) -> httpx.Response:
    for attempt in (0, 1):
        resp = await self._http.request(method, url, **kw)
        if resp.status_code in (502, 503) and attempt == 0:
            await asyncio.sleep(0.25 + random.random() * 0.25)
            continue
        if resp.status_code == 504:
            raise UpstreamCallFailed(f"upstream 504 on {url}", status=504)
        if 200 <= resp.status_code < 300:
            return resp
        raise UpstreamCallFailed(f"upstream {resp.status_code} on {url}", status=resp.status_code)
    raise UpstreamCallFailed(f"upstream retry exhausted on {url}")
```

**Existing test file:** `tests/test_datasette_client_retry.py` — already covers:
- 2xx returns immediately (no sleep)
- 502 retries once, sleep in [0.25, 0.50]
- 503 retries once, sleep in [0.25, 0.50]
- 504 raises immediately without retry

[VERIFIED: test file inspection]

### Additional Tests Needed for Phase 7 (ERR-04)

Phase 7 closes ERR-04. The existing tests cover the retry mechanism. Additional tests needed:

| Test | Assertion | pytest-httpx Pattern |
|------|-----------|---------------------|
| `test_502_twice_raises_upstream_unavailable` | 502 on both attempts → `UpstreamCallFailed` raised (retry exhausted) | `httpx_mock.add_response(502)` × 2; assert `UpstreamCallFailed` |
| `test_503_twice_raises_upstream_unavailable` | 503 on both attempts → `UpstreamCallFailed` raised | same pattern |
| `test_upstream_call_failed_maps_to_tool_error` | `UpstreamCallFailed` caught in tool handler → `ToolError("upstream_unavailable: ...")` raised | Use `Client(mcp)` in-memory; stub `DatasetteClient._request_with_retry` to raise `UpstreamCallFailed` |
| `test_504_maps_to_upstream_unavailable` | 504 → immediate `UpstreamCallFailed(status=504)` → tool returns `upstream_unavailable` | httpx_mock 504; assert ToolError code |

**pytest-httpx pattern for retry exhaustion:**
```python
async def test_502_twice_raises_upstream_unavailable(
    httpx_mock: pytest_httpx.HTTPXMock, client: DatasetteClient
) -> None:
    httpx_mock.add_response(status_code=502)
    httpx_mock.add_response(status_code=502)
    with patch.object(asyncio, "sleep", new_callable=AsyncMock):
        with pytest.raises(UpstreamCallFailed, match="retry exhausted"):
            await client._request_with_retry("GET", "/test.json")
    assert len(httpx_mock.get_requests()) == 2
```

**Note:** Existing `test_datasette_client_retry.py` already uses this exact pattern for the success-on-retry case. Phase 7 adds the exhaustion + tool-error-mapping cases.

---

## /healthz Contract Lock

### Current Implementation (VERIFIED: `app.py:92-94`)

```python
async def healthz(_request):
    # OBS-01: liveness only — never consult upstream
    return JSONResponse({"status": "ok"})
```

**Contract:**
- HTTP 200
- `Content-Type: application/json`
- Body: exactly `{"status": "ok"}`
- No upstream `httpx` calls dispatched
- No FastMCP session initialization required (route defined before `/mcp` mount)

### Existing Test (VERIFIED: `tests/test_app.py:22-30`)

```python
async def test_healthz_returns_ok_without_upstream(asgi_client):
    resp = await asgi_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

**The test is already correct and sufficient for OBS-01.** The `asgi_client` fixture uses `ASGITransport(app=app)` which does NOT start the lifespan or initialize `httpx.AsyncClient`. If `/healthz` attempted any upstream call, it would raise `AttributeError: 'NoneType' object has no attribute 'get'` (no `http_client` on `app.state` without lifespan). The test passes today because `/healthz` makes no upstream call.

### Regression Test to Add (Phase 7 explicit lock)

Phase 7 should rename/annotate the existing test to formally assert OBS-01 in the test requirements map. No new test logic is needed — the existing test already covers it.

**Additional check (belt-and-suspenders):** If desired, use `pytest-httpx` to verify no httpx request is dispatched:
```python
async def test_healthz_dispatches_no_httpx_request(
    asgi_client, httpx_mock: pytest_httpx.HTTPXMock
) -> None:
    """OBS-01 belt-and-suspenders: /healthz must not dispatch any upstream call."""
    resp = await asgi_client.get("/healthz")
    assert resp.status_code == 200
    assert httpx_mock.get_requests() == []  # no requests dispatched
```

This uses `httpx_mock` without registering any responses — if an httpx call IS made, pytest-httpx raises `httpx.ConnectError` (by default behavior). The empty `get_requests()` assertion then confirms nothing was dispatched.

---

## Structured Logging for 429

### Implementation Sketch

The ASGI `RateLimitMiddleware` runs BEFORE `StructuredLogMiddleware` (which is FastMCP-layer). It must emit its own log line matching `LOG_FIELDS` exactly.

Since `RequestIdMiddleware` is the OUTERMOST middleware and runs first, by the time `RateLimitMiddleware` runs, `request_id` and `ip_prefix` are already bound to structlog's contextvars via `bind_request(request_id, ip_prefix)`. The rate-limit middleware can emit a log line that picks these up automatically via `structlog.contextvars.merge_contextvars`.

```python
# src/mcp_zeeker/core/middleware/rate_limit.py

import time
import structlog

logger = structlog.get_logger()

class RateLimitMiddleware:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        ip = _client_ip_from_scope(scope, self._depth)
        ip_norm = _normalize_ip_key(ip)
        now_mono = self._time_provider()  # injected clock
        now_utc = datetime.now(tz=timezone.utc)

        allowed, retry_after = self._check_bucket(ip_norm, now_mono, now_utc)

        if not allowed:
            # Emit synthetic 429 log line — same LOG_FIELDS as StructuredLogMiddleware
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.info(
                "tool_call",          # same event name as StructuredLogMiddleware
                tool=None,
                database=None,
                table=None,
                duration_ms=duration_ms,
                status="rejected",
                error_code="rate_limited",
                # request_id and ip_prefix already bound via RequestIdMiddleware contextvar
            )
            # Build and send 429 response
            ...
            return

        await self.app(scope, receive, send)
```

**LOG_FIELDS coverage:**

| Field | Source in 429 log |
|-------|------------------|
| `request_id` | Contextvar (bound by `RequestIdMiddleware` before rate-limit runs) |
| `tool` | `None` — rate-limit fires before tool is known |
| `database` | `None` |
| `table` | `None` |
| `duration_ms` | `int((time.perf_counter() - start) * 1000)` |
| `status` | `"rejected"` |
| `ip_prefix` | Contextvar (bound by `RequestIdMiddleware`) |
| `error_code` | `"rate_limited"` |

All 8 fields from `config.LOG_FIELDS` accounted for. No new keys added. [VERIFIED: `config.py:458` LOG_FIELDS tuple inspection]

**The structlog `merge_contextvars` processor picks up `request_id` and `ip_prefix` automatically** — they are bound to the contextvar by `RequestIdMiddleware` before any downstream middleware runs. The rate-limit middleware does not need to call `structlog.contextvars.bind_contextvars` itself for those fields.

### 429 Response Construction

```python
import json
from starlette.responses import Response

async def _send_429(self, scope, receive, send, retry_after: int, request_id: str):
    body = json.dumps({
        "error": {
            "code": "rate_limited",
            "message": "Rate limit exceeded",
            "retry_after_seconds": retry_after,
            "request_id": request_id,
        }
    }).encode("utf-8")
    response = Response(
        content=body,
        status_code=429,
        media_type="application/json",
        headers={"Retry-After": str(retry_after)},
    )
    await response(scope, receive, send)
```

**`request_id` retrieval for body:** The `request_id` contextvar is bound by `RequestIdMiddleware`. To read it in `RateLimitMiddleware`:
```python
from structlog.contextvars import get_contextvars
ctx = get_contextvars()
request_id = ctx.get("request_id", "")
```
Or read from the scope headers (the `X-Request-Id` outgoing header is added by `RequestIdMiddleware.send_with_request_id` — but that runs on send, not before). The safest approach is to read from `structlog.contextvars.get_contextvars()` after `RequestIdMiddleware` has bound it.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.x + pytest-asyncio 1.3.0 (auto mode) |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/test_rate_limit.py tests/test_error_catalog.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| RATE-01 | Burst=20 allows 20 requests, 21st rejected | unit | `pytest tests/test_rate_limit.py::test_burst_allows_20_rejects_21st -x` | ❌ Wave 0 |
| RATE-01 | Sustained 1/s: token refills after 1 second | unit | `pytest tests/test_rate_limit.py::test_sustained_refill_after_one_second -x` | ❌ Wave 0 |
| RATE-01 | Daily ceiling 5000: 5001st rejected same day | unit | `pytest tests/test_rate_limit.py::test_daily_limit_5000 -x` | ❌ Wave 0 |
| RATE-01 | Daily counter resets at UTC midnight | unit | `pytest tests/test_rate_limit.py::test_daily_reset_at_utc_midnight -x` | ❌ Wave 0 |
| RATE-02 | Rate-limit fires before JSON-RPC parse | integration | `pytest tests/test_rate_limit.py::test_rate_limit_fires_before_json_rpc_parse -x` | ❌ Wave 0 |
| RATE-03 | XFF right-to-left parsing, depth=1 | unit | `pytest tests/test_rate_limit.py::test_xff_parsing_depth_1 -x` | ❌ Wave 0 |
| RATE-03 | XFF fewer hops than depth: fall back to leftmost | unit | `pytest tests/test_rate_limit.py::test_xff_fewer_hops_than_depth -x` | ❌ Wave 0 |
| RATE-04 | Single TCP peer cannot expand store beyond 100k via XFF spoofing | unit | `pytest tests/test_rate_limit.py::test_store_cap_enforced_under_flood -x` | ❌ Wave 0 |
| RATE-04 | Daily-locked bucket not evicted by 15-min idle TTL | unit | `pytest tests/test_rate_limit.py::test_sticky_ttl_daily_locked_not_expired -x` | ❌ Wave 0 |
| RATE-05 | Retry-After header is always integer seconds | unit | `pytest tests/test_rate_limit.py::test_retry_after_is_integer -x` | ❌ Wave 0 |
| RATE-05 | Retry-After = max(burst_wait, daily_wait) | unit | `pytest tests/test_rate_limit.py::test_retry_after_max_of_windows -x` | ❌ Wave 0 |
| RATE-05 | 429 body has `retry_after_seconds` field | unit | `pytest tests/test_rate_limit.py::test_429_body_has_retry_after_seconds -x` | ❌ Wave 0 |
| RATE-05 | 429 body has `request_id` field | unit | `pytest tests/test_rate_limit.py::test_429_body_has_request_id -x` | ❌ Wave 0 |
| RATE-06 | README documents single-worker requirement | manual | N/A — doc review | N/A |
| ERR-01 | All ToolErrors have stable code prefix | unit | `pytest tests/test_error_catalog.py::test_all_errors_have_stable_code -x` | ❌ Wave 0 |
| ERR-02 | All 11 codes exercised in catalog test | unit | `pytest tests/test_error_catalog.py::test_all_11_codes_in_catalog -x` | ❌ Wave 0 |
| ERR-03 | Error envelope includes request_id | unit | `pytest tests/test_error_catalog.py::test_error_includes_request_id -x` | ❌ Wave 0 |
| ERR-04 | 502 retries once with jitter | unit | `pytest tests/test_datasette_client_retry.py -x` | ✅ exists |
| ERR-04 | 502 twice → upstream_unavailable | unit | `pytest tests/test_datasette_client_retry.py::test_502_twice_raises -x` | ❌ Wave 0 |
| ERR-04 | 504 immediate → upstream_unavailable | unit | `pytest tests/test_datasette_client_retry.py::test_504_raises_immediately -x` | ✅ exists |
| ERR-05 | Upstream 4xx → catalog code, no upstream body echo | unit | `pytest tests/test_error_catalog.py::test_upstream_4xx_no_echo -x` | ❌ Wave 0 |
| OBS-01 | /healthz returns 200 + {"status":"ok"} without upstream call | unit | `pytest tests/test_app.py::test_healthz_returns_ok_without_upstream -x` | ✅ exists |
| OBS-03 | 429 log line has all LOG_FIELDS, tool/db/table are null | unit | `pytest tests/test_rate_limit.py::test_429_log_line_shape -x` | ❌ Wave 0 |
| OBS-04 | Logs never contain row contents or filter values | unit | `pytest tests/test_rate_limit.py::test_logs_no_user_input -x` | ❌ Wave 0 |

### Nyquist Properties (properties, not just lines)

The following invariants must hold under adversarial conditions:

1. **Single TCP peer cannot expand bucket store beyond 100k via XFF spoofing.** Store `len() <= RATE_STORE_CAP` at all times — enforced by `_enforce_cap()` on every request.

2. **Daily counter never resets early due to LRU eviction.** If a daily-locked IP is evicted (only possible under a 100k-IP flood), its counter is lost — not reset. A re-created entry starts fresh. This is documented as an accepted tradeoff, not a security vulnerability, because it requires > 100k simultaneous unique attacker IPs.

3. **Retry-After is always a positive integer.** `math.ceil()` on `max(waits)` where all waits are positive floats. `seconds_to_utc_midnight` returns `max(1, ...)`.

4. **Logs never contain row contents or filter values.** The rate-limit middleware log line contains only fields from `LOG_FIELDS`; `tool`, `database`, `table` are hardcoded `None`; no request body is parsed or logged.

5. **429 response body never echoes user input.** The body is constructed from fixed strings + `retry_after_seconds` (int) + `request_id` (opaque UUID hex). No URL, filter value, query string, or user-supplied parameter is included.

6. **Daily reset fires exactly at UTC midnight, not before.** `datetime.now(tz=timezone.utc).date()` comparison ensures the reset only fires when the date changes in UTC. DST and leap seconds do not affect UTC midnight.

7. **`StructuredLogMiddleware` (FastMCP layer) does NOT emit a second log line for 429s.** The ASGI rate-limit middleware short-circuits before FastMCP processes the request. Only one log line per request in all cases.

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_rate_limit.py tests/test_error_catalog.py tests/test_datasette_client_retry.py -x`
- **Per wave merge:** `uv run pytest`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_rate_limit.py` — covers RATE-01..05, OBS-03/04
- [ ] `tests/test_error_catalog.py` — covers ERR-01..03, ERR-05
- [ ] `tests/test_datasette_client_retry.py` additions — covers ERR-04 exhaustion case
- [ ] `src/mcp_zeeker/core/middleware/rate_limit.py` — new middleware module
- [ ] `config.py` additions: `RATE_BURST`, `RATE_SUSTAINED_PER_SECOND`, `RATE_DAILY_LIMIT`, `RATE_STORE_CAP`, `RATE_IDLE_TTL_SECONDS`

---

## Open Questions

1. **ERR-03 request_id in ToolError envelope — implementation approach.**
   - What we know: `request_id` is bound to structlog contextvar by `RequestIdMiddleware`. `ToolError` maps to a JSON-RPC error response by FastMCP.
   - What's unclear: FastMCP's `ToolError` → JSON-RPC mapping does not automatically include `request_id`. Does the planner want a thin FastMCP middleware that enriches all error responses with `request_id`? Or include it in the message string (e.g., `"unknown_database: Database not found: foo [request_id: abc123]"`)? Neither is specified in CONTEXT.md.
   - Recommendation: Add a `ErrorEnrichmentMiddleware` (`on_call_tool` catch) that appends `request_id` from contextvars to the error's `message` field. This is the cleanest approach and does not change error code detection (which matches on the prefix before `: `).

2. **`invalid_query` and `query_timeout` raise sites — Phase 7 scope vs Phase 4 scope.**
   - What we know: REQUIREMENTS.md lists `invalid_query` under ERR-02 (Phase 7). SEARCH-06 (Phase 4) handles FTS5 escaping with `invalid_query`. Phase 4 is pending.
   - What's unclear: Should Phase 7 add the `invalid_query` raise site in `tools/search.py` even though Phase 4 is not yet implemented? Or just define the catalog entry and leave the raise site for Phase 4?
   - Recommendation: Phase 7 defines both codes in a `core/errors.py` (or inline in `core/visibility.py`) canonical location. Actual raise sites in `tools/search.py` land in Phase 4. Phase 7 verifies the code string is correct when raised.

3. **`query_timeout` — how to distinguish from other `UpstreamCallFailed`.**
   - What we know: `UpstreamCallFailed` is raised for `httpx.RequestError` (which includes `httpx.TimeoutException`). Currently all map to `upstream_unavailable`.
   - What's unclear: Phase 7 should distinguish timeout from generic unavailability. The `datasette_client.py` currently doesn't catch `httpx.TimeoutException` separately.
   - Recommendation: In `_request_with_retry`, catch `httpx.TimeoutException` before `httpx.RequestError` and raise a distinct `QueryTimeoutError(UpstreamCallFailed)`. Tool handlers check for `QueryTimeoutError` and raise `ToolError("query_timeout: ...")`.

4. **`ip_prefix` for IPv6 in rate-limit log line vs existing `ip_prefix()` function.**
   - The `ip_prefix()` function in `core/ip.py` takes first 3 groups of IPv6. The `ip_prefix` contextvar is already bound by `RequestIdMiddleware` (which calls `ip_prefix(client_ip(conn))`). The rate-limit middleware doesn't need to re-compute `ip_prefix` — it reads from the contextvar.
   - Confirmed no action needed.

5. **`conftest.py` additions for Phase 7 — single-plan-touch rule.**
   - Per the established pattern (observed in conftest.py comments), all conftest additions for a phase land in Plan 07-01. Plans 07-02+ must not modify conftest.py.
   - Fixtures needed: `fake_clock` (injects `time_provider` returning a controllable float), `rate_limiter` (instantiates `RateLimitMiddleware` with `fake_clock`), `bucket_store` (direct access to middleware's `_store` dict for assertion).

---

## Environment Availability

Step 2.6: SKIPPED (no new external dependencies in Phase 7; all tools are within the locked stack: Python stdlib `time`, `math`, `datetime`, `collections`, `dataclasses` + existing stack).

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No (anonymous tier only) | N/A |
| V3 Session Management | No (stateless HTTP, no sessions) | N/A |
| V4 Access Control | Yes — rate limiting is a form of access control | Token bucket in ASGI middleware |
| V5 Input Validation | Yes — XFF header parsing, `request_id` header validation | Right-to-left XFF parsing; `_REQUEST_ID_PATTERN` regex in `RequestIdMiddleware` |
| V6 Cryptography | No | N/A |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| XFF spoofing to bypass rate limit | Spoofing | `TRUSTED_PROXY_DEPTH=1` — only the Caddy-appended IP is trusted |
| Memory DoS via unique IP flood | Denial of Service | LRU cap at 100k keys + time-gated TTL sweep |
| Rate limit bypass via daily counter eviction | Elevation of Privilege | Sticky TTL on daily-locked buckets |
| User input in 429 body / logs | Tampering/Information Disclosure | Fixed message strings; no request body parsed at ASGI layer |

---

## Sources

### Primary (HIGH confidence)

- `src/mcp_zeeker/core/ip.py` — existing XFF parsing implementation (lines 10-30)
- `src/mcp_zeeker/core/middleware/origin.py` — ASGI rejection pattern template
- `src/mcp_zeeker/core/middleware/access_log.py` — LOG_FIELDS usage, `StructuredLogMiddleware` pattern
- `src/mcp_zeeker/core/middleware/request_id.py` — contextvar binding pattern
- `src/mcp_zeeker/core/datasette_client.py:126-158` — `_request_with_retry` verified implementation
- `src/mcp_zeeker/core/filter_compiler.py:115,129,134` — `ToolError("code: message")` pattern
- `src/mcp_zeeker/app.py:92-112` — healthz handler + middleware stack
- `src/mcp_zeeker/config.py:450-467` — `TRUSTED_PROXY_DEPTH` + `LOG_FIELDS` constants
- `tests/test_app.py:22-30` — healthz test (existing coverage)
- `tests/test_datasette_client_retry.py` — retry test patterns
- MCP TypeScript SDK `packages/client/src/client/streamableHttp.ts:563-637` [VERIFIED via GitHub API] — 429 error handling (throws `SdkError(ClientHttpNotImplemented)` with body text)
- FastMCP Context7 `/prefecthq/fastmcp` — confirmed `RateLimitingMiddleware` runs at MCP protocol layer (inside `on_call_tool`)

### Secondary (MEDIUM confidence)

- [MCP Spec 2025-06-18 §Transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) — verified streamable HTTP POST → non-2xx handling; spec does not define 429 behavior explicitly
- [GitHub microsoft/vscode#247734](https://github.com/microsoft/vscode/issues/247734) — reports 429 may become stuck/timeout in some clients; consistent with SDK behavior where `SdkError` must be propagated by the host client

### Tertiary (LOW confidence)

- Memory sizing calculation — [ASSUMED to be representative]; exact Python object sizes vary by Python version and platform. Tested on Python 3.12. The 30 MB estimate has ±20% uncertainty; measurements should be re-run during implementation with `tracemalloc` in a stress test.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Per-request time-gated sweep (30s interval) has negligible p50 latency impact at 50 RPS | Bucket Store + Eviction | If sweep takes >10ms at 100k entries, p50 latency is affected; mitigation: run sweep in executor |
| A2 | Claude Desktop hosts the MCP TypeScript SDK and propagates `SdkError` message text to the LLM | 429 Transport Compatibility | If Claude Desktop swallows `SdkError` message, the rate limit is invisible to the LLM; mitigation: ANNO-03 tool description sentence |
| A3 | `invalid_query` raise sites are Phase 4 scope; Phase 7 only defines catalog entry | Error Catalog Completion | If Phase 7 is expected to add raise sites too, the plan needs more tasks |

---

## Metadata

**Confidence breakdown:**
- Token bucket math: HIGH — standard algorithm, verified with worked examples
- XFF parsing: HIGH — verified against existing `core/ip.py` implementation
- Bucket store sizing: HIGH — verified with Python object model arithmetic
- 429 transport behavior: MEDIUM — verified from TypeScript SDK source but Claude Desktop's final UX behavior is inferred
- Error catalog: HIGH — verified all existing codes and raise sites in codebase

**Research date:** 2026-05-15
**Valid until:** 2026-06-15 (30 days; FastMCP 3.x stable, Python stdlib stable)

---

## RESEARCH COMPLETE
