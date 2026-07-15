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


def escape_fts5_terms(query: str) -> str:
    """Escape a user query as independent quoted FTS5 terms (issue #12 Phase 1).

    Splits on whitespace, wraps each token in double quotes (doubling any
    embedded quote — the only character needing escaping inside an FTS5
    string), and joins with single spaces. FTS5 treats adjacent quoted
    strings as implicit AND, so all terms must match but adjacency is NOT
    required — unlike `escape_fts5`, which demands the exact phrase. This is
    the BM25-ranked default: term-level matching lets bm25() rank partial
    proximity instead of the all-or-nothing phrase gate.

    Same security discipline as `escape_fts5`: pure stdlib string operation,
    no IO, never fails — every FTS5 operator (NEAR, OR, AND, `:col:`, `*`,
    parentheses) is neutralized once inside its per-token quote wrap.
    Empty/whitespace input yields "" — the handler's empty-query gate fires
    BEFORE this function is called (D4-19 step 1), same contract as
    `escape_fts5`.

    Examples:
      escape_fts5_terms("data protection") -> two quoted tokens, implicit AND
      escape_fts5_terms("NEAR OR AND")     -> three literal quoted tokens
    """
    return " ".join('"' + token.replace('"', '""') + '"' for token in query.split())


def escape_user_query(query: str) -> str:
    """Phrase-intent dispatch between `escape_fts5` and `escape_fts5_terms`.

    Issue #12 Phase 1: when the stripped user query is enclosed in double
    quotes (explicit phrase intent, e.g. `"personal data"`), the INNER text
    is escaped as a single FTS5 phrase via `escape_fts5` — adjacency
    required. Otherwise the query is escaped term-by-term via
    `escape_fts5_terms` — implicit AND, adjacency not required.

    Degenerate quoted inputs (`\"\"`, `\"   \"` — empty inner text) fall back
    to the term path so we never emit the bare `\"\"` FTS5 syntax error for
    an input that passed the handler's non-empty gate.

    Pure function, never fails — same discipline as the two escapes it
    dispatches to. The handler's empty-query gate (D4-19 step 1) fires
    BEFORE this function is called.
    """
    stripped = query.strip()
    if len(stripped) >= 2 and stripped.startswith('"') and stripped.endswith('"'):
        inner = stripped[1:-1]
        if inner.strip():
            return escape_fts5(inner)
    return escape_fts5_terms(query)
