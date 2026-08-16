# Plan: Add `sg-law-cookies` as a first-class database

## Context

The MCP connector currently serves 4 databases: `zeeker-judgements`, `pdpc`,
`sg-gov-newsrooms`, `sglawwatch`. The `sg-law-cookies` database is already live
on `data.zeeker.sg` (45 cookies, 56 judgment_issues, 15 judgments, 232
unresolved_terms) and is already used by the eval harness
(`evals/build_known_item_set.py`), but it is **not registered** in the
connector's config. This plan makes it a first-class database — searchable,
discoverable, and retrieval-ready — with zero new code paths.

The codebase was explicitly designed for this: `searchable_tables_for`
auto-discovers FTS-indexed tables, `resolve_preview_columns` resolves preview
fields from `SEARCH_PREVIEW_DEFAULTS`, and the config module is the single
source of truth for all database registration (D4-22 / CFG-01/02). Adding a
fifth database that follows naming conventions requires **config edits only** —
no handler changes.

## Upstream schema (verified live 2026-08-16)

### `sg-law-cookies` — 4 visible content tables + 2 platform tables

| Table | Rows | FTS table | FTS indexed columns | PK |
|-------|------|-----------|---------------------|----|
| `cookies` | 45 | `cookies_fts` | `headline`, `summary`, `why_it_matters` | (none — `id` is a regular column) |
| `judgment_issues` | 56 | `judgment_issues_fts` | `question`, `holding`, `reasoning` | (none) |
| `judgments` | 15 | `judgments_fts` | `case_name`, `orders` | (none) |
| `unresolved_terms` | 232 | `unresolved_terms_fts` | `term` | (none) |
| `_zeeker_schemas` | — | (none) | — | `resource_name` |
| `_zeeker_updates` | — | (none) | — | `resource_name` |

### Column details (from live `/sg-law-cookies.json`)

**`cookies`** (14 cols): `id`, `headline`, `summary`, `why_it_matters`,
`significance`, `date`, `item_type`, `primary_area`, `folio_areas`,
`folio_entities`, `folio_concepts`, `unresolved`, `is_duplicate`,
`source_url`, `source_title`

**`judgment_issues`** (10 cols): `id`, `judgment_id`, `citation`, `court`,
`issue_index`, `question`, `holding`, `reasoning`, `concepts`, `date`,
`source_url`

**`judgments`** (11 cols): `id`, `citation`, `case_name`, `court`, `judges`,
`parties`, `legislation`, `cases_cited`, `orders`, `issue_count`, `date`,
`source_url`

**`unresolved_terms`** (4 cols): `id`, `term`, `first_seen_date`, `count`

### Key observations

1. **No fragment tables** — `sg-law-cookies` has no `*_fragments` tables. No
   `FRAGMENT_PARENTS` or `SEARCH_FRAGMENT_SOURCES` entries needed.
2. **All 4 content tables have FTS** — all will be auto-discovered by
   `searchable_tables_for` once the DB is in `ALLOWED_DATABASES`.
3. **`source_url` present** on `cookies`, `judgment_issues`, and `judgments` —
   these 3 tables are URL-keyed and can use `fetch`. `unresolved_terms` has no
   URL column.
4. **No hidden columns beyond global `id`** — `judgment_issues` has
   `judgment_id` linking to `judgments.id` (a logical FK, not a fragment
   relationship). We should hide `judgment_id` to avoid leaking internal join
   keys, matching the pattern used for `judgments_fragments.judgment_id`.
