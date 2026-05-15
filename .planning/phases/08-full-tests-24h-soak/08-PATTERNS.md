# Phase 8: Full tests + 24h soak — Pattern Map

**Mapped:** 2026-05-15
**Files analyzed:** 19 (12 NEW, 7 MODIFIED)
**Analogs found:** 19 / 19 (3 of the 12 NEW have "first-of-kind / no analog" disposition with documented closest-shape neighbors)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `scripts/soak/__init__.py` | config (package marker) | none | `tests/_corpus/__init__.py` (empty pkg marker) | exact |
| `scripts/soak/run_soak.py` | utility (driver script) | request-response (loop) | `tests/conftest.py:94-123 live_server` (uvicorn-in-thread) + `tests/test_datasette_client_retry.py` (httpx.AsyncClient driver) | role-match (no `scripts/` precedent) |
| `scripts/soak/workload.py` | utility (data definition) | none | `tests/_corpus/hostile_inputs.py` (canonical-list + helper module) | exact (data-corpus shape) |
| `scripts/soak/rss_sampler.py` | utility (stdlib syscall wrapper) | none | `src/mcp_zeeker/core/ip.py` (pure stdlib, single-purpose helper) | role-match (no soak precedent) |
| `scripts/soak/report.py` | utility (CSV reduce + CLI gate) | batch (file-I/O) | `tests/conftest.py:337-355 _load_fragments_fixture` (file-I/O reducer) | role-match (no analog for CLI gate) |
| `tests/test_dependency_footprint.py` | test (unit) | none | `tests/test_config_lookup_single_source.py` (AST/static-source assertion) | exact (whole-file static-source assertion shape) |
| `tests/test_app_lifespan_contract.py` | test (unit) | none | `tests/test_retrieved_at_middleware.py` (FastMCP middleware/lifespan unit test with monkeypatch) + `tests/test_app.py:24-50` (lifespan-adjacent invariant) | exact (monkeypatch + assert RuntimeError shape) |
| `tests/test_hidden_data_enforcement.py` | test (unit) | none | `tests/tools/test_list_tables.py:81-136` (HIDDEN_TABLES rejection sweep) | exact (parametrized hidden-data sweep) |
| `tests/test_live_golden_path.py` | test (integration) | request-response | `tests/test_metadata_cache.py:264-307` (`@pytest.mark.live` over `ALLOWED_DATABASES`) + `tests/test_heavy_column_upstream.py:52-99` (live tool call) | exact (live marker + per-tool fan-out) |
| `tests/_corpus/soak_workload.py` | test corpus (data) | none | `tests/_corpus/hostile_inputs.py` (canonical-list module under `tests/_corpus/`) | exact |
| `.github/workflows/live-tests.yml` | config (CI workflow) | event-driven (cron + dispatch) | n/a — first GH workflow in repo | no analog (first-of-kind) |
| `.github/workflows/soak.yml` | config (CI workflow) | event-driven (workflow_dispatch only) | self (live-tests.yml shipped same plan) | sibling (same plan) |
| `src/mcp_zeeker/app.py` (MODIFIED) | config (lifespan) | none | self (lines 53-70 lifespan envelope-contract guard) | self |
| `tests/test_filter_compiler.py` (MODIFIED) | test | none | self (lines 36-280, 13 per-op tests) | self |
| `tests/test_envelope_snapshot.py` (MODIFIED) | test | none | self (lines 250-319 `test_every_registered_tool_returns_envelope_with_correct_provenance`) | self |
| `tests/test_hostile_inputs_consolidated.py` (MODIFIED) | test | none | self (lines 138-263, parametrized canary × tool matrix) | self |
| `tests/_corpus/hostile_inputs.py` (MODIFIED) | test corpus | none | self (lines 22-28 `CANARY_STRINGS` literal list) | self |
| `tests/tools/test_retrieval_fragment_join.py` (MODIFIED) | test (docs only) | none | self (lines 637-720 `test_1500_fragment_walk_synthetic`) | self |
| `tests/test_rate_limit.py` (MODIFIED) | test | none | self (full file — Phase 7 GREEN body) | self |
| `tests/test_error_catalog.py` (MODIFIED) | test | none | self (lines 42-65 `test_all_11_codes_in_catalog`) | self |
| `pyproject.toml` (touched only if planner adds deps) | config | none | self (lines 1-21 dependency blocks) | self |
| `README.md` (MODIFIED) | docs | none | self (lines 105-110 "Anthropic IP allowlist" placeholder block) | self |

---

## Pattern Assignments

### `scripts/soak/__init__.py` (config, none)

**Analog:** `tests/_corpus/__init__.py` — empty package marker.

**Pattern:** Empty file. The `tests/_corpus/__init__.py` is a 0-byte file (`-rw-r--r--@ 1 houfu staff 0 14 May 22:06 __init__.py`). Replicate verbatim — no imports, no docstring, no `from __future__`. The presence of the file is the entire signal: it makes `scripts.soak` an importable package so `python -m scripts.soak.run_soak` resolves.

**Divergence from analog:** None. Match the empty-file convention exactly.

---

### `scripts/soak/run_soak.py` (utility, request-response loop)

**Analog A:** `tests/conftest.py` lines 94-123 (`live_server` fixture — uvicorn-in-process pattern).

**Uvicorn-in-thread launch pattern** (`conftest.py` lines 104-122):
```python
port = _free_port()
cfg = uvicorn.Config(
    app,
    host="127.0.0.1",
    port=port,
    log_level="warning",
    loop="asyncio",
)
server = uvicorn.Server(cfg)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
# Poll until the server is ready (up to 2.5s)
for _ in range(50):
    if server.started:
        break
    time.sleep(0.05)
assert server.started, "uvicorn did not start within 2.5s"
yield f"http://127.0.0.1:{port}/mcp/"
server.should_exit = True
thread.join(timeout=5)
```
The soak driver MUST NOT spawn the server — the operator runs `uvicorn` in terminal A. The driver only takes `--target-url` (default `http://127.0.0.1:8000/mcp/`) as a CLI arg. This pattern is shown as the COUNTEREXAMPLE: the in-thread server pattern is for fast unit tests; soak uses an external process so RSS sampling reflects the real SUT.

**Analog B:** `tests/test_datasette_client_retry.py` lines 24-27 (httpx.AsyncClient request-loop pattern).

**Async-client construction pattern** (`test_datasette_client_retry.py` lines 24-27):
```python
@pytest.fixture
def client(httpx_mock: pytest_httpx.HTTPXMock) -> DatasetteClient:
    """Return a DatasetteClient backed by a real AsyncClient that pytest-httpx patches."""
    return DatasetteClient(httpx.AsyncClient(base_url=config.UPSTREAM_URL))
```
The driver opens **one** `httpx.AsyncClient(base_url=target_url, timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0))` for the whole soak. Reuse via `async with`; do not construct per-request (DatasetteClient pattern at `src/mcp_zeeker/core/datasette_client.py`).

**Analog C:** `tests/test_rate_limit.py` lines 48-61 (asyncio loop with bounded concurrency).

