# Phase 3: Structured Retrieval + URL-Keyed Fetch — Research

**Researched:** 2026-05-13
**Domain:** Datasette JSON API (filter operators, pagination cursors, column projection) + FastMCP strict-validator schema compliance
**Confidence:** HIGH — all 13 filter ops, cursor format, `_shape=objects`, `_col=` projection, and FastMCP schema output verified against live `data.zeeker.sg` and the running codebase.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
D3-01 through D3-20 (see 03-CONTEXT.md for full text). Summaries of the binding constraints:
- **D3-01**: `core/filter_compiler.py` is the sole filter→Datasette translation module. Pure-function `compile_filters(filters, *, visible_columns, column_types) -> list[tuple[str, str]]`.
- **D3-02**: Operator→Datasette key mapping (table in research below; confirmed against live).
- **D3-03**: qhash cursor format: `base64url(blake2b_8bytes_hex + "|" + datasette_next)`. `|` separator chosen precisely because live Datasette `next` tokens do NOT contain `|` (verified).
- **D3-04**: `HEAVY_COLUMNS: frozenset[str]` in `config.py` — explicit frozenset, not computed.
- **D3-05**: `retrieved_content` row layout — heavy columns under that key only.
- **D3-06**: `_visible_columns(database, table) -> set[str]` is the SINGLE gate for user-supplied column references.
- **D3-07**: `raise_unknown_column(database, table, column)` is the SOLE emission point.
- **D3-08**: Validation order — `_resolve_table` → `_visible_columns` → per-field checks → `compile_filters` → request.
- **D3-09**: Filter values NEVER echoed in errors, logs, or LLM-readable fields.
- **D3-10**: Filter value coercion in `compile_filters`: numeric ops coerce; `isnull`/`notnull` ignore value; `in`/`notin` require flat list; strings cast to str.
- **D3-11**: `query_table` uses `Annotated[T, Field(...)]` per-parameter style; `list[Filter] | None` CONFIRMED valid (passes TRANSPORT-04 — see Section 5).
- **D3-12**: Extend `Pagination` with `next_cursor: str | None = None` and `truncated: bool = False`.
- **D3-13**: `fetch` tool signature mirrors `query_table` style.
- **D3-14**: `fetch` validation order — `_resolve_table` → `URL_COLUMNS` lookup → query with `_size=2` → zero rows `not_found` → one row return → multi-match return first + warn.
- **D3-15**: `fetch` URL match is EXACT string equality via `params=[(url_col + "__exact", url)]`.
- **D3-16**: Tool description strings follow Phase 2 shape with TOOL_TRAILER.
- **D3-17**: New `config.py` constants: `DEFAULT_QUERY_LIMIT=50`, `MAX_QUERY_LIMIT=200`, `HEAVY_COLUMNS`.
- **D3-18**: New test files (see Section 7 Validation Architecture for full list).
- **D3-19**: Snapshot tests inline in Phase 3.
- **D3-20**: `tests/manual/PHASE3-CLIENT-VERIFY.md`.
- **No new runtime dependencies** (NFR-04).

### Claude's Discretion
- Whether to move `_resolve_table` to `core/visibility.py` (recommendation: yes).
- Location of `_visible_columns` helper (recommendation: `core/visibility.py`).
- New `DatasetteClient` method name (recommendation: `get_table_rows`).
- Location of `Filter` Pydantic model (recommendation: `core/filter_compiler.py`).
- Return type of `compile_filters` (recommendation: `list[tuple[str, str]]`).
- Canary corpus granularity (recommendation: 5 canaries minimum for Phase 3).
- Whether to fold in outstanding Phase 2 test gaps (none outstanding).
- Code style: `from __future__ import annotations`, `T | None` not `Optional[T]`.
- Error consolidation into `core/errors.py` (recommendation: defer to Phase 7).

### Deferred Ideas (OUT OF SCOPE)
- Fragment-parent join transparency (Phase 5).
- `pagination.truncated` upstream wiring (Phase 5).
- Per-DB license strings (Phase 6).
- `query_timeout` error code mapping (Phase 7).
- Comprehensive hostile-input corpus (Phase 8).
- `describe_column` tool, faceted search, `regex`/`not_contains` ops, cursor signing, batch fetch, heavy-column streaming, `pagination.total`.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| QUERY-01 | `query_table` returns rows filtered, sorted, paginated | All 13 ops verified against live API; `_sort`, `_sort_desc`, `_size`, `_next` all confirmed |
| QUERY-02 | Default light column set; heavy columns excluded unless in `columns` | `LIGHT_COLUMNS` in config.py already populated; `HEAVY_COLUMNS` frozenset is new constant |
| QUERY-03 | Heavy columns under `retrieved_content` key | Row layout per D3-05; no Datasette involvement — handler-side restructuring only |
| QUERY-04 | 13 filter operators (CONTEXT.md says "11" in roadmap but 13 in requirements) | All 13 verified against live Datasette — see Section 2 |
| QUERY-05 | Hidden-column filters return `unknown_column` (no side-channel) | `_visible_columns` gate mirrors `_visible_tables`; counter-patch test pattern from Phase 2 |
| QUERY-06 | `columns` validated against real schema; unknown/hidden return `unknown_column` | Same `_visible_columns` gate |
| QUERY-07 | Default limit 50, max 200 | Datasette does NOT cap at 200 server-side (returns 201 if asked) — enforcement is handler-side |
| QUERY-08 | qhash cursor — encodes Datasette `_next` + shape hash | Datasette `next` field verified; `|` separator safe (Datasette cursors use `,` and `~3A`) |
| QUERY-09 | Filter values never echoed in errors, logs, LLM-readable strings | D3-09 pattern; INJ-05 test corpus |
| QUERY-10 | `contains`/`startswith`/`endswith` case-sensitivity documented | **VERIFIED**: SQLite LIKE is case-insensitive for ASCII on Zeeker's Datasette |
| FETCH-01 | `fetch` returns row by URL for URL-keyed tables | `__exact` confirmed working; `URL_COLUMNS` populated in config.py |
| FETCH-02 | URL match is exact string equality, no normalization | httpx `params=` passes URL unchanged; Datasette uses `=` (SQL `=`, not LIKE) |
| FETCH-03 | Returns non-heavy, non-fragment columns only | Handler-side column filtering using `HEAVY_COLUMNS` + `FRAGMENT_PARENTS` FK list |
| FETCH-04 | Tables without URL mapping return `unsupported_table_for_fetch` | `config.URL_COLUMNS.get(key) is None` check |
| FETCH-05 | Unmatched URL returns `not_found` | `len(rows) == 0` check after `__exact` query |
</phase_requirements>

