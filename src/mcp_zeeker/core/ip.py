# src/mcp_zeeker/core/ip.py
# Source: 01-RESEARCH.md Pattern G lines 561–600 (paste verbatim, import-path adjusted)
from __future__ import annotations

import ipaddress

from starlette.requests import HTTPConnection

from mcp_zeeker import config


def client_ip(conn: HTTPConnection) -> str:
    """
    Return the best-guess client IP, given exactly TRUSTED_PROXY_DEPTH
    trusted reverse proxies between us and the public internet.

    Phase 1: TRUSTED_PROXY_DEPTH defaults to 1 (Caddy). Phase 7 may make
    this configurable per RATE-03. We read XFF right-to-left and drop the
    trailing N trusted hops; the remaining rightmost entry is the client.
    """
    depth = getattr(config, "TRUSTED_PROXY_DEPTH", 1)
    xff = conn.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        # parts = [client, proxy1, proxy2, ...]; rightmost depth entries are trusted
        # but with depth=1 and one trusted hop that has *overwritten* XFF,
        # parts == [client_ip] and we return parts[0].
        if len(parts) <= depth:
            return parts[0] if parts else ""
        return parts[-(depth + 1)]
    # No XFF header — fall back to the immediate peer (loopback in our topology)
    return conn.client.host if conn.client else ""


def ip_prefix(ip: str) -> str:
    """OBS-04 / CR-01: validate input via ipaddress.ip_address() and return
    a sanitised prefix or the fixed sentinel "_invalid".

    Inputs that do not parse as a valid IPv4 or IPv6 address (including
    hostile XFF bytes that an attacker might send to poison structured
    logs) are replaced with the literal string "_invalid". This forecloses
    the CR-01 log-injection chain:
        attacker XFF -> client_ip -> ip_prefix -> structlog contextvar
        -> merge_contextvars -> every log line.

    Returns:
        - "" for the empty string (preserves existing "no IP" semantics).
        - "_invalid" for any non-parseable input.
        - "a.b.c" (first 3 octets) for IPv4 (/24 prefix per OBS-04).
        - Canonical /48 network base address string for IPv6 (closes WR-01
          incidentally — the previous naive colon-split produced malformed
          prefixes like "2001:db8:" for zero-compressed addresses).
    """
    if not ip:
        return ""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "_invalid"
    if isinstance(addr, ipaddress.IPv4Address):
        return ".".join(str(addr).split(".")[:3])
    # IPv6: canonical /48 network address (closes WR-01).
    return str(ipaddress.ip_network(f"{addr.exploded}/48", strict=False).network_address)


def client_ip_from_scope(scope: dict, depth: int) -> str:
    """Raw-ASGI sibling of client_ip() for middleware that operates on Scope.

    Reproduces the right-to-left XFF-parsing semantics of client_ip() but reads
    headers directly from the ASGI scope dict (no HTTPConnection construction).
    Used by RateLimitMiddleware which sits below RequestIdMiddleware in the
    Starlette middleware stack — both layers must agree on the keying IP for
    the bucket lookup and the structlog ip_prefix contextvar to align.

    Args:
        scope: ASGI scope dict; must contain "headers" (list[tuple[bytes,bytes]]).
            Optionally contains "client" (tuple[str, int]) for the TCP peer.
        depth: Number of trusted reverse-proxy hops (config.TRUSTED_PROXY_DEPTH).

    Returns:
        The best-guess client IP string. Empty string when no XFF header AND no
        scope client tuple is available. Caller is responsible for handling the
        empty string (RateLimitMiddleware substitutes "_unknown" for keying).
    """
    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
    }
    xff = headers.get("x-forwarded-for", "")
    if xff:
        # Strip optional port (e.g. "1.2.3.4:80" → "1.2.3.4", "[::1]:8080" → "::1")
        raw_parts = [p.strip() for p in xff.split(",") if p.strip()]
        parts: list[str] = []
        for rp in raw_parts:
            if rp.startswith("["):
                # IPv6 bracket notation — strip brackets and trailing "]:port"
                end = rp.find("]")
                parts.append(rp[1:end] if end != -1 else rp)
            elif ":" in rp:
                # IPv4 with port — drop everything after last colon
                parts.append(rp.rsplit(":", 1)[0])
            else:
                parts.append(rp)
        # parts = [client, proxy1, proxy2, ...]; rightmost depth entries are trusted.
        # With depth=1 and a single Caddy hop that has overwritten XFF, parts ==
        # [client_ip] and we return parts[0].
        if len(parts) <= depth:
            return parts[0] if parts else ""
        return parts[-(depth + 1)]
    # No XFF header — fall back to the immediate peer (Caddy in production, the
    # ASGITransport in tests).
    client = scope.get("client")
    return client[0] if client else ""


def _normalize_ip_key(ip: str) -> str:
    """Strip matching IPv6 brackets so dict-keying is consistent.

    XFF entries occasionally arrive as `[::1]:8080` or `[::1]`. The rate-limit
    bucket store keys on the bare IP form so a bracketed and an unbracketed
    duplicate of the same IPv6 client share one bucket.
    """
    if not ip:
        return ip
    if ip.startswith("[") and ip.endswith("]"):
        return ip[1:-1]
    return ip
