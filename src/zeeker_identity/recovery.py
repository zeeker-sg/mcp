"""Consistent backups and separate restore copies; there is no promotion/overwrite API."""

from __future__ import annotations

import re
import sqlite3

from .db import configure, digest_file, exclusive_file, migrate, open_registry, private_directory
from .errors import RegistryError


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise RegistryError("choose a simple new artifact name without directory components")
    return value + ".sqlite3"


def backup(directory, name):
    root = private_directory(directory)
    target = private_directory(root / "backups", create=True) / _name(name)
    exclusive_file(target)
    try:
        with open_registry(root, readonly=True) as source:
            destination = sqlite3.connect(target, isolation_level=None)
            try:
                source.backup(destination)
                configure(destination)
                migrate(destination)
            finally:
                destination.close()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"artifact": target.name, "sha256": digest_file(target)}


def restore_copy(directory, backup_name, name):
    root = private_directory(directory)
    source_path = private_directory(root / "backups") / _name(backup_name)
    if source_path.is_symlink() or not source_path.is_file():
        raise RegistryError("backup must be an existing private regular file")
    if source_path.stat().st_mode & 0o077:
        raise RegistryError("backup must be owner-only")
    target = private_directory(root / "restores", create=True) / _name(name)
    exclusive_file(target)
    source = None
    try:
        source = configure(
            sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, isolation_level=None),
            readonly=True,
        )
        migrate(source)
        destination = sqlite3.connect(target, isolation_level=None)
        try:
            source.backup(destination)
            configure(destination)
            migrate(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RegistryError("restored database failed integrity verification")
        finally:
            destination.close()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        if source:
            source.close()
    return {"artifact": target.name, "sha256": digest_file(target), "promoted": False}
