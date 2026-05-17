---
phase: quick-260517-bki
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/mcp_zeeker/core/datasette_client.py
  - tests/test_datasette_client_retry.py
autonomous: true
requirements:
  - QUICK-260517-bki
must_haves:
  truths:
    - "A Datasette HTTP 400 with JSON body `{\"title\": \"SQL Interrupted\", ...}` raises `QueryTimeoutError` (not bare `UpstreamCallFailed`)."
    - "A non-Datasette HTTP 400 (no JSON body, or JSON body without `title == 'SQL Interrupted'`) continues to raise bare `UpstreamCallFailed` — detection is scoped to status 400 + the explicit marker."
    - "Existing `except UpstreamCallFailed:` handlers in tools still catch the new path, because `QueryTimeoutError` is a subclass of `UpstreamCallFailed` (no caller changes)."
    - "The new branch fires zero times on 2xx, 5xx, or transport-error paths."
    - "All pre-existing tests in `tests/test_datasette_client_retry.py` remain green."
  artifacts:
    - path: "src/mcp_zeeker/core/datasette_client.py"
      provides: "Defensive SQL-Interrupted detection on 400 in `_request_with_retry`, mapping to `QueryTimeoutError`."
      contains: "SQL Interrupted"
    - path: "tests/test_datasette_client_retry.py"
      provides: "Two new regression tests: one asserts the Datasette-shaped 400 raises `QueryTimeoutError`; one asserts a vanilla 400 still raises bare `UpstreamCallFailed`."
      contains: "test_sql_interrupted_400_raises_query_timeout"
  key_links:
    - from: "src/mcp_zeeker/core/datasette_client.py::_request_with_retry"
      to: "QueryTimeoutError (existing class, line 44)"
      via: "new branch reached BEFORE the catch-all `UpstreamCallFailed` raise"
      pattern: "SQL Interrupted.*raise QueryTimeoutError"
    - from: "tests/test_datasette_client_retry.py"
      to: "DatasetteClient._request_with_retry"
      via: "pytest-httpx `httpx_mock.add_response(status_code=400, json={...})`"
      pattern: "title.*SQL Interrupted"
---

<objective>
Fix a production miscategorization: when `data.zeeker.sg` returns HTTP 400 with `{"title": "SQL Interrupted", ...}` (its signal for upstream SQL time-limit exhaustion on a non-indexed scan), `_request_with_retry` currently falls through to the catch-all and raises bare `UpstreamCallFailed` (→ `upstream_unavailable` to the agent, read as "service is down"). The correct semantic is the already-existing `query_timeout` catalog code, which is raised via the existing `QueryTimeoutError` subclass.

Purpose: the agent in production (real Claude session on `zeeker-judgements`/`judgments_fragments`) gave up on a recoverable query-shape problem because the error code told it the upstream was unreachable. Routing through `QueryTimeoutError` lets the agent reason about it as a timeout and react accordingly.

Output: a small, scoped branch in `_request_with_retry` that fires ONLY on status 400 with the explicit `"title": "SQL Interrupted"` JSON marker, plus two regression tests (positive + negative scope check) added to the existing pytest-httpx suite. No new dependencies. No change to the locked error catalog. No change to `core/errors.py`. No caller-side changes.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md
@.planning/PROJECT.md

<interfaces>
<!-- Extracted from src/mcp_zeeker/core/datasette_client.py and core/errors.py. -->
<!-- Executor uses these directly; no codebase scavenger-hunt needed. -->

