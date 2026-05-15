# Phase 7 — Deferred Items

Out-of-scope discoveries logged during plan execution. Not fixed here.

## 07-01 Wave 1

- **Pre-existing E501 in `src/mcp_zeeker/config.py:174-175,182`** — three `TABLE_DESCRIPTIONS` table description strings exceed the 100-char ruff line limit. Present on `main` before Phase 7. Out of scope for 07-01 (rate-limit middleware) — log here for a future formatting pass.
