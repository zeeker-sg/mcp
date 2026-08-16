"""
MCP prompts for zeeker-mcp.

Prompts are optional advisory messages that help MCP clients use tools more
effectively. They are not tools themselves — they do not fetch data. Instead,
they return a short guidance message the client can inject into its context
before calling tools.
"""

from __future__ import annotations

from mcp_zeeker.server import mcp


@mcp.prompt(
    name="search_judgements",
    description="Route Singapore court judgment searches to the judgments database",
)
def search_judgements(query: str) -> list[dict]:
    """Return a prompt that helps the client search zeeker-judgements effectively.

    The main value of this prompt is routing: Singapore court judgments live in
    zeeker-judgements.judgments. Within that table, use structured citation
    lookup for neutral citations and scoped FTS for names, parties, and topics.
    """
    return [
        {
            "role": "user",
            "content": (
                f"Find Singapore court judgments matching: {query}\n\n"
                "Use the `zeeker-judgements` database. Judgment metadata is in "
                "the `judgments` table.\n\n"
                "Strategy:\n"
                "1. If the query looks like a neutral citation, such as "
                "`[2026] SGHC 119`, first call `query_table()` on "
                "`zeeker-judgements.judgments` with a filter on `citation`. "
                "Normalize common variants like `2026 SGHC 119` or "
                "`2026_SGHC_119` to `[2026] SGHC 119` for an `exact` filter; "
                "if exact lookup fails, retry `citation contains` with the "
                "court and number, e.g. `SGHC 119`.\n"
                "2. If the query is a case name, party name, legal issue, or "
                "topic, call `search()` with `databases=['zeeker-judgements']`.\n"
                "3. To drill into an identified judgment, call `query_table()` "
                "on `zeeker-judgements.judgments` and request columns such as "
                "`citation`, `case_name`, `case_numbers`, `decision_date`, "
                "`court`, `subject_tags`, `source_url`, `pdf_url`, and, when "
                "needed, `content_text`.\n"
                "4. If a lookup fails because of a column mismatch, inspect "
                "the table schema before retrying."
            ),
        }
    ]


@mcp.prompt(
    name="search_cookies",
    description="Route Singapore law cookie searches to the sg-law-cookies database",
)
def search_cookies(query: str) -> list[dict]:
    """Return a prompt that helps the client search sg-law-cookies effectively.

    Cookies are concise curated summaries of Singapore legal developments.
    Judgment issues extract legal questions and holdings from judgments.
    """
    return [
        {
            "role": "user",
            "content": (
                f"Find Singapore legal cookies matching: {query}\n\n"
                "Use the `sg-law-cookies` database. Cookies (concise legal "
                "summaries) are in the `cookies` table. Issues extracted from "
                "judgments (legal questions and holdings) are in "
                "`judgment_issues`. Judgment metadata is in `judgments`.\n\n"
                "Strategy:\n"
                "1. Call `search()` with `databases=['sg-law-cookies']` for "
                "topical queries — this searches cookies headlines/summaries "
                "and judgment issues questions/holdings/reasoning.\n"
                "2. Call `query_table()` on `sg-law-cookies.cookies` to filter "
                "by date, significance, or primary_area.\n"
                "3. Call `query_table()` on `sg-law-cookies.judgment_issues` "
                "to drill into specific legal questions from a judgment, "
                "filtering by citation or court.\n"
                "4. Call `fetch()` on `sg-law-cookies.cookies` or "
                "`sg-law-cookies.judgments` with a `source_url` to retrieve "
                "the full row.\n"
            ),
        }
    ]
