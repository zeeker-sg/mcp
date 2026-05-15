"""RSS memory sampler for the 24h soak harness — TEST-05 / NFR-03.

Pure-stdlib module: reads /proc/{pid}/status (VmRSS) on Linux, or falls back
to resource.getrusage on macOS/other POSIX systems.

macOS vs Linux unit difference:
  Linux:  resource.ru_maxrss is in KB (cumulative-max since process start).
  macOS:  resource.ru_maxrss is in BYTES (current RSS).
This is the entire reason this helper exists — to normalise the two units.

Note: psutil is explicitly REJECTED per NFR-04 (tests/test_dependency_footprint.py).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def rss_kb_from_proc(pid: int) -> int | None:
    """Return resident-set in KB by reading /proc/{pid}/status — Linux only.

    Reads the VmRSS field from the kernel pseudo-file. Returns None on:
      - non-Linux platforms (OSError: /proc not present)
      - unreadable PID (process gone, permissions)
      - missing VmRSS field (unexpected kernel/container config)
    """
    try:
        text = Path(f"/proc/{pid}/status").read_text()
        m = re.search(r"^VmRSS:\s+(\d+)\s*kB", text, re.MULTILINE)
        return int(m.group(1)) if m else None
    except (OSError, AttributeError):
        return None


def rss_kb_from_self() -> int:
    """Fallback for non-Linux: ru_maxrss from current process, normalised to KB.

    macOS reports bytes; Linux reports KB.
    The conditional branch on os.uname().sysname == "Darwin" is unusual in this
    codebase (no other module checks platform) — it is intentional here because
    normalising to KB is the only reason this helper exists.
    """
    import resource  # local import so the module loads cleanly on platforms without resource

    rusage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports bytes; Linux reports KB. Normalize to KB.
    if os.uname().sysname == "Darwin":
        return rusage.ru_maxrss // 1024
    return rusage.ru_maxrss
