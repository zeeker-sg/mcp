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

Production topology on the zeeker host:

```
internet → host Caddy (TLS, mcp.zeeker.sg) → 127.0.0.1:8002 → MCP container (:8000)
```

The host runs a system-level (non-Docker) Caddy that owns ports 80/443 for all `*.zeeker.sg`
domains. The MCP container binds only to `127.0.0.1:8002` on the loopback. No inner Docker
Caddy, no cross-stack Docker networks.

### Deploying on the zeeker host

1. **Start the stack.** A gitignored `docker-compose.override.yml` is present on the server
   that sets `ports: ["127.0.0.1:8002:8000"]` and `UPSTREAM_URL: https://data.zeeker.sg`.
   Docker Compose merges it automatically:

   ```sh
   docker compose up --build -d
   ```

2. **Wire the host Caddy.** Copy the block from `Caddyfile.prod` into the operator's gitignored
   host Caddyfile, then reload:

   ```sh
   sudo systemctl reload caddy
   ```

   The block configures TLS, bot-scraper rejection, and the `X-Forwarded-For` / `Origin`
   header rules described below.

### Caddy header requirements

These rules are baked into `Caddyfile.prod` and apply to any reverse proxy configuration:

- **OVERWRITE `X-Forwarded-For`, do not append.** The MCP server's in-memory rate limiter
  reads `ip_prefix` from this header. An appended chain lets clients spoof it and bypass the
  rate limiter.
- **Forward the `Origin` header untouched.** The `OriginAllowlistMiddleware` checks it to
  gate `claude.ai` / `claude.com` clients; a missing Origin is allowed (covers CLI clients
  and Anthropic's server-side proxy), anything else returns 403. If Caddy strips or rewrites
  the Origin header, allowlisted Claude clients will be rejected.

### Single-worker constraint

The production command **must** run uvicorn with `--workers 1`. The in-memory rate-limit
bucket (added in Phase 7) is per-process; running multiple workers silently divides the
effective rate-limit budget by the worker count and breaks the rate-limit contract with
upstream clients. The `Dockerfile` bakes `--workers 1` into the `CMD`; if the operator
overrides the command, they must preserve this flag. Gunicorn with uvicorn workers has the
same problem — do not use it.

### Single-worker requirement (RATE-06)

Run with exactly one Uvicorn worker:
`uvicorn mcp_zeeker.app:app --host 0.0.0.0 --port 8000 --workers 1`. The in-memory
rate-limit bucket is per-process; running with `--workers 2` would silently multiply the
effective rate limit by 2 because each worker keeps its own bucket store — a class of bug
that only shows up under load. RATE-06 in REQUIREMENTS.md mandates `--workers 1` for v1.

Daily rate-limit counter resets at 00:00 UTC. Anonymous-tier clients near their daily
ceiling will see a correlated burst at UTC midnight; the burst (20) + sustained (60/min)
windows still apply, so this does not produce a thundering herd.

Upstream health is checked by calling `curl https://data.zeeker.sg/-/metadata.json` from
outside the container OR `docker exec <container> curl https://data.zeeker.sg/-/metadata.json`
from inside. The in-process `/internal/upstream-status` endpoint is deferred to v2 (see
D7-04). The public `/healthz` endpoint is liveness-only and never consults upstream
(OBS-01).

### `UPSTREAM_URL`

In local dev (`docker compose up` with no override) the default `http://datasette:8001`
reaches the sibling dev container on the shared `zeeker` bridge.

In production on the zeeker host, `UPSTREAM_URL` is set to `https://data.zeeker.sg` in the
gitignored `docker-compose.override.yml`. The MCP container has no shared Docker network
with the zeeker-datasette stack, so the public URL is the correct target.

### Anthropic IP allowlist

The deployed instance must accept inbound connections from Anthropic's MCP egress IP ranges
to be reachable via Claude Desktop and Claude Code. Anthropic does not (as of 2026-05) publish
a stable, machine-readable list of MCP-egress IPs; operators should:

1. Consult Anthropic's operator-facing documentation or registry-onboarding contact for the
   current allowlist.
2. Apply the allowlist at the host Caddy layer (or upstream firewall), NOT in the MCP
   container — Caddy already owns ingress per `Caddyfile.prod`.
3. Re-verify the allowlist at Phase 9 (registry submission) and quarterly thereafter; the
   IPs change without notice.

Operators who allowlist by domain rather than IP can use Anthropic's published egress hostnames
where available; this trades a lookup hop for resilience to IP churn.

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
