# Soak Report

## Latency
- p50: 6886.6 ms
- p95: 27102.3 ms
- max: 48562.1 ms
- samples: 39050

## RSS
- max: 102.7 MB
- samples: 328

## Errors (by class)
- ok: 39038
- rate_limited: 0
- pool_timeout: 0
- request_timeout: 6
- 5xx: 6
- 4xx: 0

## Daily Rollover
- observed: False
- reason: no 429 events recorded — cannot detect rollover

## Threshold Breaches
- p50_ms=6886.6 > limit 300
- p95_ms=27102.3 > limit 1500
