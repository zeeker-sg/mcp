# Phase 7: Rate limit + structured errors + healthz + logs - Context

**Gathered:** 2026-05-14
**Status:** Ready for planning

<domain>
## Phase Boundary

The server enforces anonymous-tier rate limits with correct `Retry-After` semantics, returns every error from the locked 11-code catalog with stable codes plus request ID, exposes liveness on `/healthz` without leaking upstream status, and emits structured JSON access logs that never echo user input.

**In scope:**
- ASGI rate-limit middleware (burst 20, sustained 1 tok/s, daily 5,000/IP/24h) — RATE-01..06
- XFF parsing with `TRUSTED_PROXY_DEPTH=1` (already in `config.py:450`) — RATE-03
- Bucket store with LRU cap (100k keys) + sticky TTL on daily-locked buckets — RATE-04 (+ D7-03)
- 429 response with `Retry-After` header + `retry_after_seconds` in body — RATE-05
- Locked error catalog: extend Phase 3's 6 retrieval codes to the full 11 (`unknown_database`, `invalid_query`, `query_timeout`, `rate_limited`, `upstream_unavailable`) — ERR-01..05
- 502/503 retry-once-with-jitter already done in `DatasetteClient._request_with_retry` — Phase 7 verifies + tests
- `/healthz` route already exists in `app.py:92-94` — Phase 7 locks the contract and tests
- Structured access logs already wired via `StructuredLogMiddleware`; Phase 7 extends to emit synthetic log for 429s short-circuited at ASGI layer

**Out of scope:**
- `/internal/upstream-status` in-process endpoint — deferred to v2 (D7-04)
- API-keyed authenticated tiers — v2
- Redis-backed distributed rate limiting — single-process is non-negotiable for v1 (RATE-06)
- Per-tool rate-limit tiers — single anonymous tier across all 6 tools
- Edge-proxy (Caddy/Cloudflare) rate limiting — orthogonal; document but don't implement

</domain>

<decisions>
## Implementation Decisions

### Daily-counter reset boundary
- **D7-01:** **Fixed UTC midnight reset.** All per-IP daily counters reset at 00:00 UTC globally. Bucket carries `daily_date` (UTC date); on each request, if `daily_date != today_utc()`, reset `daily_count = 0` and update `daily_date`. Trivially implementable, predictable for ops, easily documented in README ("daily counter resets at 00:00 UTC"). Accepted tradeoff: correlated burst at 00:00 UTC for clients near their ceiling — bounded by burst (20) + sustained (60/min) windows still in force.

### Retry-After semantics under multi-window exhaustion
- **D7-02:** **`Retry-After = max(active_window_waits)`.** When more than one limit is exceeded (e.g., daily 5k hit AND burst empty), send the longest wait. Pseudocode:
  ```
  waits = []
  if not burst_available: waits.append(seconds_to_next_token)
  if daily_exceeded:       waits.append(seconds_to_next_utc_midnight)
  retry_after = max(waits)  # integer seconds
  ```
  Guarantees a well-behaved client doesn't immediately re-trip. Conservative; correct for the Anthropic IP allowlist sticky-IP case where clients should respect Retry-After.

### Bucket store eviction (LRU + TTL)
- **D7-03:** **Sticky TTL for daily-locked buckets.** Idle TTL = `max(15 min, time-to-next-utc-midnight)` when `bucket.daily_exceeded` is true; standard 15-min idle TTL otherwise. LRU cap (100k keys) is the absolute backstop and is hit only under XFF-spoofing flood (covered by RATE-04 test). Daily-locked buckets are sticky until UTC midnight so an IP can't restart its daily counter by going idle for 15 minutes.

### /internal/upstream-status exposure model
- **D7-04:** **Defer the in-process endpoint to v2.** No `/internal/upstream-status` route in v1. Operator inspects upstream health via `curl https://data.zeeker.sg/-/metadata.json` from outside the container OR `docker exec <container> curl https://data.zeeker.sg/-/metadata.json` from inside. Documented in README operator section.
- **D7-05:** **REQUIREMENTS.md OBS-02 marked deferred to v2** in the traceability table; added to the Deferred Items section. Phase 7 does NOT close OBS-02 in code; v1 ships with it explicitly carried forward. Honest traceability over false-positive closure.

