"""Tests for scripts/soak/report.py — the gate that converts soak CSVs into pass/fail.

Focus: CR-02 fix — the report MUST NOT silently pass NFR-03 when RSS collection
failed entirely (every remote /admin/metrics poll returned -1). The fix splits
`_load_rss` into (valid_values, sentinel_count) and refuses to evaluate NFR-03
when there are zero valid samples but a non-zero sentinel count.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

from scripts.soak.report import _load_rss


def _write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_load_rss_returns_tuple_of_values_and_sentinel_count(tmp_path):
    """Mixed CSV: some valid samples, some -1 sentinels — split correctly."""
    rss_path = tmp_path / "rss.csv"
    _write_csv(
        rss_path,
        ["wall_ts", "rss_kb_REMOTE_ADMIN_METRICS"],
        [(1.0, 102400), (2.0, -1), (3.0, 104448), (4.0, -1), (5.0, -1)],
    )
    values, sentinel_count = _load_rss(rss_path)
    assert values == [102400, 104448], "should keep only non-negative readings"
    assert sentinel_count == 3, "should count three -1 rows"


def test_load_rss_all_sentinels(tmp_path):
    """All polls failed — no valid samples, sentinel count == total rows."""
    rss_path = tmp_path / "rss.csv"
    _write_csv(
        rss_path,
        ["wall_ts", "rss_kb_REMOTE_ADMIN_METRICS"],
        [(1.0, -1), (2.0, -1), (3.0, -1)],
    )
    values, sentinel_count = _load_rss(rss_path)
    assert values == []
    assert sentinel_count == 3


def test_load_rss_all_valid(tmp_path):
    """Happy path: all samples valid — sentinel count == 0."""
    rss_path = tmp_path / "rss.csv"
    _write_csv(
        rss_path,
        ["wall_ts", "rss_kb"],
        [(1.0, 100000), (2.0, 102400), (3.0, 104448)],
    )
    values, sentinel_count = _load_rss(rss_path)
    assert values == [100000, 102400, 104448]
    assert sentinel_count == 0


def test_load_rss_skips_malformed_rows(tmp_path):
    """Rows that can't be parsed are skipped (not counted as sentinels)."""
    rss_path = tmp_path / "rss.csv"
    _write_csv(
        rss_path,
        ["wall_ts", "rss_kb"],
        [(1.0, 100000), ("bad", "not-a-number"), (3.0, 104448)],
    )
    values, sentinel_count = _load_rss(rss_path)
    assert values == [100000, 104448]
    assert sentinel_count == 0


def test_report_main_refuses_to_pass_when_all_rss_polls_failed(tmp_path):
    """The end-to-end gate: zero valid RSS samples + sentinels → exit 1.

    This is the CR-02 regression. Previously max([-1, -1, ...]) / 1024.0 = -0.001
    which fell below the 256 MB threshold and silently passed NFR-03.
    """
    # Build minimal latency.csv with one fast row so latency thresholds pass.
    latency_path = tmp_path / "latency.csv"
    _write_csv(
        latency_path,
        ["wall_ts", "status", "duration_seconds", "error_class"],
        [(1.0, 200, 0.050, "ok"), (2.0, 200, 0.060, "ok")],
    )
    rss_path = tmp_path / "rss.csv"
    _write_csv(
        rss_path,
        ["wall_ts", "rss_kb_REMOTE_ADMIN_METRICS"],
        [(1.0, -1), (2.0, -1), (3.0, -1)],
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.soak.report",
            "--results-dir",
            str(tmp_path),
            "--max-p50-ms",
            "300",
            "--max-p95-ms",
            "1500",
            "--max-rss-mb",
            "256",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert proc.returncode == 1, (
        f"report should exit 1 when all RSS polls failed; got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "NFR-03 cannot be evaluated" in proc.stderr or "BREACH" in proc.stderr, (
        f"expected NFR-03 breach message in stderr; got: {proc.stderr}"
    )


def test_report_main_passes_when_rss_is_valid_and_low(tmp_path):
    """Happy path sanity check — valid RSS samples below threshold → exit 0."""
    latency_path = tmp_path / "latency.csv"
    _write_csv(
        latency_path,
        ["wall_ts", "status", "duration_seconds", "error_class"],
        [(1.0, 200, 0.050, "ok"), (2.0, 200, 0.060, "ok")],
    )
    rss_path = tmp_path / "rss.csv"
    _write_csv(
        rss_path,
        ["wall_ts", "rss_kb_REMOTE_ADMIN_METRICS"],
        [(1.0, 100000), (2.0, 102400)],
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.soak.report",
            "--results-dir",
            str(tmp_path),
            "--max-p50-ms",
            "300",
            "--max-p95-ms",
            "1500",
            "--max-rss-mb",
            "256",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert proc.returncode == 0, (
        f"happy path should exit 0; got {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
