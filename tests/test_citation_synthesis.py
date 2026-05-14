"""
Citation synthesis tests — Wave 0 STUB.

Plan 06-03 ships the GREEN body: per-(db, table) parametrized assertion that
`synthesize_citation(db, table, row, retrieved_at)` renders the configured
template; DEFAULT_CITATION_TEMPLATE applies when (db, table) absent; null-
valued source fields render as `""` (Pitfall 5 regression coverage).

Coverage planned: D6-05 / D6-06 / D6-07 / D6-08, plus the null-field
regression (`test_null_field_renders_empty_string`).
"""

from __future__ import annotations

import pytest

from mcp_zeeker import config
from mcp_zeeker.core.citation import _SafeDict, synthesize_citation  # noqa: F401


def test_wave0_citation_synthesis_stub():
    """Wave-0 stub: structural check that CITATION_TEMPLATES + DEFAULT resolve."""
    assert isinstance(config.CITATION_TEMPLATES, dict)
    # Plan 06-01 ships 13 entries (judgments + enforcement_decisions + 8
    # sg-gov-newsrooms + 3 sglawwatch — the truths-line breakdown sums to 13
    # rather than the previously-stated 10, which was an arithmetic typo;
    # 13 is the auditable single source of truth.
    assert len(config.CITATION_TEMPLATES) == 13
    assert config.DEFAULT_CITATION_TEMPLATE == "{url} (retrieved {retrieved_at})"
    # Pitfall 5 — every template uses only simple {name} placeholders
    import re

    for k, tmpl in config.CITATION_TEMPLATES.items():
        for placeholder in re.findall(r"\{([^{}]*)\}", tmpl):
            assert "." not in placeholder, f"template {k} uses attribute access: {placeholder}"
            assert "[" not in placeholder, f"template {k} uses index access: {placeholder}"

    pytest.skip(reason="Wave 0 stub — per-(db,table) citation body fills in Plan 06-03")
