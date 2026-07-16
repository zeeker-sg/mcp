# evals/ — Tier-1 known-item retrieval harness (issue #12)

Dev tooling for measuring search relevance across the `SEARCH_RANKING`
variants (`legacy` | `bm25` | `bm25_rrf`). **Not shipped server code** — no
runtime dependencies beyond stdlib + `httpx` (already a project dep). Nothing
here is imported by `src/`.

## Layout

```
evals/
  build_known_item_set.py   # builds queries/known_item.jsonl from live data.zeeker.sg
  run_eval.py               # runs the search tool handler in-process, per variant
  queries/known_item.jsonl  # cached query set (rebuild on demand, not per run)
  results/<timestamp>/      # results.jsonl + report.md per run
```

## 1. Build the query set (occasional, anonymous)

```sh
uv run python evals/build_known_item_set.py
```

Builds `evals/queries/known_item.jsonl` from live **anonymous**
`data.zeeker.sg` (override with `EVAL_DATA_URL` or `--data-url`). Three
classes:

- **doctrinal** — `sg-law-cookies/judgment_issues` questions, lightly
  keyword-ified (deterministic stopword strip, ≤8 tokens). Target =
  `source_url`; the judgment citation is kept in `notes`.
- **citation-lookup** — every unique citation string inside
  `sg-law-cookies/judgments.cases_cited` (pinpoint `" at [..]"` stripped),
  verified to exist in `zeeker-judgements.judgments` via
  `?citation__exact=`; unverifiable pairs are dropped.
- **case-name** — `case_name` lowercased + one deterministic topic word from
  `orders`. Target = `source_url`.

Row shape: `{"qid", "class", "query", "target_urls": [...], "notes"}`.

The builder is sequential with a ~1.2 s inter-request sleep to respect the
anonymous rate budget (~60 req/min). Rebuild only when the sg-law-cookies
source data changes; eval runs read the cached file.

`--classes doctrinal` builds a single class (useful for a quick refresh).

## 2. Run the eval

### Locally (anonymous → legacy baseline only)

```sh
uv run python evals/run_eval.py --sample 10
```

Anonymous `?sql=` is 403 upstream, so the SQL-backed variants (`bm25`,
`bm25_rrf`) are skipped with a message and only `legacy` runs. The default
`--sleep 15` keeps each search call's ~12 fan-out requests under the 60
req/min anonymous budget — a full 100+ query run anonymous takes a while;
use `--sample N` for smoke runs.

### On the prod host (token → all variants)

```sh
UPSTREAM_URL=http://datasette:8001 \
ZEEKER_FULL_ACCESS_TOKEN=... \
uv run python evals/run_eval.py --sleep 1
```

With the owner token the runner enables `bm25` and `bm25_rrf` (provided
`config.SEARCH_RANKING` exists — if the implementation hasn't landed, the
runner says so and falls back to legacy only). On the internal docker network
there is no anonymous rate limit, so `--sleep 1` is fine.

Flags: `--variants legacy,bm25` (subset), `--limit 10` (search limit; keep
≥10 for Success@10), `--queries`, `--out-dir`.

### How it works

- Imports the `search` tool handler and calls it **in-process**, using the
  same client bootstrap as `tests/test_live_golden_path.py` (bind
  `DatasetteClient` / `MetadataCache` / `DatabaseSummaryCache` /
  `ParentPKCache` / `tool_started_at`).
- Variants are **interleaved per query** (q1 × all variants, then q2 × …) so
  corpus drift during a run cannot bias one variant.
- A corpus snapshot (run timestamp + per-db/table row counts) is recorded in
  the report so runs are comparable.
- Deterministic: stratified sampling is round-robin over qid-sorted classes;
  no randomness anywhere in the logic.

## 3. Reading the report

`evals/results/<timestamp>/report.md` contains:

- **Overall** and **per-class** metric tables, one row per variant:
  - `S@1 / S@5 / S@10` — fraction of queries whose first target-URL hit is at
    rank ≤ k.
  - `MRR` — mean reciprocal rank of the first hit (0 when the target never
    appears).
  - `zero-res` — fraction of queries returning zero rows (the FTS5
    phrase-wrap makes long keyword queries all-or-nothing under `legacy`, so
    expect this to be high for the doctrinal class at baseline).
  - `err` — fraction of calls that raised (ToolError etc.).
  - `p50 / p95 ms` — per-query handler latency (nearest-rank percentiles).
- **Paired deltas vs legacy** — per-query rank change for each SQL variant
  (`improved / worsened / tied`, with `found (was miss)` / `lost (was hit)`
  called out). This is the primary evidence for issue #12: read the paired
  table, not just the aggregate, since aggregate MRR can hide per-class
  regressions.

`results.jsonl` has one line per (query, variant) with the raw rank,
latency, row count, and error — suitable for ad-hoc slicing.

### Known baseline caveats

- `legacy` orders by metadata date sort (not rank) and phrase-wraps the whole
  query, so multi-keyword doctrinal queries mostly return zero rows — a near-
  zero baseline is expected and is exactly what BM25+RRF is meant to fix.
- `judgments_fts` indexes `[case_name, summary, court_summary]` — the
  `citation` column is NOT indexed, so citation-lookup queries can only hit
  when the citation string appears in indexed text. Treat that class as a
  stretch goal for FTS-based variants.
