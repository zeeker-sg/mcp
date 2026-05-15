"""Soak report: CSV reducer + CLI exit-code gate + markdown summary writer.

Reads the latency.csv and rss.csv produced by scripts/soak/run_soak.py,
computes p50/p95/max latency and max RSS, detects daily rate-limit rollover,
writes a markdown summary, and exits 0 on pass or 1 on threshold breach.

Input contract (columns from run_soak.py):
  latency.csv: wall_ts, status, duration_seconds, error_class
  rss.csv:     wall_ts, rss_kb

Threshold defaults (from REQUIREMENTS.md NFR-01/03):
  --max-p50-ms 300    (NFR-01)
  --max-p95-ms 1500   (NFR-01)
  --max-rss-mb 256    (NFR-03)

Exit code:
  0 — all thresholds passed
  1 — at least one threshold breached

TEST-05 / NFR-01 / NFR-03 owner: Phase 8 (plan 08-05).
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _percentile(samples: list[float], p: float) -> float:
    """Return the p-th percentile (0.0–1.0) of samples using sort-then-index.

    Per 08-RESEARCH.md "Don't Hand-Roll" line 316:
      sorted(samples)[int(p * len(samples))]
    No hdrhistogram — stdlib only.
    """
    if not samples:
        return 0.0
    return sorted(samples)[int(p * len(samples))]


def _load_latency(path: Path) -> tuple[list[float], collections.Counter]:
    """Parse latency.csv and return (duration_ms_list, error_counter).

    Durations are stored in seconds in the CSV; this function converts to ms.
    Rows with non-numeric duration_seconds are skipped (e.g., header row if
    the caller forgets to skip it — defensive).
    """
    durations_ms: list[float] = []
    errors: collections.Counter = collections.Counter()
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue  # skip header
            if len(row) < 4:
                continue
            try:
                duration_ms = float(row[2]) * 1000.0
            except (ValueError, IndexError):
                continue
            durations_ms.append(duration_ms)
            error_class = row[3].strip() if row[3].strip() else "ok"
            errors[error_class] += 1
    return durations_ms, errors


def _load_rss(path: Path) -> list[int]:
    """Parse rss.csv and return list of rss_kb values."""
    values: list[int] = []
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue  # skip header
            if len(row) < 2:
                continue
            try:
                values.append(int(float(row[1])))
            except (ValueError, IndexError):
                continue
    return values


def _detect_daily_rollover(latency_csv_path: Path) -> tuple[bool, str]:
    """Detect if a daily rate-limit rollover was observed during the soak.

    Method (per 08-PATTERNS.md "Daily-rollover detection pseudocode"):
      1. Bucket 429 (rate_limited) events per UTC minute.
      2. Find any UTC midnight (hour=0, minute=0) crossed during the soak.
      3. For each midnight, compare 429 count at that minute vs the prior minute.
      4. Flag rollover if the count drops > 50% within ±60s of any midnight.

    Returns (observed: bool, reason: str) for the markdown summary.
    """
    per_minute_429: collections.Counter = collections.Counter()
    with latency_csv_path.open(newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                continue
            if len(row) < 4:
                continue
            error_class = row[3].strip()
            if error_class != "rate_limited":
                continue
            try:
                wall_ts = float(row[0])
            except (ValueError, IndexError):
                continue
            dt = datetime.fromtimestamp(wall_ts, tz=UTC).replace(second=0, microsecond=0)
            per_minute_429[dt] += 1

    if not per_minute_429:
        return False, "no 429 events recorded — cannot detect rollover"

    # Find UTC midnights crossed
    midnights = [m for m in per_minute_429 if m.hour == 0 and m.minute == 0]
    if not midnights:
        return False, "no UTC midnight crossed during soak window"

    daily_rollover_observed = any(
        per_minute_429[m] < 0.5 * per_minute_429[m - timedelta(minutes=1)]
        for m in midnights
        if (m - timedelta(minutes=1)) in per_minute_429
    )

    if daily_rollover_observed:
        reason = (
            f"observed >50% drop in 429 rate within 1 minute of UTC midnight "
            f"(midnights checked: {[str(m) for m in midnights]})"
        )
    else:
        reason = (
            f"no >50% drop in 429 rate detected near UTC midnight "
            f"(midnights found: {[str(m) for m in midnights]})"
        )
    return daily_rollover_observed, reason


def _write_markdown_summary(
    out_path: Path,
    durations_ms: list[float],
    errors: collections.Counter,
    rss_kb: list[int],
    rollover_observed: bool,
    rollover_reason: str,
    breaches: list[str],
) -> None:
    """Write a human-readable markdown summary of the soak results."""
    p50 = _percentile(durations_ms, 0.50)
    p95 = _percentile(durations_ms, 0.95)
    max_lat = max(durations_ms) if durations_ms else 0.0
    max_rss_mb = max(rss_kb) / 1024.0 if rss_kb else 0.0

    lines = [
        "# Soak Report",
        "",
        "## Latency",
        f"- p50: {p50:.1f} ms",
        f"- p95: {p95:.1f} ms",
        f"- max: {max_lat:.1f} ms",
        f"- samples: {len(durations_ms)}",
        "",
        "## RSS",
        f"- max: {max_rss_mb:.1f} MB",
        f"- samples: {len(rss_kb)}",
        "",
        "## Errors (by class)",
        f"- ok: {errors.get('ok', 0)}",
        f"- rate_limited: {errors.get('rate_limited', 0)}",
        f"- pool_timeout: {errors.get('pool_timeout', 0)}",
        f"- request_timeout: {errors.get('request_timeout', 0)}",
        f"- 5xx: {errors.get('5xx', 0)}",
        f"- 4xx: {errors.get('4xx', 0)}",
        "",
        "## Daily Rollover",
        f"- observed: {rollover_observed}",
        f"- reason: {rollover_reason}",
        "",
        "## Threshold Breaches",
    ]
    if breaches:
        lines.extend(f"- {b}" for b in breaches)
    else:
        lines.append("- none")

    out_path.write_text("\n".join(lines) + "\n")


def main() -> int:
    """Parse args, load CSVs, compute metrics, write summary, exit 0/1."""
    parser = argparse.ArgumentParser(
        description="Soak report gate — reads CSVs from run_soak.py, checks NFR thresholds",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--results-dir",
        default="./soak-results",
        help="Directory containing latency.csv and rss.csv",
    )
    parser.add_argument(
        "--max-p50-ms",
        type=int,
        default=300,
        help="NFR-01: p50 latency threshold in milliseconds",
    )
    parser.add_argument(
        "--max-p95-ms",
        type=int,
        default=1500,
        help="NFR-01: p95 latency threshold in milliseconds",
    )
    parser.add_argument(
        "--max-rss-mb",
        type=int,
        default=256,
        help="NFR-03: max RSS threshold in megabytes",
    )
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Path for markdown summary output (default: <results-dir>/soak-summary.md)",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    latency_path = results_dir / "latency.csv"
    rss_path = results_dir / "rss.csv"
    summary_out = Path(args.summary_out) if args.summary_out else results_dir / "soak-summary.md"

    # Load CSVs
    if not latency_path.exists():
        print(
            f"ERROR: latency.csv not found at {latency_path}; did the soak driver run?",
            file=sys.stderr,
        )
        return 1
    durations_ms, errors = _load_latency(latency_path)
    rss_kb = _load_rss(rss_path) if rss_path.exists() else []

    # Compute metrics
    p50_ms = _percentile(durations_ms, 0.50)
    p95_ms = _percentile(durations_ms, 0.95)
    max_rss_mb = max(rss_kb) / 1024.0 if rss_kb else 0.0

    # Detect daily rollover
    rollover_observed, rollover_reason = _detect_daily_rollover(latency_path)

    # Evaluate thresholds
    breaches: list[str] = []
    if p50_ms > args.max_p50_ms:
        breaches.append(f"p50_ms={p50_ms:.1f} > limit {args.max_p50_ms}")
    if p95_ms > args.max_p95_ms:
        breaches.append(f"p95_ms={p95_ms:.1f} > limit {args.max_p95_ms}")
    if max_rss_mb > args.max_rss_mb:
        breaches.append(f"max_rss_mb={max_rss_mb:.1f} > limit {args.max_rss_mb}")

    # Write markdown summary
    results_dir.mkdir(parents=True, exist_ok=True)
    _write_markdown_summary(
        summary_out,
        durations_ms,
        errors,
        rss_kb,
        rollover_observed,
        rollover_reason,
        breaches,
    )

    if breaches:
        print(f"BREACH: {breaches}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
