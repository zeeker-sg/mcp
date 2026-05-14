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


def test_keyset_payload_never_contains_parent_pk_substring() -> None:
    """CR-01 regression — the keyset cursor MUST NOT carry the parent_pk as a
    substring of `last_id`. The handler in `tools/retrieval.py` strips the
    `f"{parent_pk}_"` prefix BEFORE calling encode_keyset_cursor; this test
    verifies the cursor module respects whatever the caller passes — i.e., if
    the caller supplies the parent_pk-stripped suffix, the encoded base64 token
    does NOT contain the parent_pk anywhere.

    Production fragment IDs follow the `<parent_pk>_<suffix>` pattern (e.g.,
    `1021426d3e2a_0099`, `ef50fb826fc3_1.1.1`, `3dcef05d-..._chunk_7`). The
    handler is the security boundary that strips the prefix; this unit-level
    test enforces the cursor module never silently re-introduces the leak.
    """
    import base64

    from mcp_zeeker.core.cursor import canonical_shape_str, encode_keyset_cursor

    shape = canonical_shape_str("zeeker-judgements", "judgments_fragments", None, [], None)
    parent_pk = "1021426d3e2a"
    # The handler passes the SUFFIX (zero-padded ordinal), not the full id.
    suffix = "0099"
    encoded = encode_keyset_cursor(shape, 99, suffix)

    # Base64 is trivially reversible — re-decode to inspect the raw payload.
    padded = encoded + "=" * (-len(encoded) % 4)
    decoded_raw = base64.urlsafe_b64decode(padded).decode()
    assert parent_pk not in decoded_raw, (
        f"FRAG-02 violation — parent_pk substring {parent_pk!r} found in "
        f"base64-decoded cursor payload {decoded_raw!r}"
    )

    # Sanity: the canonical-shape digest + the suffix ARE in the payload
    # (that's what the cursor needs to carry for Datasette _next routing).
    assert "0099" in decoded_raw  # the suffix
    assert "99," in decoded_raw  # the last_order_by + separator


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
