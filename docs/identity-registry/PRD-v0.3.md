# Zeeker MCP v2 — Identity Registry and Judgment Resolution

**PRD v0.3 — Engineering Candidate**  
**Date:** 31 October 2026  
**Status:** Ready for engineering breakdown; production changes require separate authorization  
**Basis:** Zeeker MCP v2 feasibility review, Gate A evidence response, and private identity/versioning technical design

Editorial publication draft, not a verbatim copy of the attachment: review wording
against the authoritative supplied PRD before publication. The environment-specific deployment location is replaced
by an explicit portable registry-directory setting. No other PRD scope is expanded.
The companion delivery plan resolves implementation ambiguities and separates
Increment A requirements from deferred evidence-versioning requirements.

## 1. Objective

Enable AI agents to reliably resolve Singapore legal references to specific decisions,
while establishing a private identity foundation that can later support versioned
passages, provenance and change tracking.

The first user-visible capability is:

`resolve_judgment(citation="[2026] SGHC 119")`

The service should find the intended judgment, return verified metadata and source
links, and explicitly identify ambiguity or incomplete coverage rather than guessing.
The initial implementation must not depend on building complete publisher-version history.

## 2. Delivery scope

### Increment A — Private identity registry

Create a private, metadata-only registry that:

- Imports existing judgments and relevant PDPC metadata without losing duplicate records.
- Maintains source resources and their original identifiers.
- Generates conservative identity and alias candidates.
- Allows explicit human adjudication, rejection and correction.
- Maintains auditable effective bindings between source assertions and logical documents.
- Produces deterministic reports and passes identity, snapshot and isolation tests.

**Deliverable:** A working, tested, private registry and command-line review interface.
No public endpoint. **Planning estimate:** 5–8 engineer-days, subject to prerequisites.

### Increment B — Judgment metadata resolver

Using the reviewed registry, implement:

- A separate allowlisted public metadata projection.
- Exact neutral-citation lookup and approved citation variants.
- Explicit ambiguous, unresolved and no-match outcomes.
- An additive, read-only `resolve_judgment` MCP tool.
- Scope, coverage, provenance and execution-status reporting.
- Compatibility tests against existing supported clients.

**Deliverable:** A legal-native retrieval capability, available only after the public
release gate. Estimate B separately after A establishes actual identity and review costs.

### Deferred

Source and extraction version histories, passage delivery, PDPC text retrieval,
change feeds, cross-source citation graphs, fuzzy case-name resolution, automated
document merges and FastMCP 4 migration. These are not implicit prerequisites for A.

## 3. Core identity rules

1. A document represents an individual legal decision, not a proceeding, URL, PDF or row.
2. A source resource represents a publisher endpoint or specific source representation.
3. A legacy assertion represents exactly one row in an identified database snapshot.
4. A citation alias is evidence about a document, not independent proof of equivalence.
5. Private document IDs are random opaque identifiers. A allocates no public ID.
6. URLs, names, proceedings and content equality cannot independently trigger a merge.
7. Accepted decisions are auditable and reversible through append-only decisions with revision checks.
8. Conflicts remain unresolved until explicitly adjudicated.

An assertion has at most one effective document binding across all claims involving it.

## 4. Private data model

| Table | Responsibility |
|---|---|
| `schema_migration` | Migration version and digest |
| `corpus_allowed_kinds` | Permitted corpus/document-kind combinations |
| `alias_namespace` | Controlled citation and identifier namespaces |
| `legacy_snapshot` | Pinned snapshot and generation manifest |
| `legacy_row_assertion` | Every row, with snapshot-local locator |
| `source_resource` | Original source endpoint and representation |
| `document` | Private logical decision identity |
| `identity_claim` | Candidate relationship and evidence |
| `review_decision` | Append-only adjudication/correction history |
| `effective_assertion_binding` | Current assignment, keyed by assertion ID |

### Invariants

- Text primary keys are explicitly `TEXT NOT NULL PRIMARY KEY`.
- Foreign keys are enabled and verified on every connection.
- Snapshot, resource and assertion corpus agree through composite constraints.
- Document kind is permitted for its corpus.
- Legacy IDs, URLs, citations and case names are not globally unique.
- Effective bindings are keyed by `assertion_id` alone.
- Reviewer changes use expected revisions and atomic transactions.
- Conflicting assignments, stale revisions and coalescing two bound different documents fail.
- Public IDs are absent or constrained to NULL, with no allocator.

Incorporate the applicable technical-design corrections; do not run the older
illustrative SQL unchanged (design draft; not ready to run). The companion plan maps A-relevant corrections and
records deferred evidence-specific corrections without adding their tables to A.

## 5. Snapshot importer

Perform a consistent read of selected metadata without modifying source databases:

- Record database identity, generation, schema and manifest.
- Preserve every row, including duplicated legacy identifiers.
- Use rowids only as snapshot-local locators when there is no declared primary key.
- Preserve exact strings separately from normalized derivatives.
- Do not copy protected bodies or caches into the registry.
- Reimport the same pinned snapshot idempotently.
- Fail explicitly on unexpected schema or generation.

A later snapshot must not reuse rowids as durable document identities. Handle the
judgment corpus and enough PDPC metadata to validate identity and corpus boundaries.

