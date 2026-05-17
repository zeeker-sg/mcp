# Provenance Envelope

Every Zeeker tool response is wrapped in a provenance envelope. The envelope is a JSON object
with three top-level keys:

```json
{
  "data": [...],
  "provenance": {...},
  "pagination": {...}
}
```

## Envelope Shape

### `data`

An array of row objects. The exact fields per row depend on the tool and the `columns`
parameter. Every row includes a `_citation` field at the top level (see below).

### `provenance`

Citation-ready provenance for the entire response. Shape (from `core/envelope.py`):

```json
{
  "source": "data.zeeker.sg",
  "database": "pdpc",
  "table": "enforcement_decisions",
  "retrieved_at": "2026-05-17T10:00:00Z",
  "license": "CC-BY-4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "attribution": "Zeeker (zeeker.sg) — curated Singapore legal datasets"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Always `"data.zeeker.sg"` |
| `database` | string or null | Database name; null for multi-database responses |
| `table` | string or null | Table name; null for multi-table responses |
| `retrieved_at` | ISO 8601 string | UTC timestamp when the tool call started |
| `license` | string | License name; `"mixed"` for multi-database responses |
| `license_url` | string or null | License URL; null for multi-database responses |
| `attribution` | string | Always `"Zeeker (zeeker.sg) — curated Singapore legal datasets"` |

### `pagination`

Optional. Present when the response has pagination state or search metadata.

```json
{
  "next_cursor": "eyJkaWdlc3QiOiAi...",
  "truncated": false,
  "upstream_total_hits": {},
  "failed_tables": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `next_cursor` | string or null | Opaque cursor for the next page; pass to `query_table` as `cursor` |
| `truncated` | boolean | Whether the upstream response was truncated |
| `upstream_total_hits` | object | For `search` results: `{"db.table": total_count}` per table searched |
| `failed_tables` | integer | Number of tables that failed during a `search` fan-out |

## Per-Row `_citation`

Every row in `data` includes a `_citation` field at the top level. This is a synthesized
citation string combining the row's key fields (URL, date, name, etc.) with the `retrieved_at`
timestamp. Use this as the citation anchor when quoting the row in a response.

Example: `"PDPC enforcement: Organisation X — Decision Title (2023-11-15) — https://www.pdpc.gov.sg/..."`

## `retrieved_content` — Heavy Text Separation

When heavy text columns are requested via `columns=[...]` in `query_table`, those columns
appear **nested** under a `retrieved_content` key in each row — never at the top level.

```json
{
  "title": "Breach of the PDPA by Organisation X",
  "decision_date": "2023-11-15",
  "_citation": "...",
  "retrieved_content": {
    "content_text": "The full text of the decision...",
    "_policy": {
      "source": "Personal Data Protection Commission (PDPC) Singapore",
      "license": "Singapore Open Data Licence v1.0",
      "license_url": "https://...",
      "redistribution": "allowed"
    }
  }
}
```

Heavy columns (defined in `config.HEAVY_COLUMNS`):
`content_text`, `full_text`, `html_raw`, `footnote_text`, `figure_descriptions`, `text`

The `_policy` block inside `retrieved_content` describes the content's source, license, and
whether redistribution is permitted. See [Injection Resistance](injection-resistance.md) for
why heavy columns are separated structurally.

## Factory Variants

Four factory methods construct envelopes; each corresponds to a tool type:

### `for_database_list`

Used by: `list_databases`

Multi-database response. `database=null`, `table=null`, `license="mixed"`. Per-row license
and license_url are populated individually for each database row.

### `for_table_list`

Used by: `list_tables`

Single-database, multi-table response. `database` is set; `table=null`. License sourced from
upstream metadata or `config.LICENSES` fallback.

### `for_rows`

Used by: `describe_table`, `query_table`, `fetch`

Single-database, single-table response. Both `database` and `table` are set. Includes optional
`pagination` block.

### `for_search_results`

Used by: `search`

Multi-database search response. `database=null`, `table=null`, `license="mixed"`. The
`pagination` block carries `upstream_total_hits` (per-table hit counts) and `failed_tables`
(error count). Per-row `license`, `license_url`, and `_citation` are populated by the search
fan-out orchestrator from each source table.

## Example: Full Search Envelope

```json
{
  "data": [
    {
      "title": "Breach of the PDPA by Healthcare Org",
      "date": "2023-08-10",
      "summary": "Healthcare organisation failed to protect patient data...",
      "url": "https://www.pdpc.gov.sg/decisions/...",
      "database": "pdpc",
      "table": "enforcement_decisions",
      "license": "CC-BY-4.0",
      "license_url": "https://creativecommons.org/licenses/by/4.0/",
      "_citation": "PDPC enforcement: Healthcare Org — Breach of the PDPA (2023-08-10) — https://..."
    }
  ],
  "provenance": {
    "source": "data.zeeker.sg",
    "database": null,
    "table": null,
    "retrieved_at": "2026-05-17T10:00:00Z",
    "license": "mixed",
    "license_url": null,
    "attribution": "Zeeker (zeeker.sg) — curated Singapore legal datasets"
  },
  "pagination": {
    "next_cursor": null,
    "truncated": false,
    "upstream_total_hits": {
      "pdpc.enforcement_decisions": 45
    },
    "failed_tables": 0
  }
}
```
