"""
MCP prompts for zeeker-mcp.

Prompts are optional advisory messages that help MCP clients use tools more
effectively. They are not tools themselves — they do not fetch data. Instead,
they return a short guidance message the client can inject into its context
before calling tools.
"""

from __future__ import annotations

from mcp_zeeker.server import mcp


@mcp.prompt(name="search_judgements", description="Guide for searching Singapore court judgments by case name, citation, or parties")
def search_judgements(query: str) -> list[dict]:
    """Return a prompt that helps the client search zeeker-judgements effectively.

    Singapore judgments are indexed by:
    - case_name (e.g. "Public Prosecutor v Tan Wei Lin")
    - citation (e.g. "[2024] SGHC 123")
    - parties (plaintiff / defendant names)
    - content_text (full judgment text, via fragments)

    The most reliable lookup path is to search the *case_name* or *citation*
    fields directly rather than full-text content, which may miss short-form
    references.
    """
    return [
        {
            "role": "user",
            "content": (
                f"Find Singapore court judgments matching: {query}\n\n"
                "Strategy:\n"
                "1. Use `search()` with the query focused on party names, "
                "citation, or case title — e.g. search(query='Tan Wei Lin', "
                "databases=['zeeker-judgements']).\n"
                "2. If the case name or citation is known, use `query_table()` "
                "with a filter on case_name or citation instead of broad "
                "content_text search.\n"
                "3. For ambiguous names, search first, then use `fetch()` on "
                "promising rows to retrieve full metadata.\n"
                "4. If full text is needed, call `query_table()` with "
                "columns=['content_text'] and read retrieved_content.content_text.\n\n"
                "Avoid searching content_text directly for case names — the "
                "FTS index on case_name and citation is more precise."
            ),
        }
    ]
