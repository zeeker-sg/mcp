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
