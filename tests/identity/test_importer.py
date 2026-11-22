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
        {"selected_input_digest": "0" * 64},
        {"schema_digest": "0" * 64},
        {"metadata_digest": "0" * 64},
        {"file_digest": "0" * 64},
        {"canonicalization_revision": "invented"},
        {"allowlist_revision": "invented"},
        {"allowed_columns": ["id", "content_text"]},
        {"contract_version": 1},
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


def test_selected_input_digest_does_not_claim_whole_file_provenance(registry, source, contract):
    from zeeker_identity.db import digest_file

    before = digest_file(source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE judgments SET content_text='DIFFERENT_UNSELECTED_BODY'")
    assert digest_file(source) != before
    proposed = source_manifest(
        source,
        source_id=contract["source_id"],
        generation=contract["generation"],
        corpus="judgments",
        table="judgments",
        columns=COLUMNS,
    )
    assert proposed["selected_input_digest"] == contract["selected_input_digest"]
    assert "file_digest" not in proposed
    assert proposed["contract_version"] == 2
    assert proposed["canonicalization_revision"] == "selected-input-json-v2"
    assert proposed["allowlist_revision"] == "metadata-allowlist-v1"
    assert import_snapshot(registry, source, contract)["imported"] == 6
    assert b"DIFFERENT_UNSELECTED_BODY" not in (registry / "registry.sqlite3").read_bytes()


def test_parent_directory_aba_cannot_pair_approved_a_digest_with_b_rows(
    registry, tmp_path, monkeypatch
):
    import zeeker_identity.importer as importer
    from zeeker_identity.db import digest_file

    live, other, parked = (tmp_path / name for name in ("live", "other", "parked"))
    live.mkdir()
    other.mkdir()
    a, b = live / "source.sqlite3", other / "source.sqlite3"
    for path, label in ((a, "A"), (b, "B")):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE judgments(id TEXT,citation TEXT,content_text TEXT)")
            connection.execute(
                "INSERT INTO judgments VALUES (?,?,?)",
                (label, "[2099] SGHC 1", "PRIVATE_SOURCE_BODY_CANARY"),
            )
    options = dict(
        source_id="synthetic-aba",
        generation="1",
        corpus="judgments",
        table="judgments",
        columns=["id", "citation"],
    )
    approved_a = source_manifest(a, **options)
    approved_a["approval_reference"] = "synthetic-prior-approval-of-A"
    honest_b = source_manifest(b, **options)
    assert approved_a["selected_input_digest"] != honest_b["selected_input_digest"]
    original_a = digest_file(a)
    genuine_connect = sqlite3.connect
    interceptions = []

    def aba_connect(database, *args, **kwargs):
        if database == a.as_uri() + "?mode=ro":
            # Real filesystem ABA and real SQLite handle, not fabricated SQL rows.
            # SQLite opens B; A is restored before the importer can inspect paths.
            live.rename(parked)
            other.rename(live)
            try:
                connection = genuine_connect(database, *args, **kwargs)
            finally:
                live.rename(other)
                parked.rename(live)
            interceptions.append(True)
            return connection
        return genuine_connect(database, *args, **kwargs)

    monkeypatch.setattr(importer.sqlite3, "connect", aba_connect)
    before = state(registry)
    with pytest.raises(RegistryError, match="pinned import contract"):
        import_snapshot(registry, a, approved_a)
    assert state(registry) == before
    acquired = source_manifest(a, **options)
    assert acquired["selected_input_digest"] == honest_b["selected_input_digest"]
    assert acquired["metadata_digest"] == honest_b["metadata_digest"]
    assert acquired["approval_reference"] is None
    assert "file_digest" not in acquired
    assert len(interceptions) == 2
    assert digest_file(a) == original_a
    assert b"PRIVATE_SOURCE_BODY_CANARY" not in (registry / "registry.sqlite3").read_bytes()
    assert sorted(path.name for path in registry.iterdir()) == ["registry.sqlite3"]
