"""Single-source-of-truth re-export for the soak workload.

Canonical workload lives in tests/_corpus/soak_workload.py; this module
re-exports WORKLOAD for the soak driver (scripts/soak/run_soak.py).

Direction choice (W6 revision, documented in 08-05-PLAN.md SUMMARY):
  - Canonical definition: tests/_corpus/soak_workload.py (pytest-rooted)
  - Re-exporter: scripts/soak/workload.py (this file)
  - Rationale: keeping the canonical in tests/_corpus/ lets pytest collect it
    without sys.path gymnastics; scripts/ requires a one-line path adjustment
    regardless of direction, so keeping canonical pytest-side is cleaner.

sys.path adjustment:
  scripts/ is not in the default Python search path when invoked as
  `python -m scripts.soak.run_soak`. The parent.parent.parent of this file
  is the project root (the directory containing pyproject.toml), which IS
  the correct root for `from tests._corpus.soak_workload import WORKLOAD`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Insert project root so `tests._corpus.soak_workload` resolves when this
# module is imported in non-pytest contexts (e.g. `python -m scripts.soak.run_soak`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests._corpus.soak_workload import WORKLOAD as WORKLOAD  # noqa: E402

__all__ = ["WORKLOAD"]
