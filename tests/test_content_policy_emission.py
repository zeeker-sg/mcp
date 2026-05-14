"""
Content policy emission tests — Wave 0 STUB.

Plan 06-03 ships the GREEN body: per-(db, table) parametrized assertion that
requesting a heavy column emits `_policy` under `retrieved_content` with the
exact `{source, license, license_url, redistribution}` shape from
`config.CONTENT_POLICIES`. Also exercises the fallback path (key absent
from CONTENT_POLICIES → minimal `_policy`).

Coverage planned: D6-13 / D6-14 / D6-15 — content-license labelling and
redistribution-posture surfacing under retrieved_content.
"""

from __future__ import annotations

import pytest

from mcp_zeeker import config


def test_wave0_content_policy_emission_stub():
    """Wave-0 stub: structural check that Plan 06-01 CONTENT_POLICIES shape resolves."""
    assert isinstance(config.CONTENT_POLICIES, dict)
    # 14 entries per RESEARCH Probe 3 Net categorization (10 allowed + 4 process-only).
    assert len(config.CONTENT_POLICIES) == 14
    expected_keys = {"source", "license", "license_url", "redistribution"}
    for k, v in config.CONTENT_POLICIES.items():
        # Tuple key (db, table) per D6-15
        assert isinstance(k, tuple) and len(k) == 2, k
        # Exactly four keys, no extras (D6-15 explicit shape contract)
        assert set(v.keys()) == expected_keys, k
        # Enum-valued redistribution
        assert v["redistribution"] in ("allowed", "process-only", "forbidden"), k

    pytest.skip(reason="Wave 0 stub — per-(db,table) emission body fills in Plan 06-03")