**Asyncio.Semaphore concurrency-bound pattern** (canonical Python — no in-repo analog; closest behavioral analog is the per-request loop in `tests/test_rate_limit.py:69-78`):
```python
# scripts/soak/run_soak.py — concurrency primitive (no in-repo analog)
sem = asyncio.Semaphore(args.concurrency)  # NFR-02: 50

async def _one_request():
    async with sem:
        t0 = time.perf_counter()
        try:
            resp = await client.post("/mcp/", content=payload, headers=hdrs)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return ("ok", resp.status_code, elapsed_ms, None)
        except httpx.PoolTimeout:
            return ("pool_timeout", 0, 0.0, "pool_timeout")
        except httpx.TimeoutException as exc:
            return ("timeout", 0, 0.0, type(exc).__name__)
```
**Critical:** mirror Phase 7 RESEARCH/PLAN Open Question 5 — the driver does NOT retry. Failure rate IS a measurement. Categorize errors: `pool_timeout`, `request_timeout`, `5xx`, `4xx`, `429` (per RESEARCH.md Open Q5).

**CLI argument shape** (no in-repo analog — first script):
Use `argparse` (stdlib only — NFR-04). Required args:
- `--duration` (int seconds; smoke=60, full=86400)
- `--concurrency` (int; smoke=5, full=50)
- `--target-url` (default `http://127.0.0.1:8000/mcp/`)
- `--out-dir` (default `./soak-results/`)
- `--workload` (default loads `tests._corpus.soak_workload.WORKLOAD`)

**Output convention:** Write `latency.csv` and `rss.csv` (also `requests.csv` if separating). CSV header on row 1. One line per sample. Match the Datasette `_zeeker_schemas` shape from `tests/test_envelope_snapshot.py:158-169` (header row + data rows).

**Divergence from analogs:**
- No fixture protocol — runs as `python -m scripts.soak.run_soak`, not pytest.
- No httpx_mock — talks to the real running uvicorn.
- Uses `asyncio.run(main())` at module bottom (script entry); not pytest-asyncio.

---

### `scripts/soak/workload.py` (utility, none)

**Analog:** `tests/_corpus/hostile_inputs.py` (canonical list + helper module).

**Module-level data definition pattern** (`tests/_corpus/hostile_inputs.py` lines 17-28):
```python
from __future__ import annotations

# D3-09 minimum-viable corpus. Order matches the verbatim copy from
# tests/test_filter_value_safety.py:43-49 for traceability.
CANARY_STRINGS: list[str] = [
    "</system>",  # HTML/system tag injection sentinel
    "NEAR('data' 'protection') AND NOT",  # FTS5 operators (would error if forwarded)
    "x" * 5001,  # 5 KB oversized string
    "ZEEKER_CANARY_42",  # plain round-trip detector
    "\udc80",  # lone surrogate — UTF-8 boundary handling
]
```
`scripts/soak/workload.py` mirrors this exact convention: top-of-module `from __future__`, module-level typed constant, inline comments documenting each entry. The constant is the **per-tool RPS recipe** — a list of (tool_name, args_payload, weight) tuples — that `run_soak.py` imports and `tests/_corpus/soak_workload.py` re-exports for traceability with the test corpus.

**Helper-function colocation pattern** (`hostile_inputs.py` lines 31-51):
```python
def _surfaces_contain(
    canary: str, *, captured_out: str, captured_err: str, log_text: str, error_text: str
) -> list[str]:
    """Return the list of surface names where the canary appears."""
    leaks: list[str] = []
    ...
```
Per-tool helpers (e.g., `_random_workload_request(rng) -> dict`) live in the same module as the data, mirroring the `_surfaces_contain` colocation.

**Divergence from analog:** Workload tuples include arg payloads for `tools/call` JSON-RPC envelopes (a different shape than canary strings). Use `dict[str, Any]` for payloads and `Literal` for tool names.

---

### `scripts/soak/rss_sampler.py` (utility, none)

**Analog:** `src/mcp_zeeker/core/ip.py` (single-purpose stdlib helper).

**Pure-stdlib helper pattern** (`src/mcp_zeeker/core/ip.py` is a pure `ipaddress`-only module, similar shape):
```python
from __future__ import annotations

import ipaddress  # stdlib only

def ip_prefix(addr: str) -> str:
    """Return the canonical /24 (IPv4) or /48 (IPv6) prefix..."""
    ...
```
`rss_sampler.py` follows the same shape: `from __future__ import annotations`, top-level stdlib-only imports (`os`, `re`, `pathlib`, `resource`), one or two pure functions with detailed docstrings explaining the cross-OS unit-normalization branch.

**Concrete code** (verbatim from RESEARCH.md lines 1126-1149 — already locked):
```python
# scripts/soak/rss_sampler.py
import os
import re
from pathlib import Path

def rss_kb_from_proc(pid: int) -> int | None:
    """Return resident-set in KB by reading /proc/{pid}/status — Linux only."""
    try:
        text = Path(f"/proc/{pid}/status").read_text()
        m = re.search(r"^VmRSS:\s+(\d+)\s*kB", text, re.MULTILINE)
        return int(m.group(1)) if m else None
    except (OSError, AttributeError):
        return None

def rss_kb_from_self() -> int:
    """Fallback for non-Linux: ru_maxrss from current process."""
    import resource
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports bytes; Linux reports KB. Normalize to KB.
    if os.uname().sysname == "Darwin":
        return rusage.ru_maxrss // 1024
    return rusage.ru_maxrss
```

**Divergence from analog:** Conditional branch on `os.uname().sysname == "Darwin"` is unusual in this codebase (no other module checks platform). Document the macOS-vs-Linux unit difference inline; this is the entire reason the helper exists.

---

### `scripts/soak/report.py` (utility, batch / file-I/O)

**Analog:** `tests/conftest.py` lines 337-355 (`_load_fragments_fixture` — file-I/O reducer).

**File-load + parse pattern** (`conftest.py` lines 337-355):
```python
def _load_fragments_fixture(filename: str) -> dict:
    """Load a JSON fixture from tests/fixtures/."""
    path = pathlib.Path(__file__).parent / "fixtures" / filename
    return json.loads(path.read_text())
```
`report.py` extends to CSV: `csv.reader(open(path))`, parse rows into floats, sort, compute percentiles via `sorted(samples)[int(p * len(samples))]` (per RESEARCH.md decision — no hdrhistogram).

**CLI exit-code gate** (no in-repo analog — first script):
Use `sys.exit(0)` on pass, `sys.exit(1)` on threshold breach. Argparse-driven thresholds:
- `--max-p50-ms` (NFR-01: 300)
- `--max-p95-ms` (NFR-01: 1500)
- `--max-rss-mb` (NFR-03: 256)

