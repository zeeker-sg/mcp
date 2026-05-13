"""
Single-source-of-truth regression test for config_lookup helpers (D2-10, D3-04).

Mirrors the discipline asserted in module docstrings:
- `hidden_columns_for` is the SOLE reader of `config.HIDDEN_COLUMNS`.
- `url_column_for`     is the SOLE reader of `config.URL_COLUMNS`.

Direct attribute reads (`config.HIDDEN_COLUMNS[...]`, `config.URL_COLUMNS[...]`,
or `"key" in config.URL_COLUMNS`) outside `core/config_lookup.py` would
silently desync handlers from the centralized helper logic — exactly the
divergence the invariant was created to prevent (Phase 3 REVIEW CR-01).

The test scans `src/mcp_zeeker/**/*.py` for textual references to the
guarded dict names. `config.py` (where the dicts are defined) and
`config_lookup.py` (the sole call-site) are exempted.
"""

from __future__ import annotations

import ast
import pathlib


def _scan_attribute_offenders(attr_name: str) -> list[str]:
    """Return relative paths of source files that read ``config.<attr_name>``.

    Uses AST so docstring / comment mentions of the attribute do NOT trip the
    test. We only flag real attribute-access nodes — the actual security-
    relevant reads.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "mcp_zeeker"
    repo_root = root.parent.parent
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        if py.name in {"config.py", "config_lookup.py"}:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == attr_name
                and isinstance(node.value, ast.Name)
                and node.value.id == "config"
            ):
                offenders.append(str(py.relative_to(repo_root)))
                break
    return offenders


def test_url_columns_only_read_via_helper() -> None:
    """D3-04 / CR-01: config.URL_COLUMNS must not be referenced outside config_lookup."""
    offenders = _scan_attribute_offenders("URL_COLUMNS")
    assert not offenders, (
        f"direct config.URL_COLUMNS reads outside core/config_lookup.py: {offenders}. "
        f"Use core.config_lookup.url_column_for(database, table) instead (D3-04)."
    )


def test_hidden_columns_only_read_via_helper() -> None:
    """D2-10: config.HIDDEN_COLUMNS must not be referenced outside config_lookup."""
    offenders = _scan_attribute_offenders("HIDDEN_COLUMNS")
    assert not offenders, (
        f"direct config.HIDDEN_COLUMNS reads outside core/config_lookup.py: {offenders}. "
        f"Use core.config_lookup.hidden_columns_for(database, table) instead (D2-10)."
    )
