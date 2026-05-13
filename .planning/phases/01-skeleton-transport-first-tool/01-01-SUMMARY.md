---
phase: "01-skeleton-transport-first-tool"
plan: "01"
subsystem: "scaffold"
tags: [bootstrap, scaffold, pyproject, pytest, uv, wave-0]
dependency_graph:
  requires: []
  provides:
    - pyproject.toml with pinned dependency set
    - uv.lock committed
    - src/mcp_zeeker/ package skeleton
    - tests/ Wave-0 stub files (11 files, 41 tests)
  affects:
    - All Phase 1 plans (02-06) reference these test stubs in verify blocks
tech_stack:
  added:
    - fastmcp==3.2.4
    - pydantic==2.13.4
    - httpx==0.28.1
    - starlette==1.0.0
    - uvicorn==0.46.0
    - structlog==25.5.0
    - pytest==8.4.2
    - pytest-asyncio==1.3.0
    - pytest-httpx==0.35.0
    - ruff==0.15.12
  patterns:
    - src/ layout via hatchling
    - asyncio_mode=auto for pytest-asyncio
    - Wave-0 test stubs (collect-only, all pytest.skip)
key_files:
  created:
    - pyproject.toml
    - uv.lock
    - .env.example
    - src/mcp_zeeker/__init__.py
    - src/mcp_zeeker/core/__init__.py
    - src/mcp_zeeker/core/middleware/__init__.py
    - src/mcp_zeeker/tools/__init__.py
    - tests/__init__.py
    - tests/tools/__init__.py
    - tests/manual/.gitkeep
    - tests/conftest.py
    - tests/test_envelope.py
    - tests/test_envelope_contract.py
    - tests/test_app.py
    - tests/test_mcp_client_smoke.py
    - tests/test_mcp_streamable_smoke.py
    - tests/test_tool_trailer.py
    - tests/test_input_models_forbid.py
    - tests/test_config.py
    - tests/test_logging.py
    - tests/tools/test_discovery.py
  modified: []
decisions:
  - "pytest-httpx pinned to ~=0.35 (not ~=0.36 as in CLAUDE.md) — 0.36.x requires pytest>=9.dev0 which is incompatible with pytest~=8.3; 0.35.0 requires pytest==8.* exactly"
metrics:
  duration: "~5 minutes"
  completed: "2026-05-13T03:01:00Z"
  tasks_completed: 2
  tasks_total: 2
  files_created: 21
  files_modified: 0
---

# Phase 01 Plan 01: pyproject.toml + package skeleton + Wave-0 test stubs — Summary

**One-liner:** Python project scaffold with pinned 6-runtime + 4-dev dependency set via hatchling/uv, src/ layout package markers, and 41 Wave-0 test stubs across 11 files that collect cleanly and skip until later plans implement them.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| T1 | pyproject.toml + package skeleton + ruff/pytest config | d7e59bb | pyproject.toml, uv.lock, .env.example, 7 __init__.py, .gitkeep |
| T2 | Wave-0 test stub files | eb91ecd | tests/conftest.py + 10 test stub files |

## Dependency Versions Resolved by uv sync

| Package | Pin Spec | Resolved Version |
|---------|----------|-----------------|
| fastmcp | ~=3.2 | 3.2.4 |
| pydantic | ~=2.13 | 2.13.4 |
| httpx | ~=0.28 | 0.28.1 |
| starlette | >=0.41,<2 | 1.0.0 |
| uvicorn | ~=0.46 | 0.46.0 |
| structlog | ~=25.5 | 25.5.0 |
| pytest | ~=8.3 | 8.4.2 |
| pytest-asyncio | ~=1.3 | 1.3.0 |
| pytest-httpx | ~=0.35 | 0.35.0 |
| ruff | ~=0.15 | 0.15.12 |

## Wave-0 Test Stub Files Created

