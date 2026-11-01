"""Increment A's ten logical tables; no content, public IDs or evidence receipts."""

VERSION = "1"
TABLES = frozenset(
    {
        "schema_migration",
        "corpus_allowed_kinds",
        "alias_namespace",
        "legacy_snapshot",
        "source_resource",
        "legacy_row_assertion",
        "document",
        "identity_claim",
        "review_decision",
        "effective_assertion_binding",
    }
)

DDL = """
CREATE TABLE schema_migration (
    version TEXT NOT NULL PRIMARY KEY,
    digest TEXT NOT NULL
);
CREATE TABLE corpus_allowed_kinds (
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (corpus, kind)
);
INSERT INTO corpus_allowed_kinds VALUES ('judgments', 'judgment');
INSERT INTO corpus_allowed_kinds VALUES ('pdpc', 'enforcement_decision');
CREATE TABLE alias_namespace (
    namespace_id TEXT NOT NULL PRIMARY KEY,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    normalizer_revision TEXT NOT NULL,
    definition TEXT NOT NULL CHECK (json_valid(definition)),
    UNIQUE (namespace_id, corpus, kind),
    FOREIGN KEY (corpus, kind) REFERENCES corpus_allowed_kinds (corpus, kind)
);
CREATE TABLE legacy_snapshot (
    snapshot_id TEXT NOT NULL PRIMARY KEY,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    generation TEXT NOT NULL,
    file_digest TEXT NOT NULL,
    schema_digest TEXT NOT NULL,
    metadata_digest TEXT NOT NULL,
    manifest TEXT NOT NULL CHECK (json_valid(manifest)),
    imported_at TEXT NOT NULL,
    UNIQUE (corpus, kind, source_id, source_table, generation),
    UNIQUE (snapshot_id, corpus, kind),
    FOREIGN KEY (corpus, kind) REFERENCES corpus_allowed_kinds (corpus, kind)
);
CREATE TABLE source_resource (
    resource_id TEXT NOT NULL PRIMARY KEY,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    original_url TEXT,
    representation_url TEXT,
    UNIQUE (resource_id, corpus, kind),
    FOREIGN KEY (corpus, kind) REFERENCES corpus_allowed_kinds (corpus, kind)
);
CREATE TABLE legacy_row_assertion (
    assertion_id TEXT NOT NULL PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    locator TEXT NOT NULL,
    metadata TEXT NOT NULL CHECK (json_valid(metadata) AND json_type(metadata) = 'object'),
    UNIQUE (snapshot_id, locator),
    UNIQUE (assertion_id, corpus, kind),
    FOREIGN KEY (snapshot_id, corpus, kind)
        REFERENCES legacy_snapshot (snapshot_id, corpus, kind),
    FOREIGN KEY (resource_id, corpus, kind)
        REFERENCES source_resource (resource_id, corpus, kind)
);
CREATE TABLE document (
    document_id TEXT NOT NULL PRIMARY KEY,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (document_id, corpus, kind),
    FOREIGN KEY (corpus, kind) REFERENCES corpus_allowed_kinds (corpus, kind)
);
CREATE TABLE identity_claim (
    claim_id TEXT NOT NULL PRIMARY KEY,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    namespace_id TEXT NOT NULL,
    assertion_a TEXT NOT NULL,
    assertion_b TEXT,
    reason TEXT NOT NULL CHECK (reason IN ('individual_review', 'neutral_citation', 'url_variant')),
    evidence TEXT NOT NULL CHECK (json_valid(evidence)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (typeof(revision) = 'integer' AND revision >= 0),
    created_at TEXT NOT NULL,
    CHECK (assertion_b IS NULL OR assertion_b > assertion_a),
    UNIQUE (claim_id, corpus, kind),
    FOREIGN KEY (namespace_id, corpus, kind)
        REFERENCES alias_namespace (namespace_id, corpus, kind),
    FOREIGN KEY (assertion_a, corpus, kind)
        REFERENCES legacy_row_assertion (assertion_id, corpus, kind),
    FOREIGN KEY (assertion_b, corpus, kind)
        REFERENCES legacy_row_assertion (assertion_id, corpus, kind)
);
CREATE TABLE review_decision (
    decision_id TEXT NOT NULL PRIMARY KEY,
    claim_id TEXT NOT NULL,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('accept', 'reject', 'defer', 'revoke')),
    reviewer TEXT NOT NULL CHECK (length(trim(reviewer)) > 0),
    basis TEXT NOT NULL CHECK (length(trim(basis)) > 0),
    expected_claim_revision INTEGER NOT NULL CHECK (typeof(expected_claim_revision) = 'integer'),
    claim_revision INTEGER NOT NULL CHECK (typeof(claim_revision) = 'integer'),
    expected_bindings TEXT NOT NULL
        CHECK (json_valid(expected_bindings) AND json_type(expected_bindings) = 'object'),
    target_document_id TEXT,
    created_at TEXT NOT NULL,
    CHECK ((verdict = 'accept' AND target_document_id IS NOT NULL)
        OR (verdict != 'accept' AND target_document_id IS NULL)),
    CHECK (claim_revision = expected_claim_revision + 1),
    UNIQUE (claim_id, claim_revision),
    UNIQUE (decision_id, corpus, kind),
    FOREIGN KEY (claim_id, corpus, kind) REFERENCES identity_claim (claim_id, corpus, kind),
    FOREIGN KEY (target_document_id, corpus, kind) REFERENCES document (document_id, corpus, kind)
);
CREATE TABLE effective_assertion_binding (
    assertion_id TEXT NOT NULL PRIMARY KEY,
    corpus TEXT NOT NULL,
    kind TEXT NOT NULL,
    document_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (typeof(revision) = 'integer' AND revision >= 0),
    last_decision_id TEXT,
    CHECK ((revision = 0 AND document_id IS NULL AND last_decision_id IS NULL)
        OR (revision > 0 AND last_decision_id IS NOT NULL)),
    FOREIGN KEY (assertion_id, corpus, kind)
        REFERENCES legacy_row_assertion (assertion_id, corpus, kind),
    FOREIGN KEY (document_id, corpus, kind) REFERENCES document (document_id, corpus, kind),
    FOREIGN KEY (last_decision_id, corpus, kind)
        REFERENCES review_decision (decision_id, corpus, kind)
);
CREATE INDEX claim_assertion_a ON identity_claim(assertion_a);
CREATE INDEX claim_assertion_b ON identity_claim(assertion_b);
CREATE TRIGGER decision_cas BEFORE INSERT ON review_decision BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM identity_claim c
        WHERE c.claim_id = NEW.claim_id AND c.corpus = NEW.corpus AND c.kind = NEW.kind
          AND c.revision = NEW.expected_claim_revision
    ) THEN RAISE(ABORT, 'stale claim revision') END;
    SELECT CASE WHEN (
        SELECT count(DISTINCT key) FROM json_each(NEW.expected_bindings)
    ) != (SELECT 1 + (assertion_b IS NOT NULL) FROM identity_claim WHERE claim_id = NEW.claim_id)
    OR (SELECT count(*) FROM json_each(NEW.expected_bindings)) != (
        SELECT count(DISTINCT key) FROM json_each(NEW.expected_bindings)
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.expected_bindings) j
        LEFT JOIN effective_assertion_binding b ON b.assertion_id = j.key
        JOIN identity_claim c ON c.claim_id = NEW.claim_id
        WHERE j.type != 'integer' OR b.assertion_id IS NULL OR b.revision != j.value
           OR (j.key != c.assertion_a AND j.key IS NOT c.assertion_b)
    ) THEN RAISE(ABORT, 'stale or incomplete binding revisions') END;
    SELECT CASE WHEN NEW.verdict = 'accept' AND EXISTS (
        SELECT 1 FROM effective_assertion_binding b JOIN identity_claim c
            ON b.assertion_id IN (c.assertion_a, c.assertion_b)
        WHERE c.claim_id = NEW.claim_id AND b.document_id IS NOT NULL
          AND b.document_id != NEW.target_document_id
    ) THEN RAISE(ABORT, 'acceptance contradicts an existing binding') END;
    SELECT CASE WHEN NEW.verdict = 'revoke' AND NOT EXISTS (
        SELECT 1 FROM effective_assertion_binding b JOIN review_decision prior
            ON prior.decision_id = b.last_decision_id
        WHERE prior.claim_id = NEW.claim_id AND b.document_id IS NOT NULL
          AND prior.verdict = 'accept'
    ) THEN RAISE(ABORT, 'claim owns no binding to revoke') END;
END;
CREATE TRIGGER claim_revision_guard BEFORE UPDATE ON identity_claim BEGIN
    SELECT CASE WHEN NEW.claim_id != OLD.claim_id OR NEW.corpus != OLD.corpus
        OR NEW.kind != OLD.kind OR NEW.namespace_id != OLD.namespace_id
        OR NEW.assertion_a != OLD.assertion_a OR NEW.assertion_b IS NOT OLD.assertion_b
        OR NEW.reason != OLD.reason OR NEW.evidence != OLD.evidence
        OR NEW.created_at != OLD.created_at OR NEW.revision != OLD.revision + 1
        OR NOT EXISTS (SELECT 1 FROM review_decision d
            WHERE d.claim_id = OLD.claim_id AND d.claim_revision = NEW.revision)
        THEN RAISE(ABORT, 'claims are immutable except audited revisions') END;
END;
CREATE TRIGGER decision_advance AFTER INSERT ON review_decision BEGIN
    UPDATE identity_claim SET revision = NEW.claim_revision WHERE claim_id = NEW.claim_id;
END;
CREATE TRIGGER binding_initial BEFORE INSERT ON effective_assertion_binding BEGIN
    SELECT CASE WHEN NEW.revision != 0 OR NEW.document_id IS NOT NULL
        OR NEW.last_decision_id IS NOT NULL THEN RAISE(ABORT, 'binding must start unresolved') END;
END;
CREATE TRIGGER binding_change BEFORE UPDATE ON effective_assertion_binding BEGIN
    SELECT CASE WHEN NEW.assertion_id != OLD.assertion_id OR NEW.corpus != OLD.corpus
        OR NEW.kind != OLD.kind OR NEW.revision != OLD.revision + 1
        OR NEW.last_decision_id IS OLD.last_decision_id OR NOT EXISTS (
            SELECT 1 FROM review_decision d JOIN identity_claim c ON c.claim_id = d.claim_id
            WHERE d.decision_id = NEW.last_decision_id AND d.corpus = NEW.corpus
              AND d.kind = NEW.kind AND OLD.assertion_id IN (c.assertion_a, c.assertion_b)
              AND EXISTS (SELECT 1 FROM json_each(d.expected_bindings) j
                          WHERE j.key = OLD.assertion_id AND j.value = OLD.revision)
              AND ((d.verdict = 'accept' AND OLD.document_id IS NULL
                    AND NEW.document_id = d.target_document_id)
                OR (d.verdict = 'revoke' AND OLD.document_id IS NOT NULL
                    AND NEW.document_id IS NULL AND EXISTS (
                        SELECT 1 FROM review_decision prior
                        WHERE prior.decision_id = OLD.last_decision_id
                          AND prior.claim_id = d.claim_id AND prior.verdict = 'accept')))
        ) THEN RAISE(ABORT, 'binding requires an audited monotonic transition') END;
END;
"""

# Core provenance and review history are append-only even through a raw SQL connection.
for _table in sorted(TABLES - {"identity_claim", "effective_assertion_binding"}):
    for _action in ("UPDATE", "DELETE"):
        DDL += f"""
CREATE TRIGGER immutable_{_table}_{_action.lower()} BEFORE {_action} ON {_table} BEGIN
    SELECT RAISE(ABORT, 'immutable record');
END;
"""
for _table in ("identity_claim", "effective_assertion_binding"):
    DDL += f"""
CREATE TRIGGER retain_{_table} BEFORE DELETE ON {_table} BEGIN
    SELECT RAISE(ABORT, 'history and binding tombstones must be retained');
END;
"""
