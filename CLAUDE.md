<!-- GSD:project-start source:PROJECT.md -->
## Project

**Zeeker MCP Connector**

A read-only remote MCP server at `mcp.zeeker.sg` that exposes the curated Singapore legal datasets at `data.zeeker.sg` (judgments, PDPC enforcement, government newsroom releases, legal commentaries) to MCP-compatible LLM clients. It translates a small, opinionated set of MCP tools into Datasette HTTP calls and applies provenance, hidden-data stripping, injection-resistance, and rate-limiting envelopes to every response. Primary consumer: Claude through the `anthropics/claude-for-legal` plugin suite.

**Core Value:** Every successful response is **citation-ready, scope-bounded, and safe to feed back into an LLM** — provenance attached, hidden internal data stripped, retrieved third-party text labeled as data rather than instructions. If everything else fails, that contract must hold.

### Constraints

- **Tech stack**: Python (managed with `uv`, pinned via `pyproject.toml` + `uv.lock`). FastMCP over Starlette/Uvicorn for MCP/HTTP. `httpx.AsyncClient` for upstream. `pydantic` for schema. No ORM, no DB driver — Why: small, audited dependency footprint and zero local state simplifies review and security posture for registry submission.
- **Tooling**: Black formatter, Ruff linter, Pytest test runner — Why: matches the conventions expected by Anthropic's open-source review.
- **Read-only**: No write paths anywhere — Why: lowers blast radius, simplifies registry review, matches the "primary sources" use case.
- **Performance**: p50 < 300 ms and p95 < 1.5 s for non-fragment tools (server-side); 50 concurrent requests handled in a single process without saturation; < 256 MB resident under steady load — Why: connector latency directly affects the agent loop UX.
- **Anonymous-tier only in v1**: 20-request burst / 60 per minute / 5,000 per IP per 24h — Why: anonymous access keeps the connector trivially adoptable; upgrade path to API keys is a function-pointer swap.
- **No data mirror**: Each tool call is a clean request/response cycle against upstream — Why: keeps the server stateless and avoids divergence from `data.zeeker.sg`.
- **Submission target**: Must be acceptable into the default `.mcp.json` of at least one `claude-for-legal` plugin — Why: that's the distribution channel; non-negotiable for success.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Executive Recommendation
## Recommended Stack
### Core Runtime
| Technology | Version | Purpose | Why (with confidence) |
|------------|---------|---------|------------------------|
| `fastmcp` | **3.2.4** (pin to `~=3.2`) | MCP framework: tool registration, transports, middleware pipeline | HIGH — Context7 (`/prefecthq/fastmcp`, 2,697 snippets) + PyPI confirm 3.2.4 released 2026-04-14. FastMCP is the canonical Python MCP framework; the project's claim of "70% of MCP servers worldwide" is repeated across third-party 2026 write-ups. **It always tracks the latest MCP spec** (currently 2025-06-18); the standalone project ships ahead of the mirror inside `mcp`. |
| `mcp` (Python SDK) | **Do not use directly as primary framework** | — | The official SDK at `mcp` 1.27.1 (PyPI, 2026-05-08) re-exports an *older snapshot* of FastMCP under `mcp.server.fastmcp`. For new servers in 2026 the upstream advice is `pip install fastmcp` (the standalone). Keep `mcp` only as a transitive dep of `fastmcp`. |
| `pydantic` | **2.13.4** (pin `~=2.13`) | Tool input/output schemas, envelope dataclasses, config validation | HIGH — PyPI confirms 2.13.4 released 2026-05-06. FastMCP generates tool input schemas from type hints + Pydantic models automatically; pinning to 2.13 ensures we get the merged `pydantic-core` (same repo since 2.13). |
| `httpx` | **0.28.1** (pin `~=0.28`) | Async HTTP client for `data.zeeker.sg/-/*.json` calls | HIGH — PyPI + the encode/httpx releases page confirm 0.28.1 (2024-12-06) is still the current stable; 1.0 is in `dev` releases through 2025 and unreleased. The async API is stable and battle-tested. |
| `starlette` | **1.0.0** (pulled in transitively by `fastmcp`; pin compat to `>=0.41,<2`) | ASGI framework underneath FastMCP's HTTP transport | HIGH — PyPI confirms Starlette 1.0.0 released 2026-03-22 (long-awaited 1.0 cut). FastMCP's `http_app()` returns a Starlette `StarletteWithLifespan` instance; mount our `/healthz` route and any custom middleware against it. |
| `uvicorn` | **0.46.0** (pin `~=0.46`) | ASGI server | HIGH — PyPI confirms 0.46.0 released 2026-04-23. Single-worker `uvicorn` is sufficient for the PRD's "50 concurrent / <256 MB / single-process" target; do **not** add gunicorn — the PRD explicitly favors a single Python process and in-memory rate limiter, which would silently break with multiple workers. |
| `structlog` | **25.5.0** (pin `~=25.5`) | Structured JSON request logs (PRD §13) | HIGH — Context7 (`/hynek/structlog`, score 92.62) + PyPI confirm 25.5.0 (2025-10-27). The processor pipeline + `contextvars`-based binding is exactly the right tool for "request_id + tool + db + table + duration + status + IP-prefix" log lines without smearing context across async tasks. |
### Supporting Libraries
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `anyio` | `>=4.5` (transitive via FastMCP/Starlette) | Concurrency primitives, structured cancellation | Pulled in by FastMCP. Use `anyio.move_on_after` for upstream-call deadlines so we never block beyond the PRD's p95 budget. |
| `orjson` | optional, `~=3.10` | Faster JSON encode for log lines and tool responses | Optional. Adds ~600 KB to the wheel set. Add only if profiling shows JSON encode is a hotspot; skip for v1 to keep the dep footprint minimal. |
### Development Tools
| Tool | Version | Purpose | Notes |
|------|---------|---------|-------|
| `uv` | **0.11.14** | Dependency management, virtualenv, lockfile, command runner | HIGH — PyPI confirms 0.11.14 (2026-05-12). The PRD already mandates `uv` + `uv.lock`. Use `uv sync --frozen --no-dev` in deployment images so CI fails fast if `uv.lock` drifts from `pyproject.toml`. |
| `ruff` | **0.15.12** | Linter + formatter | HIGH — PyPI confirms 0.15.12 (2026-04-24). `ruff format` is Black-compatible (the PRD-mandated Black behavior, but ~30× faster). `ruff check` covers `flake8` + `isort` + many `pyupgrade` rules in one binary. |
| `black` | **only if literally required by the reviewer** | Formatter | LOW value-add — Ruff's formatter is byte-identical to Black for any code you'd write here, and Anthropic's open-source repos increasingly use Ruff. Recommendation: skip `black` and use `ruff format`; if registry review complains, the swap is one-line. |
| `pytest` | `~=8.3` | Test runner | Required by PRD. |
| `pytest-asyncio` | **1.3.0** | Async test support | HIGH — PyPI confirms 1.3.0 (2025-11-10), stable. Use `asyncio_mode = "auto"` in `pyproject.toml` so any `async def test_*` is recognized. |
| `pytest-httpx` | **0.36.2** | `httpx_mock` fixture for stubbing `data.zeeker.sg` responses | HIGH — PyPI confirms 0.36.2 (2026-04-09), Python 3.10-3.14 supported. Single source of truth for upstream-API mocking; matches Datasette's JSON contract for unit tests. |
| `mypy` or `ty` | optional | Static type checking | The PRD does not require it. Add later if helpful; FastMCP's tool decorators rely on accurate type hints, so types are worth keeping clean. Astral's `ty` is preview in 2026 — defer until 1.0. |
## Installation
# Project init
# Core runtime
# Dev
### Suggested `pyproject.toml` shape
## Key Library Notes (deep dives)
### FastMCP 3.x — what to know
# Pattern A: framework-managed (simplest)
# Pattern B: explicit Starlette app + uvicorn (preferred for /healthz, custom routes, middleware)
# uvicorn.run(http_app, host="0.0.0.0", port=8000)
- **FastMCP middleware** (`mcp.add_middleware(...)`) — runs around MCP operations (`on_request`, `on_call_tool`). Use for the rate limiter, the provenance envelope wrapper, and structured logging.
- **Starlette middleware** (passed via `mcp.http_app(middleware=[...])`) — runs at HTTP layer. Use for `RealIPMiddleware` (parse `X-Forwarded-For` into `request.scope["client"]`) so the FastMCP rate limiter, which reads the client identifier via `get_client_id()`, sees the correct IP.
### httpx AsyncClient patterns
### Starlette + Uvicorn
# Production launch
### Logging — structlog vs stdlib + JSON formatter
### `pyproject.toml` / `uv.lock` patterns for a small async service
## Alternatives Considered
| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| `fastmcp` 3.2 standalone | `mcp` 1.27 SDK directly | Only if a registry reviewer explicitly objects to the FastMCP dependency — then drop to `mcp.server.fastmcp` (same API surface, older version, lower-level transports). Unlikely. |
| `ruff format` | `black` + `isort` | If a reviewer demands literal Black. Output is identical for our code. |
| FastMCP `RateLimitingMiddleware` + ~40 LOC daily cap | `throttled-py` / `pyrate-limiter` | Only if you need 4+ different limit windows or want to swap in Redis without writing a backend. For v1's 3 windows, custom code wins on dep surface. |
| Roll-your-own one-retry-with-jitter | `stamina` | When retry policy grows beyond "one retry on 5xx" — e.g., multiple upstreams, circuit breakers, per-route policies. |
| `httpx.AsyncClient` | `aiohttp` | Never for this project. httpx is the modern, typed, sync+async client; aiohttp's API is older and less ergonomic for typed code. |
| Stateless `mcp.http_app(...)` mounted in `uvicorn` | `mcp.run(transport="streamable-http")` | The convenience method works but hides the Starlette app — you can't easily add `/healthz` or custom middleware. Use only for quick local smoke tests. |
| `structlog` | `loguru` | loguru is friendlier but harder to coerce into strict, schema-stable JSON output that's grep-friendly for ops. structlog wins for production observability. |
| Single Uvicorn worker | Gunicorn + uvicorn workers | Once we move rate-limit state to Redis and want multi-process — explicitly v2 territory. |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `requests` (sync) | Blocks the event loop. Disastrous in a FastMCP async tool handler. | `httpx.AsyncClient` |
| `aiohttp` | Older API, weaker typing, no sync mirror, smaller community in MCP/LLM tooling space. | `httpx.AsyncClient` |
| FastAPI | Adds OpenAPI/routing layer we don't need on top of Starlette; FastMCP already gives us decorators. Extra dependency surface, slower cold start. | Plain Starlette via `mcp.http_app()` |
| `python-json-logger` alone | Works, but you'll hand-roll context propagation across async tasks. Painful in middleware. | `structlog` with the JSONRenderer processor |
| `tenacity` directly | Powerful but unopinionated — easy to misconfigure (e.g., retrying non-idempotent ops, no jitter). | Either ~10 LOC custom retry, or `stamina` |
| `gunicorn` with uvicorn workers | Silently multiplies the in-memory rate limit by worker count. The bug shows up under load and is hard to diagnose. | Single `uvicorn` worker for v1; Redis-backed limiter before scale-out |
| `pyrate-limiter` / `throttled-py` | Solid libraries, but FastMCP ships its own token bucket; the only PRD limit they're missing is the 24h cap, which is 40 LOC. Dep surface costs more than the code saved. | Built-in `RateLimitingMiddleware` + small daily-cap middleware |
| `mcp` SDK as primary import | The version bundled inside `mcp` 1.27 lags the standalone `fastmcp`. Mixing imports between `mcp.server.fastmcp` and `fastmcp` causes confusing duplicate-type errors. | Pin `fastmcp~=3.2`, import everything from `fastmcp.*` |
| `black` alongside Ruff format | Two formatters that agree 99.9% of the time but occasionally fight. Pick one. | `ruff format` only |
| `pydantic` v1 | EOL. Some legacy MCP examples online still use `BaseSettings` from v1 — those are stale. | `pydantic` v2.13+ (and `pydantic-settings` if config-from-env is needed) |
| `mypy` strict mode in v1 | Adds friction for a fast-moving spec-targeted service; types still matter, but enforcement can wait. | Type hints everywhere; defer enforcement to post-M9 |
## Version Compatibility Matrix
| Package | Pin | Compatible with |
|---------|-----|-----------------|
| `fastmcp` 3.2.x | `~=3.2` | `mcp >=1.20`, `starlette >=0.41`, `pydantic >=2.10`, `httpx >=0.27` (transitively pulled in via `fastmcp`'s own deps) |
| `httpx` 0.28.x | `~=0.28` | Python 3.9+; no breaking change in 0.28 vs 0.27 for our usage |
| `pydantic` 2.13.x | `~=2.13` | Python 3.9+; `pydantic-core` now bundled |
| `starlette` 1.0.0 | `>=0.41,<2` (let FastMCP pick) | Python 3.9+; 1.0 is the long-awaited stable cut and is API-compatible with the late 0.x series |
| `uvicorn` 0.46.x | `~=0.46` | Anything ASGI-3; works with Starlette 1.x |
| `structlog` 25.5.x | `~=25.5` | stdlib `logging` interop is via `structlog.stdlib`; standalone use is independent of stdlib config |
| `pytest-asyncio` 1.3.x | `~=1.3` | pytest 8.x |
| `pytest-httpx` 0.36.x | `~=0.36` | `httpx >=0.27` |
## Sources
### Authoritative (Context7 / official docs / PyPI in past 24h)
- Context7 `/prefecthq/fastmcp` — verified streamable HTTP server creation, `RateLimitingMiddleware` shape, `get_client_id` / `get_http_headers` dependency helpers, middleware ordering. HIGH confidence.
- Context7 `/modelcontextprotocol/python-sdk` — verified streamable HTTP client+server APIs, stateless mode, `streamable_http_app()` lifecycle. HIGH confidence.
- Context7 `/hynek/structlog` — verified processor pipeline + JSON renderer + contextvars binding. HIGH confidence.
- [PyPI: fastmcp 3.2.4 (2026-04-14)](https://pypi.org/project/fastmcp/)
- [PyPI: mcp 1.27.1 (2026-05-08)](https://pypi.org/project/mcp/)
- [PyPI: pydantic 2.13.4 (2026-05-06)](https://pypi.org/project/pydantic/)
- [PyPI: httpx 0.28.1 (2024-12-06, still current stable)](https://pypi.org/project/httpx/)
- [PyPI: starlette 1.0.0 (2026-03-22)](https://pypi.org/project/starlette/)
- [PyPI: uvicorn 0.46.0 (2026-04-23)](https://pypi.org/project/uvicorn/)
- [PyPI: structlog 25.5.0 (2025-10-27)](https://pypi.org/project/structlog/)
- [PyPI: ruff 0.15.12 (2026-04-24)](https://pypi.org/project/ruff/)
- [PyPI: uv 0.11.14 (2026-05-12)](https://pypi.org/project/uv/)
- [PyPI: pytest-asyncio 1.3.0 (2025-11-10)](https://pypi.org/project/pytest-asyncio/)
- [PyPI: pytest-httpx 0.36.2 (2026-04-09)](https://pypi.org/project/pytest-httpx/)
- [MCP Spec 2025-06-18 — Transports](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) — verified streamable HTTP primary, HTTP+SSE deprecated, client-side fallback protocol.
- [FastMCP docs — Middleware](https://gofastmcp.com/servers/middleware) — verified `RateLimitingMiddleware`, `SlidingWindowRateLimitingMiddleware`, `client_id_func` parameter.
- [FastMCP docs — Running Server](https://gofastmcp.com/deployment/running-server) — verified streamable HTTP transport and `http_app()` Starlette integration.
- [httpx docs — Timeouts](https://www.python-httpx.org/advanced/timeouts/) — verified four-axis timeout model (connect/read/write/pool).
- [httpx docs — Resource Limits](https://www.python-httpx.org/advanced/resource-limits/) — verified `Limits(max_connections, max_keepalive_connections, keepalive_expiry)`.
- [uv docs — Working on Projects](https://docs.astral.sh/uv/guides/projects/) — verified `uv sync --frozen` semantics and `[dependency-groups]` shape.
### Supporting (WebSearch, verified against multiple sources)
- [FastMCP 3.0 release notes (Jlowin blog)](https://jlowin.dev/blog/fastmcp-3) — context for 3.x release cadence.
- [Why MCP deprecated SSE — fka.dev](https://blog.fka.dev/blog/2025-06-06-why-mcp-deprecated-sse-and-go-with-streamable-http/) — MCP transport history.
- [Cloudflare blog — Streamable HTTP for Python MCP](https://blog.cloudflare.com/streamable-http-mcp-servers-python/) — corroborates transport choice.
- [Choosing a Python Logging Library in 2026 — Dash0](https://www.dash0.com/guides/python-logging-libraries) — corroborates structlog recommendation.
- [Stamina (Hynek Schlawack)](https://github.com/hynek/stamina) — alternative retry library.
- [Modern Python Tooling 2026 — softaims](https://softaims.com/blog/modern-python-tooling-uv-ruff-mypy-2026) — corroborates uv + ruff toolchain.
### Open questions worth re-checking at M1
- **`X-Forwarded-For` parsing.** FastMCP's `get_client_id()` default behavior on streamable HTTP is undocumented for proxy scenarios; the discussion thread on PrefectHQ/fastmcp#1400 is still open as of last check. Our custom `client_ip()` function (see "Why" section) sidesteps this, but **verify behavior end-to-end against a reverse proxy in M1 smoke test**.
- **MCP spec version targeted by FastMCP 3.2.4.** Project says "always tracks latest"; the latest stable spec is 2025-06-18. Confirm by inspecting `fastmcp.__version__` and the negotiated protocol version in initialize handshake during M1.
- **Whether `claude-for-legal` plugins have a minimum MCP spec version pin** for default `.mcp.json` entries — check the registry's CONNECTORS.md before M9 PR.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

