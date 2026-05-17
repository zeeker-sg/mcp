"""
Pre-coerce JSON-encoded string forms of list-typed tool params (WR-260517-dvf).

Some MCP clients (observed in a real Claude session on `mcp.zeeker.sg`,
2026-05-17) `JSON.stringify` complex tool args before dispatch. Pydantic 2.13
then rejects those args with `type=list_type, input_type=str` because the
declared param type is `list[...]` — the agent reads the error as a framework
quirk and silently falls back to web search, so the user loses access to the
curated Singapore-legal datasets.

This helper is installed as a `BeforeValidator` on the three affected param
annotations:
  - `search.databases`        — `list[str] | None`
  - `query_table.filters`     — `list[Filter] | None`
  - `query_table.columns`     — `list[str] | None`

Invariants:
  1. Non-string input is returned unchanged → no behavioral change for callers
     that already pass a list (or `None`).
  2. String input that fails `json.loads` is returned unchanged → pydantic's
     standard `list_type` error fires verbatim against the original string,
     so malformed JSON is still rejected with the same canonical error.

No new failure mode, no new dependency (`json` is stdlib), no schema change
visible to MCP clients (`BeforeValidator` is invisible to pydantic JSON
Schema generation).
"""

from __future__ import annotations

import json

# WR-260517-dvf: some MCP clients (observed in real Claude sessions)
# JSON.stringify list-typed args. Pydantic 2.13 then rejects them with
# `type=list_type, input_type=str`. Pre-coerce strings via json.loads
# so successful decodes flow through pydantic's normal list validation;
# malformed input falls through to pydantic's standard list_type error.
# No recursion (single decode attempt), no type narrowing, no logging.


def _coerce_json_list(v: object) -> object:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, json.JSONDecodeError):
            return v
    return v
