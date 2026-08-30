"""
Background FTS index warmer — issue #18 (cold-index timeout mitigation).

Production symptom (2026-08-29, mcp.zeeker.sg): an unscoped `search()` against
a COLD upstream FTS index failed `zeeker-judgements.judgments_fragments` with
HTTP 400 "SQL query took too long" (dying at 1.1–1.9s), while the identical
query once warm returned in 0.05–0.4s. Warmth decays between requests, so a
once-a-day workload — the daily briefing — runs against a cold index most
mornings and eats the timeout. Datasette-side fixes (raising
`sql_time_limit_ms`, deploy-time index warm) live on the data platform; this
module is the connector-side companion: keep the index warm from OUR side so
upstream never reads the fragments index cold.

Design:

- A background task in the MCP process (`FtsWarmer.run_forever`, started by
  the app lifespan) replays ONE representative FTS query per searchable
  target on a fixed interval (`config.FTS_WARMER_INTERVAL_S`).
- Target selection (`select_warm_targets`) reuses the EXACT discovery gates
  real search uses — `searchable_tables_for` (4-gate filter, D4-02) for
  content tables and `fragment_sources_for` for `*_fragments` passage
  sources. The warmer can therefore never warm (or fail on) a target a real
  search would not touch, and stays correct as databases come and go in
  `config.ALLOWED_DATABASES` — zero target lists duplicated here.
- Dispatch reuses the EXACT production query builders: `build_bm25_sql` /
  `build_maxp_sql` over `DatasetteClient.execute_sql` when SEARCH_RANKING is
  not "legacy" (the owner-token SQL path), and the anonymous `_search=`
  table view otherwise. Warming anything other than the code path that
  times out cold would be false economy.
- Failure handling mirrors search's D4-07 contract, inverted for a
  fire-and-forget loop: per-target failures are logged (`fts_warm_table_failed`,
  INJ-05 — `database` / `table` / `error_class` only, never the SQL or query
  text) and recorded in the pass result, never raised. Warmth is best-effort;
  a broken table must not kill the loop.
- Reentrancy: an instance-level `_inflight` flag makes overlapping passes
  (interval elapsed while a pass still runs) no-ops, so a stalled upstream
  can never stack warm passes behind the connection pool.
- Read-only: warm queries are the same read-only FTS SELECTs search issues.
  No write paths, no state — the connector's read-only guarantee is intact.

Security properties (auditable by inspection):
- All HTTP IO routes exclusively through `DatasetteClient.current()`
  (D-13/14/16 carry-forward).
- The warm query text travels ONLY as the bound `:search_query` named
  parameter (phrase-wrapped via `escape_fts5`); SQL identifiers come from
  config + upstream discovery metadata exactly as in `build_bm25_sql`
  (INJ-05 / D3-09 carry-forward).
- Log bindings expose `database`, `table`, `error_class`, counts and
  durations — NEVER the query string or SQL (INJ-05 / D4-07).
"""

from __future__ import annotations

import anyio
import structlog

from mcp_zeeker import config
from mcp_zeeker.core.datasette_client import DatasetteClient
from mcp_zeeker.core.fts_escape import escape_fts5
from mcp_zeeker.core.search import (
    build_bm25_sql,
    build_maxp_sql,
    fragment_sources_for,
    searchable_tables_for,
)

log = structlog.get_logger()


async def select_warm_targets(
    db: str,
    summary=None,
    visible: set[str] | None = None,
) -> tuple[list[tuple[str, dict[str, str | None], str, list[str]]], list]:
    """Return the (tables, fragment_sources) warm plan for `db`.

    Reuses the production discovery gates verbatim — `searchable_tables_for`
    (content tables: FTS present, visible, not denylisted, preview-resolvable)
    and `fragment_sources_for` (passage-search fragment sources gated on the
    same visibility + preview rules). No target list is duplicated here: when
    upstream data or config changes, warm coverage follows search coverage
    automatically. Fragment sources are skipped in "legacy" ranking mode,
    mirroring `fan_out_search`'s defensive gate.
    """
    tables = await searchable_tables_for(db, summary=summary, visible=visible)
    fragments = (
        await fragment_sources_for(db, summary=summary, visible=visible)
        if config.SEARCH_RANKING != "legacy"
        else []
    )
    return tables, fragments


