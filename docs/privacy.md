# Privacy Policy

[Back to docs](index.md)

*Last updated: 2026-05-17*

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
| `ip_prefix` | First two octets of the caller's IP address (e.g. `203.0`) — not the full IP |
| `error_code` | Error code if the request failed (e.g. `rate_limited`, `unknown_table`) |

**What is NOT logged:**

- Filter values (the content of `filters[*].value` parameters)
- Search query text (the `query` parameter to `search`)
- Full IP addresses (only the first two octets are retained)
- URLs passed to `fetch`
- Column projection lists
- Any other user-supplied parameter values

This design implements the no-echo guarantee from the server's injection-resistance posture:
user-supplied values are not present in any stored or transmitted log data.

---

## 2. Log Retention

Log lines are retained for **30 days** on a rolling basis. After 30 days, log entries are
deleted. No log data is archived beyond this window.

---

## 3. Third-Party Data Flow

All MCP tool calls are proxied to `data.zeeker.sg` (the upstream Datasette instance hosting
the Singapore legal datasets). Requests are forwarded and responses are returned verbatim.

- No data is sent to third parties for analytics, advertising, or tracking purposes.
- No upstream request metadata is shared with any third party.
- Responses from `data.zeeker.sg` are **not cached** — each tool call is a fresh upstream
  request. No retrieved data is stored on the Zeeker server.

---

## 4. Cookies and Tracking

Zeeker MCP uses **no cookies**. There are no tracking pixels, session identifiers, analytics
scripts, or user-tracking mechanisms of any kind. The server is stateless: no session state is
maintained between requests.

---

## 5. Contact

For privacy inquiries, contact: **privacy@zeeker.sg**

---

## 6. Jurisdiction

This server operates under **Singapore law**. The upstream datasets at `data.zeeker.sg` are
also hosted in Singapore under Singapore law. Any disputes regarding this privacy policy are
subject to the jurisdiction of the courts of Singapore.
