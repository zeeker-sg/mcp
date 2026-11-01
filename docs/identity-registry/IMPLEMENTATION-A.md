# Increment A development and operator contract

Status: synthetic development authorized on 1 November 2026. This is a local
implementation of the published v0.3 plan, incorporating the 31 October adversarial
review. It is not M0 approval, M6 acceptance, public release, or authorization for B.
The original PRD and planning document retain their publication chronology.

## Scope and boundary

`zeeker-identity` is a separately invoked private CLI using only Python's standard
library. It shares the Python distribution with the connector but the MCP server
does not import, initialize, mount or expose it. The six existing tool contracts
remain unchanged. No public IDs, passages, version histories, observation or
extraction-receipt tables, automatic merges, public projection or resolver are added.

The scoped charter exception permits this CLI's private writes; it does not add
server writes. Shipping CLI code is distinct from shipping registry state. The
wheel/sdist and Docker context exclude state artifacts; state is also rejected
inside any Git repository. No production files or services need changing to run
synthetic development tests.

## Open real-data and acceptance gates

The user/operator must record these before real use:

1. Metadata-use basis, exact approved source tables/columns, retention and who may
   run the CLI, inspect evidence or retain backup/contract artifacts.
2. An approved private storage boundary with actual serving-user/mount isolation.
   Owner-only files on a developer machine do not prove that live boundary.
3. A fixture with custody/provenance and pinned digests: recover the historical
   artifact or explicitly approve a replacement/rebaseline. Matching aggregate
   counts in an available backup does not establish historical equality.
4. Accepted recovery RPO/RTO and failure model. Independently durable replay is not
   implemented; zero-loss disk-loss recovery beyond a backup is not promised.
5. Real identity adjudication and independent SQL/final acceptance reviewers.
   Hermes is a prior contributor and a bounded advisory reviewer, not final acceptance.

The old named Horizon fixture was corrected during review. Available real metadata
is not adjudicated identity truth. Until fixture annotation/custody is approved,
the tests use only synthetic shared-party/proceeding, padding and collision cases.
They do not fabricate a historical corpus or assert real legal identity labels.

## Private paths and commands

Every stateful command requires `--registry-dir` or `ZEEKER_REGISTRY_DIR`; there is
no default path. The parent directory must exist. The CLI creates its dedicated
child with owner-only access and new database/backup files with owner-only access;
it refuses permissive existing directories rather than changing permissions.
Repository paths, symbolic-link state files/directories and shared roots are rejected.
Choosing an explicit path does not approve serving isolation or data use.

After the corresponding operator approvals, the workflow is:

```sh
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" init
zeeker-identity source-manifest --source "$APPROVED_SOURCE" \
  --source-id approved-logical-source --generation approved-generation \
  --corpus judgments --table judgments \
  --columns id,citation,case_name,case_numbers,decision_date,court,source_url
```

`source-manifest` is read-only and emits a **proposed**, private JSON contract with
`approval_reference: null`. It grants no rights or custody approval. An operator
must retain the policy record and reference it explicitly before import. A nonempty
reference is an auditable pointer supplied by the operator, not automated proof of
legal permission. Do not fill it in to bypass an open gate.

```sh
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" import \
  --source "$APPROVED_SOURCE" --contract "$APPROVED_CONTRACT"
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" candidates \
  --snapshot "$SNAPSHOT_ID"
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" claims --snapshot "$SNAPSHOT_ID"
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" inspect --claim "$CLAIM_ID"
```

`inspect` returns original evidence and exact expected claim/binding revisions.
The private `review` command requires claim ID, verdict, reviewer, basis,
`--claim-revision` and `--expected-bindings` (a JSON assertion-ID/revision map).
Acceptance optionally names an explicitly approved existing `--document-id`;
otherwise it creates a new random private identity only when all members are unbound.
Reject/defer preserve existing assignments. Revocation affects only bindings owned
by that claim, appends history and retains monotonically revised unresolved tombstones.
Conflicting, stale and two-bound-document coalescing operations roll back atomically.
Raw SQL writes/DDL maintenance are unsupported; schema drift is refused on open.
Schema revision 2 refuses revision 1 files and does not upgrade or relabel their
old provenance automatically. Preserve old files/history for review. Synthetic
revision 2 evaluation uses a new dedicated private directory; migration of prior
state requires a separate reviewed design. No existing registry was migrated as
part of this implementation repair.
SQLite's own internal statistics tables are not extra logical registry tables.
If initialization was interrupted leaving an empty file, commands refuse it rather
than silently initializing it. Preserve and report it; an operator must verify that
it is empty and belongs to the failed initialization before removing/reinitializing.

