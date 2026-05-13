---
phase: 01-skeleton-transport-first-tool
plan: "06"
subsystem: deployment
tags: [deploy, docker, dockerfile, docker-compose, readme, manual-verification, caddy, transport]
dependency_graph:
  requires: [01-05]
  provides: [Dockerfile, docker-compose.yml, README.md, tests/manual/PHASE1-CLIENT-VERIFY.md, evidence/01-skeleton/]
  affects: [TRANSPORT-05, RATE-06, NFR-04]
tech_stack:
  added:
    - "ghcr.io/astral-sh/uv:0.11 (builder stage)"
    - "python:3.13-slim (runtime stage)"
    - "datasetteproject/datasette:latest (sibling container)"
  patterns:
    - "Two-stage uv Docker build: builder installs venv, runtime copies it (no uv in final image)"
    - "Single uvicorn worker (RATE-06 enforcement in Dockerfile CMD comment)"
    - "HEALTHCHECK via stdlib urllib (zero extra deps)"
key_files:
  created:
    - Dockerfile
    - docker-compose.yml
    - README.md
    - .gitignore
    - tests/manual/PHASE1-CLIENT-VERIFY.md
    - evidence/01-skeleton/.gitkeep
  modified: []
decisions:
  - "Pinned uv image to ghcr.io/astral-sh/uv:0.11 (not :latest) per plan-checker warning #5 and CLAUDE.md research confirming 0.11.14 as current"
  - "docker-compose.yml datasette service uses :latest tag (local-dev only; operator manages production datasette)"
  - "No Caddyfile in repo: README.md documents expectations in prose; operator authors their own block (D-20)"
  - ".gitignore committed via this plan (was untracked in main repo); adds .env exclusion"
metrics:
  duration: "~20 minutes"
  completed: "2026-05-13"
  tasks_completed: 3
  files_created: 6
---

# Phase 1 Plan 6: Deployment Artifacts + Manual Verification Checklist Summary

**One-liner:** uv two-stage Dockerfile (single uvicorn worker, RATE-06 compliant) + sibling-container compose + operator Deployment prose + TRANSPORT-05 manual checklist.

## What Was Built

### Task 1: Dockerfile + docker-compose.yml (commit `01d9066`)

**Dockerfile shape:**

- Stage 1 (`builder`): `FROM python:3.13-slim AS builder`. Installs uv from `ghcr.io/astral-sh/uv:0.11` (pinned to 0.11 series per CLAUDE.md research). Runs `uv sync --frozen --no-dev --no-install-project` first (dep-only cache layer), then `COPY src/` and `uv sync --frozen --no-dev` (project install).
- Stage 2 (`runtime`): `FROM python:3.13-slim`. Copies `/app/.venv` and `/app/src` from builder. Sets `PATH=/app/.venv/bin:$PATH`. Runtime command: `CMD ["uvicorn", "mcp_zeeker.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]`.
- `HEALTHCHECK --interval=10s --timeout=2s --start-period=10s --retries=3` polling `/healthz` via stdlib `urllib.request` (zero dep overhead).
- No `apt-get install`, no `build-essential`, no `gcc` — NFR-04 audit-friendly footprint.
- Inline comment on `--workers 1` line: `# RATE-06: in-memory token bucket is per-process; multi-worker would silently break rate-limit math`.

**docker-compose.yml topology:**

- Service `mcp`: builds from `.`, `UPSTREAM_URL: http://datasette:8001`, `USER_AGENT: mcp-zeeker/0.1`, port `8000:8000`, network `zeeker`, `depends_on: datasette`.
- Service `datasette`: `datasetteproject/datasette:latest`, network `zeeker`. Comment makes clear this is local-dev only.
- Network `zeeker`: bridge driver.
- Top-of-file comment: `# Caddy is NOT in this compose file — it's a pre-existing host service.`

### Task 2: README.md (commit `bcc35bb`)

Top-level sections (all `##` headings):

| Section | Content |
|---|---|
| `## Quick start` | `uv sync` + `uvicorn --reload` for local dev; points to `https://mcp.zeeker.sg/mcp` for production |
| `## Deployment` | Prose topology + Caddy expectations (XFF **overwrite** security note, Origin pass-through, routing); single-worker constraint; UPSTREAM_URL hairpin warning; Anthropic IP allowlist forward-reference |
| `## Environment` | Table: `UPSTREAM_URL`, `USER_AGENT`; `.env.example` note |
| `## Testing` | Fast (no live), `ZEEKER_LIVE=1` live, manual TRANSPORT-05 pointer |