**Daily-rollover detection pseudocode** (RESEARCH.md lines 1110, 1232):
```python
# Bucket 429 count per UTC minute; flag if >50% drop within ±60s of any midnight.
per_minute_429: dict[datetime, int] = collections.Counter()
# ... populate from latency.csv rows ...
midnights = [m for m in per_minute_429 if m.hour == 0 and m.minute == 0]
daily_rollover_observed = any(
    per_minute_429[m] < 0.5 * per_minute_429[m - timedelta(minutes=1)]
    for m in midnights
)
```

**Markdown summary writer** (no in-repo analog):
Write `soak-summary.md` with sections: Latency (p50/p95/max), RSS (max in MB), Errors (counts by category), Daily rollover observed (bool). Plain-string formatting; no jinja2.

**Divergence from analog:** No fixture loading — reads operator-supplied CSVs from `--results-dir`. Has CLI; the analog is a fixture helper.

---

### `tests/test_dependency_footprint.py` (test, none)

**Analog:** `tests/test_config_lookup_single_source.py` (whole file — AST/static-source assertion shape).

**Static-source assertion pattern** (`tests/test_config_lookup_single_source.py` lines 60-65):
```python
def test_no_direct_hidden_columns_reads_outside_config_lookup():
    """D2-10: config.HIDDEN_COLUMNS must not be referenced outside config_lookup."""
    offenders = _scan_attribute_offenders("HIDDEN_COLUMNS")
    assert offenders == [], (
        f"direct config.HIDDEN_COLUMNS reads outside core/config_lookup.py: {offenders}. "
        f"Use config_lookup.hidden_columns_for(...) instead."
    )
```
Phase 8 dependency-footprint test mirrors this "scan source artifact, assert exact set" shape but reads `pyproject.toml` via stdlib `tomllib` (RESEARCH.md A7).

**Concrete pattern for `test_runtime_deps_match_locked_set`:**
```python
"""NFR-04: dependency footprint locked to exact 6 runtime + 4 dev tuples.

Reads pyproject.toml via stdlib tomllib (Python 3.11+; pinned by
pyproject.toml:5 requires-python). Asserts set-equality with the locked
NFR-04 tuples — any add/remove/rename surfaces in the diff.
"""
from __future__ import annotations
import tomllib
from pathlib import Path

# Locked per NFR-04 (REQUIREMENTS.md). Names match PEP 508 distribution names
# as they appear in pyproject.toml [project].dependencies.
RUNTIME_DEPS_LOCKED = frozenset({
    "fastmcp", "pydantic", "httpx", "starlette", "uvicorn", "structlog",
})
DEV_DEPS_LOCKED = frozenset({
    "pytest", "pytest-asyncio", "pytest-httpx", "ruff",
})

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent

def _parse_dep_name(spec: str) -> str:
    """Return the distribution name from a PEP 508 spec like 'fastmcp~=3.2'."""
    # Strip everything from the first non-name char (~, =, <, >, ;, [, space).
    for sep in ("~", "=", "<", ">", ";", "[", " "):
        idx = spec.find(sep)
        if idx > 0:
            return spec[:idx]
    return spec

def test_runtime_deps_match_locked_set():
    pyproject = tomllib.loads((_project_root() / "pyproject.toml").read_text())
    actual = frozenset(_parse_dep_name(d) for d in pyproject["project"]["dependencies"])
    assert actual == RUNTIME_DEPS_LOCKED, (
        f"runtime deps drifted: added={actual - RUNTIME_DEPS_LOCKED!r} "
        f"removed={RUNTIME_DEPS_LOCKED - actual!r}"
    )
```
The assertion-message convention (`added={...} removed={...}`) mirrors the diff-readable style at `tests/test_error_catalog.py:64`.

**Divergence from analog:**
- Uses stdlib `tomllib` (introduced Python 3.11 per pyproject.toml line 5).
- No AST scan — just dict access.
- Assertion is set-equality, not "no offenders."

---

### `tests/test_app_lifespan_contract.py` (test, none)

**Analog A:** `tests/test_retrieved_at_middleware.py` (FastMCP middleware unit test with monkeypatch + SimpleNamespace stand-in).

**Direct-call lifespan unit pattern** (closest analog at `tests/test_error_catalog.py` lines 134-159 — middleware direct-call):
```python
async def call_next(_ctx):
    raise ToolError("unknown_database: Database not found: foo")

ctx = types.SimpleNamespace(message=types.SimpleNamespace(name="dummy"))
with pytest.raises(ToolError) as exc_info:
    await ErrorEnrichmentMiddleware().on_call_tool(ctx, call_next)
```
Phase 8 lifespan contract test uses the same stand-in technique — synthesize a non-`FunctionTool` object via `types.SimpleNamespace` (no `return_type` attribute) and feed it through the lifespan envelope-contract guard at `src/mcp_zeeker/app.py:53-67`.

**Analog B:** `tests/test_envelope_snapshot.py` lines 47-74 (monkeypatch the production middleware).

**Monkeypatch-the-handler pattern** (`tests/test_envelope_snapshot.py` lines 47-74):
```python
@pytest.fixture
def passthrough_retrieved_at_middleware(monkeypatch, frozen_retrieved_at):
    from mcp_zeeker.core.middleware.retrieved_at import tool_started_at

    async def _bind_frozen(self, context, call_next):  # noqa: ARG001
        token = tool_started_at.set(frozen_retrieved_at)
        try:
            return await call_next(context)
        finally:
            tool_started_at.reset(token)

    monkeypatch.setattr(RetrievedAtMiddleware, "on_call_tool", _bind_frozen)
```
Phase 8 test monkey-patches `mcp.list_tools` to return a list containing a synthetic non-`FunctionTool` (a `SimpleNamespace(name="x", description="..." + TOOL_TRAILER)` that lacks `return_type`), then runs the lifespan and asserts `RuntimeError` is raised — NOT `AttributeError`.

**Concrete CR-02 carryover pattern:**
```python
# tests/test_app_lifespan_contract.py — NEW
"""CR-02 carryover: app.py:59 must use getattr(tool, 'return_type', None)
so a non-FunctionTool surfaces as RuntimeError("tool contract drift: ...")
not AttributeError("'SimpleNamespace' object has no attribute 'return_type'").
"""
from __future__ import annotations
import types
import pytest
from mcp_zeeker import config
from mcp_zeeker.app import lifespan
from mcp_zeeker.server import mcp


async def test_non_function_tool_raises_runtime_error_not_attribute_error(monkeypatch):
    fake_tool = types.SimpleNamespace(
        name="fake_non_function_tool",
        description=f"some description\n\n{config.TOOL_TRAILER}",
        # Deliberately no `return_type` attr — that's the bug surface.
    )

    async def _fake_list_tools():
        return [fake_tool]

    monkeypatch.setattr(mcp, "list_tools", _fake_list_tools)

    # The lifespan async context manager should raise RuntimeError on enter.
    from starlette.applications import Starlette
    app = Starlette()
    with pytest.raises(RuntimeError, match="tool contract drift"):
        async with lifespan(app):
            pass  # pragma: no cover — must not reach here
```

**Divergence from analogs:** This test exercises the lifespan, not a middleware. The lifespan is an async context manager — must enter via `async with`, not direct call. The fix in `src/mcp_zeeker/app.py:59` is a one-liner: `if tool.return_type is not Envelope:` → `if getattr(tool, "return_type", None) is not Envelope:`.