`report` emits deterministic inventory counts. These CLI outputs and contracts are
private review artifacts, never CI/publication inputs for real data.

## Source contract and preservation

The importer accepts only the selected ordinary PK-less rowid table layouts:
`judgments.judgments` and `pdpc.enforcement_decisions` (logical corpus/table names).
This is a bounded current contract, not a claim that declared PKs are unsafe. Any
broader layout or nullable-PK policy needs explicit review and preservation fixtures.

Selection must use the code's fixed metadata allowlist. Judgment fields are legacy
ID, citation, case name, case numbers, decision date, court, source URL and PDF URL.
The PDPC menu contains only ID/title/organisation/decision/date/URL metadata; the
actual subset and source spelling still require operator approval. Bodies, caches,
summaries, fragments and extraction receipts cannot be selected.

Contract version 2 pins logical source ID, generation label, corpus/kind, table,
selected columns, the complete fixed metadata allowlist and its revision, locator
layout, rollback journal mode, schema digest, metadata digest, selected-input digest,
row count, canonicalization revision and approval reference. Generation/source IDs
are operator labels, not publisher-version observations or custody proof.

**Explicit provenance revision (synthetic development, 1 November 2026):** the
selected-input digest is SHA-256 of canonical selected metadata read in one SQLite
read transaction. It is **not an original whole-source-file digest**. The prior
contract paired a separate pathname checksum with SQL reads; parent-directory ABA
could make those refer to different files. Version 2 removes `file_digest`, rejects
version 1 contracts, and renames the registry column to `selected_input_digest`.
It makes no whole-file assertion about old artifacts. The project lead approved
this bounded development change; real fixture/custody approval and independent
final acceptance remain open. Requirements that need whole-file identity still
need a separately reviewed acquisition design.

The `selected-input-json-v2` canonical payload contains corpus/kind/table, sorted
selected columns, sorted complete allowed columns, `metadata-allowlist-v1`, locator
layout, journal mode, schema digest, metadata digest, row count, and rows in ascending
rowid order as `[string_locator, metadata_object]`. Canonical JSON uses sorted object
keys, compact separators, UTF-8 without ASCII escaping, JSON scalar types and no
NaN/Infinity or Unicode normalization. The schema digest hashes canonical
`[CREATE_TABLE_SQL, PRAGMA_table_info_rows]`; the metadata digest hashes each
`[integer_rowid, metadata_object]` plus LF in ascending rowid order. Hashes are computed
from the same connection/transaction as the rows subsequently imported. Candidate
normalization happens later and records its distinct revision, court allowlist and
implementation/URL-rule digests in the immutable alias namespace; it is not source
metadata preprocessing.

Pinning is deliberately a two-stage operator process: acquire and independently
review a proposed manifest against the trusted selected fixture and approved scope;
retain that manifest and the approval record before import. Import reads the selected
input again and checks **every** acquisition field, including the approved
selected-input digest, before opening a registry write transaction. A mismatch
aborts without writes; import never regenerates a manifest or approves the current
path contents. Merely running `source-manifest` on the current path and filling in
an approval pointer does not establish trust, custody or legal rights. If acquisition
itself encounters substitution, its digest honestly describes the selected rows
actually read, not an earlier file at that path; independent operator review is
what authorizes that input. Metadata-equal replacements and changes only to unselected
bodies are outside this digest's claim and do not prove whole-file equality.

WAL/sidecar inputs require separately authorized consistent acquisition; this CLI
refuses them instead of copying a live source. No source bodies or full databases
are copied into registry state, build artifacts or an acquisition cache.

All selected values and duplicate rows survive exactly; snapshot-local rowids are
never reused as durable identities across generations. Reimport is a no-op for
assertions, claims, decisions and bindings. Reusing a source/generation with a
different contract fails. Interrupted imports roll back rather than leaving a
partially registered snapshot. The importer never copies a full source DB into
the registry and does not modify the source.
Reimport preserves the first stored approval reference. Changing only that pointer
does not amend the immutable manifest or record a new approval/policy event.

