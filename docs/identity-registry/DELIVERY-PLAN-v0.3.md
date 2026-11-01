# Zeeker MCP v2 — PRD v0.3 delivery plan

**Date:** 31 October 2026  
**Status:** Joint planning recommendation for project-lead review; no implementation or release performed  
**Authority:** The user retains rights decisions, human identity adjudication and production approval unless expressly delegated.

## 1. Destination and publication boundary

Recommended repository: `zeeker-sg/mcp` (public), based on `main` at
`7542f617286c0d9891a5a00e9f5471cf507c7c8d`. Revalidate the base immediately
before creating a branch; do not overwrite intervening work.

Proposed documentation branch: `codex/zeeker-v03-delivery-plan`. Its name was
available when checked. The publication allowlist is only:

- `docs/identity-registry/PRD-v0.3.md`
- `docs/identity-registry/DELIVERY-PLAN-v0.3.md`

Do not publish earlier private reports, illustrative SQL, real registry files,
source snapshots, operational logs, review decisions, internal notes or attachments.
Replace the original environment-specific path with `--registry-dir` or
`ZEEKER_REGISTRY_DIR`, requiring an explicit operator-approved value kept outside Git.
This is a publication adaptation, not an additional infrastructure task.

Review the exact two-file diff before push. A feature-branch push makes its content
public. This planning step drafts locally; push follows project-lead content/privacy
review. No PR or merge is requested. The workflow inspected at the stated base on
31 October 2026 runs on PRs and main pushes, not ordinary feature-branch pushes;
main also builds/publishes an image. Reverify workflow and ruleset state at M0
and before relevant external actions; no current ruleset conclusion is assumed. A docs-only
push must not be reported as having passed CI merely because no check ran.

Future implementation branch: propose `codex/zeeker-registry-a`, rechecked at
assignment time. It is separate from the documentation branch and is not created
or authorized by publishing this plan.

## 2. Scope and package placement

Increment A is a metadata-only registry plus CLI. Exactly ten logical tables:
`schema_migration`, `corpus_allowed_kinds`, `alias_namespace`, `legacy_snapshot`,
`legacy_row_assertion`, `source_resource`, `document`, `identity_claim`,
`review_decision`, `effective_assertion_binding`.

Proposed package: `src/zeeker_identity/`, with a separate CLI entry point and tests
under `tests/identity/`. Prefer existing Python and standard-library `sqlite3` and
`argparse`; no ORM, browser UI, new database service or model dependency is needed.
Package/build metadata changes are reviewed implementation work, not part of this
docs branch.

The existing repository charter assumes a stateless HTTP server with no local
writes. Record a targeted PRD v0.3 exception for the separate private CLI only.
The MCP server must not import or initialize the registry on startup, gain a
registry mount, or change its six-tool contract in A. Do not introduce registry
state into the server image or Docker build inputs.

Out of A and B: publisher/extraction version history, source observations,
fingerprints, typed extraction input, materialization receipts, passage delivery,
PDPC text, changes_since, citation graphs, fuzzy names, automated merges and
FastMCP 4. The earlier 20–34-day strong-receipt design is not this delivery scope.
No collector, served-database or existing-export changes belong to A.

### Technical-correction scope

| Correction | Increment A | Deferred contract |
|---|---|---|
| Effective binding across all claims; stale revisions | Schema plus atomic review transactions | None needed |
| Observation/version source ownership | No observation/version tables | Required before that future milestone |
| Corpus and document-kind agreement | Composite constraints and binding checks | Extend to future evidence records later |
| HTTP success versus valid representation | No network-content collection | Required before future observations |
| Precise typed extraction input | No extraction receipts | Required before future receipts |
| Alias namespace | Controlled namespace and versioned lookup | Carried into B |
| Public-ID gate | No public-ID column or allocator | Any future allocation needs separate scope |
| Explicit NOT NULL text keys | All A text PKs and required key components | Same rule in future schemas |

"Incorporate all corrections" means honor applicable invariants and preserve the
deferred requirements as text. It does not authorize extra tables or hidden work.

## 3. People, agents and authority