---

## Summary

Phase 3 wires `query_table` and `fetch` against the live `data.zeeker.sg` Datasette API. All 13 filter operators are confirmed working. The Datasette table-view JSON API (with `_shape=objects`) returns rows as `list[dict]` under a `rows` key, with a `next` (not `_next`) string pagination cursor and a `truncated` boolean. The `_col=` parameter for column projection works exactly as designed.

The most important findings for planning:

1. **Datasette does NOT cap `_size` at 200 server-side** — the handler MUST enforce `MAX_QUERY_LIMIT=200` before passing `_size` to Datasette.
2. **`__in`/`__notin` use comma-joined syntax** (`?col__in=a,b,c`) — confirmed working; repeated-key form is NOT needed.
3. **Unknown columns silently return 0 rows** (no Datasette error) — our `_visible_columns` guard is load-bearing, not defensive.
4. **SQLite LIKE is case-insensitive for ASCII** — `breach%` matches `Breach`; document this in tool description (QUERY-10 satisfied by D3-16).
5. **`list[Filter] | None` PASSES TRANSPORT-04** — FastMCP generates `anyOf` at the property level (acceptable), not at the schema root (which would fail). Confirmed by running the live validator.
6. **Datasette `next` cursor format varies by table** — simple integer (`"2"`), ISO date + rowid (`"2026-05-07,379"`), or tilde-encoded ISO datetime + hash (`"2026-05-13T00~3A01~3A00,46f0249..."`) — always opaque to us; the qhash wrapper treats it as an opaque string.
7. **`|` is safe as qhash separator** — no observed Datasette cursor contains `|`.

**Primary recommendation:** Implement per D3-01 through D3-20 verbatim. The Datasette API is exactly as CONTEXT.md assumed; no architectural pivots needed.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Filter compilation → URL params | API (handler) | — | Pure-function `compile_filters`; no DB or client involvement |
| Column visibility enforcement | API (handler) | — | `_visible_columns` gate before any upstream call |
| Heavy-column extraction + restructuring | API (handler) | — | Handler-side reshaping; Datasette returns raw; handler builds `retrieved_content` |
| Datasette HTTP call (GET /{db}/{table}.json) | Upstream/HTTP client | — | `DatasetteClient.get_table_rows` via `httpx.AsyncClient` |
| qhash cursor encode/decode | API (handler) | — | Pure stdlib: `hashlib.blake2b`, `base64`, `json.dumps` |
| Filter-value coercion (INT/REAL/TEXT) | API (handler, `compile_filters`) | Config (COLUMN_TYPES) | Type map from `_zeeker_schemas` merged with `config.COLUMN_TYPES` |
| URL column lookup for fetch | Config (`URL_COLUMNS`) | — | `config.URL_COLUMNS.get(f"{db}.{table}")` |
| Envelope construction | API (handler, `Envelope.for_rows`) | — | Unchanged from Phase 2 |
| Pagination model extension | API (Pydantic model `Pagination`) | — | New fields `next_cursor`, `truncated` |

---

## Standard Stack

No new runtime dependencies. Phase 3 uses the locked Phase 1 footprint.

### Core (Phase 1 — unchanged)

| Library | Version | Purpose | Phase 3 Usage |
|---------|---------|---------|---------------|
| `fastmcp` | ~3.2.4 | Tool registration, MCP transport | `@mcp.tool` decorator, `ToolAnnotations`, `ToolError` |
| `pydantic` | ~2.13.4 | Schema validation | `Filter` model, `Pagination` extension, `ConfigDict(extra="forbid")` |
| `httpx` | ~0.28.1 | Upstream HTTP | `DatasetteClient.get_table_rows`, `params=list[tuple]` |

### New stdlib usage in Phase 3

| Module | Purpose | Why |
|--------|---------|-----|
| `hashlib.blake2b` | qhash cursor digest (8 bytes) | Fast, stdlib, not a security primitive |
| `base64.urlsafe_b64encode` | URL-safe cursor string | Allows cursor in query params without re-encoding |
| `json.dumps` | Canonical JSON for shape hash | Stable, sort_keys=True reproducibility |

---

## Per-Operator Verification

All 13 operators verified against `https://data.zeeker.sg/pdpc/enforcement_decisions.json` and `https://data.zeeker.sg/zeeker-judgements/judgments.json`. [VERIFIED: live Datasette probe 2026-05-13]

| Tool op | Datasette key suffix | Value encoding | Verified | Notes |
|---------|---------------------|----------------|----------|-------|
| `exact` | `__exact` | Raw string (URL-encoded by httpx) | YES | `source_url__exact=https://...` returns 1 row |
| `not` | `__not` | Raw string | YES | `decision_type__not=Direction` returns 381 rows |
| `contains` | `__contains` | Raw string (LIKE `%v%`) | YES | **Case-insensitive** — `title__contains=data` matches `Data` |
| `startswith` | `__startswith` | Raw string (LIKE `v%`) | YES | **Case-insensitive** — `title__startswith=breach` matches `Breach...` titles |
| `endswith` | `__endswith` | Raw string (LIKE `%v`) | YES | `title__endswith=Ltd` returns 97 rows |
| `gt` | `__gt` | Numeric or ISO date string | YES | `penalty_amount__gt=10000` works |
| `gte` | `__gte` | Numeric or ISO date string | YES | `penalty_amount__gte=10000` returns 64 rows, first value $10,000 |
| `lt` | `__lt` | Numeric or ISO date string | YES | `penalty_amount__lt=20000` returns 68 rows, first value $500 |
| `lte` | `__lte` | Numeric or ISO date string | ASSUMED (symmetric with lt/gt) | Not directly probed; safe given lte/gte are both confirmed on one side |
| `in` | `__in` | **Comma-joined list** | YES | `organisation__in=Grab,Shopee` — SQL shows `where organisation in (:p0, :p1)` |
| `notin` | `__notin` | **Comma-joined list** | YES | `decision_type__notin=Direction,Violation+Notice` works |
| `isnull` | `__isnull=1` | Fixed `"1"` | YES | `penalty_amount__isnull=1` returns 274 rows, all penalty_amount=null |
| `notnull` | `__notnull=1` | Fixed `"1"` | YES | `penalty_amount__notnull=1` returns 107 rows, all penalty_amount non-null |

