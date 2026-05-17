---
phase: 02-discovery-surface-denylists
verified: 2026-05-13T00:00:00Z
updated: 2026-05-17T00:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
human_verification_completed:
  - test: "F-4 dry-run obligation: walk PHASE2-CLIENT-VERIFY.md against live https://mcp.zeeker.sg/mcp"
    expected: "list_tables, describe_table, DISC-05 side-channel check all pass; 4 screenshots committed under evidence/02-discovery/"
    operator: houfu
    signed_off: 2026-05-13
    sign_off_location: "tests/manual/PHASE2-CLIENT-VERIFY.md:185"
    evidence:
      - evidence/02-discovery/claude-desktop-list-tables.png
      - evidence/02-discovery/claude-desktop-describe-table.png
      - evidence/02-discovery/claude-desktop-disc05-side-channel.png
    deferrals:
      - "claude-code-list-tables.png and claude-code-describe-table.png — same three calls through a different MCP client transport; not load-bearing for acceptance (documented in F-4 block of PHASE2-CLIENT-VERIFY.md)"
    operator_attestation: "Claude Desktop walkthrough complete; DISC-02/03/04/05 all confirmed end-to-end against https://mcp.zeeker.sg/mcp."
---

# Phase 2: Discovery Surface + Denylists Verification Report

**Phase Goal:** An MCP client can enumerate the non-hidden tables in each database and inspect their schemas, with hidden tables and columns indistinguishable from genuinely nonexistent ones.
**Verified:** 2026-05-13 (status flipped to `passed` 2026-05-17 after operator sign-off on PHASE2-CLIENT-VERIFY.md was located)
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `list_tables(database)` returns non-hidden tables with row counts and descriptions; `sglawwatch.metadata` and `sglawwatch.schema_versions` never appear | VERIFIED | `discovery.py:181–207` applies `config.HIDDEN_TABLES`; `test_list_tables.py::test_sglawwatch_filters_metadata_and_schema_versions` asserts both tables absent; passes in full test run (81 pass) |
| 2 | `describe_table` returns exactly `{name, columns, light_columns, available_columns, url_keyed, supports_fragments, row_count, description}` with no `foreign_keys`, `indexes`, or `triggers` | VERIFIED | `TableSchema` in `discovery_models.py:60–79` has `extra="forbid"` and exactly 8 fields; `test_describe_table.py::test_locked_field_set` and `test_no_foreign_keys_or_indexes_leak` both assert this; passes |
| 3 | A request for a hidden table and a request for a genuinely nonexistent table return `unknown_table` with identical message text and indistinguishable timing — no presence side-channel | VERIFIED | `_resolve_table` is the sole code path (`discovery.py:102–116`); `raise_unknown_table` is the single emission point; `test_discovery_side_channel.py::test_hidden_and_nonexistent_share_helper` uses a counter-patch proving both increment the counter exactly once; passes |
| 4 | `describe_table` distinguishes `light_columns` from `available_columns` | VERIFIED | `discovery.py:238–243` computes both sets and includes both in `TableSchema`; `LIGHT_COLUMNS` in `config.py:97–128` provides per-table definitions; `test_describe_table.py::test_heavy_in_available_not_in_light` and `test_light_subset_of_available` both assert the distinction; passes |
| 5 | `config.HIDDEN_TABLES` and `config.HIDDEN_COLUMNS` are populated for all four databases and reviewable in a single audit pass | VERIFIED | `config.py:41–55`: all four databases present in `HIDDEN_TABLES`; `_zeeker_schemas`+`_zeeker_updates` in all; `sglawwatch` has 4 entries including `metadata`+`schema_versions`; `HIDDEN_COLUMNS` keyed on `"*"` plus 3 per-fragment-table entries; `test_hidden_columns_lookup.py` confirms lookup behavior; passes |

