# Rate Limits

Zeeker applies three rate-limit windows for the anonymous tier. All limits are per IP address
(identified by the first two octets — `ip_prefix` — not the full IP).

## Limit Windows

| Window | Limit | Source constant |
|--------|-------|----------------|
| Burst (token bucket capacity) | **20 requests** | `config.RATE_BURST = 20` |
| Sustained | **60 requests per minute** (1 token per second refill) | `config.RATE_SUSTAINED_PER_SECOND = 1.0` |
| Daily ceiling | **5,000 requests per IP per 24 hours** | `config.RATE_DAILY_LIMIT = 5_000` |

The daily window resets at **00:00 UTC** every day.

## How It Works

The rate limiter uses a token-bucket algorithm for burst and sustained limits:

- Each IP starts with a full bucket of 20 tokens.
- Each request consumes 1 token.
- Tokens refill at 1 per second (up to the burst cap of 20).
- Once the daily ceiling of 5,000 is reached, all further requests are rejected until UTC midnight.

## When Limits Are Exceeded

When any limit is exceeded, the server returns:

- **HTTP 429** (Too Many Requests)
- **`Retry-After` header** set to the number of seconds until the next token is available
- **Response body:**

```json
{
  "error": "rate_limited",
  "retry_after_seconds": 42
}
```

## Retry Semantics

Clients should:

1. Check for HTTP 429 before parsing the MCP response body.
2. Read the `Retry-After` header (or `retry_after_seconds` in the JSON body).
3. Wait at least that many seconds before retrying.
4. Do **not** retry immediately — the rate limiter will reject the request again.

## Capacity Summary

| Limit | Value |
|-------|-------|
| Maximum burst | 20 requests instantaneous |
| Sustained throughput | 60 requests/minute |
| Daily ceiling | 5,000 requests/day |
| Daily reset | 00:00 UTC |
| Retry signal | `Retry-After` header + `retry_after_seconds` JSON field |

## Notes

- The limits apply to every tool call and every request to the MCP endpoint.
- The `healthz` endpoint is **not** rate-limited.
- Rate limiting is applied before tool execution — a `rate_limited` response means no tool
  was called and no upstream data was fetched.
- The `rate_limited` error code appears in the [Error Catalog](errors.md) as an ASGI-layer
  response, not a ToolError.
