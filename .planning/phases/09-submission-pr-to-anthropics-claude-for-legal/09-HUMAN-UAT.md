---
phase: 9
slug: submission-pr-to-anthropics-claude-for-legal
created: 2026-05-17
last_updated: 2026-05-17
---

# Phase 9 — Human UAT Checklist

Manual verification steps that cannot be automated. Each slice appends its items.
Tick `[x]` after completing each verification.

---

## Slice A — Docs + Privacy Publication

- [ ] **1. docs URL live and covers all 6 tools** — Open `https://mcp.zeeker.sg/docs` in a browser. Confirm:
  - The MkDocs Material theme renders
  - Nav shows: Home → Tools → Error Catalog → Rate Limits → Envelope → Injection Resistance → Privacy Policy
  - The "Tools" page shows all six tool names: `list_databases`, `list_tables`, `describe_table`, `query_table`, `fetch`, `search`
  - The "Error Catalog" page shows all 11 error codes
  - There is a visible link to `/privacy` near the top of the index page
  - `bash scripts/validate_links.sh` exits 0 from a non-production network

- [ ] **2. privacy URL live with required disclosures** — Open `https://mcp.zeeker.sg/privacy` in a browser. Confirm:
  - The "Data collected" table lists exactly 8 fields: `request_id`, `tool`, `database`, `table`, `duration_ms`, `status`, `ip_prefix`, `error_code`
  - The retention period is stated (30 days or operator-updated value)
  - The contact email is the operator's chosen address (replace `privacy@zeeker.sg` placeholder if needed)
  - Third-party data flow section is present
  - Jurisdiction (Singapore) is stated
  - The existing `/mcp` endpoint still works: `curl -fsS https://mcp.zeeker.sg/healthz` returns 200

---

## Slice B — README Use Cases + Injection Writeup

- [ ] **3. README use cases** — Read README section `## Use cases`. Each case must include:
  - A literal user prompt
  - An expected tool call sequence
  - A "Why this fits regulatory-legal" closer
  - All four database names (`zeeker-judgements`, `pdpc`, `sg-gov-newsrooms`, `sglawwatch`) referenced across the three use cases

- [ ] **4. README injection-resistance writeup** — Read README section `## Injection-resistance posture`. Confirm:
  - Six subsections present
  - `TOOL_TRAILER` sentence quoted verbatim
  - All five non-special HEAVY_COLUMNS named: `content_text`, `full_text`, `html_raw`, `footnote_text`, `figure_descriptions`

---

## Slice C — Fork + .mcp.json Edit

- [ ] **5. .mcp.json entry mimics reference entries** — Open the PR diff on GitHub. Confirm:
  - Field order: `type` → `url` → `title` → `description`
  - Key `"Zeeker"` matches title `"Zeeker"` exactly
  - `"type": "http"` (not `"streamable-http"`)
  - `"url": "https://mcp.zeeker.sg/mcp"`
  - `recommendedCategories` array is unchanged

---

## Slice D — Reachability + Live Tests Evidence

- [ ] **6. Live test evidence is fresh** — PR body links to a GitHub Actions workflow run that:
  - Completed within the last 7 days of PR submission
  - Shows conclusion: success
  - Shows 11/11 tests passed

---

## Slice E — PR Open

- [ ] **7. Reachability from Anthropic reviewer network** — Either:
  - Option A: IP allowlist configured at Caddy layer per RESEARCH §13 Q1
  - Option B: Confirmed with Anthropic registry-onboarding that no allowlist is required
  - Verify from at least one non-Singapore vantage point: `curl -fsS https://mcp.zeeker.sg/healthz` returns 200

---

*Seeded by Slice A Task 2 (2026-05-17). Append slice-specific items as each slice completes.*
