"""Explicit, audited review transactions with assertion-global compare-and-swap."""

from __future__ import annotations

import uuid

from .db import canonical, now, open_registry, transaction
from .errors import Conflict, RegistryError


def claim_evidence(directory, claim_id) -> dict:
    with open_registry(directory, readonly=True) as connection:
        claim = connection.execute(
            "SELECT * FROM identity_claim WHERE claim_id=?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise RegistryError("unknown claim")
        result = dict(claim)
        members = [claim["assertion_a"]] + ([claim["assertion_b"]] if claim["assertion_b"] else [])
        result["assertions"] = []
        result["expected_bindings"] = {}
        for assertion_id in members:
            row = connection.execute(
                "SELECT a.*, b.document_id, b.revision AS binding_revision "
                "FROM legacy_row_assertion a "
                "JOIN effective_assertion_binding b USING(assertion_id) WHERE assertion_id=?",
                (assertion_id,),
            ).fetchone()
            result["assertions"].append(dict(row))
            result["expected_bindings"][assertion_id] = row["binding_revision"]
        result["decisions"] = [
            dict(r)
            for r in connection.execute(
                "SELECT * FROM review_decision WHERE claim_id=? ORDER BY claim_revision",
                (claim_id,),
            )
        ]
        return result


def adjudicate(
    directory,
    claim_id,
    *,
    verdict,
    reviewer,
    basis,
    expected_claim_revision,
    expected_bindings,
    document_id=None,
    checkpoint=None,
) -> dict:
    if verdict not in {"accept", "reject", "defer", "revoke"}:
        raise RegistryError("unsupported review verdict")
    if not all(isinstance(v, str) and v.strip() for v in (reviewer, basis)):
        raise RegistryError("reviewer and basis must be explicit nonempty strings")
    if (
        type(expected_claim_revision) is not int
        or expected_claim_revision < 0
        or not isinstance(expected_bindings, dict)
        or any(
            not isinstance(k, str) or type(v) is not int or v < 0
            for k, v in expected_bindings.items()
        )
    ):
        raise RegistryError("explicit integer expected revisions are required")
    if verdict != "accept" and document_id is not None:
        raise RegistryError("only acceptance selects a document")
    with open_registry(directory) as connection, transaction(connection):
        claim = connection.execute(
            "SELECT * FROM identity_claim WHERE claim_id=?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise RegistryError("unknown claim")
        if claim["revision"] != expected_claim_revision:
            raise Conflict("stale claim revision")
        members = [claim["assertion_a"]] + ([claim["assertion_b"]] if claim["assertion_b"] else [])
        if set(expected_bindings) != set(members):
            raise Conflict("expected binding revisions must cover every claim assertion")
        bindings = {
            key: connection.execute(
                "SELECT * FROM effective_assertion_binding WHERE assertion_id=?", (key,)
            ).fetchone()
            for key in members
        }
        if any(bindings[key]["revision"] != expected_bindings[key] for key in members):
            raise Conflict("stale binding revision")
        existing = {row["document_id"] for row in bindings.values() if row["document_id"]}
        changed = []
        if verdict == "accept":
            if len(existing) > 1:
                raise Conflict("coalescing two already-bound documents is forbidden")
            if existing and document_id not in existing:
                raise Conflict("an existing binding requires explicit matching document approval")
            if document_id is not None:
                document = connection.execute(
                    "SELECT corpus, kind FROM document WHERE document_id=?", (document_id,)
                ).fetchone()
                if document is None or tuple(document) != (claim["corpus"], claim["kind"]):
                    raise Conflict("target document is missing or belongs to another corpus/kind")
            else:
                document_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO document VALUES (?,?,?,?)",
                    (document_id, claim["corpus"], claim["kind"], now()),
                )
            changed = [key for key in members if bindings[key]["document_id"] is None]
        elif verdict == "revoke":
            for key, row in bindings.items():
                if row["document_id"]:
                    owner = connection.execute(
                        "SELECT claim_id FROM review_decision WHERE decision_id=?",
                        (row["last_decision_id"],),
                    ).fetchone()
                    if owner and owner[0] == claim_id:
                        changed.append(key)
            if not changed:
                raise Conflict("this claim owns no active binding to revoke")
        decision_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO review_decision VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision_id,
                claim_id,
                claim["corpus"],
                claim["kind"],
                verdict,
                reviewer,
                basis,
                expected_claim_revision,
                expected_claim_revision + 1,
                canonical(expected_bindings),
                document_id,
                now(),
            ),
        )
        for key in changed:
            result = connection.execute(
                "UPDATE effective_assertion_binding SET document_id=?, revision=revision+1, "
                "last_decision_id=? WHERE assertion_id=? AND revision=?",
                (document_id, decision_id, key, expected_bindings[key]),
            )
            if result.rowcount != 1:
                raise Conflict("stale binding revision")
            if checkpoint:
                checkpoint("binding")
        return {
            "decision_id": decision_id,
            "claim_revision": expected_claim_revision + 1,
            "document_id": document_id,
            "changed_bindings": len(changed),
        }
