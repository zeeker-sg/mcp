"""
Pure-unit tests for resolve_preview_columns — Plan 04-01 (D4-12).

Five tests covering all resolution branches per 04-RESEARCH §3.8:
  1. Defaults resolve cleanly (judgments — case_name beats title because the
     defaults tuple lists case_name first AFTER title; judgments lacks a
     plain `title` column).
  2. about_singapore_law has no date column — `date` field returns None;
     title and url still resolve cleanly so the dict is returned (NOT None).
  3. Missing url → returns None drop signal (table is unsearchable).
  4. SEARCH_PREVIEW_OVERRIDES with explicit None value suppresses a field
     even when a candidate exists in `available`.
  5. A heavy column matching a SEARCH_PREVIEW_DEFAULTS candidate is rejected;
     the next non-heavy candidate is picked (D3-04 / D4-12 defense-in-depth).

The function is pure (no IO, no awaits), so tests use `monkeypatch.setattr` /
`monkeypatch.setitem` rather than fixtures + httpx_mock.
"""

from __future__ import annotations

import pytest


def test_defaults_resolve_judgments() -> None:
    """D4-12 / 04-RESEARCH §3.8 row 1: judgments resolves via defaults.

    judgments has case_name (no plain `title`), decision_date, summary,
    source_url. With SEARCH_PREVIEW_DEFAULTS["title"] = ("title","case_name",
    "name","heading") the first match in `available` wins — `case_name`.
    """
    from mcp_zeeker.core.search import resolve_preview_columns

    out = resolve_preview_columns(
        "zeeker-judgements",
        "judgments",
        available={
            "id",
            "citation",
            "case_name",
            "case_numbers",
            "decision_date",
            "court",
            "subject_tags",
            "source_url",
            "pdf_url",
            "summary",
            "content_text",  # heavy — should be excluded from any field
        },
    )
    assert out is not None
    assert out["title"] == "case_name"  # title not in available, case_name wins
    assert out["date"] == "decision_date"
    assert out["summary"] == "summary"
    assert out["url"] == "source_url"


def test_about_singapore_law_no_date_returns_null() -> None:
    """D4-12 / 04-RESEARCH §3.8 row 12: about_singapore_law has no date column.

    available = {item_url, title, section, home_page, ...} — date returns
    None (no candidate matched) but title=title, url=item_url, summary=section
    resolve so the dict is NOT None.
    """
    from mcp_zeeker.core.search import resolve_preview_columns

    out = resolve_preview_columns(
        "sglawwatch",
        "about_singapore_law",
        available={"item_url", "title", "section", "home_page", "content_length", "last_scraped"},
    )
    assert out is not None
    assert out["title"] == "title"
    assert out["url"] == "item_url"
    assert out["date"] is None  # no candidate matched → null is OK
    assert out["summary"] == "section"


def test_missing_url_returns_none_drop_signal() -> None:
    """D4-12: when title XOR url is absent the table is dropped (returns None).

    Caller (searchable_tables_for) handles the drop with a structured warning.
    """
    from mcp_zeeker.core.search import resolve_preview_columns

    # Only title — no url candidate in `available` → None drop signal.
    out = resolve_preview_columns("fake-db", "tbl", available={"title"})
    assert out is None


def test_override_suppresses_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """D4-12 / D4-22: SEARCH_PREVIEW_OVERRIDES with None value explicitly
    suppresses a field even when `available` would otherwise pick a candidate.
    """
    from mcp_zeeker import config
    from mcp_zeeker.core.search import resolve_preview_columns

    # Inject a single override entry for fake.tbl with date=None.
    monkeypatch.setitem(config.SEARCH_PREVIEW_OVERRIDES, "fake.tbl", {"date": None})

    out = resolve_preview_columns(
        "fake",
        "tbl",
        available={"title", "decision_date", "source_url"},
    )
    assert out is not None
    # Override forces date=None even though decision_date matches the default candidates.
    assert out["date"] is None
    # Other fields fall through to defaults.
    assert out["title"] == "title"
    assert out["url"] == "source_url"


def test_heavy_column_never_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """D3-04 / D4-12 defense-in-depth: a heavy column matching a default
    candidate is rejected; the next non-heavy candidate is picked.

    We patch SEARCH_PREVIEW_DEFAULTS["title"] to put a heavy column
    (`content_text`) FIRST in the tuple. With `available` containing both
    `content_text` and a non-heavy `title`, the resolver must skip
    `content_text` (a heavy column) and pick `title`.
    """
    from mcp_zeeker import config
    from mcp_zeeker.core.search import resolve_preview_columns

    monkeypatch.setattr(
        config,
        "SEARCH_PREVIEW_DEFAULTS",
        {
            **config.SEARCH_PREVIEW_DEFAULTS,
            "title": ("content_text", "title"),  # heavy first → must be skipped
        },
    )

    out = resolve_preview_columns(
        "fake",
        "tbl",
        available={"content_text", "title", "source_url"},
    )
    assert out is not None
    assert out["title"] == "title"  # heavy candidate rejected, next picked
    assert out["url"] == "source_url"