## Ten-table schema map

| Table | Fields/invariant supplied |
|---|---|
| `schema_migration` | Version and DDL digest; actual schema objects are compared on open |
| `corpus_allowed_kinds` | Explicit corpus/kind pairs |
| `alias_namespace` | Corpus/kind, normalizer revision and immutable implementation/namespace definition |
| `legacy_snapshot` | Logical source/table/generation, selected-input/schema/metadata digests, manifest and import time |
| `source_resource` | Exact original/representation URLs and source ownership; never legal identity |
| `legacy_row_assertion` | One snapshot-local locator and exact selected metadata per row; composite ownership FKs |
| `document` | Random opaque private identity, corpus/kind and creation time; no public ID |
| `identity_claim` | Individual/pair evidence, namespace and audited claim revision |
| `review_decision` | Append-only verdict/reviewer/basis, expected revisions, selected target and time |
| `effective_assertion_binding` | Assertion-global assignment/tombstone, monotonic revision and last decision |

All key components are explicitly NOT NULL; supported connections and transactions
verify foreign keys and recursive triggers. BEFORE INSERT guards reject existing
primary and alternate unique keys on every logical table, independently of recursive
triggers, preventing REPLACE's implicit-delete bypass on alternate connections.
Supported idempotent operations select existing keys instead of relying on OR IGNORE.
Immutable history/schema objects and audited transitions have database backstops;
supported review operations also validate semantic ownership in one transaction.

## Candidate versions and limits

`sg-neutral-v2` binds citation-normalizer and URL-rule implementations. Revision 1
is no longer executable; existing artifacts are not reinterpreted as revision 2.
It accepts bracketed four-ASCII-digit years, known distinct ASCII court designators
and positive one-to-six-ASCII-digit sequence numbers. Its narrow citation variants
are U+0020 spacing, ASCII court case, numeric zero padding and missing court/number
spacing; original strings remain intact. Unicode digits/whitespace and controls
are refused. URL input must be space-free printable ASCII before `urlsplit`;
TAB/CR/LF, leading C0/space and DEL cannot be stripped into an accepted URL.
SGHC(I), SGHC(A), SGHC and SGCA(I) are never collapsed. URL proposals use only
documented HTTPS eLitigation GD slugs with numeric padding variants; names and
proceedings do not independently create pair proposals. PDPC currently gets
individual review proposals, not judgment citation resolution.

Generation never binds assertions or creates documents. Repeating a revision is
idempotent; a changed definition under the same revision is refused. An explicit
supported new normalizer revision creates new evidence while retaining earlier
claims/decisions. A run exceeding 10,000 pair proposals fails atomically rather
than constructing an unbounded quadratic inventory or silently truncating it.

## Backup and recovery limits

```sh
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" backup --name checkpoint
zeeker-identity --registry-dir "$ZEEKER_REGISTRY_DIR" restore-copy \
  --backup checkpoint --name separate-validation
```

Backups are consistent SQLite copies inside the private boundary. Restore copies
go only to a new `restores` child, are integrity/schema checked and never overwrite
the authoritative file or existing artifacts. There is no promotion command.
Inspect the old copy without discarding decisions made since its backup. Recovering
post-backup decisions after loss of their only record requires an independently
durable design and separate approval; refusal of destructive replacement alone
does not close the operator's recovery policy gate.

## Verification and handoff

Run the synthetic suite and unchanged offline regression suite:

```sh
uv run pytest -m "not live" -p no:xdist
uv run ruff check src/zeeker_identity tests/identity
```

The opt-in native Docker context test additionally uses an **already available**
local daemon and exports a `FROM scratch` filesystem tar, with network disabled,
no RUN commands, no containers and no publication:

```sh
ZEEKER_DOCKER_CONTEXT=1 uv run pytest tests/identity -q
```

Wheel/sdist canaries, dependency-free installed CLI, server import/access denial,
six-tool schema equality, exact multiset/idempotency, parser negatives, namespace
drift, concurrency, ABA and synthetic transaction interruptions are development
checks. They do not establish real-corpus preservation, serving-user isolation,
human workflow throughput, recovery policy acceptance or M6 acceptance. Milestone
handoffs must distinguish passed, failed, skipped, blocked and unrun checks and
include exact code/test artifact digests. Source-content/version/receipt work and
all B functionality remain out of scope.
