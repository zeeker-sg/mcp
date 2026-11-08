"""Synthetic-only data. No real corpus, legal labels, protected content or network."""

from __future__ import annotations

import sqlite3

import pytest

from zeeker_identity.candidates import generate_candidates
from zeeker_identity.db import initialize, open_registry
from zeeker_identity.importer import import_snapshot, source_manifest
from zeeker_identity.review import adjudicate, claim_evidence

COLUMNS = ["id", "citation", "case_name", "case_numbers", "decision_date", "court", "source_url"]


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "private-state"
    initialize(path)
    return path


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "synthetic-source.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE judgments (id TEXT, citation TEXT, case_name TEXT, "
        "case_numbers TEXT, decision_date TEXT, court TEXT, source_url TEXT, "
        "content_text TEXT)"
    )
    rows = [
        (
            "duplicate",
            "[2099] SGHC 1",
            "Synthetic Party",
            "SYN/S 1/2099",
            "2099-01-01",
            "SGHC",
            "https://www.elitigation.sg/gd/s/2099_SGHC_1",
        ),
        (
            "duplicate",
            "[2099] SGHC 1",
            "Synthetic Party",
            "SYN/S 1/2099",
            "2099-01-01",
            "SGHC",
            "https://www.elitigation.sg/gd/s/2099_SGHC_1",
        ),
        (
            "other-decision",
            "[2099] SGHC 2",
            "Synthetic Party",
            "SYN/S 1/2099",
            "2099-02-01",
            "SGHC",
            "https://www.elitigation.sg/gd/s/2099_SGHC_2",
        ),
        (
            "padding-a",
            "[2099] SGHCR 2",
            "Synthetic Padding",
            "SYN/S 2/2099",
            "2099-03-01",
            "SGHCR",
            "https://www.elitigation.sg/gd/s/2099_SGHCR_02",
        ),
        (
            "padding-b",
            "[2099] SGHCR 2",
            "Synthetic Padding",
            "SYN/S 2/2099",
            "2099-03-01",
            "SGHCR",
            "https://www.elitigation.sg/gd/s/2099_SGHCR_2",
        ),
        (
            "international",
            "[2099] SGHC(I) 1",
            "Synthetic Party",
            "SYN/S 1/2099",
            "2099-01-01",
            "SGHCI",
            "https://www.elitigation.sg/gd/s/2099_SGHC(I)_1",
        ),
    ]
    connection.executemany(
        "INSERT INTO judgments VALUES (?,?,?,?,?,?,?,?)",
        [(*row, "PRIVATE_SOURCE_BODY_CANARY") for row in rows],
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def contract(source):
    result = source_manifest(
        source,
        source_id="synthetic-judgments",
        generation="fixture-v1",
        corpus="judgments",
        table="judgments",
        columns=COLUMNS,
    )
    result["approval_reference"] = "synthetic-development-only"
    return result


@pytest.fixture
def imported(registry, source, contract):
    result = import_snapshot(registry, source, contract)
    generate_candidates(registry, result["snapshot_id"])
    return result


def claims(registry, reason=None):
    with open_registry(registry, readonly=True) as connection:
        return [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM identity_claim WHERE (? IS NULL OR reason=?) ORDER BY claim_id",
                (reason, reason),
            )
        ]


def decide(registry, claim_id, verdict="accept", **kwargs):
    evidence = claim_evidence(registry, claim_id)
    options = dict(
        verdict=verdict,
        reviewer="synthetic-reviewer",
        basis="synthetic fixture instruction",
        expected_claim_revision=evidence["revision"],
        expected_bindings=evidence["expected_bindings"],
    )
    options.update(kwargs)
    return adjudicate(registry, claim_id, **options)


def state(registry):
    with open_registry(registry, readonly=True) as connection:
        return "\n".join(connection.iterdump())
