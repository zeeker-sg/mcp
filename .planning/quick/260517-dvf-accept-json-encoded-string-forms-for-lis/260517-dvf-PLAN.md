---
phase: quick-260517-dvf
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/mcp_zeeker/tools/_param_coercion.py
  - src/mcp_zeeker/tools/search.py
  - src/mcp_zeeker/tools/retrieval.py
  - tests/tools/test_param_coercion.py
autonomous: true
requirements:
  - QUICK-260517-dvf
must_haves:
  truths:
    - "Calling `search(query='x', databases=['pdpc'])` (direct list) still succeeds — coercion is a no-op on already-list input."
    - "Calling `search(query='x', databases='[\"pdpc\"]')` (JSON-encoded string) succeeds and behaves identically to the direct-list call — `databases` reaches the handler body as `['pdpc']`."
    - "Calling `search(query='x', databases='[notvalid')` (malformed JSON) still raises pydantic's `list_type` validation error, unchanged — coercion is a soft pre-step, never a new failure mode."
    - "Same three behaviors hold for `query_table(filters=...)` and `query_table(columns=...)` — three params total, one shared coercion helper."
    - "The published MCP tool schema for `search.databases`, `query_table.filters`, `query_table.columns` is unchanged from the client's perspective (still `list[...]` / `list[Filter]`) — `BeforeValidator` is invisible to JSON Schema generation."
    - "`json.loads('null')` returns `None`, and the helper passes that through to pydantic, which accepts it on `list[...] | None` fields — no special-casing needed."
    - "`fetch`, `list_databases`, `list_tables`, `describe_table` are untouched — they have no list-typed params (orchestrator audit-confirmed before spawning this plan)."
  artifacts:
    - path: "src/mcp_zeeker/tools/_param_coercion.py"
      provides: "Single shared `_coerce_json_list` helper. Sole audit point for the JSON-string→list pre-coercion behavior."
      contains: "WR-260517-dvf"
    - path: "src/mcp_zeeker/tools/search.py"
      provides: "`search.databases` annotation gains `BeforeValidator(_coerce_json_list)` inside its `Annotated[...]` stack."
      contains: "BeforeValidator"
    - path: "src/mcp_zeeker/tools/retrieval.py"
      provides: "`query_table.filters` and `query_table.columns` annotations gain `BeforeValidator(_coerce_json_list)`."
      contains: "BeforeValidator"
    - path: "tests/tools/test_param_coercion.py"
      provides: "Parametrized regression coverage — for each of the 3 params: (a) direct list passes, (b) JSON-encoded-string is coerced, (c) malformed JSON still raises `list_type`."
      contains: "WR-260517-dvf"
  key_links:
    - from: "src/mcp_zeeker/tools/search.py::search.databases"
      to: "src/mcp_zeeker/tools/_param_coercion.py::_coerce_json_list"
      via: "`Annotated[list[str] | None, BeforeValidator(_coerce_json_list), Field(...)]`"
      pattern: "BeforeValidator\\(_coerce_json_list\\)"
    - from: "src/mcp_zeeker/tools/retrieval.py::query_table.filters"
      to: "src/mcp_zeeker/tools/_param_coercion.py::_coerce_json_list"
      via: "`Annotated[list[Filter] | None, BeforeValidator(_coerce_json_list), Field(...)]`"
      pattern: "BeforeValidator\\(_coerce_json_list\\)"
    - from: "src/mcp_zeeker/tools/retrieval.py::query_table.columns"
      to: "src/mcp_zeeker/tools/_param_coercion.py::_coerce_json_list"
      via: "`Annotated[list[str] | None, BeforeValidator(_coerce_json_list), Field(...)]`"
      pattern: "BeforeValidator\\(_coerce_json_list\\)"
    - from: "tests/tools/test_param_coercion.py"
      to: "search / query_table FastMCP-validated dispatch path"
      via: "direct `await search(...)` / `await query_table(...)` invocations through the same Pydantic validation entry that FastMCP uses"
      pattern: "_coerce_json_list|databases=.*\\[\""
