import json
import sqlite3
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from zeeker_identity.candidates import (
    NORMALIZERS,
    generate_candidates,
    neutral_v2,
    publisher_url_variant,
)
from zeeker_identity.db import open_registry
from zeeker_identity.errors import Conflict, RegistryError
from zeeker_identity.review import adjudicate, claim_evidence

from .conftest import claims, decide, state


@pytest.mark.parametrize(
    "text,expected",
    [
        ("[2099] SGHC 1", "[2099] SGHC 1"),
        (" [2099] sghc001 ", "[2099] SGHC 1"),
        ("[2099] SGHC(I) 1", "[2099] SGHC(I) 1"),
        ("[2099] SGHC(A) 1", "[2099] SGHC(A) 1"),
        ("[2099] SGCA(I) 1", "[2099] SGCA(I) 1"),
        ("SIC/S 4/2017", None),
        ("[2099] SGHCI 1", None),
        ("[2099] UNKNOWN 1", None),
        ("[2099] SGHC 0", None),
        ("[2099]\u00a0SGHC 1", None),
        ("[2099] SGHC \u0661", None),
        ("[\u0662\u0660\u0669\u0669] SGHC 1", None),
        ("[2099] SGHC \uff11", None),
        ("[2099]\tSGHC 1", None),
        ("[2099] SGHC 1\n", None),
        (None, None),
    ],
)
def test_documented_parser_scope(text, expected):
    assert neutral_v2(text) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://www.elitigation.sg/gd/s/2099_SGHC_1",
        "https://evil.test/gd/s/2099_SGHC_1",
        "https://www.elitigation.sg/gd/s/2099_SGHC_1?unreviewed=1",
        "https://www.elitigation.sg@evil.test/gd/s/2099_SGHC_1",
        "https://www.elitigation.sg/gd/s/2099_SGHC_\u0661",
        "https://www.elitigation.sg/gd/s/\u0662\u0660\u0669\u0669_SGHC_1",
        "https://www.elitigation.sg/gd/s/2099_SGHC_\uff11",
        "https://www.elitigation.sg/gd/s/2099_SGHC_1\u00a0",
        "https://www.eliti\tgation.sg/gd/s/2099_SGHC_1",
        "https://www.elitigation.sg/gd/s/2099_SG\nHC_1",
    ],
)
def test_url_scope_rejects_unapproved_variants(url):
    assert publisher_url_variant(url) is None


@pytest.mark.parametrize("codepoint", [*range(0x21), 0x7F])
def test_raw_url_controls_and_spaces_are_rejected_before_urlsplit(codepoint):
    url = "https://www.elitigation.sg/gd/s/2099_SGHC_1"
    character = chr(codepoint)
    assert publisher_url_variant(character + url) is None
    assert publisher_url_variant(url.replace("SGHC", "SG" + character + "HC")) is None


def test_unapproved_variants_preserve_original_unbound_metadata(registry, tmp_path):
    from zeeker_identity.candidates import DEFAULT_REVISION
    from zeeker_identity.importer import import_snapshot, source_manifest

    source = tmp_path / "raw-variants.sqlite3"
    original = [
        ("valid", "[2099] SGHC 1", "https://www.elitigation.sg/gd/s/2099_SGHC_1"),
        ("raw", "[2099] SGHC \u0661", "\nhttps://www.elitigation.sg/gd/s/2099_SGHC_1"),
    ]
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE judgments(id TEXT,citation TEXT,source_url TEXT)")
        connection.executemany("INSERT INTO judgments VALUES (?,?,?)", original)
    contract = source_manifest(
        source,
        source_id="synthetic-raw",
        generation="1",
        corpus="judgments",
        table="judgments",
        columns=["id", "citation", "source_url"],
    )
    contract["approval_reference"] = "synthetic-only"
    snapshot = import_snapshot(registry, source, contract)
    result = generate_candidates(registry, snapshot["snapshot_id"])
    assert result["normalizer_revision"] == DEFAULT_REVISION == "sg-neutral-v2"
    assert Counter(c["reason"] for c in claims(registry)) == {"individual_review": 2}
    with open_registry(registry, readonly=True) as connection:
        preserved = [
            json.loads(r[0])
            for r in connection.execute(
                "SELECT metadata FROM legacy_row_assertion ORDER BY locator"
            )
        ]
        assert [
            tuple(row[k] for k in ("id", "citation", "source_url")) for row in preserved
        ] == original
        assert (
            connection.execute(
                "SELECT count(*) FROM effective_assertion_binding "
                "WHERE document_id IS NULL AND revision=0"
            ).fetchone()[0]
            == 2
        )
        assert connection.execute("SELECT count(*) FROM document").fetchone()[0] == 0
    before = state(registry)
    with pytest.raises(RegistryError, match="unknown normalizer"):
        generate_candidates(registry, snapshot["snapshot_id"], revision="sg-neutral-v1")
    assert state(registry) == before


