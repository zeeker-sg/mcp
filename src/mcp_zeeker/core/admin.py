"""Admin endpoints — gated on the same soak-bypass token as the rate limiter.

Currently exposes one route:
  GET /admin/metrics → {"rss_kb": <int>}   (200, when authenticated)
                    → 404                  (when not — no surface)

Authentication is delegated to core/soak_auth.is_soak_authenticated so the
rate-limit bypass and metrics gate cannot diverge.

Why /admin/metrics returns 404 (not 401/403) when unauthenticated:
  We don't want this endpoint to be discoverable. A 401/403 is a signal that
  the URL exists and is gated; a 404 is indistinguishable from any other
  unrouted path. This is defence-in-depth — if the token is rotated correctly
  the endpoint should never be reached by anyone but the soak driver anyway.

RSS source: /proc/self/status (Linux) read INLINE — production runs on
Linux so this is the hot path. macOS fallback uses resource.getrusage.

Why inline (not via scripts.soak.rss_sampler): the production Docker image
copies only `src/` (see Dockerfile); `scripts/` is not in the runtime image,
so importing from it would 500 on every authenticated request. The /proc
read is small enough (~10 lines) that duplicating it here is cleaner than
hoisting scripts/soak/* into src/.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_zeeker.core.soak_auth import is_soak_authenticated


def _read_rss_kb() -> int:
    """Read resident-set-size in KB. Linux fast path + macOS dev fallback.

    Linux: parse VmRSS from /proc/self/status (always available for the
    current process).
    macOS / non-Linux: resource.getrusage(RUSAGE_SELF).ru_maxrss; ru_maxrss
    is in bytes on macOS and in KB on Linux — we normalize to KB.
    """
    try:
        text = Path(f"/proc/{os.getpid()}/status").read_text()
        m = re.search(r"^VmRSS:\s+(\d+)\s*kB", text, re.MULTILINE)
        if m is not None:
            return int(m.group(1))
    except OSError:
        pass  # fall through to resource.getrusage

    import resource

    rusage = resource.getrusage(resource.RUSAGE_SELF)
    if os.uname().sysname == "Darwin":
        return rusage.ru_maxrss // 1024
    return rusage.ru_maxrss


async def admin_metrics(request: Request) -> Response:
    """GET /admin/metrics — RSS only, soak-token-gated, 404 on miss.

    Body shape (when authenticated):
        {"rss_kb": <integer>}

    Why no other fields: the 24h soak only needs RSS for NFR-03. Keep the
    surface narrow until a second measurement is genuinely required.
    """
    if not is_soak_authenticated(request.scope):
        # 404 with empty body — indistinguishable from "this path does not exist".
        return Response(status_code=404)
    return JSONResponse({"rss_kb": _read_rss_kb()})
