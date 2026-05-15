"""CR-02 carryover: app.py:59 must use getattr(tool, 'return_type', None)
so a non-FunctionTool surfaces as RuntimeError("tool contract drift: ...")
not AttributeError("'SimpleNamespace' object has no attribute 'return_type'").

Rationale: see .planning/phases/07-rate-limit-structured-errors-healthz-logs/
07-VERIFICATION.md "Deferred Items" row 1. Today all 6 production tools are
FunctionTool instances, so the lifespan path works. This regression test locks
the intended error surface against future non-FunctionTool registrations
(e.g., a Phase 9 TransformedTool).

Phase 8 Wave 0 — closes CR-02 end-to-end with a regression gate.
"""

from __future__ import annotations

import types

import pytest
from starlette.applications import Starlette

from mcp_zeeker import config
from mcp_zeeker.app import lifespan
from mcp_zeeker.server import mcp


async def test_non_function_tool_raises_runtime_error_not_attribute_error(
    monkeypatch,
) -> None:
    """CR-02: lifespan must raise RuntimeError('tool contract drift'), not AttributeError.

    A non-FunctionTool stand-in (SimpleNamespace without return_type attribute)
    exercises the latent AttributeError path at app.py:59. The fix — using
    getattr(tool, 'return_type', None) — converts the AttributeError into the
    intended contract-drift RuntimeError.

    This test FAILS on the current broken code and PASSES after the Task 3 fix.
    Reference: 07-VERIFICATION.md "Deferred Items" row 1 (CR-02, owner: Phase 8).
    """
    # Build a synthetic non-FunctionTool stand-in.
    # Deliberately do NOT pass a `return_type` keyword — that is the bug surface.
    # Include TOOL_TRAILER in description so the trailer guard at app.py:64
    # does not fire before the return_type guard at app.py:59.
    fake_tool = types.SimpleNamespace(
        name="fake_non_function_tool",
        description=f"some description\n\n{config.TOOL_TRAILER}",
    )

    async def _fake_list_tools():
        return [fake_tool]

    monkeypatch.setattr(mcp, "list_tools", _fake_list_tools)

    # The lifespan async context manager must raise RuntimeError on enter.
    # The match= clause verifies it is the intended contract-drift error,
    # not a different RuntimeError from e.g. build_http_client.
    app = Starlette()
    with pytest.raises(RuntimeError, match="tool contract drift"):
        async with lifespan(app):
            pass  # pragma: no cover — must not reach here
