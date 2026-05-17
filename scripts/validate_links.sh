#!/usr/bin/env bash
set -euo pipefail

# scripts/validate_links.sh — verify Phase 9 docs/privacy URLs are live.
# Used by every Phase 9 slice and by the final PR-open gate.
#
# Usage:
#   bash scripts/validate_links.sh
#   ZEEKER_DOCS_HOST=https://staging.mcp.zeeker.sg bash scripts/validate_links.sh
#
# Exit: 0 if all URLs return HTTP 200; non-zero otherwise.

HOST="${ZEEKER_DOCS_HOST:-https://mcp.zeeker.sg}"

# Default URLs checked: https://mcp.zeeker.sg/docs and https://mcp.zeeker.sg/privacy
URLS=(
  "${HOST}/docs"
  "${HOST}/docs/"
  "${HOST}/privacy"
  "${HOST}/privacy/"
  "${HOST}/healthz"
)

fail=0
for url in "${URLS[@]}"; do
  status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$url" || echo "000")
  if [[ "$status" != "200" ]]; then
    echo "FAIL: $url returned HTTP $status"
    fail=1
  else
    echo "OK:   $url ($status)"
  fi
done

exit $fail
