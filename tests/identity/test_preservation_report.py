"""Exercise the operator's preservation report using synthetic files only."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from zeeker_identity.db import DATABASE
from zeeker_identity.importer import import_snapshot, source_manifest

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_VALUES = (
    "PRESERVATION_SYNTHETIC_ID",
    "PRESERVATION_SYNTHETIC_CITATION",
    "PRESERVATION_UNSELECTED_BODY",
    "PRESERVATION_SYNTHETIC_APPROVAL",
    "[2099] SGHC 1",
    "PRIVATE_SOURCE_BODY_CANARY",
)


@pytest.fixture
def inputs(source, registry, tmp_path):
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.executemany(
            "INSERT INTO judgments (id, citation, content_text) VALUES (?, ?, ?)",
            [
                ("PRESERVATION_SYNTHETIC_ID", "PRESERVATION_SYNTHETIC_CITATION", PRIVATE_VALUES[2]),
                ("PRESERVATION_SYNTHETIC_ID", "PRESERVATION_SYNTHETIC_CITATION", PRIVATE_VALUES[2]),
                (None, None, PRIVATE_VALUES[2]),
                (None, None, PRIVATE_VALUES[2]),
                (" ", " ", PRIVATE_VALUES[2]),
                (" ", " ", PRIVATE_VALUES[2]),
            ],
        )
    contract = source_manifest(
        source,
        source_id="synthetic-preservation",
        generation="synthetic-v1",
        corpus="judgments",
        table="judgments",
        columns=["id", "citation"],
    )
    contract["approval_reference"] = PRIVATE_VALUES[3]
    contract_path = tmp_path / "private-contract.json"
    contract_path.write_text(json.dumps(contract))
    return source, contract, contract_path, registry


def run_report(inputs):
    source, _, contract_path, registry = inputs
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verify_registry_preservation.py"),
            "--source",
            str(source),
            "--contract",
            str(contract_path),
            "--registry-dir",
            str(registry),
        ],
        cwd=source.parent,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    output = result.stdout + result.stderr
    for private_value in (*PRIVATE_VALUES, str(source), str(contract_path), str(registry)):
        assert private_value not in output
    return result


def assert_rejected(inputs):
    result = run_report(inputs)
    assert result.returncode == 2
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert set(error) == {"error"}
    assert isinstance(error["error"], str) and error["error"]


def test_preservation_report_is_aggregate_and_read_only(inputs):
    source, contract, contract_path, registry = inputs
    import_snapshot(registry, source, contract)
    tracked_files = (source, contract_path, registry / DATABASE)
    before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked_files]
    private_files = set(registry.rglob("*"))

    result = run_report(inputs)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "status": "verified",
        "row_count": 12,
        "duplicate_groups": {
            "id": {"groups": 2, "rows": 4},
            "citation": {"groups": 3, "rows": 6},
        },
    }
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in tracked_files] == before
    assert set(registry.rglob("*")) == private_files


def test_preservation_report_rejects_selected_source_drift(inputs):
    source, contract, _, registry = inputs
    import_snapshot(registry, source, contract)
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("UPDATE judgments SET citation = 'synthetic-changed' WHERE rowid = 1")
    assert_rejected(inputs)


@pytest.mark.parametrize("unrelated_snapshot", [False, True])
def test_preservation_report_requires_matching_registry_snapshot(inputs, unrelated_snapshot):
    source, contract, _, registry = inputs
    if unrelated_snapshot:
        import_snapshot(registry, source, {**contract, "generation": "synthetic-unrelated"})
    assert_rejected(inputs)


def test_preservation_report_rejects_registry_assertion_tampering(inputs):
    source, contract, _, registry = inputs
    import_snapshot(registry, source, contract)
    with closing(sqlite3.connect(registry / DATABASE)) as connection, connection:
        trigger = "immutable_legacy_row_assertion_update"
        (restore_trigger,) = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'trigger' AND name = ?", (trigger,)
        ).fetchone()
        connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute(
            "UPDATE legacy_row_assertion SET metadata = json_set(metadata, '$.citation', ?) "
            "WHERE assertion_id = (SELECT min(assertion_id) FROM legacy_row_assertion)",
            ("synthetic-tampering",),
        )
        connection.execute(restore_trigger)
    assert_rejected(inputs)
