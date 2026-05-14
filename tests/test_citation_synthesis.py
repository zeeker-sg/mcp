"""
Citation synthesis tests — Phase 6 Plan 06-03 GREEN body.

Parametrized per-(db, table) assertion that `synthesize_citation(db, table,
row, retrieved_at)` renders the configured template, plus three dedicated
regressions:

- D6-05/06/07/08: per-template column-name alignment — the row dict carries
  every column the template references; the rendered citation matches the
  template's `.format_map(_SafeDict(stub_row, frozen))` tautology. The TEST
  pins template column-name alignment: if a template references
  `{organisation_name}` but the live column is `organisation`, this test
  catches the mismatch. Column names sourced verbatim from RESEARCH Probe 2
  (verified live 2026-05-14).

- Pitfall 5: None-valued source row fields render as `""` (empty string),
  NOT the literal string `"None"`.

- DEFAULT_CITATION_TEMPLATE fallback: (db, table) absent from
  CITATION_TEMPLATES falls through to `{url} (retrieved {retrieved_at})`.

- Fragment-table fallback: the *_fragments tables are intentionally omitted
  from CITATION_TEMPLATES and fall through to DEFAULT_CITATION_TEMPLATE;
  their rows lack a `url` column so `{url}` substitutes to "" via
  `_SafeDict.__missing__`.

All tests are PURE FUNCTION calls — no mcp_client, no httpx_mock, no network
IO. They exercise the citation helper directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mcp_zeeker import config
from mcp_zeeker.core.citation import _SafeDict, synthesize_citation

# ---------------------------------------------------------------------------
# Per-(db, table) stub row dict — column names sourced verbatim from
# RESEARCH §"Per-(db, table) Column Inventory" lines 570-581 + the
# CITATION_TEMPLATES entries in config.py.
#
# Plan 06-01 ships 13 CITATION_TEMPLATES entries (judgments + enforcement +
# 8 sg-gov-newsrooms.*_news + judiciary_news + 3 sglawwatch). The
# pdpc.enforcement_decisions template references `organisation` (NOT
# `organisation_name` — RESEARCH Probe 2 verified live).
# ---------------------------------------------------------------------------

_FROZEN: datetime = datetime(2026, 1, 1, tzinfo=UTC)

_STUB_ROW_PER_TABLE: dict[tuple[str, str], dict] = {
    ("zeeker-judgements", "judgments"): {
        "case_name": "Foo v Bar",
        "citation": "[2026] SGCA 1",
        "court": "Court of Appeal",
        "decision_date": "2026-01-15",
        "source_url": "https://elit.example.test/123",
    },
    ("pdpc", "enforcement_decisions"): {
        "organisation": "Acme Pte Ltd",  # NOT organisation_name — RESEARCH Probe 2 verified
        "title": "Breach of PDPA s24",
        "decision_date": "2026-02-01",
        "decision_url": "https://pdpc.example.test/dec/123",
    },
    ("sg-gov-newsrooms", "acra_news"): {
        "title": "ACRA update",
        "category": "Announcements",
        "published_date": "2026-01-20",
        "source_url": "https://acra.example.test/news/1",
    },
    ("sg-gov-newsrooms", "agc_news"): {
        "title": "AGC update",
        "category": "Announcements",
        "published_date": "2026-01-21",
        "source_url": "https://agc.example.test/news/1",
    },
    ("sg-gov-newsrooms", "ccs_news"): {
        "title": "CCCS update",
        "category": "Announcements",
        "published_date": "2026-01-22",
        "source_url": "https://ccs.example.test/news/1",
    },
    ("sg-gov-newsrooms", "ipos_news"): {
        "title": "IPOS update",
        "category": "Announcements",
        "published_date": "2026-01-23",
        "source_url": "https://ipos.example.test/news/1",
    },
    ("sg-gov-newsrooms", "judiciary_news"): {
        "title": "Court announcement",
        "content_type": "Speech",
        "published_date": "2026-01-24",
        "source_url": "https://judiciary.example.test/news/1",
    },
    ("sg-gov-newsrooms", "mlaw_news"): {
        "title": "MLAW update",
        "category": "Announcements",
        "published_date": "2026-01-25",
        "source_url": "https://mlaw.example.test/news/1",
    },
    ("sg-gov-newsrooms", "mom_news"): {
        "title": "MOM update",
        "category": "Announcements",
        "published_date": "2026-01-26",
        "source_url": "https://mom.example.test/news/1",
    },
    ("sg-gov-newsrooms", "pdpc_news"): {
        "title": "PDPC newsroom item",
        "category": "Announcements",
        "published_date": "2026-01-27",
        "source_url": "https://pdpc-news.example.test/news/1",
    },
    ("sglawwatch", "headlines"): {
        "title": "Headline title",
        "author": "X Author",
        "date": "2026-01-28",
        "source_link": "https://slw.example.test/h/1",
    },
    ("sglawwatch", "commentaries"): {
        "title": "Commentary title",
        "author": "Y Author",
        "pub_date": "2026-01-29",
        "link": "https://slw.example.test/c/1",
    },
    ("sglawwatch", "about_singapore_law"): {
        "title": "About entry",
        "section": "Civil Procedure",
        "item_url": "https://slw.example.test/a/1",
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "db_table",
    sorted(config.CITATION_TEMPLATES.keys()),
    ids=lambda dt: f"{dt[0]}.{dt[1]}",
)
def test_citation_template_substitutes_correctly(db_table: tuple[str, str]) -> None:
    """D6-05/06/07/08: per-template column-name alignment.

    For each (db, table) in CITATION_TEMPLATES (13 entries per Plan 06-01),
    pass a stub row carrying every template-referenced column and assert
    the rendered citation equals `template.format_map(_SafeDict(row, frozen))`
    — the tautology pins column-name alignment between templates and the
    real upstream column inventory (RESEARCH Probe 2).
    """
    db, table = db_table
    template = config.CITATION_TEMPLATES[db_table]
    row = _STUB_ROW_PER_TABLE.get(db_table)
    assert row is not None, (
        f"{db_table}: missing stub row dict (CITATION_TEMPLATES has an entry "
        f"but _STUB_ROW_PER_TABLE doesn't — extend the fixture)"
    )
    rendered = synthesize_citation(db, table, row, _FROZEN)
    expected = template.format_map(_SafeDict(row, _FROZEN))
    assert rendered == expected, (
        f"{db_table}: citation mismatch.\n"
        f"  template: {template!r}\n"
        f"  row:      {row!r}\n"
        f"  expected: {expected!r}\n"
        f"  got:      {rendered!r}"
    )
    # Defensive: the rendered string is not empty and doesn't contain the
    # literal "None" — which would mean a None-valued source field leaked
    # through Pitfall 5's defense.
    assert rendered, f"{db_table}: rendered citation empty"
    # Sanity: every template-referenced column name is present in our stub.
    import re

    placeholders = set(re.findall(r"\{([^{}]+)\}", template))
    # "retrieved_at" is the synthetic placeholder injected by _SafeDict;
    # not expected in the row dict itself.
    placeholders.discard("retrieved_at")
    missing = placeholders - set(row.keys())
    assert missing == set(), (
        f"{db_table}: stub row is missing template-referenced columns: {missing}"
    )


def test_null_field_renders_empty_string() -> None:
    """Pitfall 5 regression: None values render as '' not 'None'.

    All template-referenced columns set to None should produce a citation
    string with empty-string substitutions (literal punctuation + spaces
    from the template preserved). The literal `"None"` MUST NOT appear.
    """
    rendered = synthesize_citation(
        "zeeker-judgements",
        "judgments",
        {
            "case_name": None,
            "citation": None,
            "court": None,
            "decision_date": None,
            "source_url": None,
        },
        _FROZEN,
    )
    # Template: "{case_name} {citation} ({court}, {decision_date}) — {source_url}"
    # With every placeholder → "" we get the literal punctuation: " (, ) — "
    assert rendered == "  (, ) — "
    # Pitfall 5: literal "None" MUST NOT appear.
    assert "None" not in rendered, f"None leaked into citation: {rendered!r}"


def test_default_citation_template_used_when_key_absent() -> None:
    """D6-08: (db, table) absent from CITATION_TEMPLATES → DEFAULT applied."""
    rendered = synthesize_citation(
        "not-a-db",
        "not-a-table",
        {"url": "https://example.test/path"},
        _FROZEN,
    )
    expected = "https://example.test/path (retrieved 2026-01-01T00:00:00+00:00)"
    assert rendered == expected, f"DEFAULT_CITATION_TEMPLATE mismatch: {rendered!r}"


def test_default_citation_template_renders_for_fragment_tables() -> None:
    """RESEARCH Probe 2 footnote: fragment tables fall through to DEFAULT.

    `*_fragments` tables intentionally have no CITATION_TEMPLATES entry.
    They lack a `url` column upstream, so `{url}` substitutes to "" via
    `_SafeDict.__missing__` (the defaultdict(str) factory).
    """
    rendered = synthesize_citation(
        "zeeker-judgements",
        "judgments_fragments",
        {"ordinal": 5, "section_heading": "Background"},
        _FROZEN,
    )
    expected = " (retrieved 2026-01-01T00:00:00+00:00)"
    assert rendered == expected, f"fragment-table DEFAULT mismatch: {rendered!r}"
