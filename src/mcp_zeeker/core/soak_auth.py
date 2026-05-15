"""Soak-driver authentication — single source of truth for the soak bypass.

When a CI-driven 24h soak runs against the production endpoint, it must:
  1. Skip the rate limiter (otherwise the soak measures the limiter, not the stack).
  2. Read the server's resident memory (otherwise NFR-03 is unverifiable from GHA).

Both surfaces consult `is_soak_authenticated(scope)`. There is one source of truth
so the rate-limit bypass and the /admin/metrics endpoint cannot diverge — a
request the limiter trusts is the same request /admin/metrics trusts, and vice
versa.

Default-safe: if `SOAK_BYPASS_TOKEN` is unset OR empty in the environment, this
module returns False for every request regardless of header content. The
production container starts with the token deliberately absent; ops sets it
only when a soak is about to run, and unsets it afterwards.

Threat model:
  - Token leakage = rate-limit bypass + RSS read-out. Existing invariants
    (no write paths, no hidden-data leaks, no upstream tampering) still hold —
    a leaked token cannot widen any other surface.
  - Constant-time comparison via hmac.compare_digest prevents timing-oracle
    extraction of the token byte-by-byte.
  - The token never appears in logs (we never log the header value, only the
    "soak_bypass" flag if you choose to emit it).
"""

from __future__ import annotations

import hmac
import os

from starlette.types import Scope

_HEADER_NAME = b"x-soak-bypass"


def _get_configured_token() -> str | None:
    """Return the configured token, or None if unset/empty/non-ASCII.

    Read at call time (not module load) so tests can monkeypatch the env
    and ops can rotate the token by restarting the container.

    Why ASCII-only: every HTTP request runs this check (the rate limiter
    calls is_soak_authenticated for every request, soak-authenticated or
    not). A token with a character above U+007F would later trip
    `.encode("latin-1")` for some inputs and `hmac.compare_digest` for
    non-ASCII strings. Treating non-ASCII tokens as unset is safer:
    every request short-circuits to False (the default-safe path)
    instead of raising on encode.

    The canonical token-generation recipe (`openssl rand -hex 32`)
    produces a 64-char ASCII string, so this restriction has no
    operational cost.
    """
    token = os.environ.get("SOAK_BYPASS_TOKEN", "")
    if not token:
        return None
    if not token.isascii():
        return None
    return token


def _extract_header(scope: Scope) -> str | None:
    """Pull the X-Soak-Bypass header value from an ASGI scope, or None."""
    if scope.get("type") != "http":
        return None
    for name, value in scope.get("headers", ()):
        if name == _HEADER_NAME:
            try:
                return value.decode("latin-1")
            except UnicodeDecodeError:
                return None
    return None


def is_soak_authenticated(scope: Scope) -> bool:
    """Return True iff the request carries a valid soak-bypass token.

    Returns False when:
      - SOAK_BYPASS_TOKEN env is unset or empty (production default).
      - The X-Soak-Bypass header is missing or doesn't match.
      - The scope is not an HTTP scope.

    Comparison uses hmac.compare_digest to avoid timing oracles.
    """
    configured = _get_configured_token()
    if configured is None:
        return False
    presented = _extract_header(scope)
    if presented is None:
        return False
    # Encode to bytes before compare_digest — Python's compare_digest refuses
    # str arguments containing non-ASCII (TypeError). A pathological header
    # value with high-byte content would otherwise crash the comparison;
    # bytes-vs-bytes always works and is the documented constant-time path.
    return hmac.compare_digest(configured.encode("latin-1"), presented.encode("latin-1"))