### Claude's Discretion (not surfaced as gray areas — researcher / planner figures out)
- **429 response body shape** — ASGI middleware short-circuits before JSON-RPC parsing, so the response is raw HTTP. Default: a small JSON object `{"error": {"code": "rate_limited", "message": "Rate limit exceeded", "retry_after_seconds": N, "request_id": "..."}}` consistent with rest-of-error shape. Researcher should verify Claude Desktop / mcp-remote bridge tolerates this shape (does it surface to the LLM, or get swallowed as transport error?) — flag any divergence before planning.
- **Logging for rate-rejected requests** — Default: the ASGI rate-limit middleware emits its own synthetic structured log line via the same `LOG_FIELDS` set with `tool=null, error_code=rate_limited`, so 429s remain visible in tool-call logs. `StructuredLogMiddleware` (FastMCP layer) never runs for these — that's expected, the synthetic line replaces it.
- **Bucket math representation** — Default: single per-IP `BucketState` dataclass with `(tokens: float, last_refill_ts: float, daily_count: int, daily_date: date, last_seen_ts: float, daily_exceeded: bool)` in a `dict[str, BucketState]` with manual LRU + TTL sweep. Researcher should size: per-bucket bytes × 100k cap = working-set ceiling under attack (target: < 32 MB for the bucket store alone).
- **Test strategy for time-based behavior** — Default: inject a `time_provider: Callable[[], float]` into the rate-limiter constructor; tests pass a fake clock. No `freezegun`, no `monotonic` monkeypatching. Matches the Phase 3 `retrieved_at` injection pattern.
- **Middleware ordering** — Default (carry-forward from D6-10): in `app.py`, ASGI middleware order is `RequestIdMiddleware` (outermost — binds `request_id` for 429 log lines too) → `OriginAllowlistMiddleware` → `RateLimitMiddleware` → mount `mcp_app`. Inside FastMCP, `RetrievedAtMiddleware` stays first. Rate-limit at ASGI fires BEFORE `RetrievedAtMiddleware`; that's intentional — RATE-02 requires it.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 7 requirements
- `.planning/REQUIREMENTS.md` §3.5 (RATE-01..06) — token bucket sizing, ASGI placement, XFF parsing, eviction, 429 contract, single-worker
- `.planning/REQUIREMENTS.md` §3.6 (ERR-01..05) — locked 11-code error catalog, request_id echo, 502/503 retry, 4xx mapping
- `.planning/REQUIREMENTS.md` §3.7 (OBS-01..05) — /healthz contract, OBS-02 deferred per D7-05, log schema field set, request_id contextvar

### Carry-forward from prior phases
- `.planning/phases/06-envelope-hardening-injection-resistance-labelling/06-CONTEXT.md` §integration points — D6-10 middleware ordering: `RetrievedAtMiddleware` first inside FastMCP layer; rate-limit at ASGI fires before it (intentional per RATE-02)
- `.planning/phases/03-structured-retrieval-url-keyed-fetch/03-CONTEXT.md` §error-catalog — D3-12 LOCKED 6-code retrieval subset; Phase 7 extends to full 11
- `.planning/PROJECT.md` §Validated — injection-resistance posture (Phase 3 INJ-05): user-supplied URL / filter values never echo into error bodies or logs. Phase 7 MUST preserve.

### Existing code touchpoints
- `src/mcp_zeeker/app.py:97-112` — Starlette middleware list; insert `RateLimitMiddleware` between `OriginAllowlistMiddleware` and `Mount("/mcp", ...)`
- `src/mcp_zeeker/app.py:92-94` — `/healthz` handler (already returns `{"status": "ok"}`); Phase 7 locks contract + tests OBS-01
- `src/mcp_zeeker/config.py:450` — `TRUSTED_PROXY_DEPTH = 1` already defined; extend to a `RATE_*` block (burst, sustained, daily, store_cap, ttl_idle_seconds)
- `src/mcp_zeeker/config.py:458` — `LOG_FIELDS` locked tuple; ASGI rate-limit middleware MUST use this exact set
- `src/mcp_zeeker/core/middleware/access_log.py:13-43` — `StructuredLogMiddleware` (FastMCP-layer). Doesn't run for 429s; new ASGI rate-limit middleware emits its own synthetic line using the same LOG_FIELDS
- `src/mcp_zeeker/core/middleware/request_id.py` — ASGI `RequestIdMiddleware` binds `request_id` contextvar; rate-limit middleware reads it for the 429 log line + body
- `src/mcp_zeeker/core/datasette_client.py:126-158` — `_request_with_retry` already implements ERR-04 (502/503 retry-once-with-jitter, 504 immediate raise). Phase 7 verifies + adds tests; no rewrite.
- `src/mcp_zeeker/core/filter_compiler.py:115,129,134,...` — `ToolError("unknown_column: ...")`, `ToolError("invalid_filter_op: ...")`. Pattern: code colon space message. Phase 7 extends to all 11 catalog codes consistently; verifies INJ-05 (no user-input echo).

### MCP / protocol references
- MCP spec 2025-06-18 §error-handling — JSON-RPC error envelope shape; Phase 7's ASGI 429 short-circuits BEFORE JSON-RPC parsing, so the 429 body is plain HTTP/JSON (not JSON-RPC). Researcher: verify Claude Desktop tolerates raw 429 with body — if not, alternative is to let FastMCP handle rate-limit error mapping (downside: 429 then runs JSON-RPC parsing, contradicting RATE-02).
- FastMCP `RateLimitingMiddleware` (`gofastmcp.com/servers/middleware`) — researcher should evaluate whether to ROLL OWN ASGI middleware (matches RATE-02 explicitly) vs USE FastMCP's built-in (runs at MCP layer, AFTER JSON-RPC parse, which violates RATE-02). Default assumption: roll own ASGI; verify in research.

