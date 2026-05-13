"""
Pure-unit tests for qhash cursor encode/decode — Plan 03-03 (D3-03).

These tests turned GREEN once `mcp_zeeker.core.cursor` shipped. Function-body
imports (NOT module-level) were the Wave 0 RED-stub idiom — preserved on the
turn-green pass so test_cursor.py reads identically to its stub form.

Tests cover:
- Round-trip: decode_cursor(encode_cursor(shape, next), shape) == next
- Shape mismatch raises ToolError(invalid_cursor) — D3-03 anti-replay
- Malformed base64 raises ToolError(invalid_cursor) — defense-in-depth
- Empty Datasette `next` (last page) round-trips
- Canonical shape ordering: filters sorted by (column, op), columns sorted
- Cursor with length not a multiple of 4 round-trips (base64 padding safety)
- Tilde-encoded Datasette cursor (real upstream fixture) round-trips intact —
  the `|` separator is safe per 03-RESEARCH verification.
"""

from __future__ import annotations

import pytest


def test_round_trip():
    """D3-03: encode then decode returns the same datasette_next token."""
    from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor

    shape = canonical_shape_str("pdpc", "enforcement_decisions", None, [], None)
    encoded = encode_cursor(shape, "2")
    decoded = decode_cursor(encoded, shape)
    assert decoded == "2"


def test_shape_mismatch_raises_invalid_cursor():
    """D3-03: decode with a different shape rejects the cursor."""
    from fastmcp.exceptions import ToolError

    from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor

    shape_a = canonical_shape_str("pdpc", "enforcement_decisions", None, [], None)
    shape_b = canonical_shape_str("pdpc", "enforcement_decisions", "decision_date", [], None)
    encoded = encode_cursor(shape_a, "2")
    with pytest.raises(ToolError, match="invalid_cursor"):
        decode_cursor(encoded, shape_b)


def test_malformed_cursor_raises():
    """D3-03: decode rejects non-base64 / malformed tokens with invalid_cursor."""
    from fastmcp.exceptions import ToolError

    from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor

    shape = canonical_shape_str("pdpc", "enforcement_decisions", None, [], None)
    with pytest.raises(ToolError, match="invalid_cursor"):
        decode_cursor("!!!not-base64url!!!", shape)


def test_empty_datasette_next_round_trips():
    """D3-03: an empty datasette_next token (last page) round-trips cleanly."""
    from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor

    shape = canonical_shape_str("pdpc", "enforcement_decisions", None, [], None)
    encoded = encode_cursor(shape, "")
    decoded = decode_cursor(encoded, shape)
    assert decoded == ""


def test_filters_sorted_canonically():
    """D3-03: filter list order does not affect the canonical shape hash.

    Two compute orderings of the same logical filter set MUST produce the same
    canonical shape string, so a paginated cursor remains valid regardless of
    how the LLM ordered the filter clauses in the follow-up call.
    """
    from mcp_zeeker.core.cursor import canonical_shape_str

    filters_ab = [
        {"column": "title", "op": "exact", "value": "x"},
        {"column": "organisation", "op": "contains", "value": "y"},
    ]
    filters_ba = [
        {"column": "organisation", "op": "contains", "value": "y"},
        {"column": "title", "op": "exact", "value": "x"},
    ]
    shape_ab = canonical_shape_str("pdpc", "enforcement_decisions", None, filters_ab, None)
    shape_ba = canonical_shape_str("pdpc", "enforcement_decisions", None, filters_ba, None)
    assert shape_ab == shape_ba


def test_cursor_padding_safe():
    """D3-03 Pitfall 3: base64.urlsafe_b64encode().rstrip(b'=') drops trailing padding.

    The decode side must re-pad with `=` * (-len(cursor) % 4) for any cursor whose
    raw byte length is not a multiple of 3 — which is the typical case in the
    wild. This test exercises several next-token lengths to ensure encode + decode
    round-trip cleanly across the padding boundary.
    """
    from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor

    shape = canonical_shape_str("pdpc", "enforcement_decisions", None, [], None)
    # `next` token lengths 1..6 — exercises every base64 alignment.
    for next_token in ("a", "ab", "abc", "abcd", "abcde", "abcdef"):
        encoded = encode_cursor(shape, next_token)
        # Cursor SHOULD NOT carry any trailing '=' padding (RFC 4648 §3.2 url-safe).
        assert "=" not in encoded, f"encoded cursor must drop '=' padding, got {encoded!r}"
        decoded = decode_cursor(encoded, shape)
        assert decoded == next_token


def test_tilde_encoded_datasette_cursor():
    """D3-03: real Datasette `next` tokens contain commas + tilde-escaped colons.

    Fixture origin: `tests/fixtures/datasette/sglawwatch__headlines__light.json`
    captured the upstream `next` value `"2026-05-13T00~3A01~3A00,46f0249efaf2efa64b334177d1285849"`
    — verifies the `|` separator inside the qhash wrapper is safe (per
    03-RESEARCH live-probe verification: tilde-encoding never produces `|`).
    """
    from mcp_zeeker.core.cursor import canonical_shape_str, decode_cursor, encode_cursor

    shape = canonical_shape_str("sglawwatch", "headlines", "-date", [], None)
    datasette_next = "2026-05-13T00~3A01~3A00,46f0249efaf2efa64b334177d1285849"
    encoded = encode_cursor(shape, datasette_next)
    decoded = decode_cursor(encoded, shape)
    assert decoded == datasette_next
