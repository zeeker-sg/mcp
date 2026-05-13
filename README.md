# mcp-zeeker

A read-only remote MCP server at `mcp.zeeker.sg` that exposes the curated Singapore legal
datasets at `data.zeeker.sg` — judgments, PDPC enforcement decisions, government newsroom
releases, and legal commentaries — to MCP-compatible LLM clients. It translates a small,
opinionated set of MCP tools into Datasette HTTP calls and applies provenance, hidden-data
stripping, injection-resistance, and rate-limiting envelopes to every response. Primary
consumer: Claude through `claude-for-legal` plugin suite connections. Every successful
response is citation-ready, scope-bounded, and safe to feed back into an LLM — provenance
attached, hidden internal data stripped, retrieved third-party text labeled as data rather
than instructions.

## Quick start

Local development:

```sh
uv sync
uv run uvicorn mcp_zeeker.app:app --reload --port 8000
```

Then point a Claude client (Claude Desktop or Claude Code) at `http://127.0.0.1:8000/mcp`
for in-development testing. Production uses `https://mcp.zeeker.sg/mcp`.

## Deployment

The production topology runs the MCP container and a sibling Datasette container as
Docker services on a shared `zeeker` bridge network, fronted by a host-level Caddy reverse
proxy. Caddy is **not** in the compose file — it is a pre-existing host service managed by
the operator. `docker compose up --build` starts the `mcp` and `datasette` services; the
operator's Caddy routes external traffic to the `mcp` container's port 8000.

### Caddy expectations (operator-managed)

The operator must configure their Caddyfile to satisfy the following requirements. A
Caddyfile is **not** checked into this repository — the operator authors their own block.

- **TLS termination.** Caddy terminates TLS for `mcp.zeeker.sg` and forwards plain HTTP to
  the MCP container on port 8000 (or the host-mapped port).
- **Route `/mcp` and `/healthz`** to the MCP container (e.g. `reverse_proxy /* mcp:8000`
  or the equivalent host:port mapping if the container port is host-mapped).
- **OVERWRITE `X-Forwarded-For`, do not append.** Caddy's default `reverse_proxy` behaviour
  on a private upstream network is to overwrite the header rather than append to an existing
  one. Verify this on the deployed instance by inspecting one log line: the `ip_prefix` field
  must contain the requester's `/24`, not a chain of addresses. This is a security-relevant
  detail — if Caddy appends instead of overwrites, a hostile client can spoof XFF and poison
  the Phase 7 in-memory rate limiter.
- **Forward the `Origin` header untouched.** The MCP server's `OriginAllowlistMiddleware`
  validates it: a missing Origin is allowed (covers CLI clients and Anthropic's server-side
  proxy), `https://claude.ai` and `https://claude.com` are allowed, anything else returns
  403. If Caddy strips or rewrites the Origin header, allowlisted Claude clients will be
  rejected.

### Single-worker constraint

The production command **must** run uvicorn with `--workers 1`. The in-memory rate-limit
bucket (added in Phase 7) is per-process; running multiple workers silently divides the
effective rate-limit budget by the worker count and breaks the rate-limit contract with
upstream clients. The `Dockerfile` bakes `--workers 1` into the `CMD`; if the operator
overrides the command, they must preserve this flag. Gunicorn with uvicorn workers has the
same problem — do not use it.

### `UPSTREAM_URL`

`UPSTREAM_URL` must point at the Datasette container's internal docker-network URL (default
`http://datasette:8001`) when both containers share the `zeeker` network. Do **not** point
`UPSTREAM_URL` at the public `https://data.zeeker.sg` URL in sibling-container production
deployments — that routes traffic out through the public internet and back (hairpin routing)
unnecessarily. Use the internal docker-network address so traffic stays on the bridge.

### Anthropic IP allowlist (forward-looking)

The deployed instance must accept connections from Anthropic's published egress IP ranges to
be reachable via Claude Desktop and Claude Code. Phase 1 ships without an explicit IP
allowlist; Phase 9 (registry submission) will add the operational note and any Caddy-level
`trusted_proxies` configuration needed.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `UPSTREAM_URL` | `http://datasette:8001` | Base URL for upstream Datasette JSON endpoints |
| `USER_AGENT` | `mcp-zeeker/0.1` | Outbound HTTP User-Agent identifying our connector to upstream |

`.env.example` ships the canonical key set. Copy to `.env` for local development; production
uses the docker-compose `environment:` block or operator-managed secrets.

## Testing

Mocked unit and smoke suite (default — fast, no network):

```sh
uv run pytest -m "not live"
```

Live integration tests against `data.zeeker.sg` (requires network egress):

```sh
ZEEKER_LIVE=1 uv run pytest -m live
```

Manual end-to-end verification against the deployed instance (Phase 1 only; satisfies
TRANSPORT-05):

```
See tests/manual/PHASE1-CLIENT-VERIFY.md
```