class FtsWarmer:
    """Background FTS index warmer. One instance per process (app.py)."""

    def __init__(self) -> None:
        self._inflight: bool = False

    def _escaped_warm_query(self) -> str:
        """Warm-query text, escaped by the SAME boundary as search.

        Per-call (not cached) so test monkeypatching of
        ``config.FTS_WARM_QUERY`` takes effect without reloading.
        """
        return escape_fts5(config.FTS_WARM_QUERY)

    async def _warm_one_table(
        self,
        db: str,
        table: str,
        preview: dict[str, str | None],
        fts: tuple[str, list[str]] | None,
        escaped: str,
        outcomes: dict[str, str],
    ) -> None:
        """Dispatch one warm query for one content table. Never raises."""
        key = f"{db}.{table}"
        try:
            if config.SEARCH_RANKING != "legacy" and fts is not None and bool(fts[1]):
                sql, sql_params = build_bm25_sql(db, table, fts[0], fts[1], preview, escaped, 1)
                await DatasetteClient.current().execute_sql(db, sql, sql_params)
            else:
                await DatasetteClient.current().get_table_rows(
                    db,
                    table,
                    [("_search", escaped), ("_size", "1")],
                )
        except Exception as exc:
            outcomes[key] = f"failed:{type(exc).__name__}"
            log.warning(
                "fts_warm_table_failed",
                database=db,
                table=table,
                error_class=type(exc).__name__,
            )
            return
        outcomes[key] = "ok"

    async def _warm_one_fragment(
        self,
        db: str,
        source,
        escaped: str,
        outcomes: dict[str, str],
    ) -> None:
        """Dispatch one warm MaxP rollup for one fragment passage source.
        Never raises. SQL-path only — fragment sources have no legacy
        dispatch (they are skipped entirely in legacy mode by discovery)."""
        key = f"{db}.{source.fragment_table}"
        try:
            sql, sql_params = build_maxp_sql(db, source, escaped, 1)
            await DatasetteClient.current().execute_sql(db, sql, sql_params)
        except Exception as exc:
            outcomes[key] = f"failed:{type(exc).__name__}"
            log.warning(
                "fts_warm_table_failed",
                database=db,
                table=source.fragment_table,
                error_class=type(exc).__name__,
            )
            return
        outcomes[key] = "ok"

    async def warm_once(self) -> dict[str, str]:
        """Run one warm pass across every allowed database. Never raises.

        Returns a per-target outcome map keyed ``"<db>.<table>"`` with values
        ``"ok"`` | ``"failed:<ErrorClass>"``. While a pass is already
        running, the reentrancy gate returns ``{"status": "skipped"}``
        without dispatching — warm queries are best-effort and must never
        stack up behind the connection pool.
        """
        if self._inflight:
            return {"status": "skipped"}
        self._inflight = True
        try:
            out: dict[str, str] = {}
            escaped = self._escaped_warm_query()
            for db in config.ALLOWED_DATABASES:
                try:
                    tables, fragments = await select_warm_targets(db)
                except Exception as exc:
                    # Discovery failure (upstream down, cache unbound, client
                    # unbound) — skip the DB this pass; recorded so the pass
                    # map stays honest.
                    out[f"{db}.<discovery>"] = f"failed:{type(exc).__name__}"
                    log.warning(
                        "fts_warm_discovery_failed",
                        database=db,
                        error_class=type(exc).__name__,
                    )
                    continue
                for table, preview, fts_table, fts_columns in tables:
                    await self._warm_one_table(
                        db, table, preview, (fts_table, fts_columns), escaped, out
                    )
                for source in fragments:
                    await self._warm_one_fragment(db, source, escaped, out)
            return out
        finally:
            self._inflight = False

    async def run_forever(self) -> None:
        """Immediate warm pass, then one pass per `FTS_WARMER_INTERVAL_S`,
        forever — until cancelled (app shutdown). Never raises.

        A per-pass `move_on_after(FTS_WARMER_TIMEOUT_S)` budget bounds each
        pass: a stalled target cannot wedge the loop beyond the budget, and
        the reentrancy gate in `warm_once` keeps an overlap a no-op.
        """
        log.info(
            "fts_warmer_started",
            interval_s=config.FTS_WARMER_INTERVAL_S,
            pass_timeout_s=config.FTS_WARMER_TIMEOUT_S,
        )
        while True:
            outcomes: dict[str, str] = {}
            with anyio.move_on_after(config.FTS_WARMER_TIMEOUT_S) as scope:
                outcomes = await self.warm_once()
            if scope.cancelled_caught:
                log.warning(
                    "fts_warm_pass_timeout",
                    budget_s=config.FTS_WARMER_TIMEOUT_S,
                )
            else:
                failed = sum(1 for v in outcomes.values() if str(v).startswith("failed:"))
                log.info(
                    "fts_warm_pass_complete",
                    targets=len(outcomes),
                    failed=failed,
                )
            await anyio.sleep(config.FTS_WARMER_INTERVAL_S)


# UpstreamCallFailed is part of the warmer's failure taxonomy (mapped by
# DatasetteClient._request_with_retry); imported for re-export parity so
# callers can reference the failure class without reaching into the client.
from mcp_zeeker.core.datasette_client import UpstreamCallFailed  # noqa: E402

__all__ = [
    "FtsWarmer",
    "UpstreamCallFailed",
    "select_warm_targets",
]
