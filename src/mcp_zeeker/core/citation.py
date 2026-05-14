"""
Per-row citation synthesis — D6-05 / D6-06 / D6-07 / D6-08.

`synthesize_citation(database, table, row, retrieved_at)` produces the
citation string that ships under `Envelope.data[i]["_citation"]` (Plan 06-02
wires the emission site). Behavior:

- Template lookup via `config.CITATION_TEMPLATES.get((database, table),
  config.DEFAULT_CITATION_TEMPLATE)` — tuple keys, no string-key split.
- `str.format_map(_SafeDict(row, retrieved_at))` does the substitution.
- `_SafeDict` is a `defaultdict(str)` subclass — missing keys render as `""`
  (NOT `KeyError`, NOT `"{name}"`).
- Pitfall 5: None-valued source row fields are rewritten to `""` in
  `_SafeDict.__init__` BEFORE substitution, so `{case_name}` never renders
  the string `"None"`.
- Synthetic `{retrieved_at}` placeholder is injected by `_SafeDict.__init__`
  bound to `retrieved_at.isoformat()` — last-write-wins so an upstream column
  literally named `retrieved_at` cannot shadow the synthetic placeholder.

Templates use ONLY simple `{name}` placeholders — no attribute access
(`{x.y}`) and no indexing (`{x[0]}`). The lint is enforced by
`tests/test_citation_synthesis.py` (filled GREEN in Plan 06-03); the
foundation here keeps the contract trivial to audit.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from mcp_zeeker import config


class _SafeDict(defaultdict):
    """defaultdict(str) wrapper that pre-processes None values and injects
    the synthetic `{retrieved_at}` placeholder.

    Pitfall 5: a citation template renders `{x}` as the str representation
    of `x`. For Python's default `None`, that would be the literal string
    `"None"`, which is wrong for a citation. `__init__` rewrites every
    None-valued entry from `src` to `""` BEFORE storage so subsequent
    `__getitem__` returns `""`.

    Missing-key lookups return `""` via the `defaultdict(str)` factory; no
    KeyError, no template-stub leakage.
    """

    def __init__(self, src: dict, retrieved_at: datetime) -> None:
        super().__init__(str)
        for k, v in src.items():
            self[k] = "" if v is None else v
        # D6-07: synthetic retrieved_at placeholder; last-write-wins so an
        # upstream column literally named `retrieved_at` cannot shadow it.
        self["retrieved_at"] = retrieved_at.isoformat()


def synthesize_citation(database: str, table: str, row: dict, retrieved_at: datetime) -> str:
    """Render the per-row citation string.

    D6-05 / D6-06 / D6-07 / D6-08: template lookup is keyed on the
    `(database, table)` tuple; missing-key entries fall through to
    `config.DEFAULT_CITATION_TEMPLATE` (which uses `{url}` — fragments tables
    intentionally fall through here because they have no `url` column, and
    the LLM has the parent URL from the filter that drove the fragment-join
    query, so per-fragment citation is not load-bearing).
    """
    template = config.CITATION_TEMPLATES.get((database, table), config.DEFAULT_CITATION_TEMPLATE)
    return template.format_map(_SafeDict(row, retrieved_at))