def test_candidates_are_not_bindings_and_names_proceedings_are_context_only(registry, imported):
    before = state(registry)
    assert generate_candidates(registry, imported["snapshot_id"])["created"] == 0
    assert state(registry) == before
    with open_registry(registry, readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM document").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM effective_assertion_binding WHERE document_id IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        for claim in claims(registry):
            if claim["assertion_b"]:
                left, right = [
                    json.loads(
                        connection.execute(
                            "SELECT metadata FROM legacy_row_assertion WHERE assertion_id=?", (key,)
                        ).fetchone()[0]
                    )
                    for key in (claim["assertion_a"], claim["assertion_b"])
                ]
                assert left["citation"] == right["citation"]


def test_exact_synthetic_candidate_groups(registry, imported):
    proposed = claims(registry)
    assert Counter(claim["reason"] for claim in proposed) == {
        "individual_review": 6,
        "neutral_citation": 2,
        "url_variant": 2,
    }
    assert {
        json.loads(claim["evidence"])["key"]
        for claim in proposed
        if claim["reason"] == "url_variant"
    } == {
        "https://www.elitigation.sg/gd/s/2099_SGHC_1",
        "https://www.elitigation.sg/gd/s/2099_SGHCR_2",
    }


def test_explicit_new_normalizer_run_preserves_decisions(registry, imported, monkeypatch):
    old_claims = claims(registry)
    decide(registry, old_claims[0]["claim_id"], verdict="defer")
    with open_registry(registry, readonly=True) as connection:
        decisions = [tuple(r) for r in connection.execute("SELECT * FROM review_decision")]
    # A future implementation revision is simulated here. Operators cannot invent
    # revisions through the CLI: unknown normalizer names are rejected.
    monkeypatch.setitem(NORMALIZERS, "synthetic-future-v2", neutral_v2)
    result = generate_candidates(registry, imported["snapshot_id"], revision="synthetic-future-v2")
    assert result["created"] == len(old_claims)
    with open_registry(registry, readonly=True) as connection:
        assert [tuple(r) for r in connection.execute("SELECT * FROM review_decision")] == decisions
    assert len(claims(registry)) == 2 * len(old_claims)
    with pytest.raises(RegistryError):
        generate_candidates(registry, imported["snapshot_id"], revision="invented")


def test_same_revision_definition_drift_fails_closed(registry, imported, monkeypatch):
    import zeeker_identity.candidates as candidates

    before = state(registry)
    monkeypatch.setattr(candidates, "COURTS", candidates.COURTS | {"SYNTHETIC"})
    with pytest.raises(RegistryError, match="definition changed"):
        generate_candidates(registry, imported["snapshot_id"])
    assert state(registry) == before


def test_url_rule_drift_is_also_bound_to_revision(registry, imported, monkeypatch):
    import zeeker_identity.candidates as candidates

    original = candidates.publisher_url_variant

    def permissive_url(value):
        return original(value.split("?")[0]) if isinstance(value, str) else None

    before = state(registry)
    monkeypatch.setattr(candidates, "publisher_url_variant", permissive_url)
    with pytest.raises(RegistryError, match="definition changed"):
        generate_candidates(registry, imported["snapshot_id"])
    assert state(registry) == before


def test_aba_rejects_delayed_competing_claim_after_revoke(registry, imported):
    pair = claims(registry, "neutral_citation")[0]
    delayed = claim_evidence(registry, pair["claim_id"])
    singleton = next(
        c for c in claims(registry, "individual_review") if c["assertion_a"] == pair["assertion_a"]
    )
    first = decide(registry, singleton["claim_id"])
    decide(registry, singleton["claim_id"], verdict="revoke")
    before = state(registry)
    with pytest.raises(Conflict, match="binding revision"):
        adjudicate(
            registry,
            pair["claim_id"],
            verdict="accept",
            reviewer="delayed",
            basis="synthetic",
            expected_claim_revision=0,
            expected_bindings=delayed["expected_bindings"],
        )
    assert state(registry) == before
    current = claim_evidence(registry, pair["claim_id"])
    assert current["revision"] == 0
    assert current["expected_bindings"][singleton["assertion_a"]] == 2
    second = decide(registry, pair["claim_id"])
    assert second["document_id"] != first["document_id"]
    assert (
        claim_evidence(registry, pair["claim_id"])["expected_bindings"][singleton["assertion_a"]]
        == 3
    )


@pytest.mark.parametrize("verdict", ["reject", "defer"])
def test_competing_reject_defer_preserves_existing_binding(registry, imported, verdict):
    pair = claims(registry, "neutral_citation")[0]
    single = next(
        c for c in claims(registry, "individual_review") if c["assertion_a"] == pair["assertion_a"]
    )
    accepted = decide(registry, single["claim_id"])
    decide(registry, pair["claim_id"], verdict=verdict)
    evidence = claim_evidence(registry, pair["claim_id"])
    bound = next(a for a in evidence["assertions"] if a["assertion_id"] == single["assertion_a"])
    assert bound["document_id"] == accepted["document_id"]
    assert bound["binding_revision"] == 1


def test_pair_acceptance_and_revocation_are_atomic(registry, imported):
    pair = claims(registry, "neutral_citation")[0]
    before = state(registry)

    def crash(stage):
        raise RuntimeError("synthetic mid-review interruption")

    with pytest.raises(RuntimeError):
        decide(registry, pair["claim_id"], checkpoint=crash)
    assert state(registry) == before
    accepted = decide(registry, pair["claim_id"])
    assert accepted["changed_bindings"] == 2
    before = state(registry)
    with pytest.raises(RuntimeError):
        decide(registry, pair["claim_id"], verdict="revoke", checkpoint=crash)
    assert state(registry) == before
    assert decide(registry, pair["claim_id"], verdict="revoke")["changed_bindings"] == 2


def test_two_bound_documents_cannot_be_coalesced(registry, imported):
    pair = claims(registry, "neutral_citation")[0]
    singles = {c["assertion_a"]: c for c in claims(registry, "individual_review")}
    first = decide(registry, singles[pair["assertion_a"]]["claim_id"])
    decide(registry, singles[pair["assertion_b"]]["claim_id"])
    before = state(registry)
    with pytest.raises(Conflict, match="coalescing"):
        decide(registry, pair["claim_id"], document_id=first["document_id"])
    assert state(registry) == before


def test_attaching_to_existing_doc_is_explicit_and_revoke_preserves_prior_owner(registry, imported):
    pair = claims(registry, "neutral_citation")[0]
    single = next(
        c for c in claims(registry, "individual_review") if c["assertion_a"] == pair["assertion_a"]
    )
    original = decide(registry, single["claim_id"])
    with pytest.raises(Conflict, match="explicit"):
        decide(registry, pair["claim_id"])
    decide(registry, pair["claim_id"], document_id=original["document_id"])
    assert decide(registry, pair["claim_id"], verdict="revoke")["changed_bindings"] == 1
    evidence = claim_evidence(registry, pair["claim_id"])
    assert sum(a["document_id"] is not None for a in evidence["assertions"]) == 1


def test_two_reviewers_race_one_wins_one_explicit_conflict(registry, imported):
    claim = claims(registry, "individual_review")[0]
    evidence = claim_evidence(registry, claim["claim_id"])

    def attempt(reviewer):
        try:
            adjudicate(
                registry,
                claim["claim_id"],
                verdict="accept",
                reviewer=reviewer,
                basis="synthetic",
                expected_claim_revision=0,
                expected_bindings=evidence["expected_bindings"],
            )
            return "accepted"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, ["reviewer-one", "reviewer-two"])) == [
            "accepted",
            "conflict",
        ]


