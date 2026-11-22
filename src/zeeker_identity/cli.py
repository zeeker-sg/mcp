"""Private operator CLI; every stateful command requires an explicit directory."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .candidates import DEFAULT_REVISION, generate_candidates
from .db import canonical, initialize, open_registry
from .errors import RegistryError
from .importer import import_snapshot, source_manifest
from .recovery import backup, restore_copy
from .review import adjudicate, claim_evidence
from .schema import TABLES


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError("JSON objects must not contain duplicate keys")
        result[key] = value
    return result


def load_json(text):
    return json.loads(text, object_pairs_hook=_unique_object)


def parser():
    result = argparse.ArgumentParser(description="Private metadata identity registry (Increment A)")
    result.add_argument("--registry-dir", default=os.environ.get("ZEEKER_REGISTRY_DIR"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="explicitly initialize private registry state")
    manifest = commands.add_parser("source-manifest", help="propose a contract; grants no approval")
    manifest.add_argument("--source", required=True)
    manifest.add_argument("--source-id", required=True)
    manifest.add_argument("--generation", required=True)
    manifest.add_argument("--corpus", choices=["judgments", "pdpc"], required=True)
    manifest.add_argument("--table", required=True)
    manifest.add_argument("--columns", required=True, help="comma-separated metadata column names")
    importer = commands.add_parser("import", help="import explicitly pinned/approved metadata")
    importer.add_argument("--source", required=True)
    importer.add_argument("--contract", required=True)
    candidates = commands.add_parser("candidates", help="generate evidence only; never bind")
    candidates.add_argument("--snapshot", required=True)
    candidates.add_argument("--normalizer", default=DEFAULT_REVISION)
    claims = commands.add_parser("claims")
    claims.add_argument("--snapshot", required=True)
    inspect = commands.add_parser("inspect", help="private evidence and expected revisions")
    inspect.add_argument("--claim", required=True)
    review = commands.add_parser("review", help="explicit audited human decision")
    review.add_argument("--claim", required=True)
    review.add_argument("--verdict", choices=["accept", "reject", "defer", "revoke"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--basis", required=True)
    review.add_argument("--claim-revision", type=int, required=True)
    review.add_argument("--expected-bindings", required=True, help="JSON assertion-id:revision map")
    review.add_argument("--document-id")
    commands.add_parser("report", help="deterministic private inventory counts")
    save = commands.add_parser("backup")
    save.add_argument("--name", required=True)
    restore = commands.add_parser(
        "restore-copy", help="restore separately; never promote or overwrite"
    )
    restore.add_argument("--backup", required=True)
    restore.add_argument("--name", required=True)
    return result


def run(args):
    root = args.registry_dir
    if args.command == "init":
        return initialize(root)
    if args.command == "source-manifest":
        return source_manifest(
            args.source,
            source_id=args.source_id,
            generation=args.generation,
            corpus=args.corpus,
            table=args.table,
            columns=args.columns.split(","),
        )
    if args.command == "import":
        return import_snapshot(root, args.source, load_json(Path(args.contract).read_text()))
    if args.command == "candidates":
        return generate_candidates(root, args.snapshot, revision=args.normalizer)
    if args.command == "inspect":
        return claim_evidence(root, args.claim)
    if args.command == "review":
        return adjudicate(
            root,
            args.claim,
            verdict=args.verdict,
            reviewer=args.reviewer,
            basis=args.basis,
            expected_claim_revision=args.claim_revision,
            expected_bindings=load_json(args.expected_bindings),
            document_id=args.document_id,
        )
    if args.command == "backup":
        return backup(root, args.name)
    if args.command == "restore-copy":
        return restore_copy(root, args.backup, args.name)
    with open_registry(root, readonly=True) as connection:
        if args.command == "report":
            return {
                "counts": {
                    table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    for table in sorted(TABLES)
                },
                "bound_assertions": connection.execute(
                    "SELECT count(*) FROM effective_assertion_binding WHERE document_id IS NOT NULL"
                ).fetchone()[0],
            }
        if args.command == "claims":
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT c.claim_id,c.reason,c.revision FROM identity_claim c "
                    "JOIN legacy_row_assertion a ON a.assertion_id=c.assertion_a "
                    "WHERE a.snapshot_id=? ORDER BY c.claim_id",
                    (args.snapshot,),
                )
            ]
    raise RegistryError("unsupported operation")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(canonical(run(args)))
        return 0
    except (RegistryError, sqlite3.Error, OSError, ValueError) as exc:
        message = (
            str(exc) if isinstance(exc, RegistryError) else "operation failed; no approval implied"
        )
        print(canonical({"error": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
