# Zeeker MCP

**[Privacy Policy](/privacy)** — anonymous access, IP-prefix logging only, no personal data collected.

Zeeker is a read-only remote MCP server at `mcp.zeeker.sg` that exposes curated Singapore legal
datasets from [data.zeeker.sg](https://data.zeeker.sg) to MCP-compatible LLM clients. It
translates a small, opinionated set of MCP tools into Datasette HTTP calls and applies
provenance, hidden-data stripping, injection-resistance, and rate-limiting envelopes to every
response.

Every successful response is **citation-ready, scope-bounded, and safe to feed back into an
LLM** — provenance attached, hidden internal data stripped, retrieved third-party text labeled
as data rather than instructions.

## Datasets

Four Singapore legal databases are available:

| Database | Description |
|----------|-------------|
| `zeeker-judgements` | Singapore court judgments — High Court, Court of Appeal, and subordinate courts. |
| `pdpc` | PDPC enforcement decisions and advisory guidelines on Singapore personal data law. |
| `sg-gov-newsrooms` | Official Singapore government ministry and agency newsroom press releases. |
| `sglawwatch` | Curated Singapore legal commentaries, headlines, and about-Singapore-law articles. |

## MCP Endpoint

```
https://mcp.zeeker.sg/mcp
```

Transport: streamable HTTP (MCP spec 2025-06-18). No authentication required for the anonymous tier.

## Documentation

| Page | Description |
|------|-------------|
| [Tools](tools.md) | Six read-only tools: `list_databases`, `list_tables`, `describe_table`, `query_table`, `fetch`, `search` |
| [Error Catalog](errors.md) | All 11 error codes the server may emit |
| [Rate Limits](rate-limits.md) | Burst, sustained, and daily rate-limit windows |
| [Envelope](envelope.md) | Provenance envelope shape and factory variants |
| [Injection Resistance](injection-resistance.md) | Safety posture: tool trailer, structural separation, no-echo guarantee |
| [Privacy Policy](privacy.md) | Data collected, retention, contact, jurisdiction |

## Healthcheck

```
https://mcp.zeeker.sg/healthz
```

Returns HTTP 200 with `{"status": "ok"}` when the server is up.
