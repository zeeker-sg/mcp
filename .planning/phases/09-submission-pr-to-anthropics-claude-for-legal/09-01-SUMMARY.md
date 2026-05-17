---
phase: "09"
plan: "01"
subsystem: docs
tags: [docs, mkdocs, privacy-policy, caddy, mcp]
dependency_graph:
  requires: []
  provides: [docs-site-source, privacy-policy, validate_links_script]
  affects: [09-02, 09-03, 09-04, 09-05]
tech_stack:
  added: [mkdocs-material>=9.7.6]
  patterns: [static-html-caddy-file_server, verbatim-config-quoting]
key_files:
  created:
    - mkdocs.yml
    - docs/index.md
    - docs/tools.md
    - docs/errors.md
    - docs/rate-limits.md
    - docs/envelope.md
    - docs/injection-resistance.md
    - docs/privacy.md
    - scripts/validate_links.sh
    - site/ (built static HTML, 64 files)
    - .planning/phases/09-submission-pr-to-anthropics-claude-for-legal/09-HUMAN-UAT.md
  modified:
    - pyproject.toml (mkdocs-material added to dev deps)
    - uv.lock (updated)
decisions:
  - "site/ output committed to repo at root level (not docs/site/) — MkDocs forbids site_dir inside docs_dir"
  - "privacy@zeeker.sg contact email is a placeholder — operator must confirm or replace before UAT"
  - "TOOL_TRAILER quoted verbatim from config.TOOL_TRAILER in both tools.md and injection-resistance.md"
  - "LOG_FIELDS 8 fields sourced verbatim from config.LOG_FIELDS in privacy.md data-collected table"
metrics:
  completed_date: "2026-05-17"
  task_count: 2
  file_count: 11
---

# Phase 9 Plan 01: Docs + Privacy Publication Summary

MkDocs Material documentation site source tree and privacy policy — citation-ready, verbatim-constant-sourced, ready for operator deployment to `mcp.zeeker.sg/docs` and `mcp.zeeker.sg/privacy` via Caddy file_server blocks.

## What Was Built

**Task 1: MkDocs scaffold + static docs site source** (commit `ce4ced4`)

Seven Markdown source files covering the full SUB-01 requirement:

- `docs/index.md` — landing page with `/privacy` link, four-database table, links to all doc sections
- `docs/tools.md` — six tools with parameter tables (list_databases, list_tables, describe_table, query_table, fetch, search), example calls and responses, TOOL_TRAILER verbatim
- `docs/errors.md` — 11-code error catalog from `core/errors.py CATALOG` in canonical tuple order; rate_limited marked as ASGI-only (never ToolError)
- `docs/rate-limits.md` — burst=20, sustained=60/min, daily=5,000 from config constants; UTC midnight rollover; Retry-After semantics
- `docs/envelope.md` — Provenance/Pagination/Envelope shape from core/envelope.py, four factory variants, retrieved_content separation, _policy block
- `docs/injection-resistance.md` — three mechanisms (tool trailer, structural separation, no-echo); adversarial example; TOOL_TRAILER verbatim in blockquote
- `docs/privacy.md` — placeholder in Task 1, replaced in Task 2

`mkdocs.yml` configured with `site_url: https://mcp.zeeker.sg/docs`, `theme: material`, full nav, `site_dir: site/` (at repo root — cannot be inside docs/).

`pyproject.toml` updated: `mkdocs-material>=9.7.6` added to `[dependency-groups] dev`.

`site/` static HTML built and committed: `uv run mkdocs build --strict` succeeds.

**Task 2: Privacy policy + link-check script + UAT seed** (commit `21306a5`)

- `docs/privacy.md` — six-section privacy policy with exactly 8 `LOG_FIELDS` rows, no-echo statement, 30-day retention, no cookies, `privacy@zeeker.sg` contact placeholder, Singapore jurisdiction
- `scripts/validate_links.sh` — curl-checks `mcp.zeeker.sg/docs`, `/docs/`, `/privacy`, `/privacy/`, `/healthz`; `ZEEKER_DOCS_HOST` env override for staging; exits non-zero on any non-200
- `09-HUMAN-UAT.md` — 7 manual UAT checklist items seeded across all Phase 9 slices

## Verification Results