---

### `tests/test_hidden_data_enforcement.py` (test, none)

**Analog:** `tests/tools/test_list_tables.py` lines 81-136 (HIDDEN_TABLES rejection sweep).

**Imports pattern** (`tests/tools/test_list_tables.py` lines 1-15 — visible from grep):
```python
from __future__ import annotations
import pytest
import pytest_httpx
from fastmcp.exceptions import ToolError

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.metadata_cache import MetadataCache
from mcp_zeeker.tools.discovery import list_tables
```
Phase 8 hidden-data tests import the same `config`, `DatasetteClient`, `MetadataCache`, plus `from mcp_zeeker.tools.discovery import describe_table`.

**Hidden-table sweep pattern** (`tests/tools/test_list_tables.py` lines 81-110):
```python
async def test_visible_tables_only(
    datasette_client: DatasetteClient,
    metadata_cache: MetadataCache,
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """DISC-02: visible tables only — upstream-hidden flag + config-denylist both applied.

    6 table payload:
    - judgments, judgments_fragments: visible
    - _zeeker_schemas, _zeeker_updates: config.HIDDEN_TABLES (platform-internal)
    - fts_aux1, fts_aux2: upstream hidden=True
    Expected: 2 rows in envelope.data (judgments + judgments_fragments)
    """
    httpx_mock.add_response(
        url=_db_url("zeeker-judgements"),
        json=_tables_payload(
            _simple_tables(
                ["judgments", "judgments_fragments", "_zeeker_schemas", "_zeeker_updates"],
            )
            + _simple_tables(["fts_aux1", "fts_aux2"], hidden=["fts_aux1", "fts_aux2"])
        ),
    )
    envelope = await list_tables("zeeker-judgements")

    assert len(envelope.data) == 2
    names = {row["name"] for row in envelope.data}
    assert names == {"judgments", "judgments_fragments"}
```
Phase 8 `test_list_tables_strips_hidden` mirrors this exactly but parametrizes across **every** entry in `config.HIDDEN_TABLES` — one assertion per (database, hidden_table) pair, asserting the name is absent from `envelope.data`.

**Hidden-column sweep pattern** (`tests/tools/test_query_table_errors.py:50` — global-hidden `id`):
```python
"""pdpc.enforcement_decisions — `id` is global-hidden (HIDDEN_COLUMNS['*'])."""
```
`test_describe_table_strips_hidden_columns` follows the sweep shape: parametrize over `(database, table, hidden_column)` from `config.HIDDEN_COLUMNS`, call `describe_table(database, table)`, assert `hidden_column not in {col["name"] for col in envelope.data["columns"]}`.

**Single-source-of-truth approach** (`tests/test_config_lookup_single_source.py`):
Use `mcp_zeeker.core.config_lookup.hidden_columns_for(...)` to source the canonical hidden-column set per-(db,table). Do NOT read `config.HIDDEN_COLUMNS` directly (per D2-10 — would fail `test_no_direct_hidden_columns_reads_outside_config_lookup`).

**Divergence from analog:**
- Parametrized across the entire `HIDDEN_TABLES` / `HIDDEN_COLUMNS` set, not 1 hand-crafted case.
- Uses `pytest.mark.parametrize` over generated tuples (mirror `tests/test_metadata_cache.py:265 @pytest.mark.parametrize("database", config.ALLOWED_DATABASES)`).

---

### `tests/test_live_golden_path.py` (test, integration)

**Analog A:** `tests/test_metadata_cache.py` lines 264-307 (live-marker fan-out across `ALLOWED_DATABASES`).

**Live test scaffold pattern** (`tests/test_metadata_cache.py` lines 264-307):
```python
@pytest.mark.live
@pytest.mark.parametrize("database", config.ALLOWED_DATABASES)
async def test_live_metadata_parseable(database: str):
    """Live probe: real /-/metadata.json is parseable and per-DB license is
    either a non-empty string or None.

    ...

    Requires ZEEKER_LIVE=1.
    """
    http = httpx.AsyncClient(base_url=config.UPSTREAM_URL)
    cache = MetadataCache(http, config.UPSTREAM_URL, ttl=60)
    token = MetadataCache.bind(cache)
    try:
        lic = await cache.get_database_license(database)
        assert lic is None or (isinstance(lic, str) and lic != ""), (
            f"{database}: get_database_license returned unexpected value: {lic!r}"
        )
    finally:
        MetadataCache.reset(token)
        MetadataCache.clear_singleton()
        await http.aclose()
```
Phase 8 live-golden-path test follows the same shape: `@pytest.mark.live` + per-tool parametrize. The skip mechanism is `tests/conftest.py:76-83` (`pytest_collection_modifyitems` — skips `live` marker unless `ZEEKER_LIVE=1`).

**Analog B:** `tests/test_heavy_column_upstream.py` lines 52-99 (full DatasetteClient + MetadataCache + ParentPKCache + retrieved_at binding for a live tool call).

**Full-binding live test pattern** (`tests/test_heavy_column_upstream.py` lines 52-99):
```python
@pytest.mark.live
async def test_mlaw_news_heavy_column_returns_content() -> None:
    """`query_table(sg-gov-newsrooms, mlaw_news, columns=["content_text"])`
    returns a row with `retrieved_content.content_text` populated AND a
    `_policy` block — NOT `upstream_unavailable`.

    Requires ZEEKER_LIVE=1.
    """
    from datetime import UTC, datetime

    async with httpx.AsyncClient(base_url=config.UPSTREAM_URL) as http:
        dc_token = DatasetteClient.bind(DatasetteClient(http))
        mc_token = MetadataCache.bind(
            MetadataCache(http, config.UPSTREAM_URL, ttl=config.METADATA_TTL_SECONDS)
        )
        pk_token = ParentPKCache.bind(ParentPKCache())
        rt_token = tool_started_at.set(datetime(2026, 1, 1, tzinfo=UTC))
        try:
            envelope = await query_table(
                database="sg-gov-newsrooms",
                table="mlaw_news",
                columns=["content_text"],
                limit=1,
            )
        finally:
            tool_started_at.reset(rt_token)
            ParentPKCache.reset(pk_token)
            MetadataCache.reset(mc_token)
            DatasetteClient.reset(dc_token)
            ...
```
Phase 8 `test_live_golden_path.py` uses this exact bind/reset choreography. The 6 per-tool live tests share a single async-fixture that yields a fully-bound context (DatasetteClient + MetadataCache + ParentPKCache + tool_started_at).

