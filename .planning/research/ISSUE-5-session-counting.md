# Issue #5 — Counting distinct clients/sessions: approaches & recommendation

**Researched:** 2026-06-14
**Issue:** [#5 — Server has no way to count distinct clients/sessions; what to log, and the privacy trade](https://github.com/zeeker-sg/mcp/issues/5)
**Status:** Research only — no code changed. This is the "what are our options" artifact requested before implementation.
**Confidence:** HIGH on current-state facts (read from source); MEDIUM on production traffic hypotheses (need a prod log probe to confirm).

---

## 1. Problem restatement

We cannot answer "how many distinct people / sessions use `mcp.zeeker.sg`?" Three sub-questions are tangled together in the issue; this doc keeps them separate because they have different answers:

- **(Q1)** Why did `event: "tool_call"` JSON lines disappear from the docker buffer after the 2026-05-27 restart — regression, or real traffic shift?
- **(Q2)** What is the smallest change that lets us count *sessions* (not just requests)?
- **(Q3)** What does adding any per-session / per-user identifier cost us in privacy, and what retention posture should gate it?

---

## 2. Current state (grounded in code)

| Fact | Source |
|---|---|
| One JSON log line per **tool call**, emitted in a `finally` block. Event name is `"tool_call"`. Fields: `tool`, `duration_ms`, `status`, `error_code` (+ `request_id`, `ip_prefix` via contextvars). | `core/middleware/access_log.py:21-43` |
| The field set is **locked** in `config.LOG_FIELDS` and asserted by tests — extra keys are a test failure. | `config.py:488-497`, `tests/test_logging.py` |
| `request_id` and `ip_prefix` are bound to contextvars at the ASGI layer, per request. `ip_prefix` is already a /24 (IPv4) or /48 (IPv6). | `core/middleware/request_id.py:34-36`, `core/logging.py:34-39` |
| The structured-log middleware **only fires on `on_call_tool`**. There is no event on `initialize`, `tools/list`, `ping`, or any non-tool MCP message. | `server.py:22`, `access_log.py:21` |
| **The server runs `stateless_http=True`.** Chosen deliberately for restart resilience. | `app.py:38` and the comment at `app.py:34-37` |
| A `docs/privacy.md` already exists and already documents the exact logged field set + 30-day retention + "no full IP". | `docs/privacy.md` |
| No nightly digest script exists in this repo — the digest the issue refers to is host-side, outside the codebase. | repo-wide grep: no `digest` consumer in `src/` or `scripts/` |

### 2.1 The finding that reshapes the issue

Issue #5's "smallest change that would let us count" rests on this sentence:

> The MCP streamable-HTTP spec already gives us a stable per-session token: the `mcp-session-id` header.

**That token does not exist in our deployment.** We run `stateless_http=True` (`app.py:38`). In stateless mode FastMCP does **not** mint an `Mcp-Session-Id` on `initialize`, and per the streamable-HTTP spec a client must not invent one the server never issued. So:

- `mcp-session-id` will be **absent on essentially every request** we see today.
- Logging it as-is (the issue's proposed `session_id` field) would log a near-always-empty string.
- The existing `docs/privacy.md §4` already hedges this correctly ("*may* emit a protocol-level `mcp-session-id` header") — but in our config it generally won't.

The comment at `app.py:34-37` is explicit about *why* stateless was chosen: with stateful sessions, FastMCP issues `Mcp-Session-Id` and then **404s every subsequent call after a container restart** ("Session not found"), breaking every long-lived client (Claude Desktop's `mcp-remote` bridge, Claude Code) on every redeploy. Reverting that to get a session id back is a real UX regression, not a free toggle.

So the honest framing of Q2 is: **how do we count sessions without a server-issued session id, while keeping `stateless_http=True`?**

---

## 3. Q1 — the missing `tool_call` regression

Two hypotheses, and the code points at the second.

- **H1 (regression):** the post-restart build dropped/renamed the `tool_call` event.
  - *Evidence against:* `access_log.py` still emits `"tool_call"` and `tests/test_logging.py` still asserts it. Nothing in recent history (`git log`) touched the event name. Low probability.
- **H2 (real traffic shift):** post-restart traffic from claude.ai's edge is `initialize` / `tools/list` / `ping` with **no actual tool dispatch**, so the middleware — which only fires on `on_call_tool` (`access_log.py:21`) — emits nothing.
  - *Evidence for:* the buffer shows `POST /mcp/` returning 200/202 with zero `tool_call` lines. Handshake + capability-probe traffic produces exactly that signature. The `GET /mcp/` status shim (`app.py:41-56`) and `tools/list` are both invisible to our logging today.

**We currently cannot distinguish H1 from H2 from logs alone — precisely because nothing logs the non-tool messages.** This is the strongest argument for adding a `session_start` / request-level event: it's not just a counting feature, it's the instrument that closes Q1. Once `initialize` is logged, "lots of handshakes, no tool calls" becomes directly visible and H2 is confirmed or killed.

---

## 4. Q2 — approaches for counting sessions

Five options, roughly in increasing intrusiveness. Each is rated on *does it count sessions*, *privacy cost*, and *effort*.

### A1 — Log `mcp-session-id` header as-is
Add `session_id` to `LOG_FIELDS`, populate from the inbound header in `RequestIdMiddleware`.
- **Counts sessions?** ❌ Not in our config — header is absent under `stateless_http=True` (§2.1). Logs empty strings.
- **Privacy:** negligible (nothing logged).
- **Effort:** low — but it buys us nothing today. Only becomes useful if we ever go stateful (A2).
- **Verdict:** Do *not* ship alone. Keep the field plumbing in mind for the day OAuth/stateful lands.

### A2 — Turn off `stateless_http` to get a real session id
- **Counts sessions?** ✅ Server-issued, stable per connection.
- **Cost:** Reintroduces the **post-restart 404 storm** the comment at `app.py:34-37` exists to prevent. Breaks `mcp-remote` / Claude Code on every redeploy. This is a product regression, not a logging change.
- **Verdict:** ❌ Rejected unless paired with sticky/persistent session storage (a much larger piece, v2 territory).

### A3 — Emit a `session_start` event on `initialize` and count handshakes  ⭐
Add a middleware hook on the `initialize` MCP message that emits one JSON line (`event: "session_start"`) carrying the protocol version, client name/version from the `initialize` params' `clientInfo`, and the existing `request_id` + `ip_prefix`. The nightly digest counts `session_start` lines per day.
- **Counts sessions?** ✅ *Approximately but honestly.* In stateless mode a client still performs `initialize` once per logical session before it can call tools. Counting handshakes is the best available proxy for "active sessions" and needs **no session id at all**.
- **Privacy:** low — `clientInfo` is software identity ("claude-ai", "mcp-remote/x.y"), not user identity. Still no full IP, no args.
- **Effort:** medium — needs a hook on a non-tool message. FastMCP middleware exposes `on_message` / `on_request`; we filter for `method == "initialize"`. (Verify the exact hook name against the pinned FastMCP 3.2 — see Open Questions.)
- **Bonus:** directly resolves Q1 (§3).
- **Verdict:** ✅ **Recommended core of v1.** Highest signal-to-effort, lowest privacy cost, and it's the diagnostic for the regression question.

### A4 — Synthesize a pseudonymous per-handshake id, thread it onto tool calls
On `initialize`, mint a random ephemeral id, and *also* log it on subsequent `tool_call` lines so a day's tool calls can be grouped back to a handshake.
- **Problem:** under `stateless_http=True` each POST is an independent request with **no server-side connection to hang the id on.** There is no per-connection contextvar that survives across the client's separate `initialize` and `tools/call` HTTP requests. Implementing this *correctly* requires server-side session state keyed by something the client echoes — i.e. effectively A2.
- **Verdict:** ❌ Not honestly implementable while stateless. Don't fake it with `ip_prefix`+UA (that's A5, and it's worse).
- **Counts sessions?** Only if we accept the A2 cost. Defer.

### A5 — Derived fingerprint (`ip_prefix` + `User-Agent` hash) as a pseudo-session key
- **Counts sessions?** ⚠️ Poorly. claude.ai NATs through a 5-IP `/24` pool with a near-uniform UA, so the fingerprint collapses thousands of users into a handful of buckets. Over-counts on shared UAs, under-counts behind one NAT.
- **Privacy:** *higher* than it looks — a salted `hash(ip + UA)` is a stable cross-request identifier, which is a step up from the current "we can't see you" posture, for a number we already know is inaccurate.
- **Verdict:** ❌ Worst trade in the set: more privacy exposure for a less trustworthy count than A3's handshake tally.

### Summary table

| Option | Counts sessions? | Privacy cost | Effort | Keep stateless? | Recommend |
|---|---|---|---|---|---|
| A1 log `mcp-session-id` as-is | ❌ empty today | none | low | ✅ | plumb later |
| A2 disable stateless | ✅ | low | low code / **high UX** | ❌ | reject (v1) |
| **A3 `session_start` on initialize** | **✅ proxy** | **low** | **medium** | **✅** | **yes** |
| A4 synth id threaded to tool calls | only w/ A2 | low | high | ❌ | defer |
| A5 ip+UA fingerprint | ⚠️ inaccurate | medium | medium | ✅ | reject |

---

## 5. Q3 — `auth_sub`, OAuth, and the privacy step-up

`auth_sub` (the OAuth identity claim) is the one field that turns pseudonymous logs into **per-user activity records**. Two hard dependencies block it, both already flagged in the issue:

1. **OAuth discovery isn't wired up** — `/.well-known/oauth-*` all 404 today. No `auth_sub` exists to log. That's a separate, larger workstream.
2. **No retention policy is documented for per-user identifiers.** `docs/privacy.md §2` documents 30-day retention for the *current* (non-identifying) field set; it does not contemplate a per-user identifier.

**Position:** `auth_sub` is out of scope for the v1 change and must not land before (a) OAuth exists and (b) `docs/privacy.md` gains an explicit per-user retention clause (≤30 days, no long-term cross-day join). Keep `ip_prefix` at /24 — do not promote to full IP. Never log tool args / query bodies / `query` text (already the rule per `docs/privacy.md §1` and the INJ-05 no-echo guarantee).

---

## 6. Usage metrics we can derive for free (and what costs extra)

If the goal is "learn about usage" rather than "identify users," most of the value comes from aggregating logs we *already* emit. None of the following needs per-session linkage, a session id, or any new privacy exposure — they are daily `COUNT`/`GROUP BY` over existing `tool_call` lines plus the proposed `session_start` line.

### Free today (zero new code — `tool_call` already carries these)
- **Calls per tool** — `COUNT(*) GROUP BY tool`. The single most actionable usage cut: search vs query_table vs fetch vs describe/list. Already in the log (`access_log.py` logs `tool`).
- **Success/error mix** — `GROUP BY status, error_code`. Where callers hit `unknown_table`, `rate_limited`, etc.
- **Latency distribution per tool** — percentiles over `duration_ms` (this is literally what issue #6 does for `search`).
- **Coarse traffic shape** — calls per /24 per day via `ip_prefix` (blunt under NAT, but catches a single noisy non-Anthropic client).

### Free once `session_start` lands (two counts, no linkage)
- **Handshakes/day** — `COUNT(session_start)`.
- **Mean calls per session** — `COUNT(tool_call) / COUNT(session_start)`. A ratio of two independent daily counts; **no correlation between lines required**, so no privacy step-up. A very low mean (e.g. 0.05) already exposes "most sessions do nothing" — the Q1/H2 signal — without attributing any call to any session.
- **Client mix** — `GROUP BY client_name, client_version` from `session_start` (software identity, not user identity).

### What the average *cannot* show — and why that costs extra
Mean calls/session collapses the **distribution**. Recovering any of the below requires attributing each `tool_call` to a specific session, which (per §4 A4) is **not implementable under `stateless_http=True`** — it needs a client-echoed session token, i.e. the stateful-sessions project (A2) plus a persistent store, *and* a stable per-session key is itself a privacy step-up:
- **Exact zero-call bounce rate** (the precise % of sessions that never call a tool, vs. inferring it from a low mean).
- **Skew / power sessions** — whether the mean is driven by a few sessions doing 200 calls while most do 0–1.
- **Funnels / sequences** — e.g. `search → query_table → fetch` ordering within a session.
- **Per-session abuse outliers** — one session hammering, invisible in an aggregate.

**Takeaway:** for the usage goal, take the average and lean on the already-free per-tool counts. Treat per-session attribution as a separate, later, stateful project justified by a concrete distributional question — not an extension of this change.

---

## 7. Recommendation

A staged plan that delivers a real number in v1 without touching the stateless guarantee or user privacy:

**Stage 1 — Instrument & diagnose (closes Q1, foundation for Q2).**
1. Add a `session_start` event on `initialize` (Option A3): `event`, `request_id`, `ip_prefix`, `protocol_version`, `client_name`, `client_version` (from `initialize` params `clientInfo`). No user data.
2. This immediately tells us whether post-restart traffic is "all handshakes, no tools" (H2) or a real regression (H1).

**Stage 2 — Count.**
3. Nightly digest (host-side, outside this repo) counts `COUNT(*)` of `session_start` per day = "session handshakes/day". Document in `docs/` that this is a *handshake* count, not a deduplicated-human count, and why (NAT + stateless).

**Stage 3 — Docs.**
4. Update **existing** `docs/privacy.md` (the issue says "publish PRIVACY.md" — it already exists): add the `session_start` event and its fields to the §1 table; reaffirm no user identifier is logged; state the `auth_sub` precondition (OAuth + retention clause) explicitly so the bar is on record before anyone adds it.

**Deferred (not v1):** A2/A4 session-id linkage (needs persistent session store), `auth_sub` (needs OAuth). Capture as follow-up issues, not scope creep here.

### What this touches (for the eventual implementation PR)
- `config.py` — extend `LOG_FIELDS` *or* (cleaner) treat `session_start` as a distinct event with its own documented field tuple so the locked `tool_call` field-set test stays green. **Decision needed** — see Open Questions.
- `server.py` / a new `core/middleware/session_log.py` — the `initialize` hook.
- `tests/test_logging.py` — assert the new event shape and that `tool_call` is unchanged.
- `docs/privacy.md` — §1 table + `auth_sub` precondition note.

---

## 8. Open questions to resolve before / during implementation

1. **FastMCP 3.2 hook for `initialize`.** Confirm the exact middleware hook (`on_message` vs `on_request`) that fires on the `initialize` request, and that it runs in stateless mode. The pinned version is `fastmcp~=3.2` — verify against the installed wheel, not docs.
2. **Field-set test strategy.** `tool_call`'s field set is locked and test-asserted (`config.py:488`, `tests/test_logging.py`). Decide: new locked tuple `SESSION_START_FIELDS` for the new event (recommended — keeps the two events independently auditable) vs widening `LOG_FIELDS`.
3. **Confirm H2 in prod.** Before building, a one-off grep of the live docker buffer for `initialize` POST bodies (or temporarily logging the MCP method) would confirm the "handshakes, no tools" hypothesis and validate that A3 will actually produce non-zero counts.
4. **`clientInfo` reliability.** Confirm claude.ai's edge sends a usable `clientInfo.name`/`version` in `initialize` (some clients send generic values). Determines whether `session_start` can also answer "which clients".
5. **Doc location for this artifact.** Placed in `.planning/research/` to match the repo's research convention; `docs/` is the published mkdocs site with an explicit `nav`, so internal deliberation doesn't belong there.