From src/mcp_zeeker/core/datasette_client.py:
- `class UpstreamCallFailed(Exception)` — constructor `__init__(self, message: str, *, status: int | None = None)`. Existing catch-all at end of `_request_with_retry` raises this for any non-2xx, non-(502/503/504) status.
- `class QueryTimeoutError(UpstreamCallFailed)` — already defined (line 44). Subclass of `UpstreamCallFailed` so existing `except UpstreamCallFailed:` handlers in `tools/*.py` continue to catch it. Tool handlers distinguish via `isinstance(exc, QueryTimeoutError)` and map to the `query_timeout` catalog code.
- `async def _request_with_retry(self, method, url, **kw) -> httpx.Response` — current shape (lines 141-192):
  - retry loop `for attempt in (0, 1)`
  - `except httpx.TimeoutException` → raise `QueryTimeoutError` (existing, line 157-158)
  - `except httpx.RequestError` → raise `UpstreamCallFailed` (line 159-161)
  - 502/503 with jitter retry
  - 504 → immediate `UpstreamCallFailed(..., status=504)`
  - 2xx → return
  - **catch-all (line 189-191)**: `raise UpstreamCallFailed(f"upstream {resp.status_code} on {url}", status=resp.status_code)` — this is where the SQL-Interrupted 400 currently falls.

From src/mcp_zeeker/core/errors.py:
- `CATALOG` already contains `"query_timeout"` (index 8) — no catalog change required.
- `raise_query_timeout()` is the canonical raise helper; tool handlers map `QueryTimeoutError` → `raise_query_timeout()`. This wiring stays untouched.

From tests/test_datasette_client_retry.py (existing style — match exactly):
- Module-level docstring + `from __future__ import annotations` + `import asyncio` + `from unittest.mock import AsyncMock, patch` + `import httpx, pytest, pytest_httpx`.
- `@pytest.fixture def client(httpx_mock) -> DatasetteClient` returns `DatasetteClient(httpx.AsyncClient(base_url=config.UPSTREAM_URL))`.
- `async def test_*(httpx_mock, client) -> None:` signatures.
- Pattern: `httpx_mock.add_response(status_code=..., json=...)` then `await client._request_with_retry("GET", "/test.json")` inside a `pytest.raises(...)` block.
- The file does NOT carry an explicit `pytestmark = pytest.mark.asyncio` — `asyncio_mode = "auto"` is configured project-wide (per CLAUDE.md tech stack note on `pytest-asyncio`).
</interfaces>

<production_incident>
- Real upstream response shape (from bug_context): `status=400`, JSON body `{"ok": false, "error": "SQL query took too long. The time limit is controlled by ...", "status": 400, "title": "SQL Interrupted"}`.
- Triggered by: `query_table(database="zeeker-judgements", table="judgments_fragments", filters=[{"column": "source_url", "op": "exact", "value": "<judgment URL>"}], limit=100)` — upstream `judgments_fragments.judgment_id` lacks an index; 81,812 rows × 1s `sql_time_limit_ms` → SQL Interrupted.
- Other fragments tables (`pdpc.enforcement_decisions_fragments` 1,711 rows, `sglawwatch.about_singapore_law_fragments` 2,454 rows) do NOT trip this — full-scan completes inside 1s.
- Quick-task id `260517-bki` is the WHY for the new branch's terse comment.
</production_incident>