## 6. Candidates and adjudication

Implement a versioned Singapore neutral-citation parser with exact citations and a
narrow documented set of formatting variants. Keep court designators distinct,
including `SGHC(I)`. Retain original citations and normalizer revisions.

Candidates may use exact citations, approved formatting normalization, narrow
documented publisher URL variants, and supporting court/date/source metadata.
Names and proceedings are contextual evidence only.

Provide a minimal CLI to inspect evidence and accept, reject or defer a proposal.
Record reviewer, verdict, basis, revision and timestamp. Keep contradictions visible.
Do not automatically merge decisions or select a preferred public URL.

## 7. Public resolver contract

B adds one read-only MCP tool without changing the six existing tools.

Input: `citation`, initially a Singapore neutral citation.

Output:

- `resolution_status`: `resolved`, `ambiguous`, `unresolved`, `no_match`.
- `query_status`: `complete`, `partial`, `unavailable`.
- `candidates`: approved public metadata.
- `coverage`: indexed scope, snapshot basis and exclusions.
- `provenance`: source and retrieval information.
- `contract_version`.

Candidates contain citation, case name, court, decision date, source kind and approved
source URLs. Include `primary_source_url` only when verified.

An unqualified unique resolution or no-match result requires a successfully completed
relevant scope and supporting identity evidence. Partial results may include candidates
but must not imply uniqueness or corpus-wide absence.

Build the projection from an empty SQLite file using explicit DDL and named allowlisted
columns. Do not copy the private DB and delete tables. Exclude private IDs, raw content,
hashes, reviewer identities/notes, paths, observations and internal scores. Before
exposure verify schema, values, ambiguity, file digest and generation manifest.

## 8. Privacy and infrastructure

Require an explicit `--registry-dir` or `ZEEKER_REGISTRY_DIR` pointing to an
operator-approved private location. Do not publish its concrete deployment value.
Do not default to a repository, served directory or shared temporary directory.

The first milestone has:

- No new public endpoint, MCP/Datasette mount or credentials.
- No collector or served-database schema changes.
- No current-public-export changes.
- Private DB, temporary files, journals, backups and manifests within the approved boundary.
- Explicit filesystem and serving-user isolation tests.

Store any later public projection separately and publish only after exposure tests pass.

## 9. Acceptance tests

### Identity

- Preserve 10,996 judgment rows and the 15 duplicate-ID/16 duplicate-citation groups
  in the historical pinned fixture after recovery and re-verification, or use an
  explicitly approved rebaseline. Fixture availability is not yet verified;
  these are not a live ingestion target.
- Keep the two `[2015] SGHCR 2` URLs as a candidate, not an automatic merge.
- Keep different decisions in `SIC/S 4/2017` separate.
- Keep repeated Horizon Fast Ferry decisions separate.
- Reject stale revisions, conflicting bindings, NULL text IDs and invalid corpus/kind references.
- Preserve source assertions after correction.

### Import and durability

- Idempotent import; no deletion or deduplication; no cross-snapshot rowid identity.
- Deterministic migrations/manifests; recover interrupted imports.
- Verified backup/recovery without deleting newer authoritative decisions.

### Privacy

- Registry absent from public mounts/build contexts; canaries absent from outputs.
- No private fields in exported schemas; failed generation preserves approved output.
- No behavior change to the six tools.

### Resolver, when B begins

- Exact/approved-alias fixtures succeed; ambiguity never becomes unqualified uniqueness.
- Incomplete lookup never becomes unqualified absence; no private response fields.
- Supported-client tests pass; task-level comparison precedes release.

## 10. Sequence

1. Foundations: boundary, package structure, migration runner, connections, synthetic harness.
2. Registry migration: integrated schema and negative tests.
3. Importer: consistent metadata snapshots, manifests, idempotency.
4. Identity: parser, candidate generation and collision fixtures.
5. Review: append-only adjudication, effective-binding transactions and concurrency tests.
6. Checkpoint: preservation, privacy, durability, report and demonstration.

A is independently evaluable here. Separately gated B:

7. Approved metadata projection.
8. Additive resolver, output contract, completeness and client evaluation.

Completing Tasks 1–6 does not authorize Tasks 7–8.

## 11. Later versioned evidence

A future separately approved milestone may introduce validated observations,
scoped fingerprints, typed input evidence, extraction receipts, immutable extractor
identities, passage anchors and optional strongly correlated materialization receipts.

A cache import is not a publisher observation; re-extraction is not automatically a
publisher revision; a missing row or failed fetch is not withdrawal. New retention,
fingerprints and public text delivery require use-specific rights decisions.

## 12. Completion

A is done when an isolated registry passes migration, import, identity, adjudication,
preservation, durability and privacy tests; an operator can review real candidates;
and a recovery procedure is documented.

B is done when approved metadata resolution delivers correct scoped results to supported
clients without leaks or changes to existing tools. Neither milestone implies new
public document IDs, passages or source-version guarantees.

**Decision:** Proceed with engineering breakdown and A implementation after storage and
metadata-use prerequisites are recorded. B is separately gated; versioned evidence is
a subsequent workstream. This publication draft does not itself activate production work.
