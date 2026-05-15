"""Unit tests for core/soak_auth.py.

Verifies:
  - Default-safe (env unset → always False)
  - Empty env → always False
  - Token mismatch → False
  - Token match → True
  - Header missing → False
  - Non-HTTP scope → False
  - Comparison is constant-time (uses hmac.compare_digest — we verify the
    function call, not micro-benchmark wall time, which is flaky on CI).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from mcp_zeeker.core.soak_auth import is_soak_authenticated


def _scope(token: str | None = None, scope_type: str = "http") -> dict:
    headers = []
    if token is not None:
        headers.append((b"x-soak-bypass", token.encode("latin-1")))
    return {"type": scope_type, "headers": headers}


def test_returns_false_when_env_unset(monkeypatch):
    monkeypatch.delenv("SOAK_BYPASS_TOKEN", raising=False)
    assert is_soak_authenticated(_scope("anything")) is False


def test_returns_false_when_env_empty(monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "")
    assert is_soak_authenticated(_scope("anything")) is False


def test_returns_false_on_mismatch(monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    assert is_soak_authenticated(_scope("wrong-token")) is False


def test_returns_true_on_match(monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    assert is_soak_authenticated(_scope("expected-token")) is True


def test_returns_false_when_header_missing(monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    assert is_soak_authenticated(_scope(token=None)) is False


def test_returns_false_for_non_http_scope(monkeypatch):
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    assert is_soak_authenticated(_scope("expected-token", scope_type="lifespan")) is False
    assert is_soak_authenticated(_scope("expected-token", scope_type="websocket")) is False


def test_uses_constant_time_compare(monkeypatch):
    """Comparison MUST go through hmac.compare_digest, not == operator.

    We patch hmac.compare_digest and assert it was called on the match path.
    """
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    with patch("mcp_zeeker.core.soak_auth.hmac.compare_digest", return_value=True) as mock:
        result = is_soak_authenticated(_scope("expected-token"))
        assert result is True
        mock.assert_called_once_with(b"expected-token", b"expected-token")


def test_header_with_undecodable_bytes_returns_false(monkeypatch):
    """A pathological header value should be rejected, not crash."""
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "expected-token")
    scope = {"type": "http", "headers": [(b"x-soak-bypass", b"\xff\xfe")]}
    # latin-1 decodes any byte sequence, so this won't UnicodeDecodeError;
    # it just decodes to non-token characters and compares unequal.
    assert is_soak_authenticated(scope) is False


@pytest.mark.parametrize(
    "presented,configured,expected",
    [
        ("a", "a", True),
        ("a", "b", False),
        ("", "", False),  # empty configured token always = unset
        ("longer-token", "longer-token", True),
        ("longer-token-mismatch", "longer-token", False),
        ("longer-token", "longer-token-mismatch", False),
    ],
)
def test_token_table(monkeypatch, presented, configured, expected):
    if configured:
        monkeypatch.setenv("SOAK_BYPASS_TOKEN", configured)
    else:
        monkeypatch.delenv("SOAK_BYPASS_TOKEN", raising=False)
    assert is_soak_authenticated(_scope(presented)) is expected


def test_non_ascii_configured_token_treated_as_unset(monkeypatch):
    """A token with a non-ASCII character (e.g., U+00E9) is treated as unset.

    Default-safe semantics: rather than crash on .encode("latin-1") for a
    high-Unicode token, _get_configured_token returns None so every request
    short-circuits to False. Operationally, openssl rand -hex 32 always
    produces ASCII, so this restriction has no real cost.
    """
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "café")  # é = U+00E9
    # Even a request that "matches" should not authenticate.
    assert is_soak_authenticated(_scope("café")) is False
    # And no request can authenticate while the env token is non-ASCII.
    assert is_soak_authenticated(_scope("anything")) is False


def test_non_ascii_above_latin1_configured_token_treated_as_unset(monkeypatch):
    """Token with chars above U+00FF (e.g., emoji) — also unset, no crash.

    Header values are latin-1 bytes per HTTP spec, so a client can't even
    transmit an emoji in the header — but the server-side encode used to
    crash before this guard. The realistic scenario is an operator typo
    that pastes a multibyte character into the env var.
    """
    monkeypatch.setenv("SOAK_BYPASS_TOKEN", "soak-🔑")
    # Any ASCII header value must fail to authenticate (server-side token
    # is non-ASCII → treated as unset).
    assert is_soak_authenticated(_scope("anything")) is False


def test_ascii_only_token_passes_isascii_check(monkeypatch):
    """Sanity: a typical openssl-rand-hex-32 token still works."""
    monkeypatch.setenv(
        "SOAK_BYPASS_TOKEN",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    assert (
        is_soak_authenticated(
            _scope("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
        )
        is True
    )