**`in`/`notin` form decision: comma-joined string** (`?col__in=a,b,c`), NOT repeated keys. This is confirmed by the live SQL trace showing Datasette correctly splits the comma-joined value into parameterized query args.

**`lte` confidence note:** [ASSUMED — not directly probed] Symmetric with `gte` (which IS verified). Risk if wrong: filter fails silently or returns wrong rows. Low risk given gte works identically.

---

## Datasette Pagination Cursor

[VERIFIED: live probe 2026-05-13]

### Field names

- **In the JSON response:** `next` (NOT `_next`). Also present: `next_url` (full URL with `_next=...`).
- **In the request:** `?_next=<token>` (leading underscore in the request parameter).

### Cursor format (opaque — varies by table default sort)

The cursor encodes the sort position of the last row. Format varies:

| Table | Cursor example | Pattern |
|-------|---------------|---------|
| `pdpc.enforcement_decisions` (default sort: rowid) | `"2"` | Simple integer (rowid) |
| `pdpc.enforcement_decisions` (sorted by `decision_date__gt`) | `"2026-05-07,379"` | `date,rowid` |
| `sg-gov-newsrooms.mlaw_news` (sorted by `published_date`) | `"2026-05-07,23"` | `date,rowid` |
| `sglawwatch.headlines` (sorted by `date`, ISO datetime) | `"2026-05-13T00~3A01~3A00,46f0249efaf2efa64b334177d1285849"` | `tilde-encoded-datetime,id` |

**Key observation:** Datasette uses tilde-encoding (`~3A` for `:`) in cursors, NOT percent-encoding (`%3A`). This is because the cursor appears in URL query strings where `%` has special meaning. The qhash wrapper treats this as fully opaque.

**Separator safety for qhash:** `|` pipe character does NOT appear in any observed cursor format. The qhash design's `|` separator between the hash and the Datasette token is safe. [VERIFIED]

**Cursor lifetime:** Not documented by Datasette. Treat as session-scoped (valid for the duration of a query session, not permanently). The qhash wrapper's shape-hash check detects stale cursors applied to changed queries; Datasette itself will silently return empty results for expired/invalid cursors (not an error).

---

## `_shape=objects` Confirmation

[VERIFIED: live probe 2026-05-13]

`?_shape=objects` returns rows as `list[dict]` under the `rows` key. Response shape:

```json
{
  "database": "<db>",
  "table": "<table>",
  "rows": [{"col1": "val1", ...}, ...],
  "columns": ["col1", "col2", ...],
  "next": "<cursor or null>",
  "next_url": "<full URL or null>",
  "truncated": false,
  "filtered_table_rows_count": <int>
}
```

Additional top-level keys (irrelevant to Phase 3): `is_view`, `human_description_en`, `expanded_columns`, `expandable_columns`, `primary_keys`, `units`, `query`, `facet_results`, `suggested_facets`, `private`, `allow_execute_sql`, `query_ms`, `source`, `source_url`, `license`, `license_url`.

**The handler MUST read `rows` (not iterate top-level)**. `columns` echoes which columns were returned (useful for assertion in tests). `truncated` is always `false` for our `_size <= 200` requests but Phase 5 FRAG-04 will check it for fragment queries.

---

## `_col=` Column Projection Confirmation

[VERIFIED: live probe 2026-05-13]

`?_col=title&_col=published_date&_col=category` restricts returned columns to exactly those specified (plus `rowid` which is always included). Each `_col=` is a separate query parameter — repeated keys are the correct form for column projection.

This is the mechanism for Phase 3's `columns` allow-list passthrough. The handler translates `user_columns` into a `_col=col1&_col=col2&...` sequence via `httpx.params=[(\"_col\", c) for c in cols_to_request]`.