def test_independent_cli_processes_reject_stale_writer(registry, imported):
    claim = claims(registry, "individual_review")[0]
    evidence = claim_evidence(registry, claim["claim_id"])
    command = [
        sys.executable,
        "-m",
        "zeeker_identity",
        "--registry-dir",
        str(registry),
        "review",
        "--claim",
        claim["claim_id"],
        "--verdict",
        "accept",
        "--basis",
        "synthetic cross-process race",
        "--claim-revision",
        "0",
        "--expected-bindings",
        json.dumps(evidence["expected_bindings"]),
    ]
    writers = [
        subprocess.Popen(
            [*command, "--reviewer", reviewer],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for reviewer in ("synthetic-process-one", "synthetic-process-two")
    ]
    results = [(writer, writer.communicate(timeout=15)) for writer in writers]
    assert sorted(writer.returncode for writer in writers) == [0, 2]
    assert "stale" in next(output[1] for writer, output in results if writer.returncode == 2)
    with open_registry(registry, readonly=True) as connection:
        assert connection.execute("SELECT count(*) FROM review_decision").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM document").fetchone()[0] == 1


def test_raw_sql_cannot_reset_tombstone_or_replace_a_binding(registry, imported):
    claim = claims(registry, "individual_review")[0]
    decide(registry, claim["claim_id"])
    with open_registry(registry) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE effective_assertion_binding SET revision=0, document_id=NULL, "
                "last_decision_id=NULL WHERE assertion_id=?",
                (claim["assertion_a"],),
            )


