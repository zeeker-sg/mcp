"""Phase 5 — keyset cursor encode/decode unit tests.

Covers round-trip (encode → decode returns the tuple), malformed-cursor fixed
literal (D5-07), and shape-mismatch reuses Phase 3's existing fixed literal
(locked-catalog discipline — no new error code for shape drift).

Mirrors the test shape of `tests/test_cursor.py` (Phase 3) — pure tests with
function-body imports, no async needed.
"""

import pytest


def test_keyset_round_trip() -> None:
    from mcp_zeeker.core.cursor import (
        canonical_shape_str,
        decode_keyset_cursor,
        encode_keyset_cursor,
    )

    shape = canonical_shape_str("zeeker-judgements", "judgments_fragments", None, [], None)
    encoded = encode_keyset_cursor(shape, 99, "66e73dfa5db4_0099")
    last_ord, last_id = decode_keyset_cursor(encoded, shape)

    # decode returns strings (caller coerces if needed — Datasette's `_next`
    # accepts the string form per 05-RESEARCH §4.3)
    assert last_ord == "99"
    assert last_id == "66e73dfa5db4_0099"


def test_keyset_malformed_message() -> None:
    """Malformed cursor raises EXACT fixed literal — INJ-05 / D5-07 / T-03-12.

    No f-string interpolation of cursor contents anywhere in the message.
    """
    from fastmcp.exceptions import ToolError

    from mcp_zeeker.core.cursor import canonical_shape_str, decode_keyset_cursor

    shape = canonical_shape_str("zeeker-judgements", "judgments_fragments", None, [], None)

    with pytest.raises(ToolError, match=r"^invalid_cursor: keyset cursor is malformed$"):
        decode_keyset_cursor("!!!not-base64!!!", shape)


def test_keyset_shape_mismatch_reuses_phase_3_literal() -> None:
    """Shape drift between calls reuses Phase 3's existing fixed literal verbatim
    — locked-catalog discipline (D3-12 / WR-02 / D5-07): no new error code for
    shape drift; the same `invalid_cursor: cursor does not match current request
    shape` message that Phase 3's `decode_cursor` raises."""
    from fastmcp.exceptions import ToolError

    from mcp_zeeker.core.cursor import (
        canonical_shape_str,
        decode_keyset_cursor,
        encode_keyset_cursor,
    )

    shape_a = canonical_shape_str("zeeker-judgements", "judgments_fragments", None, [], None)
    shape_b = canonical_shape_str("zeeker-judgements", "judgments_fragments", None, [], ["ordinal"])
    encoded_a = encode_keyset_cursor(shape_a, 99, "x")

    with pytest.raises(
        ToolError,
        match=r"^invalid_cursor: cursor does not match current request shape$",
    ):
        decode_keyset_cursor(encoded_a, shape_b)
