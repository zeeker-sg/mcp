"""Synthetic request mix for the 24h soak driver — TEST-05.

Canonical workload definition (single source of truth). Lives under tests/_corpus/
so the same mix is reusable for future regression tests, not just the soak.
scripts/soak/workload.py re-exports WORKLOAD from here.

Distribution mirrors observed Phase 6.1 manual-UAT traffic:
- 35% list_databases (cheap discovery — no upstream fan-out)
- 30% query_table on URL-keyed tables (medium — single-DB query)
- 20% search across all DBs (expensive — cross-DB FTS fan-out)
- 10% fetch by URL (medium — single-row hydration)
- 5%  query_table on *_fragments (heavy — 2-step join)

TEST-05 owner: Phase 8 (plan 08-05).
"""

from __future__ import annotations

from typing import Any

# Typed (tool_name, args_payload, weight) tuples.
# Weights must sum to 1.0.
# tool_name must match a registered MCP tool name in mcp_zeeker.
# args_payload is the JSON-serialisable arguments dict for tools/call.
WORKLOAD: list[tuple[str, dict[str, Any], float]] = [
    # 35% discovery — list_databases is the cheapest call; no upstream DB query.
    # Rationale: agents typically start a session with discovery (observed Phase 6.1 UAT).
    ("list_databases", {}, 0.35),
    # 30% query_table on a URL-keyed table — medium cost; single-DB Datasette query.
    # Uses pdpc.enforcement_decisions which is a stable table with URL-keyed rows.
    ("query_table", {"database": "pdpc", "table": "enforcement_decisions", "limit": 5}, 0.30),
    # 20% search — most expensive: fans out to all allowed databases via FTS.
    # "data protection" is a stable high-recall query that exercises the fan-out path.
    ("search", {"query": "data protection", "limit": 5}, 0.20),
    # 10% fetch by URL — single-row hydration; medium cost.
    # URL is the first stable enforcement decision found in Phase 6.1 live UAT.
    (
        "fetch",
        {
            "database": "pdpc",
            "table": "enforcement_decisions",
            "url": "https://www.pdpc.gov.sg/enforcement-and-decisions/investigations",
        },
        0.10,
    ),
    # 5% fragment query — heaviest: 2-step join (parent fetch + fragment scan).
    # Exercises the keyset cursor path and validates NFR-02 at peak concurrency.
    (
        "query_table",
        {
            "database": "zeeker-judgements",
            "table": "judgments_fragments",
            "filters": [
                {
                    "column": "source_url",
                    "op": "exact",
                    "value": "https://www.elitigation.sg/gd/s/2024_SGHC_1",
                }
            ],
            "limit": 50,
        },
        0.05,
    ),
]

# Invariant: weights must sum to 1.0 (checked at import time in tests, enforced by acceptance
# criteria). Do not silently violate this — the driver's _pick_request relies on it.
assert abs(sum(w for _, _, w in WORKLOAD) - 1.0) < 0.001, (
    f"WORKLOAD weights sum to {sum(w for _, _, w in WORKLOAD):.4f}, expected 1.0"
)