def test_raw_replacement_attempt_cannot_make_a_stale_competing_acceptance_current(
    registry, imported
):
    pair = claims(registry, "neutral_citation")[0]
    delayed = claim_evidence(registry, pair["claim_id"])
    single = next(
        c for c in claims(registry, "individual_review") if c["assertion_a"] == pair["assertion_a"]
    )
    decide(registry, single["claim_id"])
    before = state(registry)
    with sqlite3.connect(registry / "registry.sqlite3") as raw:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute("PRAGMA recursive_triggers=OFF")
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT OR REPLACE INTO effective_assertion_binding VALUES (?,?,?,NULL,0,NULL)",
                (single["assertion_a"], single["corpus"], single["kind"]),
            )
    with pytest.raises(Conflict, match="binding revision"):
        adjudicate(
            registry,
            pair["claim_id"],
            verdict="accept",
            reviewer="delayed",
            basis="synthetic",
            expected_claim_revision=0,
            expected_bindings=delayed["expected_bindings"],
        )
    assert state(registry) == before


def test_stale_claim_and_missing_binding_map_do_not_append_history(registry, imported):
    claim = claims(registry, "individual_review")[0]
    decide(registry, claim["claim_id"], verdict="defer")
    before = state(registry)
    with pytest.raises(Conflict):
        decide(registry, claim["claim_id"], expected_claim_revision=0)
    with pytest.raises(Conflict):
        decide(registry, claim["claim_id"], expected_bindings={})
    assert state(registry) == before


def test_competing_claim_cannot_revoke_another_claims_binding(registry, imported):
    pair = claims(registry, "neutral_citation")[0]
    single = next(
        c for c in claims(registry, "individual_review") if c["assertion_a"] == pair["assertion_a"]
    )
    decide(registry, single["claim_id"])
    before = state(registry)
    with pytest.raises(Conflict, match="owns no active"):
        decide(registry, pair["claim_id"], verdict="revoke")
    assert state(registry) == before


@pytest.mark.parametrize("bad_map", ["missing", "stale", "duplicate", "boolean"])
def test_raw_decision_cas_rejects_tampered_binding_maps(registry, imported, bad_map):
    from zeeker_identity.db import canonical, now

    pair = claims(registry, "neutral_citation")[0]
    left, right = pair["assertion_a"], pair["assertion_b"]
    maps = {
        "missing": canonical({left: 0}),
        "stale": canonical({left: 1, right: 0}),
        "duplicate": '{"' + left + '":0,"' + left + '":0,"' + right + '":0}',
        "boolean": canonical({left: False, right: 0}),
    }
    before = state(registry)
    with open_registry(registry) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO review_decision VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "synthetic-bad-decision",
                pair["claim_id"],
                pair["corpus"],
                pair["kind"],
                "defer",
                "synthetic",
                "tampered expected map",
                0,
                1,
                maps[bad_map],
                None,
                now(),
            ),
        )
    assert state(registry) == before


def test_overlarge_pair_inventory_fails_atomically(registry, tmp_path):
    from zeeker_identity.importer import import_snapshot, source_manifest

    source = tmp_path / "overlarge.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE judgments(id TEXT,citation TEXT)")
        connection.executemany(
            "INSERT INTO judgments VALUES (?,?)", [(str(i), "[2099] SGHC 1") for i in range(200)]
        )
    contract = source_manifest(
        source,
        source_id="synthetic-overlarge",
        generation="1",
        corpus="judgments",
        table="judgments",
        columns=["id", "citation"],
    )
    contract["approval_reference"] = "synthetic-only"
    snapshot = import_snapshot(registry, source, contract)
    before = state(registry)
    with pytest.raises(RegistryError, match="bounded review inventory"):
        generate_candidates(registry, snapshot["snapshot_id"])
    assert state(registry) == before