| File | REQ-IDs | Implements In | Test Functions |
|------|---------|---------------|----------------|
| tests/conftest.py | — | plan 04/05 | mcp_client, asgi_client fixtures |
| tests/test_envelope.py | ENV-06 | plan 02 | test_envelope_extra_forbid, test_provenance_extra_forbid, test_pagination_extra_forbid, test_for_database_list_provenance_shape, test_retrieved_at_is_utc |
| tests/test_envelope_contract.py | ENV-07, ANNO-01..03 | plan 05 | test_every_registered_tool_returns_envelope, test_every_registered_tool_description_ends_with_trailer, test_every_registered_tool_has_required_annotations, test_schemas_flat, test_every_tool_description_mentions_rate_limits |
| tests/test_app.py | TRANSPORT-03, TRANSPORT-06 | plan 05 | test_healthz_returns_ok_without_upstream, test_origin_missing_allowed, test_origin_allowlisted_allowed, test_origin_foreign_rejected_403, test_origin_preflight_options_allowed, test_origin_allowlist |
| tests/test_mcp_client_smoke.py | TRANSPORT-01/02/04, ANNO-01, DISC-01 | plan 05 | test_initialize_handshake, test_tools_list_flat_schema, test_tool_annotations, test_inputschema_is_flat, test_list_databases_returns_four_dbs |
| tests/test_mcp_streamable_smoke.py | TRANSPORT-01/02/03 | plan 05 | test_streamable_http_transport, test_initialize_over_http, test_stateless_session, test_two_independent_sessions |
| tests/test_tool_trailer.py | ANNO-02 (INJ-01) | plan 05 | test_tool_trailer_present |
| tests/test_input_models_forbid.py | ANNO-04 | plan 05 | test_all_input_models_extra_forbid, test_schemas_flat |
| tests/test_config.py | CFG-01, CFG-02 | plan 02 | test_single_source_of_truth, test_constants_present, test_allowed_databases, test_tool_trailer_verbatim |
| tests/test_logging.py | OBS-01..05 | plan 05 | test_log_per_request, test_request_id_propagation, test_log_fields, test_log_schema_match_config, test_ip_prefix_truncation |
| tests/tools/test_discovery.py | DISC-01 | plan 04 | test_list_databases, test_list_databases_names_match_config, test_list_databases_hidden_tables_excluded, test_list_databases_provenance |

**Total: 41 tests collected, 41 skipped, 0 errors**

## Verification Results

- `uv sync --frozen`: EXIT 0 — resolved 82 packages, 76 installed
- `uv run pytest --collect-only`: EXIT 0 — 41 tests collected across 11 files
- `uv run pytest -x -q`: EXIT 0 — 41 skipped, 0 failed, 0 errors
- `uv run ruff check .`: EXIT 0 — All checks passed
- `uv run ruff format --check .`: EXIT 0 — 17 files already formatted
- `uv run python -c "import mcp_zeeker, mcp_zeeker.core, mcp_zeeker.core.middleware, mcp_zeeker.tools"`: EXIT 0

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] pytest-httpx version downgraded from ~=0.36 to ~=0.35**
- **Found during:** Task 1 (uv sync)
- **Issue:** CLAUDE.md specifies `pytest-httpx~=0.36`, but pytest-httpx 0.36.x requires `pytest>=9.dev0` which is incompatible with the also-pinned `pytest~=8.3`
- **Fix:** Changed to `pytest-httpx~=0.35`; version 0.35.0 requires `pytest==8.*` exactly, which is compatible. The `httpx_mock` fixture API is stable across 0.35 and 0.36
- **Files modified:** pyproject.toml
- **Commit:** d7e59bb

## Known Stubs

None — this plan creates only scaffold and test stubs by design. All stub files are intentional; they will be wired up in plans 02-05 as noted in each file's docstring.

## Threat Flags

None — this plan creates only scaffolding files and dependency declarations. No request-handling code, no network I/O, no untrusted input is processed (per plan threat model).

## Self-Check: PASSED

- [x] pyproject.toml exists at repo root: FOUND
- [x] uv.lock exists at repo root: FOUND
- [x] src/mcp_zeeker/__init__.py: FOUND
- [x] tests/conftest.py: FOUND
- [x] All 10 test stub files: FOUND
- [x] Commit d7e59bb (Task 1): FOUND
- [x] Commit eb91ecd (Task 2): FOUND
- [x] 41 tests collected, 0 errors: CONFIRMED
- [x] ruff check and format pass: CONFIRMED