| Role | Responsibility | Assignment status |
|---|---|---|
| Project lead — dot | Scope, sequence, delegation, independent-review appointments, eval gates, integration, reporting | Appointed by user |
| Primary implementer — Codex executor | Proposed owner of Tasks 1–6, changes/tests/handoff reports in an isolated worktree | Proposed; dot assigns after prerequisites; no calendar availability assumed |
| Hermes | Public-draft privacy review; PRD/addendum consistency; deferred-scope audit; acceptance-plan review | Accepted for this planning exchange |
| Hermes implementation reviewer | Review a Codex milestone when dot appoints it | Conditional acceptance, re-confirmed per checkpoint; not standing on-call |
| Independent final reviewer | Independently assess design assumptions, preservation, isolation and acceptance evidence | Unassigned; dot appoints; no contact or staffing commitment made |
| Human adjudicator | Approve identity relationships/corrections and approved URL presentation | User unless explicitly delegated |
| Rights/operator authority | Metadata-use basis, private boundary, backup policy and production/release approvals | User; operational availability unknown |

Hermes declines implementation authorship, sole independent final design sign-off,
rights/adjudication/production authority, blanket merge authority and continuous
monitoring. It coauthored the design, so its implementation review cannot substitute
for an independently appointed final design/acceptance reviewer. No other agents
are contacted by this planning work.

## 4. Prerequisite gate M0

Dot records the following before assigning implementation:

1. User/operator-approved private-storage boundary and metadata-use prerequisites,
   including who can run the CLI and retain private snapshots/review records.
2. A recoverable, pinned metadata fixture with schema/generation manifest and digest.
   Historical audit counts are 10,996 judgment rows, 15 duplicated ID groups/30 rows
   and 16 duplicated citation groups/32 rows. These are fixture expectations, not
   current ingestion targets. The complete historical fixture has not yet been
   verified available. Recover an authorized historical artifact and recount it,
   or obtain an explicit rebaseline decision. Never trim live data or fabricate a
   historical snapshot to meet counts.
3. Selected metadata columns, explicit corpus/kind and namespace definitions,
   schema/generation mismatch behavior, and correction semantics below.
4. Independent reviewer appointments and a private evidence channel. Reviewer and
   operator time are unknown, separate from engineering effort.
5. Targeted CLI charter exception, package/branch destination and exact test inventory.

This gate does not change permissions or services. If actual serving-user isolation
cannot be checked with existing authorized controls, request a bounded operator
handoff; do not claim a simulated user test proves the live boundary.

## 5. Increment A milestones and handoffs

| Milestone | Owner and dependency | Required evidence and acceptance |
|---|---|---|
| M1 — Foundations, Task 1 | Codex after M0; Hermes review if appointed | Separate CLI/package, explicit registry-dir handling, migrations/connections, synthetic harness. Registry access absent from server startup; FK-off connections refused; no new serving mounts/endpoints. |
| M2 — Schema, Task 2 | Codex after M1; independent SQL review | Integrated ten-table migration; NOT NULL/FK/corpus/kind negatives; assertion-global effective binding. No public-ID column/allocator; no deferred evidence tables. |
| M3 — Importer, Task 3 | Codex after M2 and pinned fixture | Consistent metadata read, exact row-multiset preservation, nonunique legacy IDs, canonical snapshot-local locators, idempotent repeated import, schema/generation rejection, interrupted-import recovery. Private full-fixture report; public CI uses synthetic/approved fixtures. |
| M4 — Candidates, Task 4 | Codex after M3 | Versioned namespace-aware parser and narrow variant list; documented SGHC(I) behavior; all collision fixtures; deterministic reports. No automatically accepted real bindings, preferred URLs or document merges. |
| M5 — Review CLI, Task 5 | Codex after M2/M4; human workflow review | Inspect/accept/reject/defer/revoke; explicit reviewer/basis/time; expected claim and binding revisions; concurrency conflict; append-only correction; assertions and IDs retained. Real adjudication requires authorized human input. |
| M6 — A checkpoint, Task 6 | Codex assembles; independent reviewer verifies; dot accepts | Full preservation, import, identity, concurrency, durability and privacy report; recovery demonstration; CLI demonstration; unchanged six-tool checks. List passed, failed, skipped and unrun checks separately. |

Every milestone handoff includes exact head SHA/diff, schema/contract decisions,
commands and test results, artifact digests, known limits and a specific reviewer
checklist. Private fixture rows, reviewer details and operational evidence stay out
of public commits, CI logs and artifacts. A skipped mandatory private-corpus or
isolation check blocks A acceptance; green synthetic CI alone is insufficient.

