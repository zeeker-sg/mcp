#!/usr/bin/env bash
set -euo pipefail

# scripts/deploy-docs.sh — build the MkDocs site and bind-deploy it to
# /var/www/zeeker-mcp/ for the host Caddy to serve at https://mcp.zeeker.sg/docs/.
#
# Usage:
#   bash scripts/deploy-docs.sh              # build then deploy
#   bash scripts/deploy-docs.sh --no-build   # deploy current site/ as-is
#
# Requires sudo for the rsync into /var/www and chown to caddy:caddy.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SITE_DIR="$REPO_ROOT/site"
WEB_ROOT="/var/www/zeeker-mcp"

BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=0 ;;
    -h|--help)
      sed -n '3,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

cd "$REPO_ROOT"

if [[ "$BUILD" == 1 ]]; then
  echo "==> mkdocs build"
  uv run mkdocs build --clean --strict
fi

if [[ ! -d "$SITE_DIR" ]]; then
  echo "ERROR: $SITE_DIR not found — run without --no-build, or run 'uv run mkdocs build' first." >&2
  exit 1
fi

echo "==> deploying $SITE_DIR -> $WEB_ROOT (sudo)"
sudo mkdir -p "$WEB_ROOT"
sudo rsync -a --delete "$SITE_DIR/" "$WEB_ROOT/"
sudo chown -R caddy:caddy "$WEB_ROOT"

echo "==> probe"
if curl -fsSI https://mcp.zeeker.sg/docs/ -o /dev/null; then
  echo "OK https://mcp.zeeker.sg/docs/"
else
  echo "WARN probe failed — check Caddy logs at /var/log/caddy/mcp.zeeker.sg.log" >&2
  exit 1
fi