</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Map Datasette SQL-Interrupted 400 → QueryTimeoutError (test-first)</name>
  <files>tests/test_datasette_client_retry.py, src/mcp_zeeker/core/datasette_client.py</files>
  <behavior>
    Two new tests added to `tests/test_datasette_client_retry.py`, matching the existing pytest-httpx style (`async def test_*(httpx_mock, client) -> None:`, no decorator):

    Test 1 — `test_sql_interrupted_400_raises_query_timeout`:
      - `httpx_mock.add_response(status_code=400, json={"ok": False, "error": "SQL query took too long. The time limit is controlled by ...", "status": 400, "title": "SQL Interrupted"})`
      - `with pytest.raises(QueryTimeoutError): await client._request_with_retry("GET", "/zeeker-judgements/judgments_fragments.json")`
      - Assert no retry happens: `assert len(httpx_mock.get_requests()) == 1` and `mock_sleep.assert_not_called()` (wrap in `patch.object(asyncio, "sleep", new_callable=AsyncMock)` to mirror sibling tests).
      - Also assert `isinstance(exc, UpstreamCallFailed)` is True (subclass relationship preserved) — use `pytest.raises(QueryTimeoutError) as excinfo` and `assert isinstance(excinfo.value, UpstreamCallFailed)`.

    Test 2 — `test_vanilla_400_still_raises_upstream_call_failed`:
      - `httpx_mock.add_response(status_code=400, json={"error": "some other 400 reason"})` (no `title` key).
      - `with pytest.raises(UpstreamCallFailed) as excinfo: await client._request_with_retry("GET", "/test.json")`.
      - Assert `not isinstance(excinfo.value, QueryTimeoutError)` — the new branch must NOT fire on vanilla 400s.
      - Optionally add a third sub-case in the same test (or a third test) where status=400 and the body is non-JSON (e.g. `httpx_mock.add_response(status_code=400, content=b"<html>...</html>")`) to confirm the JSON-parse defensive guard works. If included, also assert `pytest.raises(UpstreamCallFailed)` (not `QueryTimeoutError`).

    RED step: run `uv run pytest tests/test_datasette_client_retry.py::test_sql_interrupted_400_raises_query_timeout -x` and confirm it FAILS against the unmodified `_request_with_retry` (currently raises bare `UpstreamCallFailed`, not `QueryTimeoutError`).
  </behavior>
  <action>
    Step A — add the two tests above to the END of `tests/test_datasette_client_retry.py`. Use the exact module conventions (no decorator, fixtures `httpx_mock` + `client`, `from mcp_zeeker.core.datasette_client import DatasetteClient, QueryTimeoutError, UpstreamCallFailed` is already imported at the top). Run RED step; confirm new tests fail and ALL pre-existing tests still pass.

    Step B — modify `src/mcp_zeeker/core/datasette_client.py::_request_with_retry`. INSERT a new branch BEFORE the existing catch-all on line 189-191 (i.e. after the `if 200 <= resp.status_code < 300: return resp` line). The branch logic:

      1. Gate on `resp.status_code == 400` (cheap exit for non-400s).
      2. Try `body = resp.json()` inside a `try / except (ValueError, json.JSONDecodeError)` — `json` is already imported at the top of the module (line 17); `httpx.Response.json()` raises `json.JSONDecodeError` (a subclass of `ValueError`) on non-JSON bodies. On parse failure, `pass` / fall through to the catch-all.
      3. Inside the try: if `isinstance(body, dict) and body.get("title") == "SQL Interrupted"` → `raise QueryTimeoutError(f"upstream SQL interrupted on {url}") from None`. Use `from None` so the traceback does not leak the inner `resp.json()` call frame.
      4. Anything else (not a dict, no `title`, different `title`) → fall through to the existing catch-all `UpstreamCallFailed`.

    The branch MUST NOT retry. It MUST NOT log to structlog (the surrounding code does not log here either; observability is a tool-handler concern). It MUST NOT touch the catalog. It MUST NOT touch `core/errors.py`.

    Add a single WHY comment above the branch — terse, matching the style of sibling comments in this function:

      ```
      # WR-260517-bki: Datasette signals upstream SQL time-limit exhaustion
      # as HTTP 400 with {"title": "SQL Interrupted"}. Surface as
      # QueryTimeoutError so agents read it as `query_timeout`, not
      # `upstream_unavailable`. Scoped to 400 + explicit marker; vanilla 400s
      # fall through. No retry (re-issuing will time out the same way).
      ```

    The comment is the ONLY new prose in the source file. No docstring update on `_request_with_retry` is required (the existing docstring's "Other status: raise UpstreamCallFailed" line remains technically true — `QueryTimeoutError` IS an `UpstreamCallFailed` subclass).

    GREEN step: re-run the full file `uv run pytest tests/test_datasette_client_retry.py -x`; all tests (pre-existing 7 + new 2) MUST pass.
  </action>
  <verify>
    <automated>uv run pytest tests/test_datasette_client_retry.py -x</automated>
  </verify>
  <done>
    - `tests/test_datasette_client_retry.py` contains `test_sql_interrupted_400_raises_query_timeout` and `test_vanilla_400_still_raises_upstream_call_failed` (and optionally a non-JSON-body sub-case), all passing.
    - `src/mcp_zeeker/core/datasette_client.py::_request_with_retry` has a new branch raising `QueryTimeoutError` on `status_code == 400` AND `body.get("title") == "SQL Interrupted"`, gated by a `try/except` around `resp.json()` to handle non-JSON 400s defensively.
    - Pre-existing 7 tests in the file still pass.
    - No changes to `core/errors.py`, `CATALOG`, `tools/*.py`, or any caller.
    - Branch fires ONLY for status 400 with the explicit marker — verified by `test_vanilla_400_still_raises_upstream_call_failed`.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| upstream Datasette → DatasetteClient | Upstream JSON body is untrusted; only the literal string `"SQL Interrupted"` in the `title` field controls a code-path branch. No upstream string is interpolated into the final ToolError message that reaches the LLM (the tool-handler-level mapping via `raise_query_timeout()` still emits the FIXED literal `"query_timeout: Query timed out"` per INJ-05 / T-03-01). |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-quick-260517-bki-01 | Tampering | `_request_with_retry` SQL-Interrupted branch | mitigate | Match is exact-string `body.get("title") == "SQL Interrupted"` AND `status_code == 400`; non-dict bodies, missing key, and different titles fall through. Upstream cannot trick the gate into running for a 200 OK or a 5xx. |
| T-quick-260517-bki-02 | Information Disclosure | new `QueryTimeoutError` raise message `f"upstream SQL interrupted on {url}"` | accept | The URL is server-internal (Datasette path like `/zeeker-judgements/judgments_fragments.json`) and is only used for the exception's `str(exc)` — which is consumed by structlog logs, NOT echoed into the tool-handler's `ToolError` (per `raise_query_timeout()` in `core/errors.py`, which emits a FIXED literal and discards the exception arg). Matches the existing convention used by the sibling `UpstreamCallFailed(f"upstream {resp.status_code} on {url}", ...)` raise on line 189-191. |
| T-quick-260517-bki-03 | Denial of Service | retry policy on the new branch | mitigate | The new branch raises immediately, no retry. Re-issuing the same request would time out the same way (missing upstream index is not transient), so retrying would only amplify the load on upstream during exhaustion. Matches D-16 ("no retry on transport errors") philosophy. |
</threat_model>

<verification>
1. `uv run pytest tests/test_datasette_client_retry.py -x` — all 9 tests (7 existing + 2 new) pass.
2. `uv run pytest -x` — full suite still green (no regressions in tool handlers that catch `UpstreamCallFailed`).
3. Grep audit: `grep -n "SQL Interrupted" src/mcp_zeeker/core/datasette_client.py` returns exactly one hit (the new branch); no occurrences elsewhere in `src/`.
4. Grep audit: `grep -nE "raise QueryTimeoutError" src/mcp_zeeker/core/datasette_client.py` returns exactly TWO hits — the existing `httpx.TimeoutException` catch (line ~158) and the new 400/SQL-Interrupted branch.
5. Manual diff review: no changes to `src/mcp_zeeker/core/errors.py`, no changes to `CATALOG`, no changes to any file under `src/mcp_zeeker/tools/`.
</verification>

<success_criteria>
- The two new regression tests pass.
- The 7 pre-existing tests in `test_datasette_client_retry.py` continue to pass.
- The full project test suite (`uv run pytest -x`) is green.
- The Datasette SQL-Interrupted 400 response shape now raises `QueryTimeoutError` (which existing handlers in `tools/retrieval.py` and `tools/search.py` already map to the `query_timeout` catalog code via `isinstance(exc, QueryTimeoutError)`).
- Vanilla 400 responses (no marker / non-JSON body) continue to raise bare `UpstreamCallFailed` — detection is scoped.
- No catalog change. No caller change. No new dependency.
</success_criteria>

<output>
After completion, create `.planning/quick/260517-bki-fix-datasette-client-error-mapping-datas/260517-bki-SUMMARY.md` recording: the exact source diff in `_request_with_retry`, the two new test names, the `uv run pytest tests/test_datasette_client_retry.py` pass count, and a one-line note that the locked error catalog (D3-12) was NOT modified.
</output>