---

<objective>
Make `search` and `query_table` accept JSON-encoded-string forms for their list-typed params so that MCP clients which `JSON.stringify` complex args (observed in real Claude sessions) reach pydantic validation as a `list`, not a raw `str`. Concretely: add one shared `BeforeValidator` helper and wire it into the three affected param annotations.

Purpose: a real production Claude session sent `databases='["zeeker-judgements"]'` and `filters='[{"column":"case_name","op":"contains","value":"Law Society"}]'`, both of which pydantic 2.13 rejected with `type=list_type, input_value='[...]' (str), input_type=str`. The agent treated this as the framework being broken and gave up on the user's question. We can't force every MCP client to stop double-encoding, but pydantic's `BeforeValidator` is a clean coercion seam that runs *before* type-checking, so a successful `json.loads` returning a list flows into the same validation pipeline as a directly-passed list — and a malformed JSON string falls through unchanged, preserving the existing error.

Output: one new helper module (`tools/_param_coercion.py`) with a single private function, three annotation edits across two existing tool files, and one new parametrized regression test file. No catalog change, no schema change visible to clients, no behavioral change on any valid-list input, no new dependency (`json` is stdlib).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@CLAUDE.md

<interfaces>
<!-- Extracted from the three target files + retrieval_models.py + filter_compiler.py. -->
<!-- Executor uses these signatures directly; no codebase scavenger-hunt needed. -->

From src/mcp_zeeker/tools/search.py (lines 95-119):
```python
async def search(
    query: Annotated[str, Field(description="Full-text query (FTS5 phrase-wrapped server-side).")],
    databases: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Optional subset of databases to search. Defaults to all "
                "configured databases. Pass empty list for same effect as None."
            ),
        ),
    ] = None,
    limit: Annotated[int, Field(default=20, ge=1, le=100, description="...")] = 20,
) -> Envelope:
```

From src/mcp_zeeker/tools/retrieval.py (lines 109-158):
```python
async def query_table(
    database: Annotated[str, Field(description="Database name (e.g. 'zeeker-judgements')")],
    table: Annotated[str, Field(description="Table name (e.g. 'judgments')")],
    filters: Annotated[
        list[Filter] | None,
        Field(
            default=None,
            description=(
                "List of filter clauses. Each clause has {column, op, value}. "
                "Supported ops: exact, not, contains, startswith, endswith, "
                "gt, gte, lt, lte, in, notin, isnull, notnull. "
                "contains / startswith / endswith are case-insensitive for ASCII."
            ),
        ),
    ] = None,
    sort: Annotated[str | None, Field(default=None, description="...")] = None,
    limit: Annotated[int, Field(default=50, ge=1, le=200, description="...")] = 50,
    cursor: Annotated[str | None, Field(default=None, description="...")] = None,
    columns: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="Explicit column allow-list; when omitted, returns the table's light set.",
        ),
    ] = None,
) -> Envelope:
```

From src/mcp_zeeker/core/filter_compiler.py:
- `class Filter(BaseModel)` — `model_config = ConfigDict(extra="forbid")`, fields `column: str`, `op: FilterOp` (Literal of 13 op strings), `value: Any = None`. Pydantic coerces a dict like `{"column": "case_name", "op": "contains", "value": "Law Society"}` into a `Filter` automatically when `list[Filter]` is the declared type.

From src/mcp_zeeker/tools/__init__.py:
- Empty file. A new sibling module `src/mcp_zeeker/tools/_param_coercion.py` is the natural location for the shared helper; leading underscore signals "package-private, do not import from outside `mcp_zeeker.tools`".

Tool surface confirmed clean (no list-typed params, no change needed): `fetch` (db, table, url — all strings), `list_databases` (no params), `list_tables` (db: str), `describe_table` (db, table). Re-confirmed by the orchestrator before spawning this plan.

