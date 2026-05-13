# Stage 1: builder — installs dependencies into a venv using uv
FROM python:3.13-slim AS builder

# Pin uv to match CLAUDE.md research (0.11 series)
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifest first so Docker caches the dep-install layer
# separately from the source layer. uv sync --frozen fails fast if uv.lock
# drifts from pyproject.toml (CLAUDE.md research: uv docs §"uv sync --frozen").
COPY pyproject.toml uv.lock ./

# Install runtime dependencies (no dev extras) without installing the project
# itself — this primes the venv cache layer.
RUN uv sync --frozen --no-dev --no-install-project

# Now copy source and install the project in editable mode into the same venv
COPY src/ src/

RUN uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# Stage 2: runtime — minimal image, no uv, no build tools
# ---------------------------------------------------------------------------
FROM python:3.13-slim

WORKDIR /app

# Copy the pre-built venv and the source tree from the builder stage.
# No system-level apt-get install needed: the project has zero C-extension
# dependencies (NFR-04 audit-friendly footprint); certifi ships its own CA bundle.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Activate the venv by prepending it to PATH
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# RATE-06: in-memory token bucket is per-process; multi-worker would silently
# break rate-limit math (see CLAUDE.md "What NOT to Use" → gunicorn section).
# --workers 1 is MANDATORY. Do NOT change this value.
#
# --proxy-headers + --forwarded-allow-ips=*: Caddy terminates TLS and forwards
# over plain HTTP on the docker network. Without these flags, uvicorn ignores
# X-Forwarded-Proto and Starlette's auto-redirects (e.g., /mcp → /mcp/) emit
# `Location: http://...`, which MCP clients refuse to downgrade to. The trust
# scope is safe because the only network path that can reach uvicorn is the
# operator's Caddy container on the same docker network (not exposed to the
# public internet).
CMD ["uvicorn", "mcp_zeeker.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]

# Operator-visible health: docker compose shows healthy/unhealthy; Caddy startup
# probe can also poll this endpoint.
HEALTHCHECK --interval=10s --timeout=2s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()" || exit 1
