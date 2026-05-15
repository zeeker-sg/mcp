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

RSS source: /proc/self/status (Linux) via rss_sampler.rss_kb_from_proc(os.getpid()),
falling back to rss_kb_from_self() on macOS / non-Linux. In production this
runs on Linux so the /proc read path is the hot path; the fallback exists for
local dev only.
"""

from __future__ import annotations

import json
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_zeeker.core.soak_auth import is_soak_authenticated


def _read_rss_kb() -> int:
    """Read resident-set-size in KB. Linux-fast path, dev-fallback for macOS.

    Imported lazily so unit tests can monkeypatch.
    """
    from scripts.soak.rss_sampler import rss_kb_from_proc, rss_kb_from_self

    pid = os.getpid()
    rss = rss_kb_from_proc(pid)
    if rss is None:
        rss = rss_kb_from_self()
    return rss


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
