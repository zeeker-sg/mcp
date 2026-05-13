"""
Config lookup helpers for denylists and per-table config (D2-10).

Provides:
- hidden_columns_for(database, table) — SINGLE call-site that reads
  config.HIDDEN_COLUMNS. No other module in the codebase may read
  HIDDEN_COLUMNS directly (Pitfall 4 prevention).
- url_column_for(database, table)   — SINGLE call-site that reads
  config.URL_COLUMNS. Plan 03-04 (Slice C, FETCH-04) routes the fetch handler
  through this helper so the unsupported_table_for_fetch check has exactly
  one definition (mirror of hidden_columns_for discipline).

References: D2-10, D3-14, Pitfall 4.
"""

from __future__ import annotations

from mcp_zeeker import config


def hidden_columns_for(database: str, table: str) -> set[str]:
    """Return the union of global and per-table hidden columns for database.table.

    config.HIDDEN_COLUMNS shape (Phase 2):
      dict[str, set[str]] keyed on:
      - "*"              — global hidden columns (applied to every table)
      - "<db>.<table>"   — per-table hidden columns

    This is the ONLY call-site for config.HIDDEN_COLUMNS. Never read
    config.HIDDEN_COLUMNS directly from handlers — always use this helper.
    Centralizes the union logic and makes future changes a one-line edit.
    """
    return config.HIDDEN_COLUMNS.get("*", set()) | config.HIDDEN_COLUMNS.get(
        f"{database}.{table}", set()
    )


def url_column_for(database: str, table: str) -> str | None:
    """Return the URL column name for `database.table`, or None if not URL-keyed.

    config.URL_COLUMNS shape (Phase 2):
      dict[str, str] keyed on "<db>.<table>" (no global default).
      Example: "zeeker-judgements.judgments" -> "source_url".

    This is the ONLY call-site for config.URL_COLUMNS. The fetch handler
    (Plan 03-04, D3-14 step 2) consumes this helper and raises
    unsupported_table_for_fetch when the return value is None. Mirror of
    hidden_columns_for's single-source-of-truth discipline (D2-10).

    No walrus, no defaults beyond None — callers MUST handle the None case
    via raise_unsupported_table_for_fetch (FETCH-04).
    """
    return config.URL_COLUMNS.get(f"{database}.{table}")