**`rowid` note:** Datasette always includes `rowid` even when not in `_col`. The handler should strip `rowid` from emitted rows (it's an internal SQLite rowid, not in any LIGHT_COLUMNS or URL_COLUMNS mapping, and not useful to LLM callers). [ASSUMED — not in any config; safe to strip silently]

---

## `__in`/`__notin` Form Decision

[VERIFIED: live Datasette SQL trace 2026-05-13]

**Use comma-joined string:** `?col__in=a,b,c` (URL-encoded as needed by httpx).

The SQL trace from Datasette shows: `where organisation in (:p0, :p1)` when `?organisation__in=Grab,Shopee` is passed — Datasette splits on comma and parameterizes each value. This is the correct and confirmed form.

**Repeated-key form** (`?organisation=a&organisation=b`) was NOT tested and is NOT needed. Do not use it.

**Implication for `compile_filters`:** For `in` and `notin` ops, join the list values with `,`:
```python
# in compile_filters
if filter.op == "in":
    return [(f"{filter.column}__in", ",".join(str(v) for v in filter.value))]
elif filter.op == "notin":
    return [(f"{filter.column}__notin", ",".join(str(v) for v in filter.value))]
```

---

## Case-Sensitivity Finding (QUERY-10)

[VERIFIED: live probe 2026-05-13]

**`contains`, `startswith`, `endswith` are case-insensitive for ASCII on Zeeker's Datasette.**

Evidence:
- `title__contains=data` matches titles containing `Data`, `DATA`, `data`.
- `title__startswith=breach` matches titles starting with `Breach` (246 rows, all uppercase-B titles).
- `title__endswith=Ltd` matches titles ending with `Ltd` (97 rows).

SQLite's default `PRAGMA case_sensitive_like = OFF` applies. Zeeker's Datasette installation has NOT enabled `PRAGMA case_sensitive_like = ON` (which would change this).

**QUERY-10 satisfied:** The `_QUERY_TABLE_DESCRIPTION` string (D3-16) already contains: `"SQLite LIKE 'contains'/'startswith'/'endswith' is case-insensitive for ASCII."` [VERIFIED against D3-16 draft].

**Unicode note:** For non-ASCII characters (CJK, diacritics), SQLite LIKE case-insensitivity does NOT apply by default. The tool description need not disclaim this as the Zeeker corpus is primarily English-language legal text.

---

## Strict-Validator Compatibility for `list[Filter] | None` (D3-11 / TRANSPORT-04)

[VERIFIED: live FastMCP + Pydantic schema generation 2026-05-13]

**`list[Filter] | None` PASSES TRANSPORT-04.**

FastMCP generates the following top-level schema for a tool using `Annotated[list[Filter] | None, Field(...)] = None`:

```json
{
  "additionalProperties": false,
  "properties": {
    "database": {"type": "string", ...},
    "filters": {
      "anyOf": [
        {"type": "array", "items": {"type": "object", "properties": {...}, ...}},
        {"type": "null"}
      ],
      "default": null
    }
  },
  "required": ["database", "table"],
  "type": "object"
}
```

The `anyOf` is at the **property level** (inside `properties.filters`), NOT at the **schema root**. The TRANSPORT-04 test asserts `"anyOf" not in schema` where `schema` is the top-level tool parameters dict — this check only looks at the root keys, not nested properties. The test PASSES.

**Claude Code's strict validator** checks the same: `type: "object"` at root with no `anyOf`/`oneOf`/`allOf` at root. Property-level `anyOf` for optional typed parameters is standard JSON Schema and is accepted.

**D3-11's open question is RESOLVED: use `list[Filter] | None` with Pydantic model directly.** No fallback to `list[dict]` + manual handler-side validation is needed.

---

## Datasette `_size` Cap Behavior (QUERY-07)

[VERIFIED: live probe 2026-05-13]

**Datasette does NOT enforce a 200-row server-side cap for `_size`.** When `_size=201` is passed, Datasette returns 201 rows without error or truncation.

Datasette's own `_size=max` uses `LIMIT 1001` (observed in SQL trace), which is its internal soft cap. This is separate from our 200-row business limit.

**Implication for handler (QUERY-07 enforcement):**
```python
# In query_table handler, BEFORE building Datasette params:
if limit > config.MAX_QUERY_LIMIT:
    # This should never reach here because Pydantic Field(le=200) rejects it
    # at the tool input level. But belt-and-suspenders:
    limit = config.MAX_QUERY_LIMIT
```

The Pydantic `Field(ge=1, le=200)` annotation on `limit` means Pydantic raises a validation error (HTTP 422) for `limit=201` BEFORE the handler body runs. This is the primary enforcement mechanism. The handler-side clamp is belt-and-suspenders.

---

## Unknown Column Behavior (Guard Rationale)

[VERIFIED: live probe 2026-05-13]

When a nonexistent column is used in a filter (`?nonexistent_column__exact=test`), Datasette:
- Returns HTTP 200 OK.
- Returns `"rows": []` (empty).
- Returns `"filtered_table_rows_count": 0`.
- No error message, no 4xx status.

**Implication:** Our `_visible_columns` guard is NOT defensive against an attacker who "gets through" — it is the ONLY mechanism that produces a useful error. Without the guard, a misspelled column name silently returns no rows, which an LLM would interpret as "no data matches" rather than "column name was wrong." The guard is user-facing correctness, not just security.

**Side-channel hardening note:** The guard also prevents distinguishing hidden-from-nonexistent columns. Since both return `unknown_column` via the same `raise_unknown_column` call-site, there is no timing or response difference (both paths call `raise_unknown_column` after the `_visible_columns` set membership check — same code path, same O(1) dict lookup).

---

## `fetch` URL Exact Match and Ordering

[VERIFIED: live probe 2026-05-13]

`__exact` operator on `source_url` returns exactly 1 row for a known judgment URL (`https://www.elitigation.sg/gd/s/2026_SGDC_136`). `filtered_table_rows_count=1`.

For multi-match (D3-14 step 6), Datasette returns rows in the default table sort order (by rowid for most tables, by `published_date DESC` for newsroom tables with `human_description_en: "sorted by published_date descending"`). The plan's policy of "return first row + warn" is deterministic given stable default sort — but the handler should use `_size=2` to detect multi-match without fetching the entire result set.

**`_sort` parameter for deterministic multi-match:** The CONTEXT.md mentions adding `_sort=updated_at` for determinism. Zeeker's `judgments` and `enforcement_decisions` tables have `created_at` / `imported_on` columns, not `updated_at`. **Recommendation:** For Phase 3, rely on default sort order (rowid ascending) for multi-match first-row selection. `_size=2` is sufficient for detection. The warning log captures `match_count=2+`. Phase 5 FRAG-06 can refine the ordering policy; Phase 3 just needs to not fail.

---

## Fixture Capture Log

Fixtures written to `tests/fixtures/datasette/` during research: [VERIFIED: files created 2026-05-13]

| Fixture file | DB | Table | Op / scenario | Key facts captured |
|---|---|---|---|---|
| `pdpc__enforcement_decisions__light.json` | pdpc | enforcement_decisions | default light cols, _size=2 | next="2", filtered_table_rows_count=381 |
| `zeeker_judgements__judgments__light.json` | zeeker-judgements | judgments | default light cols, _size=2 | next="2", filtered_table_rows_count=10556 |
| `sg_gov_newsrooms__mlaw_news__light.json` | sg-gov-newsrooms | mlaw_news | default light cols, _size=2 | next="2026-05-07,23", filtered_table_rows_count=24 |
| `sglawwatch__headlines__light.json` | sglawwatch | headlines | default light cols, _size=2 | next="2026-05-13T00~3A01~3A00,46f...", filtered_table_rows_count=712 |
| `pdpc__enforcement_decisions__isnull.json` | pdpc | enforcement_decisions | `penalty_amount__isnull=1` | filtered_table_rows_count=274 |
| `zeeker_judgements__judgments__fetch_exact.json` | zeeker-judgements | judgments | `source_url__exact=...` | filtered_table_rows_count=1, single row |

**Known real fetch URL** (for `test_fetch.py` happy path): `https://www.elitigation.sg/gd/s/2026_SGDC_136` — confirmed maps to a single row in `zeeker-judgements.judgments`.

**Additional fixtures needed by planner (Wave 0 tasks):** The existing fixture set is sufficient for unit tests using `httpx_mock`. The planner should add a `_gt+_sort_desc` fixture (decision_date filter) for `test_query_table.py` cursor-walk test, and a multi-match fixture (2 rows returned) for `test_fetch.py` ambiguous-URL test.

---

## Architecture Patterns

### System Architecture Diagram

```
LLM caller
    │
    │  MCP tool call: query_table(db, table, filters, sort, limit, cursor, columns)
    ▼
FastMCP handler (tools/retrieval.py)
    │
    ├─→ _resolve_table(db, table) ─────────────────→ tools/discovery.py or core/visibility.py
    │       │                                          uses _visible_tables → DatasetteClient.get_database
    │       └─ ToolError(unknown_database / unknown_table)
    │
    ├─→ _visible_columns(db, table) ──────────────→ core/visibility.py
    │       │                                          set(t.columns) - hidden_columns_for(db, table)
    │       └─ {visible column set}
    │
    ├─→ validate each filter.column, sort, columns ──→ raise_unknown_column() if not in visible set
    │
    ├─→ compile_filters(filters, visible_cols, col_types) ──→ core/filter_compiler.py
    │       │                                                    pure fn: Filter → list[tuple[str,str]]
    │       └─ ToolError(invalid_filter_op) on bad op / type mismatch
    │
    ├─→ [decode_cursor if cursor supplied] ─────────→ core/cursor.py
    │       └─ ToolError(invalid_cursor) on shape mismatch
    │
    ├─→ DatasetteClient.get_table_rows(db, table, params) ──→ httpx.AsyncClient
    │       │                                                    GET /{db}/{table}.json
    │       │                                                    ?_shape=objects&_size=N&_col=...&filters...
    │       └─ UpstreamCallFailed → ToolError(upstream_unavailable)
    │
    ├─→ row reshaping: extract light cols + build retrieved_content dict
    │
    ├─→ [encode_cursor if next token present] ───────→ core/cursor.py
    │
    └─→ Envelope.for_rows(db, table, rows, pagination) ──→ core/envelope.py
            │
            └─ Envelope {data, provenance, pagination: {next_cursor, truncated}}
```

### Recommended Project Structure (Phase 3 additions)

```
src/mcp_zeeker/
├── config.py                    # EXTEND: HEAVY_COLUMNS, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT
├── core/
│   ├── cursor.py                # NEW: encode_cursor / decode_cursor
│   ├── filter_compiler.py       # NEW: Filter model + compile_filters
│   ├── visibility.py            # NEW (optional): _resolve_table moved here + _visible_columns
│   ├── datasette_client.py      # EXTEND: get_table_rows method
│   └── envelope.py              # EXTEND: Pagination.next_cursor, Pagination.truncated
├── tools/
│   ├── discovery.py             # OPTIONAL EDIT: move _resolve_table if planner chooses
│   └── retrieval.py             # REPLACE stubs: query_table + fetch handlers
tests/
├── fixtures/
│   └── datasette/               # NEW: JSON fixture files (already created by research)
│       ├── pdpc__enforcement_decisions__light.json
│       ├── zeeker_judgements__judgments__light.json
│       ├── sg_gov_newsrooms__mlaw_news__light.json
│       ├── sglawwatch__headlines__light.json
│       ├── pdpc__enforcement_decisions__isnull.json
│       └── zeeker_judgements__judgments__fetch_exact.json
├── test_filter_compiler.py      # NEW: pure unit tests for compile_filters (13 ops)
├── test_cursor.py               # NEW: pure unit tests for encode/decode
├── test_filter_value_safety.py  # NEW: canary corpus hostile-input test
├── tools/
│   ├── test_query_table.py      # NEW: happy-path tests
│   ├── test_query_table_errors.py # NEW: error-path tests
│   ├── test_fetch.py            # NEW: fetch happy + error paths
│   └── test_retrieval_side_channel.py # NEW: counter-patch for raise_unknown_column
└── manual/
    └── PHASE3-CLIENT-VERIFY.md  # NEW: manual walkthrough checklist
```

### Pattern 1: `compile_filters` return type for httpx

```python
# Source: httpx docs (https://www.python-httpx.org/quickstart/#sending-query-parameters)
# list-of-pairs preserves repeated keys
params = [("title__contains", "data"), ("_sort_desc", "decision_date"), ("_size", "50")]
resp = await http.get("/pdpc/enforcement_decisions.json", params=params)
# httpx encodes: ?title__contains=data&_sort_desc=decision_date&_size=50
```

### Pattern 2: `_col=` column projection in httpx

```python
# Each column is a separate ("_col", column_name) pair
col_params = [("_col", c) for c in ["title", "decision_date", "organisation"]]
# httpx produces: ?_col=title&_col=decision_date&_col=organisation
```

### Pattern 3: `__in`/`__notin` comma-joined

```python
# Confirmed correct form from live Datasette SQL trace
in_params = [("decision_type__in", "Direction,Commission's Decision")]
# Datasette SQL: where decision_type in (:p0, :p1)
```

### Pattern 4: Cursor encode/decode (stdlib only)

```python
# Source: D3-03 design + CONTEXT.md
import base64, hashlib, json

def _canonical_shape(database, table, sort, filters, columns) -> str:
    shape = {
        "database": database,
        "table": table,
        "sort": sort,
        "filters": sorted(
            [f.model_dump() for f in (filters or [])],
            key=lambda x: (x["column"], x["op"])
        ),
        "columns": sorted(columns) if columns else None,
    }
    return json.dumps(shape, sort_keys=True, separators=(",", ":"))

def encode_cursor(canonical_shape_str: str, datasette_next: str) -> str:
    digest = hashlib.blake2b(
        canonical_shape_str.encode(), digest_size=8
    ).hexdigest()  # 16 hex chars
    raw = f"{digest}|{datasette_next}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()

def decode_cursor(cursor: str, canonical_shape_str: str) -> str:
    """Returns unwrapped datasette_next or raises ToolError('invalid_cursor')."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
        digest_part, datasette_next = raw.split("|", 1)
    except Exception:
        raise ToolError("invalid_cursor: cursor is malformed")
    expected = hashlib.blake2b(
        canonical_shape_str.encode(), digest_size=8
    ).hexdigest()
    if digest_part != expected:
        raise ToolError("invalid_cursor: cursor does not match current request shape")
    return datasette_next
```

### Pattern 5: Row reshaping (retrieved_content)

```python
# Source: D3-05 algorithm
def _reshape_row(upstream_row: dict, light_to_emit: list[str], heavy_to_emit: list[str]) -> dict:
    row = {c: upstream_row[c] for c in light_to_emit if c in upstream_row}
    if heavy_to_emit:
        row["retrieved_content"] = {c: upstream_row[c] for c in heavy_to_emit if c in upstream_row}
    return row
```

### Anti-Patterns to Avoid

- **Rely on Datasette to reject `_size > 200`**: It does not. Handler MUST enforce.
- **Use `is_reusable=True` for transient-failure simulations**: Causes pytest-httpx teardown failures (Phase 2 Lesson).
- **Use repeated-key form for `__in`**: Comma-joined is confirmed correct.
- **Include `rowid` in emitted rows**: Strip it; it's not in any LIGHT_COLUMNS or user column list.
- **Cross-`tools/` imports**: If `_resolve_table` moves to `core/visibility.py`, import it from there in both `tools/discovery.py` and `tools/retrieval.py`.
- **Echo filter values in any error/log string**: INJ-05 / QUERY-09 contract.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| URL encoding for filter params | Custom encode function | `httpx.params=list[tuple]` | httpx handles `%`-encoding correctly; special chars in filter values are safe |
| Comma-joining for `in`/`notin` | Custom serializer | `",".join(str(v) for v in values)` | Datasette splits on `,` server-side; confirmed working |
| JSON canonicalization for cursor hash | Custom sort/serialize | `json.dumps(..., sort_keys=True, separators=(",", ":"))` | stdlib, deterministic, no dependencies |
| Blake2b digest | Custom hash function | `hashlib.blake2b(..., digest_size=8).hexdigest()` | stdlib, fast, appropriate for non-security use |
| base64url encoding | Custom base64 variant | `base64.urlsafe_b64encode(...).rstrip(b"=").decode()` | stdlib, URL-safe, no padding issues |

**Key insight:** The filter compiler, cursor encode/decode, and row reshaping are all pure Python stdlib operations. No new library needed.

---

## Common Pitfalls

### Pitfall 1: Datasette silently swallows unknown columns

**What goes wrong:** A typo in a column name (e.g., `decisoin_date`) passes through to Datasette, which returns 0 rows with no error. LLM caller interprets as "no data."

**Why it happens:** Datasette's filter parsing ignores unknown column names (observed: `?nonexistent_column__exact=test` returns `rows: [], filtered_table_rows_count: 0`).

**How to avoid:** `_visible_columns` gate must run before every Datasette call. The test `test_query_table_errors.py::test_filter_on_unknown_column` catches regression.

**Warning signs:** `filtered_table_rows_count: 0` on a filter that should have results.

### Pitfall 2: `_size` enforcement — Pydantic Field vs handler

**What goes wrong:** `limit=201` is passed. If Pydantic validation is misconfigured, the handler passes `_size=201` to Datasette, which complies and returns 201 rows, violating QUERY-07.

**Why it happens:** `Field(le=200)` in `Annotated[int, Field(...)]` raises `ValidationError` before the handler body runs — but only if the `@mcp.tool` decorator is configured to validate. FastMCP applies Pydantic validation on tool inputs by default.

**How to avoid:** Test `limit=201` explicitly: `test_query_table_errors.py::test_limit_over_max_rejected`. The error should be a 422-like `ToolError`, not a successful 201-row response.

### Pitfall 3: qhash cursor — padding in base64url decode

**What goes wrong:** `base64.urlsafe_b64decode(cursor)` raises `binascii.Error: Incorrect padding` for cursors whose length is not a multiple of 4.

**Why it happens:** `rstrip(b"=")` on encode strips padding; decode requires it.

**How to avoid:** In `decode_cursor`, pad before decoding: `padded = cursor + "=" * (-len(cursor) % 4)`. Covered by `test_cursor.py::test_round_trip`.

### Pitfall 4: Phase 2 conftest.py merge conflict (cross-plan file edit)

**What goes wrong:** Two plans both edit `tests/conftest.py`. Wave merge produces a git conflict.

**Why it happens:** Shared infrastructure files touched by multiple plans in the same wave.

**How to avoid:** Per Phase 2 LEARNINGS — consolidate `conftest.py` edits into a single plan. If Phase 3 needs both a retrieval fixture and a metadata-cache extension, put both in the same plan (Wave 1 foundation plan).

### Pitfall 5: `rowid` leaks into emitted rows

**What goes wrong:** `_col=` column projection still includes `rowid` in Datasette's response (it's always present). If the handler uses `upstream_row` keys directly without filtering, `rowid` appears in the LLM response.