Pydantic 2.13 `BeforeValidator` semantics (relevant points):
- Runs BEFORE the declared type's own validation step. Return value is then validated as the declared type.
- Order inside `Annotated[T, BeforeValidator(...), Field(...)]` is positionally tolerant; convention here is `BeforeValidator` before `Field` so the coercion is visually next to the type.
- Does NOT alter the generated JSON Schema — clients still see `array` / `array of Filter`. The coercion is purely server-side.
- Raises by re-raising; if the helper returns the input unchanged on parse failure, pydantic's standard `list_type` error fires with the original `str` value (which is exactly what we want).
</interfaces>

<production_incident>
- Real Claude session, `mcp.zeeker.sg`, 2026-05-17.
- Failed call 1: `search(databases='["zeeker-judgements"]', query='Law Society v X')` → `1 validation error for call[search] databases: Input should be a valid list [type=list_type, input_value='["zeeker-judgements"]' (str), input_type=str]`.
- Failed call 2: `query_table(database='zeeker-judgements', table='judgments', filters='[{"column":"case_name","op":"contains","value":"Law Society"}]', sort='-decision_date', limit=20)` → same error shape, on `filters`.
- Agent's read of the situation: "the MCP is rejecting the filter param shape — that's a framework quirk I can work around at build time" → silent fallback to web-search, user got no Singapore-curated results.
- Quick-task id `260517-dvf` is the WHY for the helper's terse comment.
</production_incident>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add `_coerce_json_list` helper, wire it into 3 param annotations, ship parametrized regression test (RED → GREEN)</name>
  <files>src/mcp_zeeker/tools/_param_coercion.py, src/mcp_zeeker/tools/search.py, src/mcp_zeeker/tools/retrieval.py, tests/tools/test_param_coercion.py</files>
  <behavior>
    Parametrized regression test file `tests/tools/test_param_coercion.py` covers, per the three affected params (`search.databases`, `query_table.filters`, `query_table.columns`):

    Test shape A — direct-list passthrough (3 cases via `@pytest.mark.parametrize`):
      - `search(query="appeal", databases=["pdpc-enforcement-decisions"])` returns an `Envelope` (or raises a domain ToolError like `upstream_unavailable` due to no httpx_mock — the assertion is "did NOT raise a pydantic ValidationError or `list_type` ToolError"). Pin the assertion to "no `list_type` substring in any raised error".
      - `query_table(database="zeeker-judgements", table="judgments", filters=[{"column": "case_name", "op": "contains", "value": "Law Society"}])` — same assertion shape. Pydantic coerces the inner dict to `Filter` automatically; no `list_type` error.
      - `query_table(database="zeeker-judgements", table="judgments", columns=["case_name", "decision_date"])` — same assertion shape.

    Test shape B — JSON-encoded-string is coerced (3 cases):
      - `search(query="appeal", databases='["pdpc-enforcement-decisions"]')` — same "no `list_type`" assertion as case A. The behavior MUST be equivalent: the request progresses past pydantic validation into the handler body.
      - `query_table(database="zeeker-judgements", table="judgments", filters='[{"column": "case_name", "op": "contains", "value": "Law Society"}]')` — same.
      - `query_table(database="zeeker-judgements", table="judgments", columns='["case_name", "decision_date"]')` — same.

    Test shape C — malformed JSON still raises pydantic's `list_type` error (3 cases):
      - `search(query="appeal", databases='[notvalid')` → raises (pydantic `ValidationError` or fastmcp `ToolError` wrapping it) AND the error string contains the substring `list_type` (pydantic's canonical error tag). The exact wrapping class depends on how FastMCP surfaces validation errors when the handler is awaited directly; use `pytest.raises(Exception)` and assert on `str(excinfo.value)` containing `"list_type"`. If the project's existing direct-call tests use `pytest.raises(ValidationError)` directly, mirror that.
      - `query_table(..., filters='[{bad json')` → same.
      - `query_table(..., columns='[bad json')` → same.

    Implementation notes for the test file:
    - Use `from __future__ import annotations` per project convention.
    - Use the same `datasette_client` fixture style as `tests/tools/test_search_errors.py` and `tests/tools/test_query_table_errors.py` (httpx_mock + DatasetteClient.bind/reset). Set `is_optional=True` on any `httpx_mock.add_response` you wire up — the tests only need to get *past* pydantic validation; whether the upstream call later fails is irrelevant to the regression. If the simplest approach is to NOT pre-wire any upstream response and accept that test A/B will surface an `upstream_unavailable` or `unknown_database` ToolError, that's fine — assertion is purely "the error, if any, is NOT a `list_type` validation error". Document this choice with a one-line comment at the top of the test class.
    - Pick a `database` value that survives `ALLOWED_DATABASES` membership check (e.g. `"pdpc-enforcement-decisions"`, `"zeeker-judgements"` — both are in the configured set per existing test fixtures).
    - The test file does NOT need `pytestmark = pytest.mark.asyncio` — `asyncio_mode = "auto"` is project-wide (per CLAUDE.md tech stack section).
    - Use `pytest.mark.parametrize` with descriptive `ids=` so each case is independently visible in test output (e.g. `id="search.databases:direct_list"`, `id="query_table.filters:json_string"`, etc.).

    RED step: run `uv run pytest tests/tools/test_param_coercion.py -x` against the unmodified tool files. The 3 cases in Test shape B (JSON-encoded-string) MUST FAIL with a pydantic `list_type` error — that's the bug we're fixing. The 3 cases in Test shape A (direct list) and 3 cases in Test shape C (malformed JSON) should already pass before the source change — they encode the invariants we MUST preserve.
  </behavior>
  <action>
    Step A — create `src/mcp_zeeker/tools/_param_coercion.py`. The full module body (single helper, no class wrapper, no public exports beyond `_coerce_json_list`):

    Module docstring should be terse — explain WHY (production-observed client double-encoding via JSON.stringify), name the quick id (WR-260517-dvf), enumerate the 3 call sites (`search.databases`, `query_table.filters`, `query_table.columns`), and state the two invariants:
      1. Non-string input is returned unchanged → no behavioral change for callers that already pass a list.
      2. String input that fails `json.loads` is returned unchanged → pydantic's standard `list_type` error fires verbatim, no new failure mode.

    The function signature: `def _coerce_json_list(v: object) -> object:` — returns the same object on the no-op path so pydantic sees the original `str` and emits its canonical error. Imports: `import json` at module top.

    Function body — exactly these branches, no more:
      - If `isinstance(v, str)`: wrap a single `json.loads(v)` in `try / except (ValueError, json.JSONDecodeError)`. On success: return the decoded value (it may be a `list`, `dict`, `None`, etc.; pydantic checks list-ness next — that's the contract). On exception: `return v` (pass-through; pydantic emits the standard `list_type` error against the original string).
      - Otherwise: `return v` unchanged.

    Critical constraints — encode each as an inline comment, no separate explanation prose:
      - NO recursion. If `json.loads("\"foo\"")` returns a `str`, we do NOT re-decode. Single attempt only. The observed client behavior is single-level `JSON.stringify`; protecting against double-encoded payloads is out of scope and would mask malformed input.
      - NO type narrowing. The helper does not check whether the decoded value is a list — pydantic does that immediately after, and its error message is the canonical one our tests assert against.
      - NO logging. This is a hot path on every tool dispatch; structlog calls here would be noise. If we ever need observability, add it at the middleware layer.
      - WR-260517-dvf comment is mandatory. Format matches the WR-260517-bki precedent in `datasette_client.py`:

        ```
        # WR-260517-dvf: some MCP clients (observed in real Claude sessions)
        # JSON.stringify list-typed args. Pydantic 2.13 then rejects them with
        # `type=list_type, input_type=str`. Pre-coerce strings via json.loads
        # so successful decodes flow through pydantic's normal list validation;
        # malformed input falls through to pydantic's standard list_type error.
        # No recursion (single decode attempt), no type narrowing, no logging.
        ```

    Step B — edit `src/mcp_zeeker/tools/search.py`:
      1. Add `from pydantic import BeforeValidator, Field` (extend the existing `from pydantic import Field` import on line 45).
      2. Add `from mcp_zeeker.tools._param_coercion import _coerce_json_list` to the imports block (preserve alphabetical-within-group convention if observed in nearby modules).
      3. Modify the `databases` annotation (lines 100-109) to insert `BeforeValidator(_coerce_json_list)` between the type union and the `Field(...)`:

         ```python
         databases: Annotated[
             list[str] | None,
             BeforeValidator(_coerce_json_list),
             Field(
                 default=None,
                 description=(
                     "Optional subset of databases to search. Defaults to all "
                     "configured databases. Pass empty list for same effect as None."
                 ),
             ),
         ] = None,
         ```

      Do NOT change the `description` text — the published schema MUST stay byte-identical from the client's perspective. Do NOT touch `query` or `limit`. Do NOT touch the validation-order docstring or any handler-body logic (the runtime check on `target_dbs = list(databases) if databases else list(config.ALLOWED_DATABASES)` already handles None, empty list, and now-coerced list identically).

    Step C — edit `src/mcp_zeeker/tools/retrieval.py`:
      1. Add `BeforeValidator` to the existing `from pydantic import Field` import (line 53).
      2. Add `from mcp_zeeker.tools._param_coercion import _coerce_json_list` to the imports block (place near other `from mcp_zeeker.tools.*` imports if any; otherwise top of the `mcp_zeeker.*` group).
      3. Modify the `filters` annotation (lines 112-123) — insert `BeforeValidator(_coerce_json_list)` between `list[Filter] | None` and `Field(...)`. Description and default unchanged.
      4. Modify the `columns` annotation (lines 151-157) — insert `BeforeValidator(_coerce_json_list)` between `list[str] | None` and `Field(...)`. Description and default unchanged.
      5. Do NOT touch `database`, `table`, `sort`, `limit`, `cursor`. Do NOT touch the validation-order docstring (D3-08) — the per-call `Filter.model_validate(f)` normalization step on line 203-205 already handles dicts vs Filter instances regardless of whether the outer list came from a direct call or from BeforeValidator's decoded output.

    Step D — create `tests/tools/test_param_coercion.py` per the `<behavior>` block above. Mirror the fixture style of `tests/tools/test_search_errors.py` (httpx_mock + DatasetteClient.bind/reset). Use `pytest.mark.parametrize` for the 9 cases (3 params × 3 shapes); a single parametrize call with `ids=` is cleaner than three separate ones.

    GREEN step: run `uv run pytest tests/tools/test_param_coercion.py -x` after all four files exist. ALL 9 cases MUST pass. Then run `uv run pytest -x` for the full suite to confirm zero regressions.

    Hygiene step: `uv run ruff format src/mcp_zeeker/tools/_param_coercion.py src/mcp_zeeker/tools/search.py src/mcp_zeeker/tools/retrieval.py tests/tools/test_param_coercion.py` then `uv run ruff check src/mcp_zeeker/tools/_param_coercion.py src/mcp_zeeker/tools/search.py src/mcp_zeeker/tools/retrieval.py tests/tools/test_param_coercion.py` — both clean.
  </action>
  <verify>
    <automated>uv run pytest tests/tools/test_param_coercion.py -x && uv run pytest -x && uv run ruff format --check src/mcp_zeeker/tools/_param_coercion.py src/mcp_zeeker/tools/search.py src/mcp_zeeker/tools/retrieval.py tests/tools/test_param_coercion.py && uv run ruff check src/mcp_zeeker/tools/_param_coercion.py src/mcp_zeeker/tools/search.py src/mcp_zeeker/tools/retrieval.py tests/tools/test_param_coercion.py</automated>
  </verify>
  <done>
    - `src/mcp_zeeker/tools/_param_coercion.py` exists with a single `_coerce_json_list(v: object) -> object` function and the WR-260517-dvf WHY comment.
    - `src/mcp_zeeker/tools/search.py::search.databases` annotation has `BeforeValidator(_coerce_json_list)` inserted between the type union and `Field(...)`. No other changes to that file.
    - `src/mcp_zeeker/tools/retrieval.py::query_table.filters` and `query_table.columns` annotations have `BeforeValidator(_coerce_json_list)` inserted between their type unions and `Field(...)`. No other changes to that file.
    - `tests/tools/test_param_coercion.py` exists with 9 parametrized cases (3 params × {direct list, JSON-string, malformed JSON}); all pass.
    - `uv run pytest -x` is green (no regressions).
    - `uv run ruff format --check` and `uv run ruff check` are both clean on the four touched files.
    - Grep audit: `grep -n "_coerce_json_list" src/mcp_zeeker/tools/` returns exactly 4 hits (definition in `_param_coercion.py`, import + use in `search.py`, import + 2 uses in `retrieval.py`).
    - Grep audit: `grep -rn "BeforeValidator" src/mcp_zeeker/tools/` returns exactly 3 hits (one per affected param).
    - No changes to `core/errors.py`, `CATALOG`, `tools/discovery.py`, `tools/discovery_models.py`, `tools/search_models.py`, `tools/retrieval_models.py`, or any caller.
    - The MCP tool input schemas (as observed by a client via the MCP `tools/list` response) for `search`, `query_table`, `list_databases`, `list_tables`, `describe_table`, `fetch` are unchanged from before the patch — `BeforeValidator` is invisible to JSON Schema generation by design.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| MCP client → FastMCP dispatch | Tool args are untrusted JSON. Existing pydantic validation is the trust boundary; `BeforeValidator` runs INSIDE that boundary (as part of the same pydantic validation step) — it does not bypass any check, it only widens the accepted input shape for one type (`list[...]`) by one form (`str`-that-is-JSON-array). |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-quick-260517-dvf-01 | Tampering | `_coerce_json_list` JSON decode of attacker-controlled string | mitigate | `json.loads` is the Python stdlib parser — no eval, no code execution path. Output is a plain Python `list` / `dict` / scalar / `None`. Pydantic then re-validates the result against the declared type (`list[str]` / `list[Filter]`); a decoded dict-when-list-expected, or a list containing non-string / non-Filter-shaped items, raises the standard pydantic validation error. No new code path that trusts the decoded structure. |
| T-quick-260517-dvf-02 | Denial of Service | unbounded `json.loads` input size | accept | FastMCP / Starlette already cap request body size via Uvicorn's default `--limit-request-body` (and Datasette's own upstream limits are independently enforced). The `json.loads` call here is bounded by the same request-body cap that already exists for any tool arg. No new DoS surface; we are not introducing a separate parser or a deeper recursion than `json.loads` itself provides (which is iterative for arrays/objects in CPython 3.10+). |
| T-quick-260517-dvf-03 | Information Disclosure | exception messages leaking decoded payload | mitigate | The helper does NOT construct any error message. On `json.JSONDecodeError`, it silently falls through and lets pydantic emit its standard `list_type` error — and pydantic's `list_type` error in 2.13 includes the original input value in the `input_value` field of the error context. This is unchanged from current behavior: today, when a string is passed where a list is expected, pydantic ALREADY echoes that string in the error. Our change does not increase the disclosure surface. (The locked T-03-01 / D3-09 / INJ-05 discipline applies to tool-handler-level `ToolError` messages emitted via `raise_*` helpers — pydantic's pre-handler validation errors are a separate channel that already echoes input by design, and is documented as such in the published tool surface.) |
| T-quick-260517-dvf-04 | Elevation of Privilege | `BeforeValidator` bypassing downstream validation | mitigate | Pydantic 2.13 runs all post-validators (the declared type check, `Field` constraints, model-level validators) AFTER `BeforeValidator`. Concretely: for `search.databases`, the `D4-10 unknown_database` check (`if db not in config.ALLOWED_DATABASES: raise_unknown_database(db)`) on line 153-155 still runs against every element. For `query_table.filters`, the per-item `Filter.model_validate(f)` normalization (line 203-205), the `extra="forbid"` Filter config, and the `FilterOp` Literal-of-13 still run. For `query_table.columns`, the `_visible_columns` allow-list check (line 245-247) still runs. The coercion only changes the SHAPE of the input from `str` to `list`; the security gates downstream are unchanged. |
</threat_model>

