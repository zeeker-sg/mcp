# Phase 1 Manual Client Verification — TRANSPORT-05

Walk this checklist against the DEPLOYED instance at `https://mcp.zeeker.sg/mcp`. Do NOT
use `localhost` — the point is to prove DNS + TLS + Caddy + the docker-network
sibling-container path all work end-to-end.

## Pre-conditions

- [ ] `mcp.zeeker.sg` resolves to the operator's host
- [ ] `https://mcp.zeeker.sg/healthz` returns HTTP 200 with body `{"status":"ok"}` (verify with `curl -sf https://mcp.zeeker.sg/healthz`)
- [ ] One log line on the deployed instance shows the requester's /24 in `ip_prefix` (proves Caddy XFF overwrite semantics — VALIDATION.md operator-side check)
- [ ] A trailing-slash redirect from `/mcp` preserves HTTPS (uvicorn `--proxy-headers` flag honored):

  ```
  curl -sI -X POST https://mcp.zeeker.sg/mcp | grep -i ^location
  ```

  Must return `location: https://mcp.zeeker.sg/mcp/` — NOT `http://`. If it returns `http://`, the Dockerfile is missing `--proxy-headers --forwarded-allow-ips=*`.

- [ ] `initialize` handshake completes over real HTTP (NOT one-shot `tools/list` — MCP requires session handshake first):

  ```
  curl -sN -X POST -H 'Accept: application/json, text/event-stream' \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual-curl","version":"0.1"}}}' \
    https://mcp.zeeker.sg/mcp/ -D -
  ```

  Must return HTTP 200 with a `mcp-session-id:` response header. The body is an SSE stream containing the `initialize` result. (For programmatic `tools/list` calls, capture that session ID and resend with the `Mcp-Session-Id` header — Claude Desktop and Claude Code handle this for you.)

## Claude Desktop

1. Open `claude_desktop_config.json` (Settings → Developer → Edit Config)
2. Add to `mcpServers`:
   ```json
   {
     "zeeker": {
       "url": "https://mcp.zeeker.sg/mcp"
     }
   }
   ```
3. Restart Claude Desktop
4. Open a new chat, type: `What databases do you have access to via zeeker?`
5. Claude should call `list_databases` and present the four DBs with descriptions + `table_count` values
6. Screenshot the response (full window). Commit to `evidence/01-skeleton/claude-desktop-list-databases.png`

## Claude Code

1. From the project root in a terminal: `claude mcp add zeeker https://mcp.zeeker.sg/mcp`
2. Confirm registration: `claude mcp list` should show `zeeker` with status `connected`
3. Open Claude Code, type: `What Singapore legal databases can I query through zeeker?`
4. Claude should call `list_databases` and present the four DBs
5. Screenshot the response. Commit to `evidence/01-skeleton/claude-code-list-databases.png`

## Acceptance

Both clients return exactly the four DBs (`zeeker-judgements`, `pdpc`, `sg-gov-newsrooms`,
`sglawwatch`) with non-empty descriptions and `table_count > 0` for each. Both screenshots
committed under `evidence/01-skeleton/`.

## Troubleshooting

- **403 on POST /mcp**: Caddy is appending instead of overwriting XFF, OR a foreign Origin
  reached the allowlist. Check Caddy `reverse_proxy` block (see README "Deployment").
- **500 on first POST /mcp**: the nested `mcp_app.lifespan(mcp_app)` is not running
  (Pitfall 1). Restart container; if it persists, check `mcp_zeeker/app.py` for the nested
  `async with mcp_app.lifespan(mcp_app):` line.
- **`table_count` is 0 for all DBs**: `UPSTREAM_URL` is misconfigured or upstream Datasette
  is unreachable. Check `docker compose logs mcp` for `UpstreamCallFailed` lines.
- **Empty `description` for any DB**: `config.DATABASE_DESCRIPTIONS` has a missing key. Plan
  02 should have populated all four; verify with
  `uv run python -c "from mcp_zeeker import config; print(config.DATABASE_DESCRIPTIONS)"`.
