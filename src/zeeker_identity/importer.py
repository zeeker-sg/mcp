"""Pinned metadata-only import from an explicitly selected, frozen source layout."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import uuid
from pathlib import Path

from .db import canonical, digest_file, now, open_registry, transaction
from .errors import Conflict, RegistryError

KINDS = {"judgments": "judgment", "pdpc": "enforcement_decision"}
SOURCE_TABLES = {"judgments": "judgments", "pdpc": "enforcement_decisions"}
ALLOWED_COLUMNS = {
    "judgments": frozenset(
        {
            "id",
            "citation",
            "case_name",
            "case_numbers",
            "decision_date",
            "court",
            "source_url",
            "pdf_url",
        }
    ),
    "pdpc": frozenset(
        {
            "id",
            "title",
            "organisation",
            "organisation_name",
            "decision_type",
            "decision_date",
            "date",
            "url",
            "document_url",
            "decision_url",
            "pdf_url",
        }
    ),
}
CONTRACT_KEYS = frozenset(
    {
        "contract_version",
        "source_id",
        "generation",
        "corpus",
        "kind",
        "table",
        "columns",
        "layout",
        "journal_mode",
        "file_digest",
        "schema_digest",
        "metadata_digest",
        "row_count",
        "approval_reference",
    }
)


def stable_id(*parts) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical(parts)))


def _selection(corpus, table, columns):
    if not isinstance(corpus, str) or corpus not in KINDS or table != SOURCE_TABLES[corpus]:
        raise RegistryError("unsupported corpus or source table")
    if (
        not isinstance(columns, list)
        or not columns
        or any(not isinstance(c, str) for c in columns)
        or len(set(columns)) != len(columns)
        or "id" not in columns
        or not set(columns) <= ALLOWED_COLUMNS[corpus]
    ):
        raise RegistryError("select unique named columns from the metadata allowlist, including id")
    return sorted(columns)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{name} must be a nonempty string")


def _scan(source, corpus, table, columns):
    columns = _selection(corpus, table, columns)
    raw = Path(source).absolute()
    if raw.is_symlink() or not raw.is_file():
        raise RegistryError("source must be a frozen regular file, not a symbolic link")
    path = raw.resolve()
    before = path.stat()
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise RegistryError("source sidecars require a separately acquired consistent fixture")
    with path.open("rb") as stream:
        header = stream.read(20)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
        raise RegistryError("only frozen rollback-journal SQLite sources are supported")
    file_digest = digest_file(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        table_row = connection.execute(
            "SELECT type, sql FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()
        if (
            not table_row
            or table_row[0] != "table"
            or re.match(r"CREATE\s+VIRTUAL\s+TABLE\b", table_row[1], re.IGNORECASE)
        ):
            raise RegistryError("selected source must be an ordinary table")
        info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        names = {r[1].lower() for r in info}
        if (
            any(r[5] for r in info)
            or "WITHOUT ROWID" in table_row[1].upper()
            or names & {"rowid", "_rowid_", "oid"}
        ):
            raise RegistryError("unsupported source locator layout")
        if not set(columns) <= names:
            raise RegistryError("selected source columns are missing")
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if journal != "delete":
            raise RegistryError("source journal mode must be delete")
        schema_digest = hashlib.sha256(canonical([table_row[1], info]).encode()).hexdigest()
        selected = ",".join(f'"{column}"' for column in columns)
        rows = []
        metadata_hash = hashlib.sha256()
        for row in connection.execute(
            f'SELECT _rowid_, {selected} FROM "{table}" ORDER BY _rowid_'
        ):
            values = row[1:]
            if any(
                not isinstance(v, (str, int, float, type(None)))
                or isinstance(v, float)
                and not math.isfinite(v)
                for v in values
            ):
                raise RegistryError("unsupported metadata value type")
            metadata = dict(zip(columns, values, strict=True))
            metadata_hash.update((canonical([row[0], metadata]) + "\n").encode())
            rows.append((str(row[0]), metadata))
        # Verify the read itself as well as the file. Input is required to stay
        # frozen; a caller cannot label a changing read using an advisory build ID.
        final_digest = digest_file(path)
        after = path.stat()
        if final_digest != file_digest or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise RegistryError("source changed while it was being inspected")
    finally:
        connection.close()
    return (
        {
            "columns": columns,
            "layout": "pkless-rowid-v1",
            "journal_mode": journal,
            "file_digest": file_digest,
            "schema_digest": schema_digest,
            "metadata_digest": metadata_hash.hexdigest(),
            "row_count": len(rows),
        },
        rows,
    )


def source_manifest(source, *, source_id, generation, corpus, table, columns) -> dict:
    """Propose a private contract, without importing or granting any data-use approval."""
    _text(source_id, "source_id")
    _text(generation, "generation")
    result, _ = _scan(source, corpus, table, columns)
    return {
        "contract_version": 1,
        "source_id": source_id,
        "generation": generation,
        "corpus": corpus,
        "kind": KINDS[corpus],
        "table": table,
        **result,
        "approval_reference": None,
    }


def validate_contract(contract: dict) -> dict:
    if not isinstance(contract, dict) or set(contract) != CONTRACT_KEYS:
        raise RegistryError("import contract has missing or unknown fields")
    if (
        contract["contract_version"] != 1
        or type(contract["contract_version"]) is not int
        or not isinstance(contract["corpus"], str)
        or contract["corpus"] not in KINDS
        or contract["kind"] != KINDS[contract["corpus"]]
        or contract["layout"] != "pkless-rowid-v1"
        or contract["journal_mode"] != "delete"
    ):
        raise RegistryError("unsupported import contract")
    for name in ("source_id", "generation", "approval_reference"):
        _text(contract[name], name)
    for name in ("file_digest", "schema_digest", "metadata_digest"):
        if not isinstance(contract[name], str) or not re.fullmatch(r"[0-9a-f]{64}", contract[name]):
            raise RegistryError("contract digests must be lowercase SHA-256")
    if type(contract["row_count"]) is not int or contract["row_count"] < 0:
        raise RegistryError("invalid source row count")
    result = dict(contract)
    result["columns"] = _selection(contract["corpus"], contract["table"], contract["columns"])
    return result


def import_snapshot(directory, source, contract, *, checkpoint=None) -> dict:
    contract = validate_contract(contract)
    actual, rows = _scan(source, contract["corpus"], contract["table"], contract["columns"])
    if any(actual[k] != contract[k] for k in actual):
        raise RegistryError("source does not match the pinned import contract")
    identity = {k: v for k, v in contract.items() if k != "approval_reference"}
    snapshot_id = stable_id("snapshot", identity)
    corpus, kind = contract["corpus"], contract["kind"]
    with open_registry(directory) as connection, transaction(connection):
        old = connection.execute(
            "SELECT snapshot_id FROM legacy_snapshot WHERE corpus=? AND kind=? AND source_id=? "
            "AND source_table=? AND generation=?",
            (corpus, kind, contract["source_id"], contract["table"], contract["generation"]),
        ).fetchone()
        if old:
            if old[0] != snapshot_id:
                raise Conflict("generation is already registered with a different input contract")
            return {"snapshot_id": snapshot_id, "imported": 0, "unchanged": True}
        connection.execute(
            "INSERT INTO legacy_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                snapshot_id,
                corpus,
                kind,
                contract["source_id"],
                contract["table"],
                contract["generation"],
                contract["file_digest"],
                contract["schema_digest"],
                contract["metadata_digest"],
                canonical(contract),
                now(),
            ),
        )
        for locator, metadata in rows:
            assertion_id = stable_id("assertion", snapshot_id, locator)
            url = next(
                (
                    metadata[c]
                    for c in ("source_url", "decision_url", "url", "document_url")
                    if metadata.get(c) is not None
                ),
                None,
            )
            representation = metadata.get("pdf_url")
            if any(
                value is not None and not isinstance(value, str) for value in (url, representation)
            ):
                raise RegistryError("resource URLs must be strings or NULL")
            # A missing URL does not collapse unrelated source rows into a resource.
            resource_id = stable_id(
                "resource",
                corpus,
                kind,
                contract["source_id"],
                url,
                representation,
                assertion_id if url is None else None,
            )
            connection.execute(
                "INSERT OR IGNORE INTO source_resource VALUES (?,?,?,?,?,?)",
                (resource_id, corpus, kind, contract["source_id"], url, representation),
            )
            connection.execute(
                "INSERT INTO legacy_row_assertion VALUES (?,?,?,?,?,?,?)",
                (
                    assertion_id,
                    snapshot_id,
                    resource_id,
                    corpus,
                    kind,
                    locator,
                    canonical(metadata),
                ),
            )
            connection.execute(
                "INSERT INTO effective_assertion_binding VALUES (?,?,?,NULL,0,NULL)",
                (assertion_id, corpus, kind),
            )
            if checkpoint:
                checkpoint("assertion")
        return {"snapshot_id": snapshot_id, "imported": len(rows), "unchanged": False}