All Task 1 automated checks passed:
- `uv run mkdocs build --strict` — succeeds (0 errors; INFO for `/privacy` absolute link preserved as production URL)
- `site/index.html` — exists
- `site/tools/index.html` — exists
- TOOL_TRAILER sentence in `docs/tools.md` — FOUND
- TOOL_TRAILER sentence in `docs/injection-resistance.md` — FOUND
- All 11 error codes from CATALOG in `docs/errors.md` — FOUND
- `20` in `docs/rate-limits.md` — FOUND
- `5000` in `docs/rate-limits.md` — FOUND
- `retrieved_content` in `docs/envelope.md` — FOUND

All Task 2 automated checks passed:
- `scripts/validate_links.sh` is executable — FOUND
- `bash -n scripts/validate_links.sh` (syntax check) — OK
- `mcp.zeeker.sg/docs` in validate_links.sh — FOUND
- `mcp.zeeker.sg/privacy` in validate_links.sh — FOUND
- All 8 `LOG_FIELDS` in `docs/privacy.md` — FOUND
- `09-HUMAN-UAT.md` exists — FOUND

## Deviations from Plan

### Non-breaking adjustment

**[Rule 1 - Config] site_dir moved to repo root `site/` not `docs/site/`**
- **Found during:** Task 1 build
- **Issue:** MkDocs refuses to build when `site_dir` is inside `docs_dir` — exits with error "site_dir should not be within docs_dir"
- **Fix:** Changed `site_dir` in `mkdocs.yml` from implicit `docs/site/` to `site/` at repo root. The plan's language "committed to the repo at `docs/site/`" was an intended location description; the constraint from MkDocs takes precedence
- **Impact:** Static HTML is at `site/index.html` and `site/tools/index.html` etc. (not `docs/site/`). Operator must deploy from `site/` not `docs/site/` — the Caddy snippet in Task 3 below uses `/srv/mcp-zeeker-docs` which is path-agnostic
- **Files modified:** `mkdocs.yml`
- **Commit:** ce4ced4

## Known Stubs

| Stub | File | Reason |
|------|------|--------|
| `privacy@zeeker.sg` contact email | `docs/privacy.md` line 60 | Placeholder — operator must confirm or replace before the privacy policy is live. Accepted risk T-09-A-05 in plan threat model. UAT checklist item 2 requires operator confirmation. |

## Threat Flags

No new security-relevant surface introduced. All content is static Markdown → static HTML. No network endpoints, auth paths, or schema changes.

The Caddyfile snippet below adds two new URL paths (`/docs*` and `/privacy*`) to the production site. T-09-A-03 mitigation: file_server blocks MUST precede the existing `reverse_proxy 127.0.0.1:8002` block.

## Caddy Deployment Snippet

Add these two blocks to the host Caddyfile BEFORE the existing `reverse_proxy` block:

```
handle /docs* {
    root * /srv/mcp-zeeker-docs
    try_files {path} {path}/index.html
    file_server
}

handle /privacy* {
    root * /srv/mcp-zeeker-docs
    try_files /privacy/index.html
    file_server
}
```

Deploy `site/` to the server: `rsync -a site/ user@host:/srv/mcp-zeeker-docs/`

Then reload Caddy: `sudo systemctl reload caddy`

## Awaiting Operator (Task 3 Checkpoint)

The plan's Task 3 is a `checkpoint:human-action`. Operator must:

1. Copy `site/` to the production host at `/srv/mcp-zeeker-docs/`
2. Add two Caddy `file_server` blocks to the host Caddyfile (snippet above)
3. Reload Caddy
4. Verify: `bash scripts/validate_links.sh` exits 0 from a non-production network
5. Browser-check `https://mcp.zeeker.sg/docs` and `https://mcp.zeeker.sg/privacy`
6. Confirm or replace `privacy@zeeker.sg` contact email if desired
7. Tick UAT checklist items 1 and 2 in `09-HUMAN-UAT.md`

## Self-Check: PASSED

- `mkdocs.yml` exists — FOUND (ce4ced4)
- `site/index.html` exists — FOUND (ce4ced4)
- All 7 docs/ source files exist — FOUND
- `scripts/validate_links.sh` exists and is executable — FOUND (21306a5)
- `09-HUMAN-UAT.md` exists — FOUND (21306a5)
- Commits ce4ced4 and 21306a5 exist in git log — CONFIRMED