### Out-of-scope but referenced
- `.planning/PROJECT.md` §Out of Scope — Redis-backed distributed rate limiting (v2)
- `.planning/PROJECT.md` §Out of Scope — authenticated tiers (v2)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `RequestIdMiddleware` (`core/middleware/request_id.py`) — already binds per-request `request_id` to contextvar via `structlog.contextvars.bind_contextvars`. The new ASGI rate-limit middleware can read it directly when emitting 429 body / log line.
- `OriginAllowlistMiddleware` (`core/middleware/origin.py`) — pattern for ASGI middleware that rejects with a custom HTTP status before reaching the mount; rate-limit middleware mirrors this shape.
- `StructuredLogMiddleware` (`core/middleware/access_log.py`) — uses `LOG_FIELDS` schema. The new synthetic log line for 429s reuses the same field set verbatim (zero new keys).
- `_SafeDict` pattern in `core/citation.py` — `defaultdict`-based safe templating; used here only if the 429 response body templates anything (unlikely — fixed shape).

### Established Patterns
- **Contextvar-based context propagation** — `request_id`, `tool_started_at` (Phase 6 `RetrievedAtMiddleware`), `MetadataCache`, `DatasetteClient`. Rate-limit state stored in a process-level singleton (no contextvar needed — ASGI middleware reads request, computes IP, looks up bucket).
- **Single-source-of-truth config** — `config.URL_COLUMNS`, `config.HIDDEN_COLUMNS`, `config.HEAVY_COLUMNS` accessed only via helper functions; AST regression test (`tests/test_config_lookup_single_source.py`) gates direct reads. Apply the same discipline to new `config.RATE_*` constants.
- **No mocking the database in tests** — `pytest-httpx` stubs upstream HTTP. Rate-limit tests use the same pattern + injected fake clock; do NOT use freezegun (matches Phase 6 `retrieved_at` middleware test pattern).
- **Atomic commits per task** — Phase 7 plans should follow the same `fix(07): ...` / `feat(07): ...` / `test(07): ...` per-task commit pattern as 6.1.

### Integration Points
- `app.py:102-110` — Starlette `middleware=[...]` list. Insert `RateLimitMiddleware` between origin and mount.
- `app.py:39-89` — `lifespan` async-context-manager. If rate-limit store needs startup/shutdown hooks (e.g., for periodic TTL sweep task), thread them here.
- `config.py` — append `RATE_BURST`, `RATE_SUSTAINED_PER_SECOND`, `RATE_DAILY_LIMIT`, `RATE_STORE_CAP`, `RATE_IDLE_TTL_SECONDS`, `RATE_DAILY_LOCK_TTL_FN` (or compute inline).
- `core/middleware/` — new `rate_limit.py` module follows the existing `*_middleware.py` shape.
- `tests/conftest.py` — add fake-clock fixture + bucket-store-binding fixture for rate-limit tests, following Phase 6's `frozen_retrieved_at` pattern.

</code_context>

<specifics>
## Specific Ideas

- **Daily reset = UTC midnight.** Operator-facing wording: "Each IP gets 5,000 requests per UTC day. The counter resets at 00:00 UTC."
- **OBS-02 honestly deferred, not silently glossed.** REQUIREMENTS.md update lands inside Phase 7 — the verifier check must specifically allow this carry-forward.
- **Sticky TTL is non-negotiable for daily-locked buckets.** Without it, an LRU eviction inside a 100k flood lets a daily-locked IP restart its day. Document the threat model in the rate-limit module docstring.
- **Synthetic 429 log line uses the same LOG_FIELDS tuple as tool-call logs** — no new keys. `tool=null, database=null, table=null, error_code=rate_limited, status=rejected` (or similar).

</specifics>

<deferred>
## Deferred Ideas

- **`/internal/upstream-status` in-process endpoint** — deferred to v2 (D7-04). Add ops-token / 127.0.0.1-listener / shared-listener model as a v2 design exercise when API-keyed tiers also land. Trigger: operator demand or post-launch incident requiring in-container upstream health checks.
- **API-keyed authenticated tiers** — v2; per-key rate limits with higher ceilings. Out of scope for v1 anonymous-only.
- **Redis-backed distributed rate limiting** — v2; required only when scaling past one uvicorn worker (RATE-06 forbids this in v1).
- **Per-tool rate-limit tiers** — v2; `search` (cross-DB) is empirically heavier than `list_databases` but single tier is fine for v1.
- **Edge-proxy rate limiting (Caddy / Cloudflare)** — orthogonal infra layer. Document in README operator section as a defense-in-depth recommendation; don't implement in this codebase.

</deferred>

---

*Phase: 7-rate-limit-structured-errors-healthz-logs*
*Context gathered: 2026-05-14*
