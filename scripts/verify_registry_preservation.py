"""Verify preservation against a pinned v2 source without writing registry state."""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from zeeker_identity.cli import load_json
from zeeker_identity.db import canonical, open_registry
from zeeker_identity.errors import RegistryError
from zeeker_identity.importer import scan_pinned_source, stable_id


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may quote supplied path values in its error message.
        self.exit(2, canonical({"error": "invalid arguments; see --help"}) + "\n")


def duplicate_counts(rows, columns):
    summary = {}
    for column in ("id", "citation"):
        if column not in columns:
            continue
        values = (metadata[column] for _, metadata in rows)
        counts = Counter(
            canonical(value)
            for value in values
            if value is not None and (not isinstance(value, str) or value.strip())
        )
        duplicates = [count for count in counts.values() if count > 1]
        summary[column] = {"groups": len(duplicates), "rows": sum(duplicates)}
    return summary


def verify(source, contract, registry_dir):
    contract, source_rows = scan_pinned_source(source, contract)
    identity = {key: value for key, value in contract.items() if key != "approval_reference"}
    snapshot_id = stable_id("snapshot", identity)
    with open_registry(registry_dir, readonly=True) as connection:
        connection.execute("BEGIN")
        snapshot = connection.execute(
            "SELECT corpus,kind,source_id,source_table,generation,selected_input_digest,"
            "schema_digest,metadata_digest,manifest FROM legacy_snapshot WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        expected_snapshot = (
            contract["corpus"],
            contract["kind"],
            contract["source_id"],
            contract["table"],
            contract["generation"],
            contract["selected_input_digest"],
            contract["schema_digest"],
            contract["metadata_digest"],
        )
        if snapshot is None or tuple(snapshot)[:-1] != expected_snapshot:
            raise RegistryError("snapshot identity or provenance mismatch")
        if canonical(load_json(snapshot["manifest"])) != canonical(contract):
            raise RegistryError("stored manifest mismatch")

        stored_rows = connection.execute(
            "SELECT corpus,kind,locator,metadata FROM legacy_row_assertion WHERE snapshot_id=?",
            (snapshot_id,),
        )
        # Canonical JSON keeps bool, integer, real and text values distinct.
        actual = Counter(
            (c, k, locator, canonical(load_json(metadata)))
            for c, k, locator, metadata in stored_rows
        )
    expected = Counter(
        (contract["corpus"], contract["kind"], locator, canonical(metadata))
        for locator, metadata in source_rows
    )
    if actual != expected:
        raise RegistryError("assertion locator or metadata mismatch")

    return {
        "status": "verified",
        "row_count": len(source_rows),
        "duplicate_groups": duplicate_counts(source_rows, contract["columns"]),
    }


def main():
    parser = PrivateArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--registry-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        contract = load_json(args.contract.read_text(encoding="utf-8"))
        result = verify(args.source, contract, args.registry_dir)
    except (RegistryError, sqlite3.Error, OSError, ValueError, TypeError, RecursionError):
        print(canonical({"error": "registry preservation could not be verified"}), file=sys.stderr)
        return 2
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