### Review correction semantics

Acceptance binds an unassigned assertion to a new private document or an explicitly
approved existing one. Candidate creation never mints a document automatically.
Rejecting or deferring a new claim leaves an initially unbound assertion unbound;
it does not revoke or alter any existing accepted binding. A later competing
claim's rejection/deferral cannot displace that binding. Only an explicit audited
revocation with the expected binding revision can do so. Revocation appends a decision and returns
the affected binding to unresolved while preserving the old document and history.
Any later acceptance onto another document requires another explicit human decision
and current expected revisions. The sequence accept → revoke → accept remains
auditable. No in-place acceptance overwrite or automatic reassignment is allowed.

Confirming a same-document claim must reject two already-bound different document
IDs; that would be coalescing, not an ordinary correction. The prototype does not
merge document records. A reused URL yields a new snapshot assertion; neither URL
nor rowid silently carries a binding forward. A does not need source-version or
identity-epoch tables to make this rule explicit.

## 6. Increment A eval gates

Freeze the case inventory before milestone verification. Required pass criteria:

- Exact source/registry metadata multiset equality for the pinned fixture, including
  duplicate rows; exact expected duplicate-group counts. No unapproved deduplication.
- Repeat import is a no-op for assertions, claims, decisions and bindings; a new
  snapshot is distinct and does not equate rowids across generations.
- SGHCR URL forms generate a proposal only; distinct proceeding/title fixtures do
  not acquire equivalence; exact original strings remain recoverable.
- All NULL-key, invalid corpus/kind, stale revision, contradictory-claim and
  two-bound-document negative cases reject the write without partial state.
- Two reviewers racing on one expected revision yield one accepted operation and
  one explicit conflict. Correction retains all assertions and prior decisions.
  Rejecting/deferring a competing new claim preserves any existing accepted binding.
- Fault injection at import/migration/review boundaries leaves a recoverable state;
  perform this only in the synthetic test harness, never against production.
  normal rollback preserves newer authoritative IDs/decisions. Destructive recovery
  cannot claim preservation without a durable delta and reconciliation.
- Private paths, journals, snapshots, backups and manifests remain outside serving
  mounts/build contexts; private canaries and fields never enter public outputs.
  Serving-user tests use actual approved controls or remain explicitly blocked.
- Existing six tool schemas/defaults/behavior pass the unchanged offline suite;
  registry code is not imported by server startup. No collector/export changes.

All required cases must pass, with zero unresolved critical correctness/privacy
defects. These are fixture guarantees, not population-level accuracy certification.

## 7. Increment B planning gate and contract

A completion does not authorize B. Dot first returns A's measured review cost,
available approved identities/URLs, proposed client matrix, evaluation budget,
release scope and separate engineering estimate. User approval is required for
public exposure and any necessary deployment change. No stable public document
identifier is required or allocated in B.
B implementation/reviewer assignments remain open; dot appoints them after the
separate scope, estimate and authorization gate rather than assuming A's staffing continues.

Candidates return only citation, case_name, court, decision_date, source_kind,
approved source_urls and an optional verified primary_source_url. Record the
verification/snapshot basis in provenance; do not imply live publisher verification.
An internal generation-local integer join key may exist inside the projection,
but is never returned or promised stable. The older public-ID-based projection SQL
is superseded and must not be used unchanged.

Build from an empty file using explicit allowlists. A generation-scoped,
proposed disposition index covers every parseable judgment citation in the
pinned scope: approved candidate references or an unresolved marker. Publication
of those markers and the citation population itself requires explicit metadata-use
and publication-allowlist approval at B0. Removing private IDs/notes is not approval
or proof of public safety; before approval, export no citation population. This avoids
misreporting unreviewed identities as absent. Markers contain citation/disposition
only, not private evidence, IDs, notes or scores. PDPC is not B's resolver scope.
If approval is withheld or narrower, block the projection or restrict it to the
approved scope. Where the resulting coverage cannot establish absence, return
unresolved/unavailable, never no_match. Gate B approval remains separate.

| Lookup state | query_status | resolution_status |
|---|---|---|
| One approved match, no unresolved collision, completed relevant scope | complete | resolved |
| Multiple materially plausible matches, completed relevant scope | complete | ambiguous |
| Matching unadjudicated/conflicting evidence | complete | unresolved |
| No candidate and no unresolved entry in fully searched supported scope | complete | no_match |
| Any relevant lookup component incomplete | partial | unresolved; candidates permitted |
| Required lookup generation/scope unavailable or unsupported | unavailable | unresolved |

