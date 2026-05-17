# Error Catalog

Zeeker emits exactly 11 error codes. The error codes are locked — the server will never emit a
code outside this catalog. Most errors are returned as MCP `ToolError` responses. The
`rate_limited` code is the exception: it is returned as an HTTP 429 response by the ASGI
rate-limit middleware **before** any tool is called — it is never a ToolError.

## Error Codes

| Code | Where raised | Trigger | Meaning |
|------|-------------|---------|---------|
| `unknown_database` | `core/visibility.py:raise_unknown_database` | `database` parameter not in the four allowed databases | The requested database does not exist or is not accessible |
| `unknown_table` | `core/visibility.py:raise_unknown_table` | `table` parameter not found among visible tables (includes hidden tables — no side-channel) | The requested table does not exist or is not accessible |
| `unknown_column` | `core/visibility.py:raise_unknown_column`, `core/filter_compiler.py:115` (defense-in-depth) | Column name in `filters`, `sort`, or `columns` not in the table's available columns | The requested column does not exist or is not accessible |
| `invalid_filter_op` | `core/filter_compiler.py` (multiple sites) | Unrecognized `op` value in a filter clause | The filter operator is not one of the 13 supported operators |
| `invalid_cursor` | `tools/retrieval.py` (multiple sites) | Opaque cursor malformed, expired, or provided with a different query shape | The pagination cursor is invalid for this query |
| `invalid_query` | `core/visibility.py:raise_invalid_query`, `tools/search.py` | Empty search query, or all-upstream-400 result from FTS5 | The query is malformed or empty |
| `unsupported_table_for_fetch` | `core/visibility.py:raise_unsupported_table_for_fetch` | `fetch` called on a table without a URL column | This table does not support single-record fetch by URL |
| `not_found` | `core/visibility.py:raise_not_found` | `fetch` called with a URL that matches no row | No record was found at the given URL |
| `query_timeout` | `core/errors.py:raise_query_timeout`, `core/datasette_client.py` | Upstream Datasette request timed out | The upstream query exceeded the server's timeout budget |
| `rate_limited` | `core/middleware/rate_limit.py` (ASGI 429 body only — **never a ToolError**) | Request count exceeds burst, sustained, or daily limit | See [Rate Limits](rate-limits.md) for retry semantics |
| `upstream_unavailable` | `core/errors.py:raise_upstream_unavailable`, `tools/retrieval.py:263,493,716`, `tools/search.py:209` | Upstream Datasette returned 5xx or failed after retry | The upstream data source is temporarily unavailable |

## Error Response Shape

All ToolErrors (all codes except `rate_limited`) are returned as MCP error responses:

```json
{
  "error": {
    "code": -32000,
    "message": "unknown_table: Table 'foo' not found"
  }
}
```

## rate_limited Response Shape

HTTP 429 with body:

```json
{
  "error": "rate_limited",
  "retry_after_seconds": 42
}
```

The `Retry-After` HTTP header is also set to the same value in seconds. See
[Rate Limits](rate-limits.md) for the full retry semantics.

## Security Note

Error messages contain only structural identifiers (database names, table names, column names,
operator names). User-supplied filter values, query strings, and URL parameters are **never**
echoed in error messages or log lines. This prevents injection of hostile content into error
responses that might be fed back to an LLM (INJ-05).
