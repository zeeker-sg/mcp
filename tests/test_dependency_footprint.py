"""NFR-04: dependency footprint locked to exact 6 runtime + 5 dev tuples.

Reads pyproject.toml via stdlib tomllib (Python 3.11+; pinned by
pyproject.toml requires-python = ">=3.11"). Asserts set-equality with the
locked NFR-04 tuples — any add/remove/rename surfaces in the diff.

Reference: REQUIREMENTS.md NFR-04, pyproject.toml lines 6-21.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# Locked per NFR-04 (REQUIREMENTS.md). Names match PEP 508 distribution names
# as they appear in pyproject.toml [project].dependencies (lines 6-13).
RUNTIME_DEPS_LOCKED = frozenset(
    {
        "fastmcp",
        "pydantic",
        "httpx",
        "starlette",
        "uvicorn",
        "structlog",
    }
)

# Locked per NFR-04. Names match [dependency-groups].dev (lines 15-21).
DEV_DEPS_LOCKED = frozenset(
    {
        "mkdocs-material",
        "pytest",
        "pytest-asyncio",
        "pytest-httpx",
        "ruff",
    }
)


def _project_root() -> Path:
    """Return the project root (directory containing pyproject.toml).

    Per 08-PATTERNS.md "Trustable Project-Root Resolution": this test file
    lives at tests/test_dependency_footprint.py (fixed depth), so .parent.parent
    from the resolved path returns the directory containing pyproject.toml.
    """
    return Path(__file__).resolve().parent.parent


def _parse_dep_name(spec: str) -> str:
    """Return the distribution name from a PEP 508 specifier like 'fastmcp~=3.2'.

    Strips everything from the first non-name character (~, =, <, >, ;, [, space).
    Uses regex split to correctly handle combined operators like >=.
    """
    m = re.split(r"[~=<>!;\[\s]", spec, maxsplit=1)
    return m[0] if m else spec


def test_runtime_deps_match_locked_set() -> None:
    """NFR-04: [project.dependencies] must equal exactly the 6 locked runtime names."""
    pyproject = tomllib.loads((_project_root() / "pyproject.toml").read_text())
    declared = frozenset(_parse_dep_name(d) for d in pyproject["project"]["dependencies"])
    assert declared == RUNTIME_DEPS_LOCKED, (
        f"runtime deps drifted: "
        f"added={declared - RUNTIME_DEPS_LOCKED!r} "
        f"removed={RUNTIME_DEPS_LOCKED - declared!r}"
    )


def test_dev_deps_match_locked_set() -> None:
    """NFR-04: [dependency-groups.dev] must equal exactly the 5 locked dev names."""
    pyproject = tomllib.loads((_project_root() / "pyproject.toml").read_text())
    declared = frozenset(_parse_dep_name(d) for d in pyproject["dependency-groups"]["dev"])
    assert declared == DEV_DEPS_LOCKED, (
        f"dev deps drifted: "
        f"added={declared - DEV_DEPS_LOCKED!r} "
        f"removed={DEV_DEPS_LOCKED - declared!r}"
    )


def test_pinning_discipline_runtime() -> None:
    """NFR-04: every [project.dependencies] entry must carry a version operator.

    Operators accepted: ~=, >=, ==. Entries without a version operator
    silently allow any version, undermining the NFR-04 lock.
    """
    pyproject = tomllib.loads((_project_root() / "pyproject.toml").read_text())
    REQUIRED_OPERATORS = ("~=", ">=", "==")
    for entry in pyproject["project"]["dependencies"]:
        has_pin = any(op in entry for op in REQUIRED_OPERATORS)
        assert has_pin, (
            f"runtime dep missing version operator (expected one of {REQUIRED_OPERATORS}): "
            f"{entry!r}"
        )