Malformed input is an explicit validation error, never no_match. No unqualified
uniqueness or absence is returned on partial/unavailable results. Coverage names
the generation, supported court/date scope and exclusions; a requested reference
outside that scope is not a clean no_match. Execution completeness is not corpus
or legal completeness.

## 8. Increment B evaluation and handoffs

At B0, an independent reviewer freezes ground truth, scope, metric definitions and
the test split before the implementation is evaluated. Ground truth comes from
approved identity decisions and verified publisher metadata; unresolved real
identities remain unresolved, not guessed labels. Group all variants of a document
within one split. Keep the held-out set out of author-selected tuning fixtures.

Proposed minimum held-out cohorts:

- 100 eligible canonical citations from distinct decisions, stratified by supported
  court, era and format, with reviewed expected metadata.
- 40 approved-variant queries, linked to document groups.
- 30 ambiguity, unresolved, malformed, no-match and injected-failure cases, with each
  class represented. Synthetic true-collision cases are labelled synthetic.

Gate: 100% expected fixture statuses/candidates/metadata and zero private-field,
false-uniqueness or false-absence failures. Report denominators by cohort; do not
describe this as proof of 99% population accuracy. Missing human adjudication
capacity blocks cohort completion rather than licensing automatic ground truth.

Run at least 30 paired metadata-resolution tasks against warmed v1 and B using
the same inputs, pinned source basis and client conditions. No task may regress
in adjudicated correctness; report paired call, payload, p50/p95 latency and cost
results. There is no assumed 25% call-reduction mandate. Any model-driven or paid
evaluation needs an approved runner, data policy and budget before execution.

A <=20% p95 relative-regression budget is a provisional planning value, not an
already validated release threshold. At B0 measure baseline repeatability, choose
numeric budgets and any paired-interval method, then freeze them before unblinding
B. If baseline noise prevents a defensible threshold, return to B0 measurement;
do not waive the performance gate after seeing results.

Proposed client test pins, subject to availability and re-freezing at B0:

| Client | Candidate version/variant |
|---|---|
| Python protocol harness | Existing lockfile: FastMCP 3.4.7 / MCP SDK 1.27.1 |
| Codex | 0.166.0-alpha.5.1, direct and through mcp-remote 0.15.2 |
| Claude Code | 2.1.314 |

These are proposed pins, not tested support claims or staffing availability.
Record actual versions/digests in the B report; unavailable required clients block
the associated support claim. Hosted-client self-reported generic versions are
not reproducible pins. Preserve existing legacy protocol/schema tests. FastMCP 4
migration is not part of this matrix or release.

B handoff: reviewed projection schema/disposition rules, contract fixtures,
generation/digest/coverage manifest, compatibility report, held-out results, paired
task report, rollback/reopen plan and public release checklist. Projection promotion
is atomic and readers reopen by generation; failed builds preserve the approved one.

## 9. Effort, gates and open decisions

A's 5–8 engineer-days is a conditional estimate for a focused executor with approved
fixtures/prerequisites, a small stdlib CLI and prompt review. Engineering allocation:
foundation/schema 1–2; importer 1–2; parser/candidates about 1; review/CAS 1–2;
integration/report/recovery about 1. This is an effort budget, not elapsed delivery
time or a verified staffing promise. Snapshot recovery, isolation handoffs or schema/
CLI surprises can add 1–3 engineer-days; re-estimate at M2/M3 rather than hiding them.

Human legal-identity review, metadata-use decisions, operator work and review-gate
waiting time are unestimated. B is estimated only after A. The earlier versioning/
strong-receipt budget must not be carried into this scope.

Gates, in order: documentation privacy/content review → docs-only branch push;
M0 prerequisites and implementation assignment → A milestones → independent A
checkpoint and dot acceptance → separately approved B plan/budget → public release
approval. None implies a PR, merge, production deployment, rights permission or
human adjudication authority beyond what the user explicitly grants.

Open decisions: approve the package/CLI exception; recover versus explicitly
rebaseline the fixture; appoint the independent reviewer and human adjudicator;
record metadata-use/storage/recovery policy; confirm client availability and B0
eval budgets later. No other agent is contacted and no implementation begins in
this planning step.