SUB-03 (LLM use cases) and SUB-04 (injection-resistance writeup) explicitly deferred to Phase 9.

### Task 3: Manual checklist + evidence placeholder (commit `c0b195f`)

`tests/manual/PHASE1-CLIENT-VERIFY.md` contents:

- **Pre-conditions**: DNS resolution, `/healthz` curl, XFF overwrite log check, `tools/list` returns exactly one tool.
- **Claude Desktop**: edit `claude_desktop_config.json`, restart, prompt `list_databases`, screenshot to `evidence/01-skeleton/claude-desktop-list-databases.png`.
- **Claude Code**: `claude mcp add zeeker https://mcp.zeeker.sg/mcp`, confirm `claude mcp list`, prompt, screenshot to `evidence/01-skeleton/claude-code-list-databases.png`.
- **Acceptance**: both clients return all 4 DBs (`zeeker-judgements`, `pdpc`, `sg-gov-newsrooms`, `sglawwatch`) with non-empty descriptions and `table_count > 0`.
- **Troubleshooting**: 403 (XFF/Origin), 500 (lifespan pitfall), `table_count=0` (UPSTREAM_URL), empty description (config).

`evidence/01-skeleton/.gitkeep` — empty placeholder so the directory exists in git for screenshot commits.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing] Pinned uv image tag**
- **Found during:** Task 1
- **Issue:** Plan text said `ghcr.io/astral-sh/uv:latest` (unpinned); parallel-execution context flagged plan-checker warning #5
- **Fix:** Used `ghcr.io/astral-sh/uv:0.11` (0.11 series, matching CLAUDE.md research confirming 0.11.14 as current)
- **Files modified:** `Dockerfile`
- **Commit:** `01d9066`

**2. [Rule 1 - Bug] .gitignore .env entry**
- **Found during:** Task 1
- **Issue:** The `.gitignore` in the main repo (untracked) lacked a `.env` exclusion; plan required adding `.env` to `.gitignore` if absent
- **Fix:** Committed `.gitignore` with `.env` exclusion included; also caught and committed the pre-existing `.gitignore` content that was previously untracked
- **Files modified:** `.gitignore`
- **Commit:** `01d9066`

### Docker Build Smoke Test Not Run

The automated verify step (`docker build -t mcp-zeeker:test .`) could not be executed because the Docker daemon was not running in the execution environment. This is a deviation from the Task 1 `<verify>` block — documented here, not a blocker for the deployment artifacts themselves. The operator must run `docker compose up --build` on the deploy host.

## TRANSPORT-05 Status

**Pending human verification.** All file-authoring tasks are complete and committed. TRANSPORT-05 acceptance requires:

1. Operator deploys to `https://mcp.zeeker.sg/mcp`
2. Human walks `tests/manual/PHASE1-CLIENT-VERIFY.md` against Claude Desktop and Claude Code
3. Screenshots committed to `evidence/01-skeleton/`

Resume signal: `approved` | `defer-to-operator` | describe blockers.

## Known Stubs

None introduced by this plan. The deployment artifacts reference `mcp_zeeker.app:app` which is fully implemented (Plan 01-04). The `datasette` service in `docker-compose.yml` uses `:latest` for local-dev convenience — not a stub, but the operator should pin this to a specific version for production stability (deferred concern, not this plan's scope).

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: operator-misconfiguration | `README.md` | XFF append-vs-overwrite documented but not enforced; operator must verify Caddy config. Mitigated by explicit prose in README "Deployment" section per T-1-XFF-01. |

## Self-Check: PASSED

- `Dockerfile` exists and contains `--workers 1`, `uv sync --frozen`, `HEALTHCHECK`
- `docker-compose.yml` exists and contains `UPSTREAM_URL: http://datasette:8001`, `Caddy is NOT in this compose`, `datasette` service
- `README.md` contains `## Quick start`, `## Deployment`, `## Environment`, `## Testing`, `overwrite`, `single-worker`, `UPSTREAM_URL`, `mcp.zeeker.sg`, `Origin`, `ZEEKER_LIVE=1`
- `tests/manual/PHASE1-CLIENT-VERIFY.md` exists with `https://mcp.zeeker.sg/mcp` content
- `evidence/01-skeleton/.gitkeep` exists
- All 3 commits verified: `01d9066`, `bcc35bb`, `c0b195f`
- `uv run ruff check` and `uv run ruff format --check` pass (no Python files changed)