<verification>
1. `uv run pytest tests/tools/test_param_coercion.py -x` — all 9 parametrized cases pass.
2. `uv run pytest -x` — full suite green, zero regressions.
3. `uv run ruff format --check` + `uv run ruff check` clean on the 4 touched files.
4. Grep audit (definition): `grep -n "_coerce_json_list" src/mcp_zeeker/tools/_param_coercion.py` returns exactly 1 hit on the `def` line.
5. Grep audit (call sites): `grep -rn "BeforeValidator(_coerce_json_list)" src/mcp_zeeker/tools/` returns exactly 3 hits (search.databases, query_table.filters, query_table.columns).
6. Grep audit (catalog untouched): `git diff src/mcp_zeeker/core/errors.py` is empty; `git diff src/mcp_zeeker/tools/discovery.py` is empty; `git diff src/mcp_zeeker/tools/retrieval_models.py` is empty; `git diff src/mcp_zeeker/tools/search_models.py` is empty; `git diff src/mcp_zeeker/tools/discovery_models.py` is empty.
7. Schema audit: spot-check that `BeforeValidator` is invisible to FastMCP's JSON Schema generation — `uv run python -c "from mcp_zeeker.tools.search import search; from fastmcp.tools.tool import FunctionTool; tool = FunctionTool.from_function(search); print(tool.parameters)"` should still emit `"type": "array"` (or equivalent) for `databases`. (This is a property of pydantic 2.13's `BeforeValidator` by design; the check is a paranoia confirmation.)
8. Manual diff review: only four files changed (`_param_coercion.py` created, `search.py` edited, `retrieval.py` edited, `test_param_coercion.py` created).
</verification>

