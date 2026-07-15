#!/usr/bin/env python3
"""
Tier-1 known-item eval runner for issue #12 (BM25 / RRF search relevance).

Dev tool, NOT shipped server code. Runs the `search` tool handler
IN-PROCESS (same bootstrap pattern as tests/test_live_golden_path.py:
bind DatasetteClient + MetadataCache + DatabaseSummaryCache + ParentPKCache
+ tool_started_at, then `await search(...)` directly) against a live
upstream, once per (query, variant) pair.

Variants are selected via the SEARCH_RANKING flag (config.py, landed by a
concurrent agent): legacy | bm25 | bm25_rrf. Selection is DEFENSIVE:
  - If config has no SEARCH_RANKING attribute yet, only `legacy` runs and
    the report says so.
  - The bm25 / bm25_rrf variants require the owner token
    (ZEEKER_FULL_ACCESS_TOKEN) because anonymous ?sql= is 403 upstream;
    without the token they are skipped with a clear message.

Variants are INTERLEAVED per query (q1: all variants, then q2: ...) so
corpus drift during a run cannot bias one side. The run records a corpus
snapshot marker (timestamp + per-db/table row counts) up front.

Metrics per variant × class (and overall): Success@1/@5/@10, MRR (rank of
first result row whose url is in target_urls), zero-result rate, latency
p50/p95 (nearest-rank percentile — deterministic).

Outputs under evals/results/<timestamp>/:
  results.jsonl — one line per (query, variant)
  report.md     — metrics tables per class + paired per-query deltas vs legacy

Usage (local, anonymous → legacy baseline only):
    uv run python evals/run_eval.py --sample 10

Usage (prod host with token → all variants):
    ZEEKER_FULL_ACCESS_TOKEN=... uv run python evals/run_eval.py --sleep 2
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# --- env must be set BEFORE importing mcp_zeeker.config (env read at import) ---
os.environ.setdefault("UPSTREAM_URL", "https://data.zeeker.sg")

import anyio  # noqa: E402
import structlog  # noqa: E402

# Quiet the server-side structlog chatter (search_timing etc.) during eval runs.
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from mcp_zeeker import config  # noqa: E402
from mcp_zeeker.core.database_summary_cache import DatabaseSummaryCache  # noqa: E402
from mcp_zeeker.core.datasette_client import DatasetteClient  # noqa: E402
from mcp_zeeker.core.fragment_join import ParentPKCache  # noqa: E402
from mcp_zeeker.core.http_client import build_http_client  # noqa: E402
from mcp_zeeker.core.metadata_cache import MetadataCache  # noqa: E402
from mcp_zeeker.core.middleware.retrieved_at import tool_started_at  # noqa: E402
from mcp_zeeker.tools.search import search  # noqa: E402

SQL_VARIANTS = ("bm25", "bm25_rrf")
ALL_VARIANTS = ("legacy",) + SQL_VARIANTS


# ---------------------------------------------------------------------------
# Variant selection / application
# ---------------------------------------------------------------------------


def select_variants(requested: list[str] | None) -> tuple[list[str], list[str]]:
    """Return (variants_to_run, notes). Defensive per the task spec."""
    notes: list[str] = []
    flag_supported = hasattr(config, "SEARCH_RANKING")
    have_token = bool(config.UPSTREAM_TOKEN)

    if not flag_supported:
        notes.append(
            "config.SEARCH_RANKING does not exist yet (implementation not landed) — "
            "running legacy only."
        )
        return ["legacy"], notes

    candidates = list(requested) if requested else list(ALL_VARIANTS)
    out: list[str] = []
    for v in candidates:
        if v not in ALL_VARIANTS:
            notes.append(f"unknown variant {v!r} skipped (known: {ALL_VARIANTS}).")
            continue
        if v in SQL_VARIANTS and not have_token:
            notes.append(
                f"variant {v!r} skipped: no ZEEKER_FULL_ACCESS_TOKEN set and anonymous "
                "?sql= is 403 upstream. Run on the prod host with the token to enable it."
            )
            continue
        out.append(v)
    if not out:
        notes.append("no requested variant runnable — falling back to legacy.")
        out = ["legacy"]
    return out, notes


def apply_variant(variant: str) -> None:
    """Point the search implementation at `variant` via env + config attr."""
    os.environ["SEARCH_RANKING"] = variant
    if hasattr(config, "SEARCH_RANKING"):
        config.SEARCH_RANKING = variant  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Query set loading / sampling
# ---------------------------------------------------------------------------


def load_queries(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def stratified_sample(queries: list[dict], n: int) -> list[dict]:
    """Deterministic round-robin sample across classes (sorted by qid)."""
    if n <= 0 or n >= len(queries):
        return queries
    by_class: dict[str, list[dict]] = {}
    for q in sorted(queries, key=lambda r: r["qid"]):
        by_class.setdefault(q["class"], []).append(q)
    classes = sorted(by_class)
    out: list[dict] = []
    i = 0
    while len(out) < n:
        progressed = False
        for c in classes:
            if i < len(by_class[c]):
                out.append(by_class[c][i])
                progressed = True
                if len(out) >= n:
                    break
        if not progressed:
            break
        i += 1
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _norm_url(u: str | None) -> str:
    return (u or "").strip().rstrip("/")


def rank_of_target(rows: list[dict], target_urls: list[str]) -> int | None:
    """1-based rank of the first row whose url is in target_urls, else None."""
    targets = {_norm_url(u) for u in target_urls}
    for i, row in enumerate(rows, 1):
        if _norm_url(row.get("url")) in targets:
            return i
    return None


def percentile(values: list[float], p: float) -> float | None:
    """Deterministic nearest-rank percentile (no interpolation).

    nearest-rank: ceil(p/100 * n), clamped to [1, n].
    """
    if not values:
        return None
    s = sorted(values)
    k = min(len(s), max(1, math.ceil(p / 100.0 * len(s))))
    return s[k - 1]


def summarize(results: list[dict]) -> dict:
    """Aggregate metrics for a homogeneous slice of result rows."""
    n = len(results)
    if n == 0:
        return {"n": 0}
    ranks = [r["rank"] for r in results]
    lat = [r["latency_ms"] for r in results if r["latency_ms"] is not None]
    return {
        "n": n,
        "success_at_1": sum(1 for r in ranks if r is not None and r <= 1) / n,
        "success_at_5": sum(1 for r in ranks if r is not None and r <= 5) / n,
        "success_at_10": sum(1 for r in ranks if r is not None and r <= 10) / n,
        "mrr": sum(1.0 / r for r in ranks if r is not None) / n,
        "zero_result_rate": sum(1 for r in results if r["n_rows"] == 0) / n,
        "error_rate": sum(1 for r in results if r["error"]) / n,
        "latency_p50_ms": percentile(lat, 50),
        "latency_p95_ms": percentile(lat, 95),
    }


# ---------------------------------------------------------------------------
# Corpus snapshot
# ---------------------------------------------------------------------------


async def corpus_snapshot() -> dict:
    """Per-db/table row counts as a drift marker (4 upstream requests)."""
    snap: dict[str, dict[str, int | None]] = {}
    dc = DatasetteClient.current()
    for db in config.ALLOWED_DATABASES:
        try:
            summary = await dc.get_database(db)
            snap[db] = {t.name: t.count for t in summary.tables if not t.hidden}
        except Exception as exc:  # noqa: BLE001 — snapshot is best-effort
            snap[db] = {"_error": type(exc).__name__}  # type: ignore[dict-item]
    return snap


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    queries = load_queries(Path(args.queries))
    if not queries:
        print(f"no queries in {args.queries} — run build_known_item_set.py first", file=sys.stderr)
        return 1
    queries = stratified_sample(queries, args.sample) if args.sample else queries

    variants, notes = select_variants(
        [v.strip() for v in args.variants.split(",")] if args.variants else None
    )
    for note in notes:
        print(f"[variants] {note}", file=sys.stderr)
    print(f"[variants] running: {variants}", file=sys.stderr)

    run_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir) / run_ts
    out_dir.mkdir(parents=True, exist_ok=True)

    http = build_http_client()
    results: list[dict] = []
    try:
        dc = DatasetteClient(http)
        DatasetteClient.bind(dc)
        MetadataCache.bind(
            MetadataCache(http, config.UPSTREAM_URL, ttl=config.METADATA_TTL_SECONDS)
        )
        # DatabaseSummaryCache keeps discovery to ~1 fetch/db per TTL instead of
        # 4 fetches per search call — matters for the anonymous 60/min budget.
        DatabaseSummaryCache.bind(DatabaseSummaryCache(dc, ttl=config.DATABASE_SUMMARY_TTL_SECONDS))
        ParentPKCache.bind(ParentPKCache())
        tool_started_at.set(datetime.now(UTC))

        snapshot = await corpus_snapshot()

        total = len(queries) * len(variants)
        done = 0
        for q in queries:
            for variant in variants:
                apply_variant(variant)
                t0 = time.perf_counter()
                error: str | None = None
                rows: list[dict] = []
                try:
                    envelope = await search(query=q["query"], limit=args.limit)
                    rows = envelope.data or []
                except Exception as exc:  # noqa: BLE001 — ToolError etc.
                    error = f"{type(exc).__name__}: {exc}"
                latency_ms = (time.perf_counter() - t0) * 1000.0
                rank = rank_of_target(rows, q["target_urls"]) if not error else None
                results.append(
                    {
                        "qid": q["qid"],
                        "class": q["class"],
                        "query": q["query"],
                        "variant": variant,
                        "rank": rank,
                        "success_at_1": bool(rank is not None and rank <= 1),
                        "success_at_5": bool(rank is not None and rank <= 5),
                        "success_at_10": bool(rank is not None and rank <= 10),
                        "reciprocal_rank": (1.0 / rank) if rank else 0.0,
                        "n_rows": len(rows),
                        "latency_ms": round(latency_ms, 1),
                        "error": error,
                        "target_urls": q["target_urls"],
                        "run_ts": run_ts,
                    }
                )
                done += 1
                print(
                    f"[{done}/{total}] {q['qid']} × {variant}: rank={rank} "
                    f"rows={len(rows)} {latency_ms:.0f}ms" + (f" ERROR={error}" if error else ""),
                    file=sys.stderr,
                )
                await anyio.sleep(args.sleep)
    finally:
        await http.aclose()

    # --- persist per-query results ---
    results_path = out_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # --- report.md ---
    report = build_report(results, variants, notes, snapshot, run_ts, args)
    report_path = out_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"\nwrote {results_path}\nwrote {report_path}", file=sys.stderr)
    # Terse stdout summary for scripted callers.
    for variant in variants:
        overall = summarize([r for r in results if r["variant"] == variant])
        print(json.dumps({"variant": variant, **overall}))
    return 0


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

_METRIC_COLS = (
    ("n", "n"),
    ("success_at_1", "S@1"),
    ("success_at_5", "S@5"),
    ("success_at_10", "S@10"),
    ("mrr", "MRR"),
    ("zero_result_rate", "zero-res"),
    ("error_rate", "err"),
    ("latency_p50_ms", "p50 ms"),
    ("latency_p95_ms", "p95 ms"),
)


def _fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}" if v < 10 else f"{v:.0f}"
    return str(v)


def _metrics_table(results: list[dict], variants: list[str]) -> str:
    lines = ["| variant | " + " | ".join(h for _, h in _METRIC_COLS) + " |"]
    lines.append("|" + "---|" * (len(_METRIC_COLS) + 1))
    for v in variants:
        m = summarize([r for r in results if r["variant"] == v])
        lines.append(f"| {v} | " + " | ".join(_fmt(m.get(k)) for k, _ in _METRIC_COLS) + " |")
    return "\n".join(lines)


def build_report(
    results: list[dict],
    variants: list[str],
    notes: list[str],
    snapshot: dict,
    run_ts: str,
    args: argparse.Namespace,
) -> str:
    classes = sorted({r["class"] for r in results})
    parts: list[str] = []
    parts.append(f"# Known-item eval report — {run_ts}\n")
    parts.append(f"- upstream: `{config.UPSTREAM_URL}`")
    parts.append(f"- token: {'set' if config.UPSTREAM_TOKEN else 'NOT set (anonymous)'}")
    parts.append(f"- queries file: `{args.queries}` (sample={args.sample or 'all'})")
    parts.append(f"- search limit: {args.limit}; inter-call sleep: {args.sleep}s")
    parts.append(f"- variants run: {', '.join(variants)}")
    if notes:
        parts.append("\n## Variant notes\n")
        parts.extend(f"- {n}" for n in notes)

    parts.append("\n## Corpus snapshot (per-db/table row counts)\n")
    parts.append("```json")
    parts.append(json.dumps(snapshot, indent=2, sort_keys=True))
    parts.append("```")

    parts.append("\n## Overall\n")
    parts.append(_metrics_table(results, variants))

    for c in classes:
        parts.append(f"\n## Class: {c}\n")
        parts.append(_metrics_table([r for r in results if r["class"] == c], variants))

    # Paired per-query deltas vs legacy.
    if "legacy" in variants and len(variants) > 1:
        legacy_by_qid = {r["qid"]: r for r in results if r["variant"] == "legacy"}
        for v in variants:
            if v == "legacy":
                continue
            parts.append(f"\n## Paired deltas: {v} vs legacy (rank; lower is better)\n")
            parts.append("| qid | class | legacy rank | " + v + " rank | delta |")
            parts.append("|---|---|---|---|---|")
            improved = worsened = tied = 0
            for r in [x for x in results if x["variant"] == v]:
                lr = legacy_by_qid.get(r["qid"], {}).get("rank")
                vr = r["rank"]
                if lr is None and vr is None:
                    delta = "both miss"
                    tied += 1
                elif lr is None:
                    delta = "found (was miss)"
                    improved += 1
                elif vr is None:
                    delta = "lost (was hit)"
                    worsened += 1
                else:
                    d = lr - vr
                    delta = f"{d:+d}"
                    improved += d > 0
                    worsened += d < 0
                    tied += d == 0
                parts.append(f"| {r['qid']} | {r['class']} | {_fmt(lr)} | {_fmt(vr)} | {delta} |")
            parts.append(f"\n**Summary:** improved {improved}, worsened {worsened}, tied {tied}.")
    else:
        parts.append(
            "\n_No paired comparison: only the legacy variant ran "
            "(flag missing or SQL variants skipped)._"
        )
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queries",
        default=str(Path(__file__).parent / "queries" / "known_item.jsonl"),
        help="Path to known_item.jsonl (default: evals/queries/known_item.jsonl)",
    )
    parser.add_argument(
        "--variants",
        default=None,
        help="Comma-separated variants (legacy,bm25,bm25_rrf). Default: all runnable.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="Deterministic stratified subsample size (0 = all queries).",
    )
    parser.add_argument("--limit", type=int, default=10, help="Search limit (needs >=10 for S@10).")
    parser.add_argument(
        "--sleep",
        type=float,
        default=15.0,
        help=(
            "Seconds between search calls. Anonymous default 15s keeps the ~12 "
            "fan-out requests/call under the 60 req/min upstream budget; with a "
            "token on the prod host 1-2s is fine."
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).parent / "results"),
        help="Results root (default: evals/results).",
    )
    args = parser.parse_args()
    return anyio.run(run, args)


if __name__ == "__main__":
    raise SystemExit(main())
