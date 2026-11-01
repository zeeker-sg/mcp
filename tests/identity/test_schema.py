import sqlite3

import pytest

from zeeker_identity.db import configure, migrate, open_registry, transaction
from zeeker_identity.errors import RegistryError
from zeeker_identity.schema import TABLES


def test_exact_tables_keys_and_foreign_keys(registry):
    with open_registry(registry) as connection:
        actual = {
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert actual == TABLES
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        for table in actual:
            for column in connection.execute(f'PRAGMA table_info("{table}")'):
                if column[5]:
                    assert column[3] == 1, (table, column[1])
        assert not any(
            "public_id" in r[1]
            for table in actual
            for r in connection.execute(f'PRAGMA table_info("{table}")')
        )


@pytest.mark.parametrize(
    "values",
    [
        (None, "judgments", "judgment", "synthetic"),
        ("wrong", "pdpc", "judgment", "synthetic"),
        ("wrong", "judgments", "enforcement_decision", "synthetic"),
    ],
)
def test_null_and_invalid_kind_reject(registry, values):
    with open_registry(registry) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO document VALUES (?,?,?,?)", values)


def test_connection_with_foreign_keys_disabled_refuses_transaction(registry):
    with open_registry(registry) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(RegistryError, match="disabled"):
            with transaction(connection):
                pytest.fail("must reject before entering transaction")


def test_empty_file_never_implicitly_initializes(tmp_path):
    from zeeker_identity.db import initialize

    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    path = root / "registry.sqlite3"
    path.touch(mode=0o600)
    for readonly in (False, True):
        with pytest.raises(RegistryError, match="uninitialized"):
            with open_registry(root, readonly=readonly):
                pytest.fail("must reject incomplete initialization")
    with pytest.raises(RegistryError, match="uninitialized"):
        initialize(root)
    assert path.stat().st_size == 0


def test_migration_fault_rolls_back_every_table():
    connection = configure(sqlite3.connect(":memory:", isolation_level=None))

    def crash(stage):
        raise RuntimeError("synthetic migration interruption")

    with pytest.raises(RuntimeError):
        migrate(connection, checkpoint=crash)
    assert not connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    migrate(connection)
    assert (
        len(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        == 10
    )
    connection.close()


def test_cross_corpus_assertion_resource_ownership_rejects(registry, imported):
    with open_registry(registry) as connection:
        row = connection.execute("SELECT * FROM legacy_row_assertion LIMIT 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO legacy_row_assertion VALUES (?,?,?,?,?,?,?)",
                (
                    "forged",
                    row["snapshot_id"],
                    row["resource_id"],
                    "pdpc",
                    "enforcement_decision",
                    "999",
                    "{}",
                ),
            )


@pytest.mark.parametrize(
    "table",
    [
        "legacy_snapshot",
        "legacy_row_assertion",
        "source_resource",
        "document",
        "review_decision",
        "identity_claim",
        "effective_assertion_binding",
    ],
)
def test_history_and_tombstones_cannot_be_deleted(registry, imported, table):
    from .conftest import claims, decide

    decide(registry, claims(registry, "individual_review")[0]["claim_id"])
    with open_registry(registry) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(f'DELETE FROM "{table}"')
