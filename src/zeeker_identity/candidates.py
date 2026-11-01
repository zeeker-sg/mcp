"""Versioned evidence proposals. No candidate operation creates a document or binding."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections import defaultdict
from itertools import combinations
from urllib.parse import urlsplit, urlunsplit

from .db import canonical, now, open_registry, transaction
from .errors import RegistryError
from .importer import stable_id

COURTS = frozenset(
    {
        "SGHC",
        "SGCA",
        "SGHCF",
        "SGHCR",
        "SGHC(I)",
        "SGHC(A)",
        "SGDC",
        "SGFC",
        "SGCA(I)",
        "SGMC",
        "SGSCT",
        "SGECT",
        "SGYC",
        "SGPHC",
        "SGCDT",
        "SGAD",
    }
)
DEFAULT_REVISION = "sg-neutral-v1"
MAX_PAIR_PROPOSALS = 10_000


def neutral_v1(value):
    """Only ASCII spacing/case variants and the documented missing-space form."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r" *\[(\d{4})\] *([A-Za-z]+(?:\([A-Za-z]+\))?) *(\d{1,6}) *", value)
    if not match:
        return None
    year, court, number = match.groups()
    court = court.upper()
    if court not in COURTS or int(number) == 0:
        return None
    return f"[{year}] {court} {int(number)}"


NORMALIZERS = {DEFAULT_REVISION: neutral_v1}


def publisher_url_variant(value):
    """Only eLitigation GD neutral-citation slugs; no generic URL equivalence."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"elitigation.sg", "www.elitigation.sg"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    match = re.fullmatch(r"/gd/s/(\d{4})_([A-Z]+(?:\([A-Z]+\))?)_(\d{1,6})", parsed.path)
    if not match or match[2] not in COURTS or int(match[3]) == 0:
        return None
    path = f"/gd/s/{match[1]}_{match[2]}_{int(match[3])}"
    return urlunsplit(("https", "www.elitigation.sg", path, "", ""))


def generate_candidates(directory, snapshot_id, *, revision=DEFAULT_REVISION) -> dict:
    if revision not in NORMALIZERS:
        raise RegistryError("unknown normalizer revision")
    normalizer = NORMALIZERS[revision]
    with open_registry(directory) as connection, transaction(connection):
        snapshot = connection.execute(
            "SELECT * FROM legacy_snapshot WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if snapshot is None:
            raise RegistryError("unknown snapshot")
        corpus, kind = snapshot["corpus"], snapshot["kind"]
        namespace_id = stable_id("namespace", corpus, kind, revision)
        definition = canonical(
            {
                "revision": revision,
                "courts": sorted(COURTS),
                "implementation_digest": hashlib.sha256(
                    inspect.getsource(normalizer).encode()
                ).hexdigest(),
                "scope": "evidence-only; no automatic binding",
            }
        )
        previous = connection.execute(
            "SELECT definition FROM alias_namespace WHERE namespace_id=?", (namespace_id,)
        ).fetchone()
        if previous and previous[0] != definition:
            raise RegistryError("normalizer definition changed; use an explicit new revision")
        connection.execute(
            "INSERT OR IGNORE INTO alias_namespace VALUES (?,?,?,?,?)",
            (namespace_id, corpus, kind, revision, definition),
        )
        rows = connection.execute(
            "SELECT assertion_id, metadata FROM legacy_row_assertion WHERE snapshot_id=? "
            "ORDER BY assertion_id",
            (snapshot_id,),
        ).fetchall()
        groups = defaultdict(list)
        proposals = []
        for row in rows:
            assertion_id = row["assertion_id"]
            metadata = json.loads(row["metadata"])
            proposals.append((assertion_id, None, "individual_review", {}))
            if corpus == "judgments":
                citation = normalizer(metadata.get("citation"))
                if citation:
                    groups[("neutral_citation", citation)].append(assertion_id)
                url = publisher_url_variant(metadata.get("source_url"))
                if url:
                    groups[("url_variant", url)].append(assertion_id)
        if (
            sum(len(members) * (len(members) - 1) // 2 for members in groups.values())
            > MAX_PAIR_PROPOSALS
        ):
            raise RegistryError(
                "candidate pairs exceed the bounded review inventory; generation refused"
            )
        for (reason, key), members in sorted(groups.items()):
            for left, right in combinations(sorted(members), 2):
                proposals.append((left, right, reason, {"key": key}))
        inserted = 0
        for left, right, reason, evidence in proposals:
            claim_id = stable_id("claim", namespace_id, left, right, reason, evidence)
            result = connection.execute(
                "INSERT OR IGNORE INTO identity_claim VALUES (?,?,?,?,?,?,?,?,0,?)",
                (
                    claim_id,
                    corpus,
                    kind,
                    namespace_id,
                    left,
                    right,
                    reason,
                    canonical(evidence),
                    now(),
                ),
            )
            inserted += result.rowcount
        return {"snapshot_id": snapshot_id, "normalizer_revision": revision, "created": inserted}
