"""
SQLite FTS5 phrase-wrap escape — pure-function security boundary (D4-08, SEARCH-06).

This module is the SOLE FTS5 query-string escape implementation. Mirroring the
single-call-site discipline of `core/cursor.py` and `core/filter_compiler.py`,
no handler may inline-construct an FTS5 phrase wrap; every `_search=` parameter
flows through `escape_fts5()` first.

Security properties (auditable by inspection):
- NO IO — pure stdlib string operation. No httpx, no DatasetteClient access,
  no logging.
- NEVER raises — empty/whitespace input is the handler's responsibility per
  D4-19 step 1 (`if not query.strip(): raise_invalid_query()` fires BEFORE
  this function is called). The empty-string case ("" → '""') would produce
  an FTS5 syntax error upstream if not gated; that's a handler invariant,
  not this module's concern.
- The double-quote phrase wrap is the FTS5 spec's documented escape:
  wrapping in `"..."` makes the entire query a phrase (token-by-token AND),
  and doubling any embedded `"` to `""` is the only character that needs
  escaping inside a phrase. FTS5 operators (NEAR, OR, AND, `:column:`, `*`,
  parentheses, asterisks) are all neutralized once inside a phrase.
- 04-RESEARCH §3.6 verified all 13 corpus inputs upstream against the live
  Datasette `data.zeeker.sg` deployment — no operator slipped through.

References: D4-08 (FTS5 phrase wrap), SEARCH-06 (escape contract), 04-RESEARCH
§3.6 (13-input corpus verdict).
"""

from __future__ import annotations


def escape_fts5(query: str) -> str:
    """Wrap user query as an FTS5 phrase to neutralize operators (D4-08).

    Wraps the entire query in double quotes (FTS5 phrase syntax) and doubles
    any embedded double-quote characters (the only character that needs
    escaping inside an FTS5 phrase). Operators like NEAR, OR, AND, `:column:`,
    `*`, parentheses are all treated as literal phrase tokens once inside the
    phrase wrap — no operator can leak through.

    Examples (full 13-input contract corpus in tests/test_fts_escape.py):
      escape_fts5("Section 5(a)")    -> Section 5(a) wrapped in double quotes
      escape_fts5('he said "hi"')    -> doubled internal quotes inside wrap
      escape_fts5("OR AND NEAR")     -> phrase, not operators
      escape_fts5("")                -> empty-quote phrase (gated by handler)

    The empty-string case triggers an FTS5 syntax error upstream; the
    handler's D4-19 step 1 guard `if not query.strip(): raise_invalid_query()`
    fires BEFORE this function is called.
    """
    return '"' + query.replace('"', '""') + '"'