<success_criteria>
- All 9 new parametrized regression cases pass.
- Full project test suite (`uv run pytest -x`) is green.
- Ruff format and check are clean on all four touched files.
- A real Claude session sending `databases='["zeeker-judgements"]'` or `filters='[{...}]'` (i.e. the production-observed shape) now reaches the tool-handler body and returns a normal envelope (or a domain error like `unknown_database` for a bad DB) — never a pydantic `list_type` error.
- A real Claude session sending `databases=["zeeker-judgements"]` (i.e. a direct list, the canonical shape) behaves identically to before — no regression.
- A real Claude session sending malformed JSON for these params (e.g. `databases='[bad'`) gets the same pydantic `list_type` error as before — detection of malformed input is preserved, the helper does not silently swallow it.
- The published MCP tool surface (input schemas as seen via `tools/list`) is byte-identical for all six tools — no client-visible schema change.
- No catalog change. No caller change. No new dependency. Single shared helper module (one audit point).
</success_criteria>

<output>
After completion, create `.planning/quick/260517-dvf-accept-json-encoded-string-forms-for-lis/260517-dvf-SUMMARY.md` recording: the exact source diff in `_param_coercion.py`, the three annotation edits (file + line + before/after snippet), the 9 parametrized test case names, the `uv run pytest -x` pass count, the ruff format + check results, and a one-line note that the locked error catalog (D3-12) was NOT modified and that the published MCP tool surface is byte-identical from the client's perspective.
</output>
</content>
</invoke>