5. **No primary keys** — all 4 content tables have empty `primary_keys`. The
   sort fallback logic in `query_table` (PR #15) will fall through to `rowid`
   for these tables.
6. **No upstream metadata** — `/-/metadata.json` returns empty for
   `sg-law-cookies`. `TABLE_DESCRIPTIONS` fallback is the sole description
   source.

---

## Steps

All edits are in a single file: `src/mcp_zeeker/config.py`. No handler,
orchestrator, or visibility code changes are needed.

### Step 1 — `ALLOWED_DATABASES` (line 16)

Add `"sg-law-cookies"` to the tuple.

```python
ALLOWED_DATABASES: tuple[str, ...] = (
    "zeeker-judgements",
    "pdpc",
    "sg-gov-newsrooms",
    "sglawwatch",
    "sg-law-cookies",
)
```

### Step 2 — `DATABASE_DESCRIPTIONS` (line 23)

Add a one-line description.

```python
"sg-law-cookies": (
    "Singapore law cookies — curated case summaries, judgment issues, "
    "and metadata extracted from Singapore court judgments."
),
```

### Step 3 — `HIDDEN_TABLES` (line 41)

Add the platform-internal tables (same denylist as all other DBs).

```python
"sg-law-cookies": {"_zeeker_schemas", "_zeeker_updates"},
```

### Step 4 — `HIDDEN_COLUMNS` (line 50)

Hide `judgment_id` on `judgment_issues` (internal FK to `judgments.id`,
analogous to `judgments_fragments.judgment_id`).

```python
"sg-law-cookies.judgment_issues": {"id", "judgment_id"},
```

### Step 5 — `URL_COLUMNS` (line 58)

Register the 3 URL-keyed tables.

```python
"sg-law-cookies.cookies": "source_url",
"sg-law-cookies.judgment_issues": "source_url",
"sg-law-cookies.judgments": "source_url",
```

### Step 6 — `FRAGMENT_PARENTS` (line 80)

**No entries.** `sg-law-cookies` has no fragment tables. Skip this section.

### Step 7 — `LIGHT_COLUMNS` (line 105)

Add light column sets for all 4 content tables. These are the columns returned
by default in `query_table` (heavy text excluded).

```python
"sg-law-cookies.cookies": [
    "headline",
    "summary",
    "significance",
    "date",
    "item_type",
    "primary_area",
    "source_url",
    "source_title",
],
"sg-law-cookies.judgment_issues": [
    "citation",
    "court",
    "issue_index",
    "question",
    "date",
    "source_url",
],
"sg-law-cookies.judgments": [
    "citation",
    "case_name",
    "court",
    "date",
    "source_url",
],
"sg-law-cookies.unresolved_terms": [
    "term",
    "first_seen_date",
    "count",
],
```

**Rationale:** `why_it_matters` (cookies) and `holding`/`reasoning`
(judgment_issues) are longer text fields — exclude from light set to keep
previews compact. `folio_areas`, `folio_entities`, `folio_concepts`,
`concepts`, `legislation`, `cases_cited`, `judges`, `parties`, `orders`,
`unresolved` are all JSON arrays/objects — exclude from light set (they're not
in `HEAVY_COLUMNS` so callers can still request them explicitly, but they're
not useful as default preview fields).

### Step 8 — `TABLE_DESCRIPTIONS` (line 172)

Add fallback descriptions (upstream metadata is empty for this DB).

```python
"sg-law-cookies": {
    "cookies": (
        "Curated Singapore law cookies — concise summaries of legal "
        "developments with significance ratings and Folio taxonomy tags."
    ),
    "judgment_issues": (
        "Issues extracted from Singapore court judgments — each issue "
        "has a question, holding, and reasoning."
    ),
    "judgments": (
        "Singapore court judgment metadata — citation, case name, court, "
        "judges, parties, legislation, and cases cited."
    ),
    "unresolved_terms": (
        "Terms not yet resolved in the Folio legal taxonomy, with first-seen "
        "date and occurrence count."
    ),
},
```

### Step 9 — `COLUMN_DESCRIPTIONS` (line 193)

Add column descriptions for the most useful columns (the eval builder already
references these). This is optional but improves `describe_table` output.

```python
"sg-law-cookies": {
    "cookies": {
        "headline": "Short headline summarizing the legal development",
        "summary": "Concise summary of the case or legal point",
        "why_it_matters": "Explanation of why this development is significant",
        "significance": "Significance rating (e.g. high, medium, low)",
        "date": "Date of the judgment or development (YYYY-MM-DD)",
        "item_type": "Type of item (e.g. judgment)",
        "primary_area": "Primary area of law (e.g. Criminal Law)",
        "source_url": "URL to the full source document",
        "source_title": "Title of the source document",
    },
    "judgment_issues": {
        "citation": "Neutral citation of the judgment (e.g. [2026] SGHC 119)",
        "court": "Court that issued the judgment",
        "issue_index": "Sequential index of the issue within the judgment",
        "question": "The legal question posed by this issue",
        "holding": "The court's holding on this issue",
        "reasoning": "The court's reasoning for the holding",
        "date": "Date of the judgment (YYYY-MM-DD)",
        "source_url": "URL to the full judgment",
    },
    "judgments": {
        "citation": "Neutral citation (e.g. [2026] SGHC 119)",
        "case_name": "Case name",
        "court": "Court that issued the judgment",
        "judges": "JSON array of judge names",
        "parties": "JSON array of party descriptions",
        "legislation": "JSON array of legislation references",
        "cases_cited": "JSON array of cited cases with citations",
        "orders": "Court orders text",
        "issue_count": "Number of issues extracted from this judgment",
        "date": "Date of the judgment (YYYY-MM-DD)",
        "source_url": "URL to the full judgment on eLitigation",
    },
},
```

### Step 10 — `LICENSES` (line 260)

Add the license entry. Cookies are derived from Singapore court judgments, so
the license posture matches `zeeker-judgements` (Crown Copyright / eLitigation).

```python
"sg-law-cookies": ("CC-BY-4.0", LICENSE_DEFAULT_URL),
```

### Step 11 — `CONTENT_POLICIES` (line 301)

Add per-table content policies for the 3 tables that carry source text.
`cookies` contains curated summaries (Zeeker's own derived content).
`judgment_issues` and `judgments` contain extracted metadata from court
judgments.

```python
("sg-law-cookies", "cookies"): {
    "source": "Zeeker — curated Singapore legal cookies",
    "license": "CC-BY-4.0",
    "license_url": LICENSE_DEFAULT_URL,
    "redistribution": "allowed",
},
("sg-law-cookies", "judgment_issues"): {
    "source": "Singapore courts — extracted judgment issues via Zeeker",
    "license": "Crown Copyright Singapore",
    "license_url": _ELIT_URL,
    "redistribution": "process-only",
},
("sg-law-cookies", "judgments"): {
    "source": "Singapore courts — judgment metadata via Zeeker",
    "license": "Crown Copyright Singapore",
    "license_url": _ELIT_URL,
    "redistribution": "process-only",
},
```

**Rationale:** `cookies` are Zeeker's own curated summaries (CC-BY, allowed for
redistribution). `judgment_issues` and `judgments` are derived from court
judgments (Crown Copyright, process-only — same posture as
`zeeker-judgements.judgments`).

### Step 12 — `CITATION_TEMPLATES` (line 408)

Add citation templates for URL-keyed tables.

```python
("sg-law-cookies", "cookies"): "{headline} ({date}) — {source_url}",
("sg-law-cookies", "judgment_issues"): "{citation} — {question} ({date}) — {source_url}",
("sg-law-cookies", "judgments"): "{case_name} {citation} ({court}, {date}) — {source_url}",
```

### Step 13 — `SEARCH_BM25_WEIGHTS` (line 651)

Add BM25 weights for the 4 FTS-indexed tables (for ranked search modes).

```python
# fts columns: [headline, summary, why_it_matters]
"sg-law-cookies.cookies": {
    "headline": 10.0,
    "summary": 5.0,
    "why_it_matters": 3.0,
},
# fts columns: [question, holding, reasoning]
"sg-law-cookies.judgment_issues": {
    "question": 10.0,
    "holding": 5.0,
    "reasoning": 3.0,
},
# fts columns: [case_name, orders]
"sg-law-cookies.judgments": {
    "case_name": 10.0,
    "orders": 2.0,
},
# fts columns: [term]
"sg-law-cookies.unresolved_terms": {
    "term": 10.0,
},
```

### Step 14 — `SEARCH_FRAGMENT_SOURCES` (line 717)

**No entries.** No fragment tables in this DB. Skip.

### Step 15 — `SEARCH_PREVIEW_OVERRIDES` (line 617)

**No entries needed.** Auto-discovery via `SEARCH_PREVIEW_DEFAULTS` resolves
all 4 tables:

| Table | title | date | summary | url |
|------|-------|------|---------|-----|
| `cookies` | `headline` ✓ | `date` ✓ | `summary` ✓ | `source_url` ✓ |
| `judgment_issues` | `question` ✗ | `date` ✓ | `holding` ✗ | `source_url` ✓ |
| `judgments` | `case_name` ✓ | `date` ✓ | (none matches) → `None` | `source_url` ✓ |
| `unresolved_terms` | (none matches) → `None` | `first_seen_date` ✗ | (none) → `None` | (none) → `None` |

**Problem:** `judgment_issues` and `unresolved_terms` will fail Gate 4 of
`searchable_tables_for` — `resolve_preview_columns` returns `None` when title
or url can't be resolved, dropping the table from search with a warning.

**Fix:** Add overrides for these tables.

```python
SEARCH_PREVIEW_OVERRIDES: dict[str, dict[str, str | None]] = {
    # judgment_issues: no "title" candidate in SEARCH_PREVIEW_DEFAULTS —
    # use "question" as title (it's the most descriptive field).
    "sg-law-cookies.judgment_issues": {
        "title": "question",
        "summary": "holding",
    },
    # unresolved_terms: no title/date/summary/url candidates at all.
    # It's a reference table of unresolved taxonomy terms — not useful as
    # a search preview, so suppress it from search discovery (title=None
    # drops it at Gate 4). query_table still works on it directly.
    "sg-law-cookies.unresolved_terms": {
        "title": None,
    },
    # judgments: no "summary" candidate in defaults — use "citation" as
    # a fallback summary-like field (the neutral citation is informative).
    "sg-law-cookies.judgments": {
        "summary": "citation",
    },
}
```

**Wait** — `resolve_preview_columns` returns `None` (drop signal) when title OR
url is `None`. For `unresolved_terms`, setting `title: None` drops it from
search (desired — it's a taxonomy reference table). But `url` would also be
`None` (no URL column), which also drops it. So `unresolved_terms` is dropped
regardless — no override needed for it.

For `judgment_issues`, `title` resolves to `None` from defaults (no
`question` in the title candidates tuple). We need the override to map
`title → question`. And `url` resolves to `source_url` (in the defaults), so
the table stays if we fix title.

For `judgments`, `title` resolves to `case_name` ✓, `url` resolves to
`source_url` ✓, but `summary` resolves to `None` (no match in defaults). This
is OK — `None` summary means the field is null in the preview, not a drop
signal. Only title/url being None causes a drop. So `judgments` needs no
override to stay in search; but adding `summary: "citation"` improves the
preview.

**Revised `SEARCH_PREVIEW_OVERRIDES`:**

```python
SEARCH_PREVIEW_OVERRIDES: dict[str, dict[str, str | None]] = {
    "sg-law-cookies.judgment_issues": {
        "title": "question",
        "summary": "holding",
    },
    "sg-law-cookies.judgments": {
        "summary": "citation",
    },
}
```

`unresolved_terms` is silently dropped from search discovery (no title/url
resolvable) — this is correct behavior. `query_table` and `describe_table`
still work on it directly.

### Step 16 — Prompts (`src/mcp_zeeker/prompts.py`)

Consider adding a `search_cookies` prompt to help the LLM route cookie
searches. This is optional and can be deferred.

```python
@mcp.prompt(
    name="search_cookies",
    description="Route Singapore law cookie searches to the sg-law-cookies database",
)
def search_cookies(query: str) -> list[dict]:
    return [
        {
            "role": "user",
            "content": (
                f"Find Singapore legal cookies matching: {query}\n\n"
                "Use the `sg-law-cookies` database. Cookies are concise summaries "
                "of legal developments in the `cookies` table. Judgment issues "
                "(legal questions and holdings) are in `judgment_issues`. "
                "Judgment metadata is in `judgments`.\n\n"
                "Strategy:\n"
                "1. Call `search()` with `databases=['sg-law-cookies']` for "
                "topical queries.\n"
                "2. Call `query_table()` on `sg-law-cookies.cookies` for "
                "structured filtering by date, significance, or primary_area.\n"
                "3. Call `query_table()` on `sg-law-cookies.judgment_issues` "
                "to drill into specific legal questions from a judgment.\n"
            ),
        }
    ]
```

---

## What needs NO changes

- **`tools/search.py`** — auto-discovery via `searchable_tables_for` +
  `resolve_preview_columns` handles the new DB with no code changes (D4-22).
- **`tools/retrieval.py`** — `query_table` and `fetch` are generic; they work
  with any DB in `ALLOWED_DATABASES`.
- **`tools/discovery.py`** — `list_databases`, `list_tables`, `describe_table`
  all iterate over `ALLOWED_DATABASES` / use `config` lookups.
- **`core/search.py`** — fan-out, BM25 SQL builder, RRF merge are all generic.
- **`core/visibility.py`** — visibility gates read from `config`.
- **`core/config_lookup.py`** — `hidden_columns_for` / `url_column_for` read
  from config dicts.
- **`server.py`** — no changes.
- **`app.py`** — no changes.
- **Middleware** — no changes.

---

## Testing

### Existing tests to verify

Run the full suite after the config edits:

```sh
cd ~/zeeker-mcp && uv run pytest -x -q
```

Key tests that exercise config-driven behavior:
- `tests/test_config.py` — validates config constants
- `tests/tools/test_discovery.py` — `list_databases` returns all DBs
- `tests/tools/test_search.py` — cross-DB search includes new DB
- `tests/tools/test_search_auto_discovery.py` — auto-discovery finds new tables
- `tests/test_config_lookup_single_source.py` — AST single-source-of-truth

### Tests that may need fixture updates

Several tests hardcode the list of 4 databases or assert exact counts. Search
for `ALLOWED_DATABASES` usage in tests:

- `tests/tools/test_discovery.py` — likely asserts `len(rows) == 4`; update to 5
- `tests/test_config.py` — may assert `len(ALLOWED_DATABASES) == 4`; update to 5
- `tests/tools/test_search.py` — may mock all 4 DBs; add `sg-law-cookies` mock
- `tests/tools/test_search_auto_discovery.py` — may assert table counts
- `conftest.py` — may have fixture DB summaries; add `sg-law-cookies`

### New tests to add

- **`tests/tools/test_cookies_search.py`** — verify `sg-law-cookies` tables
  are discovered and search returns results (mock upstream).
- **`tests/tools/test_cookies_retrieval.py`** — verify `query_table` and
  `fetch` work on `sg-law-cookies.cookies` / `judgment_issues` / `judgments`.

### Live verification

After deploying:
1. `curl https://mcp.zeeker.sg/healthz` — server up
2. Call `list_databases` — should show 5 DBs including `sg-law-cookies`
3. Call `search(query="contempt of court", databases=["sg-law-cookies"])` —
   should return cookies + judgment_issues results
4. Call `query_table(database="sg-law-cookies", table="cookies", limit=5)` —
   should return 5 rows with light columns
5. Call `fetch(database="sg-law-cookies", table="cookies", url="<source_url>")`
   — should return the full row

---

## Deployment

Per the user's workflow: feature branch → merge → rebuild Docker → verify.

1. Create feature branch: `git checkout -b feat/add-sg-law-cookies`
2. Apply config edits
3. Run tests: `uv run pytest -x -q`
4. Commit + push
5. PR → merge to main
6. Rebuild container: `docker compose up -d --build zeeker-mcp`
7. Verify health: `curl https://mcp.zeeker.sg/healthz`
8. Live end-to-end test (steps above)

---

## Summary of config edits

| Config key | Action | Entries |
|-----------|--------|---------|
| `ALLOWED_DATABASES` | Add | `"sg-law-cookies"` |
| `DATABASE_DESCRIPTIONS` | Add | 1 entry |
| `HIDDEN_TABLES` | Add | `{"_zeeker_schemas", "_zeeker_updates"}` |
| `HIDDEN_COLUMNS` | Add | `"sg-law-cookies.judgment_issues": {"id", "judgment_id"}` |
| `URL_COLUMNS` | Add | 3 entries (cookies, judgment_issues, judgments) |
| `FRAGMENT_PARENTS` | Skip | No fragment tables |
| `LIGHT_COLUMNS` | Add | 4 entries (all content tables) |
| `TABLE_DESCRIPTIONS` | Add | 4 table descriptions |
| `COLUMN_DESCRIPTIONS` | Add | 3 tables with column descriptions |
| `COLUMN_TYPES` | Skip | No missing schema (upstream has columns) |
| `LICENSES` | Add | 1 entry |
| `CONTENT_POLICIES` | Add | 3 entries (cookies, judgment_issues, judgments) |
| `CITATION_TEMPLATES` | Add | 3 entries |
| `SEARCH_BM25_WEIGHTS` | Add | 4 entries |
| `SEARCH_FRAGMENT_SOURCES` | Skip | No fragment tables |
| `SEARCH_PREVIEW_OVERRIDES` | Add | 2 entries (judgment_issues, judgments) |
| `prompts.py` | Optional | 1 new prompt |

**Total: ~30 config entries across 12 config dicts. Zero handler code changes.**