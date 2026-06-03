from __future__ import annotations

from mcp_zeeker.prompts import search_judgements


def _content_for(query: str) -> str:
    messages = search_judgements(query)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    return messages[0]["content"]


def test_search_judgements_prompt_routes_to_judgments_table() -> None:
    content = _content_for("DXB v DXC")

    assert "zeeker-judgements" in content
    assert "judgments" in content
    assert "databases=['zeeker-judgements']" in content


def test_search_judgements_prompt_prioritizes_citation_lookup() -> None:
    content = _content_for("[2026] SGHC 119")

    assert "`[2026] SGHC 119`" in content
    assert "`2026 SGHC 119`" in content
    assert "`2026_SGHC_119`" in content
    assert "`citation`" in content
    assert "`exact`" in content
    assert "`SGHC 119`" in content
    assert content.index("first call `query_table()`") < content.index("call `search()`")


def test_search_judgements_prompt_lists_drilldown_columns() -> None:
    content = _content_for("what is [2026] SGHC 119 about?")

    for column in [
        "citation",
        "case_name",
        "case_numbers",
        "decision_date",
        "court",
        "subject_tags",
        "source_url",
        "pdf_url",
        "content_text",
    ]:
        assert f"`{column}`" in content
