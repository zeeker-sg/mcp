"""Run the private CLI against two invented metadata-only SQLite inputs."""

import json
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

JUDGMENT_URL = "https://www.elitigation.sg/gd/s/2099_SGHCR_"
COMMON_URL = JUDGMENT_URL + "2"
JUDGMENT_COLUMNS = ("id", "citation", "case_name", "case_numbers", "source_url")
PDPC_COLUMNS = ("id", "title", "organisation", "decision_date", "decision_url")
JUDGMENT_DUPLICATE = ("duplicate", "[2099] SGHCR 2", "Synthetic Party", "SYN/S 1/2099", COMMON_URL)
PDPC_DUPLICATE = (
    "duplicate",
    "Synthetic PDPC Decision",
    "Synthetic Organisation",
    "2099-01-01",
    COMMON_URL,
)
JUDGMENTS = (
    JUDGMENT_DUPLICATE,
    JUDGMENT_DUPLICATE,
    ("padding", "[2099] sghcr 02", "Synthetic Party", "SYN/S 1/2099", JUDGMENT_URL + "02"),
    ("separate", "[2099] SGHCR 3", "Synthetic Party", "SYN/S 1/2099", JUDGMENT_URL + "3"),
    ("international", "[2099] SGHC(I) 2", "Synthetic Party", "SYN/S 1/2099", None),
)
PDPC = (
    PDPC_DUPLICATE,
    PDPC_DUPLICATE,
    ("separate", "Synthetic PDPC Decision", "Synthetic Organisation", "2099-02-01", None),
)


def require_equal(actual, expected, label):
    if actual != expected:
        raise RuntimeError(f"{label}: expected {expected!r}, got {actual!r}")


def cli(*args):
    result = subprocess.run(
        [sys.executable, "-m", "zeeker_identity", *map(str, args)],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return json.loads(result.stdout)


def duplicate_groups(rows, column):
    counts = Counter(row[column] for row in rows if row[column] is not None)
    return {value: count for value, count in counts.items() if count > 1}


def run_corpus(work, corpus, kind, table, columns, rows, expected_groups, expected_reasons):
    registry = work / "private-state"
    source = work / f"{corpus}.sqlite3"
    contract_path = work / f"{corpus}.json"
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute(f"CREATE TABLE {table} ({', '.join(c + ' TEXT' for c in columns)})")
        connection.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(columns))})", rows)

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
        source_rows = connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()

    contract = cli(
        "source-manifest",
        "--source",
        source,
        "--source-id",
        "synthetic-m6",
        "--generation",
        "fixture-v1",
        "--corpus",
        corpus,
        "--table",
        table,
        "--columns",
        ",".join(columns),
    )
    require_equal(contract["approval_reference"], None, f"{corpus} proposed approval")
    contract["approval_reference"] = "synthetic-fixture-development-only"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    command = ("--registry-dir", registry)
    import_args = (*command, "import", "--source", source, "--contract", contract_path)
    imported = cli(*import_args)
    snapshot = imported["snapshot_id"]
    require_equal(imported["imported"], len(rows), f"{corpus} import count")
    require_equal(
        cli(*import_args),
        {"snapshot_id": snapshot, "imported": 0, "unchanged": True},
        f"{corpus} repeat import",
    )

    generate = (*command, "candidates", "--snapshot", snapshot)
    require_equal(cli(*generate)["created"], sum(expected_reasons.values()), f"{corpus} proposals")
    require_equal(cli(*generate)["created"], 0, f"{corpus} repeat proposals")
    claims = cli(*command, "claims", "--snapshot", snapshot)
    require_equal(
        Counter(claim["reason"] for claim in claims), expected_reasons, f"{corpus} reasons"
    )
    expected_pair_keys = {"neutral_citation": "[2099] SGHCR 2", "url_variant": COMMON_URL}
    pair_members = {}
    for claim in claims:
        evidence = cli(*command, "inspect", "--claim", claim["claim_id"])
        members = evidence["assertions"]
        require_equal({(m["corpus"], m["kind"]) for m in members}, {(corpus, kind)}, "claim scope")
        require_equal(
            evidence["expected_bindings"],
            dict.fromkeys((m["assertion_id"] for m in members), 0),
            "expected binding revisions",
        )
        require_equal([m["document_id"] for m in members], [None] * len(members), "unbound members")
        if claim["reason"] != "individual_review":
            require_equal(
                json.loads(evidence["evidence"])["key"],
                expected_pair_keys[claim["reason"]],
                "judgment pair key",
            )
            pair = frozenset(member["assertion_id"] for member in members)
            pair_members.setdefault(claim["reason"], set()).add(pair)
            require_equal(
                {json.loads(member["metadata"])["id"] for member in members}
                <= {"duplicate", "padding"},
                True,
                "judgment pair members",
            )
    if corpus == "judgments":
        require_equal(
            pair_members["neutral_citation"], pair_members["url_variant"], "matching groups"
        )
        require_equal(len(pair_members["neutral_citation"]), 3, "unique judgment pairs")

    with closing(
        sqlite3.connect(f"{(registry / 'registry.sqlite3').as_uri()}?mode=ro", uri=True)
    ) as db:
        records = db.execute(
            "SELECT corpus,kind,metadata FROM legacy_row_assertion WHERE snapshot_id=?", (snapshot,)
        ).fetchall()
    require_equal({(c, k) for c, k, _ in records}, {(corpus, kind)}, f"{corpus} assertion scope")
    selected = [json.loads(metadata) for _, _, metadata in records]
    for metadata in selected:
        require_equal(set(metadata), set(columns), f"{corpus} metadata columns")
    preserved = [tuple(metadata[column] for column in columns) for metadata in selected]
    require_equal(Counter(preserved), Counter(source_rows), f"{corpus} metadata multiset")
    for column, groups in expected_groups.items():
        require_equal(
            duplicate_groups(preserved, columns.index(column)), groups, f"{corpus} {column}"
        )
    return {
        "snapshot_id": snapshot,
        "rows": len(rows),
        "duplicate_groups": expected_groups,
        "proposals": expected_reasons,
    }


def main():
    # macOS may expose /tmp as a symlink. The registry requires a resolved private path.
    with TemporaryDirectory(prefix="zeeker-synthetic-m6-") as directory:
        work = Path(directory).resolve()
        registry = work / "private-state"
        cli("--registry-dir", registry, "init")
        judgments = run_corpus(
            work,
            "judgments",
            "judgment",
            "judgments",
            JUDGMENT_COLUMNS,
            JUDGMENTS,
            {"id": {"duplicate": 2}, "citation": {"[2099] SGHCR 2": 2}},
            {"individual_review": 5, "neutral_citation": 3, "url_variant": 3},
        )
        pdpc = run_corpus(
            work,
            "pdpc",
            "enforcement_decision",
            "enforcement_decisions",
            PDPC_COLUMNS,
            PDPC,
            {"id": {"duplicate": 2}, "decision_url": {COMMON_URL: 2}},
            {"individual_review": 3},
        )
        require_equal(judgments["snapshot_id"] == pdpc["snapshot_id"], False, "snapshot separation")
        report = cli("--registry-dir", registry, "report")
        require_equal(report["bound_assertions"], 0, "bound assertions")
        require_equal(report["counts"]["effective_assertion_binding"], 8, "binding inventory")
        require_equal(report["counts"]["document"], 0, "documents")
        print(json.dumps({"judgments": judgments, "pdpc": pdpc, "bound_assertions": 0}, indent=2))


if __name__ == "__main__":
    main()
