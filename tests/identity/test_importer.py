import json
import sqlite3
from collections import Counter

import pytest

from zeeker_identity.db import open_registry
from zeeker_identity.errors import Conflict, RegistryError
from zeeker_identity.importer import import_snapshot, source_manifest

from .conftest import COLUMNS, claims, decide, state


def test_exact_metadata_multiset_duplicates_and_body_exclusion(registry, source, contract):
    result = import_snapshot(registry, source, contract)
    with sqlite3.connect(source) as original, open_registry(registry, readonly=True) as connection:
        rows = original.execute(
            "SELECT " + ",".join(sorted(COLUMNS)) + " FROM judgments"
        ).fetchall()
        imported = [
            json.loads(r[0])
            for r in connection.execute("SELECT metadata FROM legacy_row_assertion")
        ]
        assert Counter(rows) == Counter(tuple(row[k] for k in sorted(COLUMNS)) for row in imported)
        assert len(imported) == 6
        assert sum(row["id"] == "duplicate" for row in imported) == 2
        assert connection.execute("SELECT count(*) FROM document").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM identity_claim").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM effective_assertion_binding WHERE revision=0"
            ).fetchone()[0]
            == 6
        )
    assert result["imported"] == 6
    assert b"PRIVATE_SOURCE_BODY_CANARY" not in (registry / "registry.sqlite3").read_bytes()


def test_reimport_preserves_all_state_even_after_review(registry, source, contract, imported):
    decide(registry, claims(registry, "individual_review")[0]["claim_id"])
    before = state(registry)
    assert import_snapshot(registry, source, contract)["unchanged"]
    assert state(registry) == before


def test_new_snapshot_never_carries_rowid_bindings(registry, source, contract, imported):
    decide(registry, claims(registry, "individual_review")[0]["claim_id"])
    new = dict(contract, generation="fixture-v2")
    result = import_snapshot(registry, source, new)
    with open_registry(registry, readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM effective_assertion_binding b JOIN "
                "legacy_row_assertion a USING(assertion_id) WHERE a.snapshot_id=? "
                "AND b.document_id IS NOT NULL",
                (result["snapshot_id"],),
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT count(*) FROM legacy_row_assertion").fetchone()[0] == 12


@pytest.mark.parametrize(
    "change",
    [
        {"approval_reference": None},
        {"columns": ["id", "content_text"]},
        {"corpus": []},
        {"file_digest": "0" * 64},
        {"schema_digest": "0" * 64},
        {"metadata_digest": "0" * 64},
        {"generation": ""},
        {"layout": "pk-v1"},
        {"row_count": True},
        {"surprise": "field"},
    ],
)
def test_bad_contract_fails_without_partial_import(registry, source, contract, change):
    before = state(registry)
    with pytest.raises(RegistryError):
        import_snapshot(registry, source, dict(contract, **change))
    assert state(registry) == before


def test_changed_bytes_cannot_reuse_generation(registry, source, contract, imported):
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE judgments SET case_name='synthetic changed' WHERE rowid=1")
    new = source_manifest(
        source,
        source_id=contract["source_id"],
        generation=contract["generation"],
        corpus="judgments",
        table="judgments",
        columns=COLUMNS,
    )
    new["approval_reference"] = "synthetic-only-new-contract"
    before = state(registry)
    with pytest.raises(Conflict):
        import_snapshot(registry, source, new)
    assert state(registry) == before


def test_import_crash_rolls_back_and_can_be_retried(registry, source, contract):
    before = state(registry)

    def crash(stage):
        raise RuntimeError("synthetic interrupted import")

    with pytest.raises(RuntimeError):
        import_snapshot(registry, source, contract, checkpoint=crash)
    assert state(registry) == before
    assert import_snapshot(registry, source, contract)["imported"] == 6


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE judgments (id TEXT PRIMARY KEY, citation TEXT)",
        "CREATE TABLE judgments (id TEXT PRIMARY KEY, citation TEXT) WITHOUT ROWID",
        "CREATE TABLE judgments (id TEXT, citation TEXT, rowid TEXT)",
    ],
)
def test_unapproved_source_layout_rejected_before_import(tmp_path, ddl):
    path = tmp_path / "layout.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(ddl)
    with pytest.raises(RegistryError, match="layout"):
        source_manifest(
            path,
            source_id="synthetic",
            generation="1",
            corpus="judgments",
            table="judgments",
            columns=["id", "citation"],
        )


def test_schema_and_file_substitution_rejected(registry, source, contract):
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE judgments ADD COLUMN unexpected TEXT")
    before = state(registry)
    with pytest.raises(RegistryError):
        import_snapshot(registry, source, contract)
    assert state(registry) == before


def test_wal_source_refused_without_creating_registry_state(registry, source, contract):
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    before = state(registry)
    try:
        with pytest.raises(RegistryError):
            import_snapshot(registry, source, contract)
        assert state(registry) == before
    finally:
        connection.close()


def test_pdpc_has_own_corpus_boundary(registry, tmp_path):
    path = tmp_path / "pdpc.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE enforcement_decisions (id INTEGER, title TEXT, url TEXT)")
        connection.execute(
            "INSERT INTO enforcement_decisions VALUES (1,'Synthetic','https://example.test/1')"
        )
    contract = source_manifest(
        path,
        source_id="synthetic-pdpc",
        generation="1",
        corpus="pdpc",
        table="enforcement_decisions",
        columns=["id", "title", "url"],
    )
    contract["approval_reference"] = "synthetic-only"
    import_snapshot(registry, path, contract)
    with open_registry(registry, readonly=True) as connection:
        assert connection.execute("SELECT corpus,kind FROM legacy_row_assertion").fetchone()[:] == (
            "pdpc",
            "enforcement_decision",
        )


def test_virtual_word_in_ordinary_column_is_not_a_virtual_table(source):
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE judgments ADD COLUMN virtual_hint TEXT")
    proposed = source_manifest(
        source,
        source_id="synthetic",
        generation="1",
        corpus="judgments",
        table="judgments",
        columns=COLUMNS,
    )
    assert proposed["row_count"] == 6
