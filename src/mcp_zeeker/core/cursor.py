"""
qhash cursor encode / decode / canonical-shape helper — D3-03 (Plan 03-03).

This module is the SOLE producer of opaque pagination cursors used by
`query_table`. Every cursor encodes (1) an 8-byte BLAKE2b digest of the
canonical request shape and (2) the upstream Datasette `_next` token, joined
by `|` and url-safe base64-encoded. Cursor reuse with a changed request shape
(sort / filters / columns) decodes to a digest mismatch and is rejected with
`ToolError("invalid_cursor: cursor does not match current request shape")`
BEFORE any upstream call is issued.

Security properties (auditable by inspection):
- NO IO — pure functions. No httpx, no DatasetteClient access.
- The qhash is NOT a security primitive. It is a single-purpose detector of
  shape-changed cursor reuse. A tampering attacker still cannot violate
  Datasette's parameterized SQL boundary — the decoded `next` token flows
  through httpx URL-encoding and Datasette's own validation downstream.
- ToolError messages are FIXED LITERALS — no f-string interpolation of cursor
  contents (T-03-12 / INJ-05). The two strings the module ever raises are:
    "invalid_cursor: cursor is malformed"
    "invalid_cursor: cursor does not match current request shape"
- The `|` separator between digest and `_next` is safe: live-probe verification
  in 03-RESEARCH confirmed Datasette tilde-encodes ASCII `|` to `~7C` in its
  emitted next tokens, so split-on-first-`|` is unambiguous.

References: D3-03 (cursor format), D3-12 (Pagination extension), 03-RESEARCH
§"Datasette Pagination Cursor" + §"Pattern 4".
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from fastmcp.exceptions import ToolError


def canonical_shape_str(
    database: str,
    table: str,
    sort: str | None,
    filters: list[Any] | None,
    columns: list[str] | None,
) -> str:
    """Return a stable JSON serialization of the request shape (D3-03).

    The serialization is deterministic with respect to filter order and column
    order so an LLM can reorder filter clauses (or pass columns in a different
    order) on the follow-up paginated call without invalidating the cursor.

    Args:
        database: target database name (preserved as-is).
        table: target table name (preserved as-is).
        sort: sort spec — `"col"` ASC, `"-col"` DESC, or None. Leading `-` is
            preserved in the canonical shape so ASC and DESC pagination
            produce distinct cursors (a desired invariant — switching sort
            direction MUST invalidate any in-flight cursor).
        filters: list of Filter pydantic models or plain dicts. Sorted by
            (column, op) for stability. `model_dump()` is called on pydantic
            instances; plain dicts pass through unchanged.
        columns: explicit column allow-list or None. Sorted when non-None;
            `None` preserved verbatim (distinct from `[]` — empty list is
            "explicit no projection" but never reaches this path in practice).

    Returns:
        A compact JSON string (sort_keys=True, no whitespace). The string is
        fed to BLAKE2b in encode_cursor / decode_cursor; it is never exposed
        to the caller.
    """
    shape = {
        "database": database,
        "table": table,
        "sort": sort,
        "filters": sorted(
            [f if isinstance(f, dict) else f.model_dump() for f in (filters or [])],
            key=lambda d: (d["column"], d["op"]),
        ),
        "columns": sorted(columns) if columns else None,
    }
    return json.dumps(shape, sort_keys=True, separators=(",", ":"))


def encode_cursor(canonical_shape_str_value: str, datasette_next: str) -> str:
    """Encode `datasette_next` under a BLAKE2b shape digest (D3-03).

    Returns a url-safe base64 string with trailing `=` padding stripped (RFC
    4648 §3.2). `decode_cursor` re-pads on the way back.

    The digest is 16 hex characters (BLAKE2b digest_size=8 = 8 bytes = 16 hex
    chars) — small enough to keep the cursor compact, large enough that a
    random cursor has negligible chance of decoding cleanly under any shape.
    """
    digest = hashlib.blake2b(
        canonical_shape_str_value.encode(), digest_size=8
    ).hexdigest()  # 16 hex chars
    raw = f"{digest}|{datasette_next}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str, canonical_shape_str_value: str) -> str:
    """Decode `cursor`, verifying it matches the current shape (D3-03).

    Returns the unwrapped Datasette `_next` token on success. Raises
    `ToolError("invalid_cursor: ...")` with a fixed-literal message on any
    failure — the cursor contents are NEVER echoed (T-03-12 / INJ-05).

    Failure modes:
        malformed → "invalid_cursor: cursor is malformed"
            - not base64
            - decoded bytes are not utf-8
            - decoded string has no `|` separator
            - any other unpacking failure
        mismatch  → "invalid_cursor: cursor does not match current request shape"
            - decoded digest does not match BLAKE2b(canonical_shape_str_value)
    """
    try:
        # base64.urlsafe_b64decode requires multiples of 4; we strip padding
        # on encode, so re-pad here. `-len(cursor) % 4` is 0..3.
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        digest_part, datasette_next = raw.split("|", 1)
    except Exception:  # noqa: BLE001 — any decode failure → malformed
        # `from None` suppresses the original exception chain so cursor contents
        # cannot leak via __cause__ / __context__ (mirror of filter_compiler's
        # numeric-coercion `from None` discipline — T-03-12 / INJ-05).
        raise ToolError("invalid_cursor: cursor is malformed") from None

    expected = hashlib.blake2b(canonical_shape_str_value.encode(), digest_size=8).hexdigest()
    if digest_part != expected:
        raise ToolError("invalid_cursor: cursor does not match current request shape")

    return datasette_next