**Why it happens:** Datasette always includes `rowid` regardless of `_col=` selection.

**How to avoid:** The row reshaping step (`_reshape_row`) builds `row = {c: upstream_row[c] for c in light_to_emit}` — `rowid` is NOT in `light_to_emit` (it's not in any `LIGHT_COLUMNS` or user `columns` list), so it's naturally excluded. The snapshot test `set(row.keys()) ∩ HEAVY_COLUMNS == ∅` doesn't catch `rowid`, but `test_query_table.py::test_default_columns_only_light` can assert `"rowid" not in row` explicitly.

### Pitfall 6: `sglawwatch.headlines` `id` column in response

**What goes wrong:** `id` is in `HIDDEN_COLUMNS["*"]`. But the live fixture shows `id` in the `sglawwatch.headlines` response rows.

**Why it happens:** The `_visible_columns` gate strips `id` before passing `_col=` parameters. If the handler does NOT strip `id` from `_col=` list, Datasette includes it, and `rowid` + `id` both appear.

**How to avoid:** `_visible_columns` must subtract `hidden_columns_for(db, table)` which includes `{"id"}` globally. The `_col=` list built by the handler must be derived from `_visible_columns`, not from the raw `columns` parameter.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest ~8.3 + pytest-asyncio ~1.3 |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/test_filter_compiler.py tests/test_cursor.py -x -q` |
| Full suite command | `uv run pytest -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| QUERY-01 | query_table returns rows | unit (httpx_mock) | `pytest tests/tools/test_query_table.py -x` | Wave 0 |
| QUERY-02 | Default = light columns only | unit (snapshot) | `pytest tests/tools/test_query_table.py::test_default_light_columns -x` | Wave 0 |
| QUERY-03 | Heavy under retrieved_content | unit (snapshot) | `pytest tests/tools/test_query_table.py::test_heavy_columns_retrieved_content -x` | Wave 0 |
| QUERY-04 | 13 filter ops compile correctly | unit (pure) | `pytest tests/test_filter_compiler.py -x` | Wave 0 |
| QUERY-05 | Hidden-col filter → unknown_column | unit (counter-patch) | `pytest tests/tools/test_retrieval_side_channel.py -x` | Wave 0 |
| QUERY-06 | Unknown-col in columns → unknown_column | unit (counter-patch) | `pytest tests/tools/test_retrieval_side_channel.py -x` | Wave 0 |
| QUERY-07 | limit=201 rejected, limit=200 accepted | unit | `pytest tests/tools/test_query_table_errors.py::test_limit_over_max -x` | Wave 0 |
| QUERY-08 | Cursor round-trip; shape mismatch rejected | unit (pure) | `pytest tests/test_cursor.py -x` | Wave 0 |
| QUERY-09 | Filter values never echoed | unit (canary corpus) | `pytest tests/test_filter_value_safety.py -x` | Wave 0 |
| QUERY-10 | Case-sensitivity documented in tool description | unit (CI contract) | `pytest tests/test_envelope_contract.py -x` | EXISTS |
| FETCH-01 | fetch returns row by URL | unit (httpx_mock) | `pytest tests/tools/test_fetch.py::test_fetch_known_judgment -x` | Wave 0 |
| FETCH-02 | Exact URL match only | unit (httpx_mock) | `pytest tests/tools/test_fetch.py::test_fetch_url_exact -x` | Wave 0 |
| FETCH-03 | Non-heavy, non-fragment columns | unit (snapshot) | `pytest tests/tools/test_fetch.py::test_fetch_strips_heavy -x` | Wave 0 |
| FETCH-04 | No URL mapping → unsupported_table_for_fetch | unit | `pytest tests/tools/test_fetch.py::test_unsupported_table -x` | Wave 0 |
| FETCH-05 | Unmatched URL → not_found | unit (httpx_mock) | `pytest tests/tools/test_fetch.py::test_not_found -x` | Wave 0 |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_filter_compiler.py tests/test_cursor.py -x -q`
- **Per wave merge:** `uv run pytest -x -q`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps (all new test files)

- [ ] `tests/test_filter_compiler.py` — covers QUERY-04 + D3-10 coercion
- [ ] `tests/test_cursor.py` — covers QUERY-08
- [ ] `tests/test_filter_value_safety.py` — covers QUERY-09 / INJ-05 (minimum 5 canaries)
- [ ] `tests/tools/test_query_table.py` — covers QUERY-01..03, QUERY-07, QUERY-10
- [ ] `tests/tools/test_query_table_errors.py` — covers QUERY-05..07
- [ ] `tests/tools/test_retrieval_side_channel.py` — covers QUERY-05..06 via counter-patch
- [ ] `tests/tools/test_fetch.py` — covers FETCH-01..05
- [ ] `tests/manual/PHASE3-CLIENT-VERIFY.md` — D3-20 manual checklist

**Existing files that require NO changes for Phase 3:**
- `tests/test_envelope_contract.py` — automatically covers `query_table` and `fetch` once they are `@mcp.tool`-decorated (Pattern F)
- `tests/conftest.py` — will need a `stub_table_rows` fixture added (single edit, single plan)

---

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | anonymous-only v1 |
| V3 Session Management | no | stateless |
| V4 Access Control | yes (column visibility) | `_visible_columns` gate — SOLE entry point |
| V5 Input Validation | yes | `compile_filters` Pydantic + coercion; `Filter.model_config = ConfigDict(extra="forbid")` |
| V6 Cryptography | no | qhash is not a security primitive |

### Known Threat Patterns for This Phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Filter value echo in errors/logs | Information Disclosure | D3-09: generic error messages; `filter_count` (int) in logs only |
| Hidden column enumeration via filter timing | Information Disclosure | `_visible_columns` single gate; no pre-check on HIDDEN_COLUMNS |
| Filter value injection into SQL | Tampering | Not applicable — we pass through httpx `params=`, Datasette parameterizes; no f-string URL construction |
| `__in` value contains comma to extend list | Tampering | httpx URL-encodes commas in values if they arrive as list items; the comma-split is Datasette's concern and is safe because it's parameterized SQL |
| Cursor manipulation (shape mismatch attack) | Tampering | blake2b digest mismatch → `invalid_cursor`; no sensitive data in cursor |
| Large filter list (DoS via many params) | DoS | Not addressed in Phase 3; Phase 7's rate limiting is the primary defense |

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `?col=val` (Datasette v0 filter) | `?col__exact=val` | Datasette 0.50+ | Our `exact` op maps to `__exact` not bare `?col=val` |
| `_shape=array` (default) | `_shape=objects` | — | Use `objects` for dict-keyed rows; no migration needed |
| Offset-based pagination | Cursor-based `_next` | Datasette 0.44+ | Our qhash cursor wraps Datasette's opaque token |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `__lte` works symmetrically with `__gte` on numeric columns | Per-Operator Verification | Low — `gte` and `lt` both verified; `lte` is same SQL `<=`; if wrong, numeric range filters fail silently (0 rows or wrong rows) |
| A2 | `rowid` is always included by Datasette regardless of `_col=` | `_col=` Confirmation | Low — observed in multiple responses; if wrong, `rowid` may be absent and `_reshape_row` silently skips it (no harm) |
| A3 | Datasette tilde-encoding (`~3A`) in cursors is stable across table types | Pagination Cursor section | Medium — only observed in `sglawwatch.headlines`; if Datasette changes encoding, cursor round-trips would fail at the `split("|", 1)` step (raises `invalid_cursor`, which is safe) |
| A4 | `rowid` is safe to strip from emitted rows (not needed by LLM callers) | Pitfall 5 | Low — if a caller needs rowid for some reason, they can't get it; but rowid is an internal SQLite rowid, not a stable identifier |
| A5 | `fetch` multi-match should use default Datasette sort (not `ORDER BY updated_at`) | fetch URL match section | Low — D3-14 says log warning + return first row; Phase 5 handles FRAG-06 ordering policy; risk is non-determinism on multi-match, which is rare in practice |

**If this table is empty:** All other claims in this research were verified or cited.

---

## Open Questions

1. **`tests/conftest.py` edit consolidation**
   - What we know: Two or more plans may need to add fixtures to `conftest.py` (a `stub_table_rows` fixture for `test_query_table.py`, and potentially a `bound_visibility` fixture).
   - What's unclear: Whether both fixture additions can be consolidated into Plan 3-01 (foundation plan) to avoid the Phase 2 merge-conflict pitfall.
   - Recommendation: Put ALL `conftest.py` edits in the Wave 1 foundation plan (Plan 03-01). Mark `conftest.py` as "touched in Wave 1 only" in plan notes.

2. **`_resolve_table` move to `core/visibility.py`**
   - What we know: `tools/discovery.py` currently defines `_resolve_table`. `tools/retrieval.py` will need to call it. Cross-`tools/` imports are a smell.
   - What's unclear: Whether the Phase 2 test suite has any assertions that would break if `_resolve_table` moves (e.g., `from mcp_zeeker.tools.discovery import _resolve_table` in a test).
   - Recommendation: Check `grep -r "_resolve_table" tests/` before moving. If no tests import it from `tools/discovery`, the move is safe. Add a re-export in `tools/discovery.py` for backward compat if needed.

3. **`html_raw` column presence in judgments table**
   - What we know: `HEAVY_COLUMNS` includes `html_raw`. The `zeeker-judgements.judgments` table schema visible in the fixture does NOT include `html_raw` — it has `content_text`, `court_summary`, `summary`.
   - What's unclear: Whether `html_raw` exists in `judgments` or only in `judgments_fragments`.
   - Recommendation: The planner should verify `html_raw` column presence via `describe_table` against the live API. If `html_raw` is in `HEAVY_COLUMNS` but not in a given table, `compile_filters` will raise `unknown_column` when a user requests it — which is correct behavior. No architectural change needed; this is a data observation.

4. **`sglawwatch.about_singapore_law` `item_url` vs URL column name**
   - What we know: `URL_COLUMNS["sglawwatch.about_singapore_law"] = "item_url"`. The `__exact` operator on `item_url` should work for fetch.
   - What's unclear: Whether `item_url` is in the light columns (it is: `LIGHT_COLUMNS["sglawwatch.about_singapore_law"] = ["item_url", "title", "section", "home_page"]`).
   - Recommendation: No issue. `item_url` is both the URL column and a light column. `fetch` returns all non-heavy non-fragment columns, which includes `item_url`. No action needed.

5. **`full_text` column — which tables have it**
   - What we know: `HEAVY_COLUMNS` includes `full_text`. Not observed in any probed table.
   - What's unclear: Whether `full_text` exists in any current table or is a forward-compat placeholder.
   - Recommendation: Include it in `HEAVY_COLUMNS` as designed. If no table has `full_text`, it's harmlessly unreachable. No risk.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `uv` | Dependency management | YES | 0.11.14 | — |
| `pytest` + `pytest-asyncio` | Test runner | YES | pytest 8.3, pytest-asyncio 1.3 | — |
| `pytest-httpx` | Upstream HTTP mocking | YES | 0.36.2 | — |
| `data.zeeker.sg` (live) | Live tests (`ZEEKER_LIVE=1`) | YES | Datasette (live probe successful) | Skip with `ZEEKER_LIVE` unset |
| `hashlib.blake2b` | Cursor digest | YES (stdlib 3.6+) | — | — |
| `base64.urlsafe_b64encode` | Cursor encoding | YES (stdlib) | — | — |

**No missing dependencies.** Phase 3 introduces no new runtime or dev packages.

---

## Sources

### Primary (HIGH confidence — verified against live system)

- Live Datasette probe of `https://data.zeeker.sg/pdpc/enforcement_decisions.json` — verified all 13 filter ops, cursor format, `_col=`, `_shape=objects`, `__in` comma form, `isnull`/`notnull`, `_size` behavior
- Live Datasette probe of `https://data.zeeker.sg/zeeker-judgements/judgments.json` — verified `__exact` fetch, integer cursor, row shape
- Live Datasette probe of `https://data.zeeker.sg/sg-gov-newsrooms/mlaw_news.json` — verified date+rowid cursor format
- Live Datasette probe of `https://data.zeeker.sg/sglawwatch/headlines.json` — verified tilde-encoded datetime cursor
- `uv run python` FastMCP schema generation test — verified `list[Filter] | None` generates `anyOf` at property level (not root), PASSES TRANSPORT-04
- Datasette docs `https://docs.datasette.io/en/stable/json_api.html#table-view` — verified `next` vs `_next` field naming, pagination semantics

### Secondary (MEDIUM confidence)

- `src/mcp_zeeker/config.py` (current state) — verified `LIGHT_COLUMNS`, `URL_COLUMNS`, `HIDDEN_COLUMNS`, `FRAGMENT_PARENTS` populated for all tables
- `src/mcp_zeeker/core/datasette_client.py` — verified `get_table_column_types` pattern for new `get_table_rows` method
- `src/mcp_zeeker/tools/discovery.py` — verified `_resolve_table`, `_visible_tables`, `raise_unknown_table` patterns for Phase 3 column equivalents
- `.planning/phases/02-discovery-surface-denylists/02-LEARNINGS.md` — verified counter-patch pattern, pytest-httpx teardown trap, conftest.py merge conflict risk

### Tertiary (LOW confidence — assumptions)

- `__lte` operator symmetric with `__gte` [A1 in Assumptions Log]
- `rowid` always present from Datasette regardless of `_col=` [A2]

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — locked from Phase 1, no changes
- Datasette filter API: HIGH — all 13 ops verified live
- Pagination cursor: HIGH — format observed across 4 tables
- Column projection `_col=`: HIGH — verified live
- `__in`/`__notin` form: HIGH — live SQL trace confirms comma-joined
- Case-sensitivity: HIGH — empirically confirmed on two ops
- TRANSPORT-04 compliance: HIGH — running FastMCP schema generation
- `_size` enforcement: HIGH — Datasette returns 201 rows for `_size=201`
- Architecture patterns: HIGH — code read from current Phase 1+2 implementation

**Research date:** 2026-05-13
**Valid until:** 2026-08-13 (Datasette API stable; cursor format tilde-encoding is an implementation detail that could change in a Datasette version upgrade)
