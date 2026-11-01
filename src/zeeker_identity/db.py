"""Explicit private paths, checked connections and atomic migrations."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from .errors import RegistryError
from .schema import DDL, TABLES, VERSION

DATABASE = "registry.sqlite3"


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def private_directory(value: str | Path | None, *, create: bool = False) -> Path:
    if not value:
        raise RegistryError("an explicit registry directory is required")
    raw = Path(value).expanduser().absolute()
    if raw.is_symlink():
        raise RegistryError("registry directory must not be a symbolic link")
    path = raw.resolve()
    if any((parent / ".git").exists() for parent in [path, *path.parents]):
        raise RegistryError("registry state must be outside a repository")
    # A caller can select a private development child of a temporary directory,
    # but never the shared root itself. Actual serving-user isolation is an M0 gate.
    if path in {Path("/"), Path("/tmp").resolve(), Path.home().resolve()}:
        raise RegistryError("select a dedicated private directory")
    if create:
        # Do not chmod existing operator directories. Only create the final child;
        # the operator must already control its parent.
        try:
            path.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise RegistryError("cannot create registry directory") from exc
    try:
        info = path.stat()
    except OSError as exc:
        raise RegistryError("registry directory does not exist") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RegistryError("registry directory must be owner-only and owned by the current user")
    return path


def exclusive_file(path: Path) -> None:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError as exc:
        raise RegistryError("destination already exists or cannot be created") from exc


def configure(connection: sqlite3.Connection, *, readonly: bool = False) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RegistryError("foreign-key enforcement is required")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA temp_store=MEMORY")
    if readonly:
        connection.execute("PRAGMA query_only=ON")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection):
    if connection.in_transaction:
        raise RegistryError("nested transactions are not supported")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RegistryError("foreign-key enforcement was disabled")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def migrate(connection: sqlite3.Connection, *, checkpoint=None) -> None:
    expected = hashlib.sha256(DDL.encode()).hexdigest()
    tables = {
        r[0]
        for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if tables:
        if tables != TABLES:
            raise RegistryError("unexpected registry schema")
        rows = connection.execute("SELECT version, digest FROM schema_migration").fetchall()
        if [tuple(r) for r in rows] != [(VERSION, expected)]:
            raise RegistryError("unknown migration version or digest")
        if schema_signature(connection) != expected_schema_signature():
            raise RegistryError("registry schema objects differ from the migration")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RegistryError("registry foreign keys are invalid")
        return
    with transaction(connection):
        statement = ""
        for line in DDL.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            raise RegistryError("incomplete migration")
        if checkpoint:
            checkpoint("schema")
        connection.execute("INSERT INTO schema_migration VALUES (?, ?)", (VERSION, expected))


def schema_signature(connection):
    return [
        tuple(r)
        for r in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        )
    ]


@lru_cache(maxsize=1)
def expected_schema_signature():
    reference = sqlite3.connect(":memory:")
    try:
        reference.executescript(DDL)
        return schema_signature(reference)
    finally:
        reference.close()


@contextmanager
def open_registry(directory, *, readonly: bool = False):
    root = private_directory(directory)
    path = root / DATABASE
    try:
        info = path.lstat()
    except OSError as exc:
        raise RegistryError("initialize the registry explicitly first") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RegistryError("registry database must be a private regular file")
    connection = configure(
        sqlite3.connect(
            path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, isolation_level=None
        ),
        readonly=readonly,
    )
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migration' AND type='table'"
        ).fetchone():
            raise RegistryError("registry is uninitialized or invalid")
        migrate(connection)  # Existing-only validation; no implicit schema upgrade.
        yield connection
    finally:
        connection.close()


def initialize(directory) -> dict:
    root = private_directory(directory, create=True)
    path = root / DATABASE
    if path.exists():
        with open_registry(root, readonly=True):
            return {"schema_version": VERSION, "created": False}
    exclusive_file(path)
    connection = configure(sqlite3.connect(path, isolation_level=None))
    try:
        migrate(connection)
    except BaseException:
        connection.close()
        # Only the newly created, uninitialized file is removed on failure.
        path.unlink(missing_ok=True)
        raise
    connection.close()
    return {"schema_version": VERSION, "created": True}
