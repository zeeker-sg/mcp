# Changelog

## [Unreleased]

### Changed — Stateless MCP spec migration (partial)

Adopted the July 28, 2026 MCP spec revision in substance. The server was
already stateless (`stateless_http=True`, no session store, self-contained
qhash pagination cursors, TTL caches only); this release formalizes the
transition while maintaining full backwards compatibility with legacy clients.

- **Dependency bump**: `fastmcp` 3.2.4 → 3.4.7. All 579 tests pass. The
  middleware API (`Middleware`, `MiddlewareContext`, `add_middleware` FIFO
  ordering) and `http_app(path="/", stateless_http=True)` construction are
  unchanged. The FastMCP 4.x bump (which carries formal July-2026 spec
  support) is gated on its stable release — currently `4.0.0b2` on PyPI.
- **Session metric**: `SessionLogMiddleware` now emits two events:
  - `session_start` — on every MCP `initialize` handshake (legacy clients,
    unchanged from #5)
  - `first_request` — on the first non-`initialize` MCP request (new-spec
    clients that skip the handshake per the July 2026 revision)
  - `FIRST_REQUEST_FIELDS` locked field set added to `config.py`
- **Protocol string**: GET `/mcp/` status body now includes `"stateless":true`
  alongside the existing `"protocol":"2025-06-18"` advertisement
- **CORS headers**: `mcp-session-id` and `mcp-protocol-version` kept in the
  allow-list for legacy clients; removal note added with target date 2027-01
- **Documentation**: README "Stable server identifier" section rewritten for
  the new discovery mechanism; CLAUDE.md notes the targeted spec version and
  legacy-client compatibility stance

### Compatibility

- Old clients (pre-2026 spec): `initialize` handshake works as before;
  `session_start` events emitted
- New clients (July 2026 spec): no handshake needed; `first_request` events
  emitted on first tool call
- Container restart mid-conversation: no impact (stateless by construction
  since commit 4ce06d5; spec-guaranteed under the new revision)