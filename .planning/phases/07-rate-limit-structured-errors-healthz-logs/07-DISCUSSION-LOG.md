# Phase 7: Rate limit + structured errors + healthz + logs - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-14
**Phase:** 7-rate-limit-structured-errors-healthz-logs
**Areas discussed:** Daily-counter reset boundary, /internal/upstream-status exposure model
**Areas not selected (Claude's discretion):** 429 response body shape, Logging for rate-rejected requests

---

## Initial gray-area presentation

| Option | Description | Selected |
|--------|-------------|----------|
| Daily-counter reset boundary | Rolling-24h per-IP vs fixed UTC midnight vs precise sliding window | ✓ |
| 429 response body shape | JSON-RPC-mimic vs plain error envelope vs text/plain | |
| /internal/upstream-status exposure model | Defer to v2 vs 127.0.0.1 listener vs shared-listener+secret vs query-param-gated /healthz | ✓ |
| Logging for rate-rejected requests | Synthetic structured log from ASGI middleware vs separate plain access log vs accept invisible | |

**User's choice:** Two areas selected for deep-dive. The other two flow to Claude's discretion (with researcher input).

---

## Daily-counter reset boundary

### Q1: How should the per-IP daily 5,000-request counter reset?

| Option | Description | Selected |
|--------|-------------|----------|
| Rolling 24h per-IP | Bucket tracks `daily_window_start_ts` on first request; resets after 24h elapses from that start. Fair across timezones, no global cliff. | |
| Fixed UTC midnight | All daily counters reset at 00:00 UTC globally. Trivially implementable, predictable for ops, easily documented. | ✓ |
| Rolling 24h sliding window (precise) | Per-IP timestamp deque; decay exactly 24h after each event. Most accurate but memory cost scales with request volume. | |

**User's choice:** Fixed UTC midnight (D7-01).
**Notes:** User accepted the documented tradeoff: correlated burst at 00:00 UTC for clients near their ceiling, bounded by the burst (20) + sustained (60/min) windows still in force. Simplicity and operator-explainability win for v1.

### Q2: When more than one limit is exceeded simultaneously, which Retry-After does the server send?

| Option | Description | Selected |
|--------|-------------|----------|
| The longest (max) | `max(burst_refill, daily_until_midnight)`. Conservative; well-behaved clients won't immediately re-trip. | ✓ |
| The window that triggered first | Whichever check hit first (burst → sustained → daily). Mirrors per-window behavior. | |
| The shortest (min) | `min(active waits)`. Optimistic; almost always wrong when daily is the limiter. | |

**User's choice:** Longest / max-of-active-windows (D7-02).
**Notes:** Anthropic IP allowlist clients have sticky IPs and should respect Retry-After. Sending the most pessimistic value guarantees a single retry succeeds (or fails cleanly on a different code).

### Q3: How should bucket TTL interact with the daily cap to prevent eviction-bypass?

| Option | Description | Selected |
|--------|-------------|----------|
| TTL = max(15 min, time-to-midnight) | Idle TTL stretches to cover daily reset boundary for daily-locked buckets. LRU cap (100k) remains absolute backstop. | ✓ |
| Fixed 15-min idle TTL, accept slop | Attacker spoofing 100k+ XFF values could evict daily-locked buckets and let the original IP restart. Accept as documented threat tradeoff. | |
| No TTL — LRU only | Memory drift on transient IPs; unsuitable for 24h-soak target. | |

**User's choice:** Sticky TTL = max(15 min idle, time-to-midnight) when daily-locked (D7-03).
**Notes:** Closes the eviction-bypass hole. Documented in the rate-limit module docstring as part of the explicit threat model.

---

## /internal/upstream-status exposure model

### Q1: How should the operator-only upstream-status diagnostic be exposed?

| Option | Description | Selected |
|--------|-------------|----------|
| Defer to v2 — ship without it | Operator curls `data.zeeker.sg/-/metadata.json` directly or via `docker exec`. No in-process route in v1. | ✓ |
| 127.0.0.1-only listener (extra port) | Second Starlette listener on `127.0.0.1:8001`. Clean separation, adds infra. | |
| Shared listener + shared-secret env var | `/internal/upstream-status` on `:8000`, gated by `Authorization: Bearer $ZEEKER_OPS_TOKEN`. Simpler infra, requires secret rotation. | |
| Public health-extended on /healthz with operator-only query param | `/healthz?upstream=1&token=$SECRET`. Blurs OBS-01 contract; token in access logs risk. | |

**User's choice:** Defer to v2 (D7-04).
**Notes:** Operators already have two viable out-of-band paths (external curl + docker exec). v1 minimises surface area to audit for the Anthropic registry submission.

### Q2: How does Phase 7 close OBS-02 in REQUIREMENTS.md now that the in-process endpoint is deferred?

| Option | Description | Selected |
|--------|-------------|----------|
| Reword OBS-02 to a doc obligation | Edit OBS-02 to require README documentation of the operator runbook only. No in-process endpoint mentioned. Phase 7 closes via README update. | |
| Mark OBS-02 deferred to v2 in REQUIREMENTS.md table | Keep wording, mark phase as v2 in traceability, add to Deferred Items. v1 ships with OBS-02 explicitly carried forward. | ✓ |
| Strict close — add minimal endpoint after all | Reverse D7-04; ship the 127.0.0.1-only listener. | |

**User's choice:** Mark deferred to v2 in REQUIREMENTS.md table (D7-05).
**Notes:** Honest traceability. Phase 7 verifier must explicitly allow this carry-forward when reconciling requirement coverage.

---

## Claude's Discretion

Two gray areas were not selected for user discussion and flow to Claude's discretion (with researcher input via `/gsd-research-phase 7`):

- **429 response body shape** — Default: `{"error": {"code": "rate_limited", "message": "Rate limit exceeded", "retry_after_seconds": N, "request_id": "..."}}`. Researcher should verify Claude Desktop / mcp-remote bridge tolerates this shape — if not, fallback alternative is FastMCP handling the rate-limit code (downside: contradicts RATE-02 "before JSON-RPC parsing").
- **Logging for rate-rejected requests** — Default: ASGI rate-limit middleware emits its own synthetic structured log line using the same `LOG_FIELDS` set with `tool=null, error_code=rate_limited`. Keeps 429s visible in tool-call logs without adding a parallel access-log stream.

Plus the implementation choices documented under "Claude's Discretion" in CONTEXT.md:
- Bucket math representation (single per-IP `BucketState` dataclass in `dict[str, BucketState]`)
- Test strategy for time-based behavior (inject `time_provider: Callable[[], float]`; no freezegun)
- Middleware ordering (Request-ID → Origin → RateLimit → Mount mcp_app; carry-forward from D6-10)

## Deferred Ideas

- `/internal/upstream-status` in-process endpoint → v2 (D7-04)
- API-keyed authenticated tiers → v2
- Redis-backed distributed rate limiting → v2 (RATE-06 forbids in v1)
- Per-tool rate-limit tiers → v2
- Edge-proxy rate limiting (Caddy / Cloudflare) — orthogonal infra; document only, don't implement