**Per-tool fan-out shape:**
```python
# tests/test_live_golden_path.py — NEW
@pytest.mark.live
async def test_live_list_databases(bound_live_clients):
    """TEST-02: live list_databases returns ≥1 DB envelope from data.zeeker.sg."""
    from mcp_zeeker.tools.discovery import list_databases
    envelope = await list_databases()
    assert len(envelope.data) >= 1
    assert envelope.provenance.source == "data.zeeker.sg"

@pytest.mark.live
async def test_live_list_tables(bound_live_clients): ...  # one DB

@pytest.mark.live
async def test_live_describe_table(bound_live_clients): ...

@pytest.mark.live
async def test_live_search(bound_live_clients): ...

@pytest.mark.live
async def test_live_query_table(bound_live_clients): ...

@pytest.mark.live
async def test_live_fetch(bound_live_clients): ...  # use a known-stable URL
```

**Live-test invariant:** Assert on **shape**, not content (per RESEARCH.md V12 line 1280). Where content is asserted, it must be a known-public string (e.g., `provenance.source == "data.zeeker.sg"`). This avoids the Phase 6 D6.1-04 trap where literal license strings drifted within 48h.

**Divergence from analogs:** Six tools instead of one; shared fixture for binding. CI runs via `nightly.yml` cron, not on every PR (per `tests/conftest.py:76-83` skip-unless-env mechanism).

---

### `tests/_corpus/soak_workload.py` (test corpus, none)

**Analog:** `tests/_corpus/hostile_inputs.py` (canonical-list module under `tests/_corpus/`).

**Module shape pattern** (`tests/_corpus/hostile_inputs.py` lines 1-28 — full top of file):
```python
"""
Shared hostile-input canary corpus — INJ-05 consolidation (D6 Phase 6).

Verbatim copy of `CANARY_STRINGS` (D3-09 minimum-viable corpus) and the
`_surfaces_contain` leak-detection helper from `tests/test_filter_value_safety.py`
lines 43-49 and 147-167 (Phase 3). ...
"""

from __future__ import annotations

# D3-09 minimum-viable corpus. Order matches the verbatim copy from
# tests/test_filter_value_safety.py:43-49 for traceability.
CANARY_STRINGS: list[str] = [
    ...
]
```
`tests/_corpus/soak_workload.py` mirrors this docstring style ("Shared <topic> — <REQ-ID> consolidation"), `from __future__` import, top-level typed constant with traceability comments.

**Cross-package re-export pattern:** The soak workload is the SAME data needed by `scripts/soak/run_soak.py`. Per REQUIREMENTS / single-source-of-truth: define canonical workload in `tests/_corpus/soak_workload.py`; `scripts/soak/workload.py` re-exports via `from tests._corpus.soak_workload import WORKLOAD as WORKLOAD`. Caveat: `scripts/` is not pytest-rooted, so `scripts/soak/workload.py` must adjust `sys.path` (one-line `Path(__file__).resolve().parent.parent.parent` insert) OR the canonical lives in `scripts/soak/workload.py` and the test corpus re-exports. **Recommendation per Phase 7 PATTERNS.md "single source of truth" discipline:** canonical in `scripts/soak/workload.py`; `tests/_corpus/soak_workload.py` re-exports via `from scripts.soak.workload import WORKLOAD`. Planner picks; document choice in plan.

**Divergence from analog:** Workload entries are tuples `(tool_name: str, args: dict, weight: float)`, not raw strings. Type as `list[tuple[str, dict[str, Any], float]]`.

---

### `.github/workflows/live-tests.yml` (config, event-driven)

**Analog:** **No analog — first GH workflow in this repo.** (`/Users/houfu/Projects/zeeker-mcp/.github/` does not exist.)

**Closest-shape neighbor:** `Caddyfile.prod` (operator-config YAML-ish file with comments documenting blast-radius). The workflow file should embed a header comment matching the Caddyfile.prod / Dockerfile pattern:
```yaml
# .github/workflows/live-tests.yml
# Source: 08-RESEARCH.md "CI Scheduling" lines 1167-1175
# Purpose: TEST-02 — live golden path against data.zeeker.sg.
# Trigger: nightly cron + manual workflow_dispatch (pre-release).
```

**Skeleton structure** (canonical GitHub Actions, no in-repo precedent — match RESEARCH.md lines 1167-1175):
```yaml
name: Live Tests
on:
  schedule:
    - cron: "0 2 * * *"  # 02:00 UTC nightly
  workflow_dispatch:
jobs:
  live:
    runs-on: ubuntu-latest  # RESEARCH.md A1 — assumed runner
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.11.14"  # CLAUDE.md pinned version
      - run: uv sync --frozen
      - run: ZEEKER_LIVE=1 uv run pytest -m live -p no:xdist
        env:
          UPSTREAM_URL: https://data.zeeker.sg
```

**Divergence from analog:** First GH Actions workflow in repo. Pin `actions/checkout@v4` and `astral-sh/setup-uv@v3` to specific majors (operator security posture matches the Caddyfile.prod "OVERWRITE not append" hardening discipline at README.md:61-67).

---

### `.github/workflows/soak.yml` (config, event-driven — workflow_dispatch only)

**Analog:** `.github/workflows/live-tests.yml` (sibling, shipped same plan).

**Reuse the live-tests.yml skeleton** with these deltas (per RESEARCH.md lines 1172, 1110-1111):
- `on:` block has only `workflow_dispatch:` (no cron — too expensive per RESEARCH.md line 1174 CI-minutes budget).
- `timeout-minutes: 1500` (25h budget).
- Job runs uvicorn server in background, then driver, then report:

```yaml
# .github/workflows/soak.yml
name: 24h Soak (manual)
on:
  workflow_dispatch:
jobs:
  soak:
    runs-on: ubuntu-latest
    timeout-minutes: 1500  # 25h budget per RESEARCH.md line 1111
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          version: "0.11.14"
      - run: uv sync --frozen
      - name: Start uvicorn (single worker per RATE-06)
        run: |
          uv run uvicorn mcp_zeeker.app:app --host 127.0.0.1 --port 8000 --workers 1 &
          sleep 5  # poll-until-ready preferred — see live_server pattern
      - name: Run 24h soak
        run: uv run python -m scripts.soak.run_soak --duration 86400 --concurrency 50
      - name: Report + gate
        run: uv run python -m scripts.soak.report --max-p50-ms 300 --max-p95-ms 1500 --max-rss-mb 256
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: soak-results
          path: ./soak-results/
```