**Score:** 5/5 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/mcp_zeeker/config.py` | HIDDEN_TABLES + HIDDEN_COLUMNS + LIGHT_COLUMNS | VERIFIED | All present; HIDDEN_TABLES covers 4 DBs; HIDDEN_COLUMNS is `dict[str, set[str]]` with `"*"` global key and 3 per-table keys |
| `src/mcp_zeeker/core/metadata_cache.py` | Singleton+contextvar dual-binding, anyio.Lock single-flight, stale-on-error, D2-05 normalize-at-ingest | VERIFIED | All patterns present; `.lower()` at line 95 only inside `_fetch_and_normalize`; zero `.lower()` in four public methods |
| `src/mcp_zeeker/core/config_lookup.py` | `hidden_columns_for` as single call-site for `HIDDEN_COLUMNS` | VERIFIED | 31-line module; sole function unions global `"*"` with per-table key |
| `src/mcp_zeeker/tools/discovery.py` | `@mcp.tool` on `list_tables` and `describe_table`; shared `_visible_tables`/`_resolve_table`/`raise_unknown_table` helpers | VERIFIED | Lines 142, 172, 210: all three `@mcp.tool` decorators present; helpers at lines 59, 68, 81, 102 |
| `src/mcp_zeeker/tools/discovery_models.py` | `TableSchema(extra="forbid")` with exactly 8 fields | VERIFIED | Lines 60–79: 8 fields, `ConfigDict(extra="forbid")` |
| `src/mcp_zeeker/core/envelope.py` | `Envelope.for_table_list` factory present; both new tools return `Envelope` | VERIFIED | `for_table_list` at lines 73–93; `list_tables` returns it at line 207; `describe_table` uses `for_rows` at line 289 |
| `tests/tools/test_list_tables.py` | Covers DISC-02, hidden tables, row_count=None, description merge | VERIFIED | 7 test cases; all pass |
| `tests/tools/test_describe_table.py` | Covers DISC-03, DISC-04, hidden cols, no schema leak | VERIFIED | 10 test cases; all pass |
| `tests/tools/test_discovery_side_channel.py` | Counter-mechanism proving code-path identity; DISC-05 | VERIFIED | 3 tests; counter assertion at line 98 proves both hidden and nonexistent call `raise_unknown_table` exactly once each |
| `tests/test_metadata_cache.py` | D2-02/03/05/08 lifecycle tests | VERIFIED | 8 tests (1 live-gated, skipped); all others pass |
| `tests/test_hidden_columns_lookup.py` | D2-10 hidden_columns_for coverage | VERIFIED | 3 tests; all pass |
| `tests/test_transport_proxy_headers.py` | F-1 regression: proxy headers do not cause 500 | VERIFIED | 2 tests; both pass |
| `tests/test_transport_stateless_session.py` | F-3 regression: no Mcp-Session-Id issued; bogus session not 404'd | VERIFIED | 2 tests; both pass |
| `tests/manual/PHASE2-CLIENT-VERIFY.md` | F-4 dry-run obligation block present | VERIFIED | File exists; F-4 block at bottom (lines 157–182); sign-off line explicitly states "Pending human action — dry-run NOT yet performed" |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `discovery.py:list_tables` | `config.HIDDEN_TABLES` | Direct dict lookup at line 194 | WIRED | `hidden_set = config.HIDDEN_TABLES.get(database, set())` |
| `discovery.py:describe_table` | `_resolve_table` | First call in handler (line 232) | WIRED | Gate for hidden and nonexistent tables — single code path |
| `discovery.py:_resolve_table` | `raise_unknown_table` | Sole emission point (line 116) | WIRED | No alternative `unknown_table` raise sites found |
| `discovery.py:describe_table` | `hidden_columns_for` | Called at line 237 | WIRED | Returns union of global `"*"` and per-table hidden columns |
| `discovery.py:describe_table` | `MetadataCache.current()` | Lines 258, 273 | WIRED | Used for column descriptions and table descriptions |
| `app.py:lifespan` | `MetadataCache.bind` | Lines 77–78 | WIRED | MetadataCache constructed and bound in lifespan with shared httpx client |
| `envelope.py:for_table_list` | `list_tables` return | Line 207 | WIRED | `return Envelope.for_table_list(database=database, rows=rows)` |
| `envelope.py:for_rows` | `describe_table` return | Line 289 | WIRED | `return Envelope.for_rows(database=database, table=table, rows=[schema.model_dump()])` |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `list_tables` | `rows` (table list) | `DatasetteClient.get_database` → `/{db}.json` upstream | Yes — live HTTP fetch with retry; tests stub the upstream response | FLOWING |
| `describe_table` | `columns`, `available_columns` | `DatasetteClient.get_database` + `get_table_column_types` | Yes — columns from `/{db}.json`, types from `/_zeeker_schemas.json` | FLOWING |
| `describe_table` | `description` | `MetadataCache.get_table_metadata` + config fallback | Yes — TTL cache of `/-/metadata.json`; config fallback for absent entries | FLOWING |

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All unit tests pass | `uv run pytest -q` | 81 passed, 2 skipped, 0 failures | PASS |
| D2-05 `.lower()` only in `_fetch_and_normalize` | `grep -n '\.lower()' src/mcp_zeeker/core/metadata_cache.py` | Line 95 only | PASS |
| `raise_unknown_table` is single emission point | `grep -rn "unknown_table" src/mcp_zeeker/` | Only in `discovery.py:raise_unknown_table` definition and calls | PASS |
| `TableSchema` has exactly 8 fields + `extra="forbid"` | Manual inspection of `discovery_models.py:60–79` | 8 fields: name, columns, light_columns, available_columns, url_keyed, supports_fragments, row_count, description; `extra="forbid"` confirmed | PASS |
| HIDDEN_TABLES all 4 DBs populated with correct values | `uv run python -c "from mcp_zeeker import config; print(config.HIDDEN_TABLES)"` | All 4 databases present; sglawwatch has 4 entries including `metadata` and `schema_versions` | PASS |

---

## Probe Execution

Step 7c: No `scripts/*/tests/probe-*.sh` files found in the repository. Phase declares no probes. SKIPPED.

---

## Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|----------|
| DISC-02 | `list_tables(database)` returns non-hidden tables with row counts and descriptions | SATISFIED | `discovery.py:181–207`; 7 tests in `test_list_tables.py` all pass |
| DISC-03 | `describe_table` returns locked 8-field schema, never forwards `foreign_keys`/`indexes`/`triggers` | SATISFIED | `TableSchema(extra="forbid")` with 8 fields; `test_locked_field_set` and `test_no_foreign_keys_or_indexes_leak` pass |
| DISC-04 | `describe_table` distinguishes default light column set from full available columns | SATISFIED | `LIGHT_COLUMNS` in config; `discovery.py:238–243`; `test_heavy_in_available_not_in_light` and `test_light_subset_of_available` pass |
| DISC-05 | Requests for hidden tables return `unknown_table` identical to genuinely nonexistent — no presence side-channel | SATISFIED | `_resolve_table` single code path; counter-patch test in `test_discovery_side_channel.py` proves code-path identity |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/mcp_zeeker/core/envelope.py` | 103 | `TODO(phase-6, ENV-04): wire citation into provenance` | Info | References a formal future phase (ENV-04); scheduled work, not unresolved debt. Not a blocker. |
| `src/mcp_zeeker/config.py` | 200 | `# Placeholder per-DB license strings; real values land in Phase 6 (ENV-03).` | Info | Inline code comment naming Phase 6/ENV-03; not a TBD/FIXME/XXX marker. Not a blocker. |

No TBD, FIXME, or XXX markers found in any Phase 2 source files.

---

## Human Verification Required

### 1. F-4 Live Deployment Dry-Run

**Test:** Walk every item in `tests/manual/PHASE2-CLIENT-VERIFY.md` against the live `https://mcp.zeeker.sg/mcp` instance.

**Expected:**
- `tools/list` returns exactly 3 tool names: `list_databases`, `list_tables`, `describe_table`
- `list_tables(zeeker-judgements)` returns `judgments` and `judgments_fragments` with no `_zeeker`-prefixed tables
- `describe_table(zeeker-judgements, judgments)` response includes `light_columns`, `available_columns`, `url_keyed: true`, `supports_fragments: true`
- DISC-05: error messages for `sglawwatch.metadata` (hidden) and `sglawwatch.totally_fictitious_table` (nonexistent) have the same prefix `unknown_table: Table not found:` and differ only in the table identifier
- 4 screenshots committed under `evidence/02-discovery/`
- F-4 sign-off line filled in by operator

**Why human:** Requires a human at a keyboard with access to Claude Desktop and Claude Code against the deployed production instance (`https://mcp.zeeker.sg/mcp`). The automated test suite exercises the code paths in isolation using mock HTTP; it cannot verify the full deployment stack (DNS, TLS, Caddy, docker-network sibling-container path, MCP client handshake behavior in real clients).

### F-4 Sign-Off (located 2026-05-17)

Operator sign-off recorded at `tests/manual/PHASE2-CLIENT-VERIFY.md:185`:

> **Operator sign-off:** houfu, 2026-05-13 — Claude Desktop walkthrough complete; DISC-02/03/04/05 all confirmed end-to-end against `https://mcp.zeeker.sg/mcp`. Claude Code section deferred (same three calls through a different MCP client transport; not load-bearing for acceptance).

**F-4 checklist state:** 5 of 6 items `[x]`; one item `[ ]` (Claude Code CLI walkthrough) with explicit deferral rationale captured in the same block.

**Screenshots:** 3 of 5 captured (the three Claude Desktop variants — all checked `[x]` in the F-4 block); 2 deferred-with-rationale (the Claude Code variants — `[ ]` with explanatory note in line 182-183).

Status flip from `human_needed` to `passed` reflects the operator action that already happened — VERIFICATION.md frontmatter was simply not updated when the walkthrough completed on 2026-05-13.

---

## Gaps Summary

No blocking gaps found. All 5 must-have truths verified by automated tests. The sole outstanding item is the F-4 human dry-run obligation explicitly documented in `tests/manual/PHASE2-CLIENT-VERIFY.md` as "pending human action" — this is a known-acceptable checkpoint and does not block the phase goal as implemented in code.

The automated evidence is strong:
- 81 tests pass (0 failures, 2 live-gated skips)
- Counter-patch test in `test_discovery_side_channel.py` proves DISC-05 code-path identity mechanically, not just by message string comparison
- `TableSchema(extra="forbid")` and the `_resolve_table` single-gate pattern together make the response shape and side-channel properties structural guarantees, not just runtime assertions

---

*Verified: 2026-05-13 (status: human_needed) → operator-walked + signed off 2026-05-13 → status flipped to `passed` 2026-05-17 after sign-off located*
*Verifier: Claude (gsd-verifier); F-4 sign-off: houfu, 2026-05-13*
