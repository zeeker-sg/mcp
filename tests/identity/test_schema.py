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
        assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
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


def test_connection_with_recursive_triggers_disabled_refuses_transaction(registry):
    with open_registry(registry) as connection:
        connection.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(RegistryError, match="disabled"):
            with transaction(connection):
                pytest.fail("must reject before entering transaction")


@pytest.mark.parametrize("operation", ["INSERT OR REPLACE", "REPLACE"])
@pytest.mark.parametrize("recursive", [0, 1])
def test_replacement_cannot_reset_any_existing_registry_key(
    registry, imported, operation, recursive
):
    from .conftest import claims, decide, state

    decide(registry, claims(registry, "individual_review")[0]["claim_id"])
    before = state(registry)
    # Deliberately bypass configure(): the insert guards must work even on an
    # unsupported alternate connection with foreign keys and recursion disabled.
    with sqlite3.connect(registry / "registry.sqlite3") as raw:
        raw.execute(f"PRAGMA recursive_triggers={recursive}")
        assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        for table in sorted(TABLES):
            row = list(raw.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone())
            if table == "legacy_row_assertion":
                row[-1] = '{"id":"synthetic-tampered"}'
            if table == "effective_assertion_binding":
                row[3:] = [None, 0, None]
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    f'{operation} INTO "{table}" VALUES ({",".join("?" for _ in row)})', row
                )
    assert state(registry) == before


@pytest.mark.parametrize("table", ["legacy_snapshot", "legacy_row_assertion", "review_decision"])
def test_replacement_cannot_delete_a_different_primary_key_via_unique_key(
    registry, imported, table
):
    from .conftest import claims, decide, state

    decide(registry, claims(registry, "individual_review")[0]["claim_id"], verdict="defer")
    before = state(registry)
    with sqlite3.connect(registry / "registry.sqlite3") as raw:
        raw.execute("PRAGMA recursive_triggers=OFF")
        row = list(raw.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone())
        row[0] = "synthetic-different-primary-key"
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(f'REPLACE INTO "{table}" VALUES ({",".join("?" for _ in row)})', row)
    assert state(registry) == before


def test_old_schema_version_is_refused_without_implicit_rewrite(registry):
    # Simulate an old migration record without importing a second schema into the
    # implementation. Both write and read opens must refuse, preserving the file.
    with sqlite3.connect(registry / "registry.sqlite3") as raw:
        raw.execute("DROP TRIGGER immutable_schema_migration_update")
        raw.execute("UPDATE schema_migration SET version='1'")
    before = (registry / "registry.sqlite3").read_bytes()
    for readonly in (False, True):
        with pytest.raises(RegistryError, match="no automatic upgrade"):
            with open_registry(registry, readonly=readonly):
                pytest.fail("must not reinterpret old state")
    assert (registry / "registry.sqlite3").read_bytes() == before


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


def test_sqlite_statistics_are_not_additional_logical_tables(registry):
    with open_registry(registry) as connection:
        connection.execute("ANALYZE")
    with open_registry(registry, readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            == 10
        )


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