**Divergence from sibling:** No cron (manual only). `timeout-minutes: 1500` (live-tests.yml uses 90). Background-uvicorn step (live tests don't need it — they hit the public endpoint).

---

### `src/mcp_zeeker/app.py` (MODIFIED — CR-02 carryover one-liner)

**Analog:** self (lines 53-67 envelope-contract guard).

**Current code at line 59** (`src/mcp_zeeker/app.py` lines 56-62):
```python
tools = await mcp.list_tools()
for tool in tools:
    # Check return type annotation — every tool must return Envelope
    if tool.return_type is not Envelope:
        raise RuntimeError(
            f"tool contract drift: {tool.name} return_type is not Envelope"
        )
```
**One-liner change for CR-02 closure:**
```python
if getattr(tool, "return_type", None) is not Envelope:
```
This converts the latent `AttributeError` (when `tool` is a non-`FunctionTool` lacking `return_type`) into the intended `RuntimeError("tool contract drift: ...")`. Verified by `tests/test_app_lifespan_contract.py` (above).

**Divergence from self:** None — surgical change. Do NOT touch the surrounding ImportError tolerance (lines 68-70).

---

### `tests/test_filter_compiler.py` (MODIFIED — extend to all 13 ops)

**Analog:** self (lines 36-280 — 13 per-op tests).

**Existing pattern** (`tests/test_filter_compiler.py` lines 36-43):
```python
def test_exact_op_returns_url_pair():
    """D3-02: op='exact' compiles to ('{col}__exact', str(value))."""
    out = compile_filters(
        [Filter(column="title", op="exact", value="Data Protection")],
        visible_columns=VISIBLE,
        column_types=TYPES,
    )
    assert out == [("title__exact", "Data Protection")]
```
**Phase 8 addition** (verbatim from RESEARCH.md lines 958-981 — already locked):
```python
ALL_OPS = ("exact", "not", "contains", "startswith", "endswith",
           "gt", "gte", "lt", "lte", "in", "notin", "isnull", "notnull")

@pytest.mark.parametrize("op", ALL_OPS)
def test_op_in_locked_set(op):
    """TEST-01 / D3-02: verify the FilterOp Literal contains exactly 13 names."""
    from mcp_zeeker.core.filter_compiler import FilterOp
    from typing import get_args
    assert op in get_args(FilterOp)
    assert len(get_args(FilterOp)) == 13

@pytest.mark.parametrize("op,col_type", [
    (op, ct)
    for op in ("gt", "gte", "lt", "lte")
    for ct in ("INTEGER", "REAL", "TEXT")
])
def test_numeric_ops_across_column_types(op, col_type):
    """TEST-01 / D3-10: numeric ops behave deterministically by column type."""
```

**Divergence from self:** Adds two parametrized sweeps. Existing 13 per-op tests stay verbatim — the parametrized sweep is **complementary**, not a replacement (Phase 7 PATTERNS.md "extend in place" discipline).

---

### `tests/test_envelope_snapshot.py` (MODIFIED — TEST-03 explicit row-key partition)

**Analog:** self (lines 250-319 `test_every_registered_tool_returns_envelope_with_correct_provenance`).

**Existing snapshot iteration** (`tests/test_envelope_snapshot.py` lines 306-319):
```python
tools = await mcp_client.list_tools()
assert tools, "no tools registered"
invoked = 0
for tool in tools:
    args = _DISPATCH_ARGS.get(tool.name)
    if args is None:
        continue
    result = await mcp_client.call_tool(tool.name, args)
    assert not result.is_error, f"tool '{tool.name}' returned error: {result.content}"
    envelope = result.structured_content
    assert isinstance(envelope, dict), f"tool '{tool.name}' did not return a dict envelope"
    # (b) source
    assert envelope["provenance"]["source"] == "data.zeeker.sg"
```
**Phase 8 addition:** Explicit row-key partition assertion per row, with named-key error messages (RESEARCH.md line 1019 — "current file uses bare `assert` which makes triage painful"):
```python
# Inside the existing per-tool loop, add:
HEAVY_COLUMNS = {"content_text", "_policy"}  # actual set lives in core/visibility.py

for row in envelope.get("data", []):
    leaked = set(row.keys()) & HEAVY_COLUMNS - {"retrieved_content"}
    assert not leaked, (
        f"TEST-03 leak: tool={tool.name!r} top-level row keys "
        f"intersect HEAVY_COLUMNS: {leaked!r}"
    )
    if "retrieved_content" in row:
        rc_extra = set(row["retrieved_content"].keys()) - HEAVY_COLUMNS
        assert not rc_extra, (
            f"TEST-03 leak: tool={tool.name!r} retrieved_content carries "
            f"non-HEAVY keys: {rc_extra!r}"
        )
```
The named-key error message format (`leaked: {set!r}`) mirrors the diff-readable convention at `tests/test_dependency_footprint.py` (above) and `tests/test_error_catalog.py:64`.

**Divergence from self:** Adds explicit-leak assertion messages; existing assertion shape stays (the new lines are a superset).

---

### `tests/test_hostile_inputs_consolidated.py` (MODIFIED — extend matrix to 9 × 3 × 3)

**Analog:** self (lines 138-263 — parametrized canary × tool matrix).

**Existing matrix shape** (`tests/test_hostile_inputs_consolidated.py` lines 138-149):
```python
@pytest.mark.parametrize("tool", ["query_table", "search", "fetch"])
@pytest.mark.parametrize("canary", CANARY_STRINGS)
async def test_hostile_input_never_echoed(
    tool: str,
    canary: str,
    mcp_client,
    datasette_client_for_canary,
    bound_metadata_cache,
    httpx_mock,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
```
**Phase 8 expansion:** With 4 new canaries (see `tests/_corpus/hostile_inputs.py` modification below), `CANARY_STRINGS` grows from 5 → 9 entries. The decorator stays unchanged — pytest auto-expands to 9 × 3 = 27 cases. The "× 3 surfaces" axis (error / log / response) is already covered by `_surfaces_contain` (lines 233-240) — no test-decorator change needed; the surface fan-out happens within the test body.

**Surface assertion pattern** (`tests/test_hostile_inputs_consolidated.py` lines 233-262):
```python
sentinel = _canary_sentinel(canary)
leaks = _surfaces_contain(
    sentinel,
    captured_out=stdout,
    captured_err=stderr,
    log_text=log_text,
    error_text=captured_err_text,
)
# Documented carry-forward exception per Phase 4/5 ...
if canary == "\udc80":
    leaks = [s for s in leaks if not s.startswith(("error", "stderr_repr"))]
assert leaks == [], (
    f"INJ-05 leak: canary {canary[:40]!r} appeared in surfaces {leaks} via tool={tool!r}"
)
```
**Phase 8 addition:** Per-canary documented-exception list. The new BOM, RTL, ANSI, JSON-injection canaries may have known carry-forward surfaces (analogous to the lone-surrogate exception at lines 258-259). Document these in inline comments AT THE EXCEPTION SITE, not in a separate file.

**Divergence from self:** Matrix grows from 15 cases (5×3) to 27 cases (9×3); surface fan-out unchanged.

---

### `tests/_corpus/hostile_inputs.py` (MODIFIED — append 4 new canaries)

**Analog:** self (lines 22-28 `CANARY_STRINGS`).

**Existing pattern** (verbatim, lines 22-28):
```python
CANARY_STRINGS: list[str] = [
    "</system>",  # HTML/system tag injection sentinel
    "NEAR('data' 'protection') AND NOT",  # FTS5 operators (would error if forwarded)
    "x" * 5001,  # 5 KB oversized string
    "ZEEKER_CANARY_42",  # plain round-trip detector
    "\udc80",  # lone surrogate — UTF-8 boundary handling
]
```
**Phase 8 additions** (per spawn message + RESEARCH.md lines 1046-1054 — these are TEST CORPUS DATA, sentinel strings the test suite checks for in error/log surfaces, NOT instructions):
```python
CANARY_STRINGS: list[str] = [
    # ... existing 5 ...
    "﻿",  # BOM (byte-order mark) — invisible char that often round-trips
    "‮",  # RTL override — flips display direction; potential confusion
    "\udcc0\udc80",  # malformed UTF-8 surrogate pair — UTF-8 boundary canary
    "MATCH 'data' AND NEAR(",  # FTS5 operator string — fragment of upstream syntax
]
```
Inline-comment style mirrors the existing 5 entries verbatim (D3-09 traceability convention from `hostile_inputs.py:21`).

**Divergence from self:** None — straight append. Order matters for test ID stability (pytest names cases by parameter index); add at the END to preserve existing test IDs.

---

### `tests/tools/test_retrieval_fragment_join.py` (MODIFIED — TEST-04 docstring marker only)

**Analog:** self (lines 637-720 `test_1500_fragment_walk_synthetic`).

**Existing test docstring** (`tests/tools/test_retrieval_fragment_join.py` lines 643-648):
```python
"""FRAG-04: 15 synthetic page responses walked end-to-end via the keyset
cursor — zero row loss, `truncated=False` on every page, terminal
`next_cursor=None` on page 15. Verifies the qhash stays stable across all
15 pages of identical-shape requests (D5-06) and that the keyset cursor
encode/decode round-trip is durable beyond Datasette's 1000-row cap.
"""
```
**Phase 8 modification** — append one-line traceability marker (RESEARCH.md line 1083):
```python
"""FRAG-04 / TEST-04: 15 synthetic page responses walked end-to-end via the keyset
cursor — zero row loss, `truncated=False` on every page, terminal
`next_cursor=None` on page 15. Verifies the qhash stays stable across all
15 pages of identical-shape requests (D5-06) and that the keyset cursor
encode/decode round-trip is durable beyond Datasette's 1000-row cap.

TEST-04 owner: Phase 8 (regression test originated in Phase 5 D5-06).
"""
```
**No code changes.** This is the smallest possible edit — adds the TEST-04 traceability per RESEARCH.md lines 1081-1086.

**Divergence from self:** Docstring-only edit. Single-line change in the docstring header + one new traceability paragraph.

---

### `tests/test_rate_limit.py` (MODIFIED — confirm coverage; minor edits)

**Analog:** self (full file — Phase 7 GREEN body, 800+ lines).

**Existing keyword-selectable tests:** Per VALIDATION.md, the following must pass via `pytest -k`:
- `pytest tests/test_rate_limit.py -k burst -x` (line 69 `test_burst_allows_20_rejects_21st`)
- `pytest tests/test_rate_limit.py -k sustained -x` (sustained-window tests added in plan 07-02/03)
- `pytest tests/test_rate_limit.py -k daily -x` (daily-window tests added in plan 07-02/03)

**Phase 8 action:** Confirm by `grep -n "def test_.*\(burst\|sustained\|daily\)" tests/test_rate_limit.py`. If a window has no test name containing the keyword, ADD the keyword to the function name (rename, not new test). Do NOT add new tests — Phase 7 closed RATE-01..06.

**Concurrency stress test** (RESEARCH.md line 927) — OPTIONAL Phase 8 ADD if planner deems necessary:
Use `asyncio.gather` over 50 simultaneous `_drive(rate_limiter, _build_scope(ip))` calls (helper at `tests/test_rate_limit.py:48-61`). Assert the sum of allows + denies equals 50 and the daily counter is incremented atomically.

**Divergence from self:** Minor renames or one optional concurrency-stress test. No structural changes.

---

### `tests/test_error_catalog.py` (MODIFIED — confirm 11 codes; minor edits)

**Analog:** self (lines 42-65 `test_all_11_codes_in_catalog`).

**Existing assertion** (verbatim, lines 50-65):
```python
expected = (
    "unknown_database",
    "unknown_table",
    "unknown_column",
    "invalid_filter_op",
    "invalid_cursor",
    "invalid_query",
    "unsupported_table_for_fetch",
    "not_found",
    "query_timeout",
    "rate_limited",
    "upstream_unavailable",
)
assert len(CATALOG) == 11, f"expected 11 codes, got {len(CATALOG)}"
assert set(CATALOG) == set(expected), f"set mismatch: {set(CATALOG) ^ set(expected)}"
assert CATALOG == expected, f"order mismatch: {CATALOG!r} != {expected!r}"
```
**Phase 8 action:** No edit needed unless a code is missing. Confirm via `pytest tests/test_error_catalog.py -x`. The Phase 7 plan already shipped this test as GREEN (per `tests/test_error_catalog.py:42-65`).

**Divergence from self:** None — verification only.

---

### `pyproject.toml` (touched only if planner adds deps)

**Analog:** self (lines 1-21).

**Existing pinned set** (verbatim, lines 6-21):
```toml
dependencies = [
    "fastmcp~=3.2",
    "pydantic~=2.13",
    "httpx~=0.28",
    "starlette>=0.41,<2",
    "uvicorn~=0.46",
    "structlog~=25.5",
]

[dependency-groups]
dev = [
    "pytest~=8.3",
    "pytest-asyncio~=1.3",
    "pytest-httpx~=0.35",
    "ruff~=0.15",
]
```
**Phase 8 invariant:** NFR-04 locks this exact set. `tests/test_dependency_footprint.py` enforces it. Phase 8 must NOT add deps — if a planner needs a new dep, it triggers a NFR-04 carve-out conversation, not a code change.

**Divergence from self:** None. The locked set IS the design — `tests/test_dependency_footprint.py` is the gate.

---

### `README.md` (MODIFIED — append Anthropic IP allowlist details)

**Analog:** self (lines 105-110 — existing forward-looking placeholder).

**Existing placeholder** (verbatim, `README.md` lines 105-110):
```markdown
### Anthropic IP allowlist (forward-looking)

The deployed instance must accept connections from Anthropic's published egress IP ranges to
be reachable via Claude Desktop and Claude Code. Phase 1 ships without an explicit IP
allowlist; Phase 9 (registry submission) will add the operational note and any Caddy-level
`trusted_proxies` configuration needed.
```
**Phase 8 modification:** Replace the "Phase 1 ships without ... Phase 9 will add" wording with the operator-actionable placeholder per RESEARCH.md A5 + Open Q3:
```markdown
### Anthropic IP allowlist

The deployed instance must accept inbound connections from Anthropic's MCP egress IP ranges
to be reachable via Claude Desktop and Claude Code. Anthropic does not (as of 2026-05) publish
a stable, machine-readable list of MCP-egress IPs; operators should:

1. Consult Anthropic's operator-facing documentation or registry-onboarding contact for the
   current allowlist.
2. Apply the allowlist at the host Caddy layer (or upstream firewall), NOT in the MCP
   container — Caddy already owns ingress per `Caddyfile.prod`.
3. Re-verify the allowlist at Phase 9 (registry submission) and quarterly thereafter; the
   IPs change without notice.

Operators who allowlist by domain rather than IP can use Anthropic's published egress hostnames
where available; this trades a lookup hop for resilience to IP churn.
```
**Verification:** `grep -q 'Anthropic IP' README.md` (per VALIDATION.md row TBD/08-06).

**Style match:** Mirror the existing `## Caddy header requirements` section's bullet-list operator-action pattern (`README.md` lines 57-67).

**Divergence from self:** Replaces a 4-line placeholder with a 10-line operator-actionable section. Same Markdown-heading depth and bullet style.

---

## Shared Patterns

### Stdlib-only Discipline (NFR-04)
**Source:** `src/mcp_zeeker/core/ip.py` (pure `ipaddress`); `src/mcp_zeeker/core/visibility.py` (only `fastmcp.exceptions`)
**Apply to:** `scripts/soak/*` (all five modules), `tests/test_dependency_footprint.py`
Every Phase 8 NEW file must import only from: stdlib + already-pinned `fastmcp/pydantic/httpx/starlette/uvicorn/structlog` (runtime) + `pytest/pytest-asyncio/pytest-httpx/ruff` (dev). Adding any other import triggers `tests/test_dependency_footprint.py` failure. The `scripts/` directory is bound by the same rule (it ships in the wheel via the soak harness even if not imported by the app).

### Single-Plan-Touch on conftest.py
**Source:** Phase 6 conftest section header at `tests/conftest.py:414-444`; Phase 7 fixtures at `tests/conftest.py:446-487`
**Apply to:** Phase 8 — DO NOT modify `tests/conftest.py`
RESEARCH.md "Wave 0 Requirements" line 88 explicitly states "No new fixtures in `tests/conftest.py` — single-plan-touch already enforced; Phase 8 reuses existing fixtures only." Phase 8 plans MUST NOT touch conftest.py. The available fixtures (`mcp_client`, `asgi_client`, `httpx_mock`, `bound_datasette_client`, `bound_metadata_cache`, `bound_parent_pk_cache`, `frozen_retrieved_at`, `fake_clock`, `rate_limiter`, `bucket_store`) are sufficient.

### Live-Marker Skip Mechanism
**Source:** `tests/conftest.py:76-83 pytest_collection_modifyitems` + `pyproject.toml:44-45` markers
**Apply to:** `tests/test_live_golden_path.py`, `tests/test_metadata_cache.py:264`, `tests/test_heavy_column_upstream.py:52`
Every live test must carry `@pytest.mark.live`. The conftest hook at lines 76-83 deselects `live` markers unless `ZEEKER_LIVE=1`. The marker is registered in `pyproject.toml:45` (`live: hits real data.zeeker.sg (skipped unless ZEEKER_LIVE=1)`). The `-p no:xdist` CLI flag is required because live tests share rate-limit budget against the upstream — sequential, not parallel.

### Diff-Readable Assertion Messages
**Source:** `tests/test_error_catalog.py:64` (`f"set mismatch: {set(CATALOG) ^ set(expected)}"`)
**Apply to:** `tests/test_dependency_footprint.py`, `tests/test_envelope_snapshot.py` (TEST-03 row-key assertions)
Every set-equality / membership assertion includes a diff-readable error message: `f"added={actual - expected!r} removed={expected - actual!r}"` for sets, `f"order mismatch: {actual!r} != {expected!r}"` for tuples. Bare `assert` → painful triage; named-diff `assert` → fix in 30s. The Phase 7 PATTERNS.md "ToolError code: message" convention has the same property at the error-message layer; this is the test-assertion-message analog.

### Monkeypatch + SimpleNamespace for Middleware/Lifespan Unit Tests
**Source:** `tests/test_envelope_snapshot.py:47-74 passthrough_retrieved_at_middleware`; `tests/test_error_catalog.py:134-159 test_error_includes_request_id`
**Apply to:** `tests/test_app_lifespan_contract.py`
For unit-testing FastMCP middleware or lifespan code, synthesize the FastMCP context with `types.SimpleNamespace(message=types.SimpleNamespace(name="dummy"))`. Use `monkeypatch.setattr` to swap `mcp.list_tools` or middleware methods. Avoid the in-memory `mcp_client` fixture for narrow contract tests — it pulls in too much.

### CSV-Reduce-and-Gate (Soak Report)
**Source:** No prior analog; closest is `tests/conftest.py:337-355 _load_fragments_fixture` (file-I/O reducer)
**Apply to:** `scripts/soak/report.py`
CSV inputs → reduce to scalar metrics → compare against operator-supplied thresholds → exit 0/1. CLI threshold flags match RESEARCH.md "Validation Architecture" lines 1212-1218 exactly:
- `--max-p50-ms 300` (NFR-01)
- `--max-p95-ms 1500` (NFR-01)
- `--max-rss-mb 256` (NFR-03)
Sort-then-index percentile per RESEARCH.md line 907 (`sorted(samples)[int(p * len(samples))]`).

### Trustable Project-Root Resolution
**Source:** `tests/test_config_lookup_single_source.py` (uses `pathlib.Path(__file__).parent.parent`)
**Apply to:** `tests/test_dependency_footprint.py`
Per RESEARCH.md A7: `Path(__file__).resolve().parent.parent` from a test under `tests/` returns the project root (the directory containing `pyproject.toml`). Use `.resolve()` so symlinked checkouts work. Hardcode this in the dependency-footprint test rather than walking-up-until-pyproject — the test lives at a fixed depth.

---

## No Analog Found

| File | Departure / Closest-Shape Neighbor |
|---|---|
| `scripts/soak/run_soak.py` | First script in `scripts/`. Closest neighbors: `tests/conftest.py:94-123 live_server` (uvicorn-in-thread; counterexample) and `tests/test_datasette_client_retry.py:24-27` (`httpx.AsyncClient` driver shape). The `asyncio.Semaphore(N)` concurrency-bound pattern has no in-repo analog — use canonical Python idiom. |
| `scripts/soak/rss_sampler.py` | First soak helper. Closest shape: `src/mcp_zeeker/core/ip.py` (pure stdlib, single-purpose). The `os.uname().sysname == "Darwin"` platform branch is unprecedented in this codebase — document inline. |
| `scripts/soak/report.py` | First reporter / CLI gate in repo. Closest data-shape: percentile / set-aggregate logic in `tests/test_logging.py:75-89` (IPv4/IPv6 prefix-bucket assertions); CLI exit-code gate has no precedent. |
| `.github/workflows/live-tests.yml` | First GitHub Actions workflow in repo. Closest shape: `Caddyfile.prod` (operator-config file with header comments documenting blast-radius). |
| `.github/workflows/soak.yml` | Sibling of live-tests.yml — copy that skeleton with the workflow_dispatch-only / 1500-min-timeout / background-uvicorn deltas. |

---

## Metadata

**Analog search scope:** `src/mcp_zeeker/` (all modules), `tests/` (all test files + `_corpus/`), `scripts/` (does not exist), `.github/` (does not exist), root markdown (`README.md`, `Caddyfile.prod`).
**Files scanned:** ~25 source files + ~30 test files + 2 root config files.
**Pattern extraction date:** 2026-05-15

---
