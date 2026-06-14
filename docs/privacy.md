# Privacy Policy

[Back to docs](index.md)

*Last updated: 2026-06-14*

This privacy policy describes what data Zeeker MCP collects when you use the anonymous-tier
MCP server at `mcp.zeeker.sg`. Zeeker is a read-only connector — it does not accept user
registrations, store user data, or operate on behalf of callers beyond proxying requests to
public Singapore legal datasets at [data.zeeker.sg](https://data.zeeker.sg).

---

## 1. Data Collected

Each request to the MCP server generates one structured log line. The logged fields are exactly:

| Field logged | Description |
|--------------|-------------|
| `request_id` | Random UUID generated per request; used to correlate log entries |
| `tool` | Name of the MCP tool called (e.g. `search`, `query_table`) |
| `database` | Database name requested (e.g. `pdpc`, `zeeker-judgements`) |
| `table` | Table name requested (e.g. `enforcement_decisions`) |
| `duration_ms` | Time in milliseconds from request receipt to response sent |
| `status` | HTTP response status code (e.g. `200`, `429`, `500`) |
| `ip_prefix` | Truncated IP prefix — first three octets of the caller's IPv4 address (e.g. `203.0.113`, the `/24` network), or the `/48` network base address for IPv6. The full IP address is never retained. Inputs that do not parse as a valid IP address are recorded as the fixed sentinel `_invalid` instead. |
| `error_code` | Error code if the request failed (e.g. `rate_limited`, `unknown_table`) |

**What is NOT logged:**

- Filter values (the content of `filters[*].value` parameters)
- Search query text (the `query` parameter to `search`)
- Full IP addresses (only a truncated network prefix is retained — `/24` for IPv4, `/48` for IPv6)
- URLs passed to `fetch`
- Column projection lists
- Any other user-supplied parameter values

This design implements the no-echo guarantee from the server's injection-resistance posture:
user-supplied values are not present in any stored or transmitted log data.

### 1.1 Session-start event

Once per MCP `initialize` handshake, the server emits a single additional structured log
line (event `session_start`) so it can count connection handshakes. The logged fields are
exactly:

| Field logged | Description |
|--------------|-------------|
| `request_id` | Random UUID generated per request; used to correlate log entries |
| `ip_prefix` | Truncated IP prefix (same `/24` IPv4 / `/48` IPv6 truncation as above — never the full IP) |
| `protocol_version` | MCP protocol version advertised by the client (e.g. `2025-06-18`) |
| `client_name` | Software client identity from the handshake (e.g. `claude-ai`, `mcp-remote`) |
| `client_version` | Software client version string from the handshake |

This line records **software** client identity only — the name and version of the MCP client
program. It is **not** a user identity, account, or session token, and contains no full IP
address and no tool arguments. It is emitted exactly once per `initialize` handshake.

---

## 2. Log Retention

Log lines are retained for **30 days** on a rolling basis. After 30 days, log entries are
deleted. No log data is archived beyond this window.

Retention is enforced by two mechanisms on the host: the container's log output is rotated
by the Docker daemon at a 10 MB segment size with a maximum of 30 retained segments (a
hard upper bound on disk usage), and a daily host-side prune deletes rotated segments
whose modification time exceeds 30 days. No log shipping (rsyslog forwarding, SIEM agents,
file backup) is configured on the host, so log data does not leave the server.

---

## 3. Third-Party Data Flow

All MCP tool calls are proxied to `data.zeeker.sg` (the upstream Datasette instance hosting
the Singapore legal datasets). Requests are forwarded and responses are returned verbatim.

- No data is sent to third parties for analytics, advertising, or tracking purposes.
- No upstream request metadata is shared with any third party.
- Responses from `data.zeeker.sg` to MCP tool calls (search results, table rows, fragment
  texts, fetched URLs) are **not cached** — each tool call is a fresh upstream request,
  and no retrieved dataset content is stored on the Zeeker server.
- The upstream catalog at `data.zeeker.sg/-/metadata.json` (the list of databases, tables,
  and license strings — public, non-personal) is cached in memory with a 30-minute TTL so
  the server does not re-fetch the catalog on every tool call. This cache holds no user
  data and no upstream dataset content.

---

## 4. Cookies and Tracking

Zeeker MCP uses **no cookies**. There are no tracking pixels, analytics scripts, or
user-tracking mechanisms of any kind. The server holds no user-identifying session state.

The server runs in stateless HTTP mode and does **not** mint a protocol-level
`mcp-session-id` header; no per-connection session token is issued or retained. The
`session_start` event described in §1.1 records only software client identity (name and
version), never a user identity or account.

---

## 5. Contact

For privacy inquiries, contact: **privacy@zeeker.sg**

---

## 6. Jurisdiction

This server operates under **Singapore law**. The upstream datasets at `data.zeeker.sg` are
also hosted in Singapore under Singapore law. Any disputes regarding this privacy policy are
subject to the jurisdiction of the courts of Singapore.
