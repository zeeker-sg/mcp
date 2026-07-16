#!/usr/bin/env python3
"""
Build evals/queries/known_item.jsonl — Tier-1 known-item retrieval set (issue #12).

Dev tool, NOT shipped server code. Runs against live *anonymous*
data.zeeker.sg (override with EVAL_DATA_URL). Rerun-on-demand: eval runs
consume the cached JSONL, they never rebuild it.

Three query classes (see issue #12 plan comment):

  doctrinal        — sg-law-cookies/judgment_issues (56 rows). Query = the
                     `question` text lightly keyword-ified (deterministic:
                     lowercase, punctuation stripped, stopwords removed,
                     capped at KEYWORD_CAP tokens). Target = `source_url`.
                     The judgment's `citation` is recorded in `notes`.
  citation-lookup  — sg-law-cookies/judgments `cases_cited` (JSON string
                     field). Each cited citation string becomes a query
                     (pinpoint suffix " at [..]" stripped). Target = the
                     zeeker-judgements judgment whose `citation` column
                     equals the bare citation, verified live via
                     ?citation__exact= — pairs whose target is not in the
                     corpus are dropped.
  case-name        — sg-law-cookies/judgments. Query = case_name lowercased
                     + one deterministic topic word taken from `orders`.
                     Target = `source_url`.

JSONL row shape: {"qid", "class", "query", "target_urls": [...], "notes"}.

Rate-limit posture: strictly sequential requests, small pages, a fixed
inter-request sleep (POLITE_SLEEP_S) — the anonymous tier budget on
data.zeeker.sg is ~60 req/min; this builder stays well under it.

Usage:
    uv run python evals/build_known_item_set.py [--out evals/queries/known_item.jsonl]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

DATA_URL = "https://data.zeeker.sg"
POLITE_SLEEP_S = 1.2  # ~50 req/min ceiling — under the 60/min anonymous budget
PAGE_SIZE = 50
KEYWORD_CAP = 8  # max tokens kept per doctrinal query — keep it short + deterministic

# Small fixed stopword set — deterministic keyword-ification, no NLP deps.
STOPWORDS = frozenset(
    """
    a an and are as at based be by did do does for from had has have how if in
    into is it its no not of on or over respect should such that the their
    there this to under upon was were what when where whether which who whom
    why will with would
    """.split()
)

# Pinpoint suffix on cited citations, e.g. "[2016] 3 SLR 1 at [85]" → "[2016] 3 SLR 1".
_PINPOINT_RE = re.compile(r"\s+at\s+\[.*$")
# Bracketed-year anchor: cited strings sometimes embed the case name before the
# citation proper ("Tian Kong ... Temple [2021] 4 SLR 286") — take from the
# first "[yyyy]" onward so ?citation__exact= sees a citation-shaped string.
_CITATION_ANCHOR_RE = re.compile(r"\[\d{4}\].*$")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _sleep() -> None:
    time.sleep(POLITE_SLEEP_S)


def fetch_all_rows(client: httpx.Client, path: str) -> list[dict]:
    """Paginate a Datasette table view sequentially via the `next` token."""
    rows: list[dict] = []
    params: dict[str, str] = {"_shape": "objects", "_size": str(PAGE_SIZE)}
    while True:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("rows", []))
        nxt = payload.get("next")
        if not nxt:
            return rows
        params["_next"] = nxt
        _sleep()


def keywordify(question: str, cap: int = KEYWORD_CAP) -> str:
    """Deterministic light keyword-ification of an issue question.

    Lowercase → punctuation to spaces → drop stopwords (which removes the
    leading "Whether the ..." boilerplate as a side effect) → cap length.
    """
    tokens = [t for t in _NON_WORD_RE.split(question.lower()) if t]
    kept = [t for t in tokens if t not in STOPWORDS]
    return " ".join(kept[:cap])


def bare_citation(cited: str) -> str:
    """Extract the bare citation from a cited string.

    Anchors on the first "[yyyy]" (dropping any embedded case name before it)
    and strips the pinpoint suffix (" at [..]"). Returns "" when the string
    has no bracketed year (unverifiable — caller skips it).
    """
    m = _CITATION_ANCHOR_RE.search(cited)
    if not m:
        return ""
    return _PINPOINT_RE.sub("", m.group(0)).strip()


def topic_word(orders: str, case_name: str) -> str:
    """One deterministic topic word from `orders`.

    First token of length >= 6 that is not a stopword and does not already
    appear in the case name; falls back to the longest token, then "".
    """
    name_lower = case_name.lower()
    tokens = [t for t in _NON_WORD_RE.split(orders.lower()) if t]
    for t in tokens:
        if len(t) >= 6 and t not in STOPWORDS and t not in name_lower:
            return t
    return max(tokens, key=len) if tokens else ""


def build_doctrinal(client: httpx.Client) -> list[dict]:
    rows = fetch_all_rows(client, "/sg-law-cookies/judgment_issues.json")
    print(f"[doctrinal] fetched {len(rows)} judgment_issues rows", file=sys.stderr)
    out = []
    for i, r in enumerate(rows, 1):
        question = (r.get("question") or "").strip()
        source_url = (r.get("source_url") or "").strip()
        citation = (r.get("citation") or "").strip()
        if not question or not source_url:
            continue
        query = keywordify(question)
        if not query:
            continue
        out.append(
            {
                "qid": f"doctrinal-{i:03d}",
                "class": "doctrinal",
                "query": query,
                "target_urls": [source_url],
                "notes": f"citation={citation}; question={question}",
            }
        )
    return out


def verify_citation_in_corpus(client: httpx.Client, citation: str) -> str | None:
    """Return the corpus judgment's source_url when `citation` exists in
    zeeker-judgements.judgments (via ?citation__exact= — verified working
    anonymously), else None."""
    resp = client.get(
        "/zeeker-judgements/judgments.json",
        params={"citation__exact": citation, "_shape": "objects", "_size": "1"},
    )
    if resp.status_code == 400:
        # Upstream rejects some citation strings (e.g. long ones with embedded
        # punctuation) — treat as "not in corpus" and drop the pair.
        print(f"[citation-lookup] upstream 400 for {citation!r} — dropped", file=sys.stderr)
        return None
    resp.raise_for_status()
    rows = resp.json().get("rows", [])
    if rows:
        return (rows[0].get("source_url") or "").strip() or None
    return None


def build_from_judgments(client: httpx.Client) -> tuple[list[dict], list[dict]]:
    """Build citation-lookup + case-name classes from sg-law-cookies/judgments."""
    rows = fetch_all_rows(client, "/sg-law-cookies/judgments.json")
    print(f"[judgments] fetched {len(rows)} sg-law-cookies judgments rows", file=sys.stderr)

    # --- citation-lookup: dedupe cited citations across all judgments ---
    seen: dict[str, str] = {}  # bare citation → raw cited string (first occurrence)
    for r in rows:
        raw = r.get("cases_cited") or "[]"
        try:
            cited_list = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for c in cited_list:
            cited = (c.get("citation") or "").strip() if isinstance(c, dict) else ""
            if not cited:
                continue
            bare = bare_citation(cited)
            if bare and bare not in seen:
                seen[bare] = cited

    citation_rows: list[dict] = []
    dropped = 0
    for i, (bare, raw_cited) in enumerate(sorted(seen.items()), 1):
        _sleep()
        target = verify_citation_in_corpus(client, bare)
        if target is None:
            dropped += 1
            continue
        citation_rows.append(
            {
                "qid": f"cite-{i:03d}",
                "class": "citation-lookup",
                "query": bare,
                "target_urls": [target],
                "notes": f"raw_cited={raw_cited}",
            }
        )
    print(
        f"[citation-lookup] {len(seen)} unique cited citations, "
        f"{len(citation_rows)} in corpus, {dropped} dropped",
        file=sys.stderr,
    )

    # --- case-name: query = case_name lowercased + one topic word from orders ---
    name_rows: list[dict] = []
    for i, r in enumerate(rows, 1):
        case_name = (r.get("case_name") or "").strip()
        orders = (r.get("orders") or "").strip()
        source_url = (r.get("source_url") or "").strip()
        if not case_name or not source_url:
            continue
        tw = topic_word(orders, case_name)
        query = (case_name.lower() + (" " + tw if tw else "")).strip()
        name_rows.append(
            {
                "qid": f"name-{i:03d}",
                "class": "case-name",
                "query": query,
                "target_urls": [source_url],
                "notes": f"citation={(r.get('citation') or '').strip()}; topic_word={tw}",
            }
        )
    return citation_rows, name_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "queries" / "known_item.jsonl"),
        help="Output JSONL path (default: evals/queries/known_item.jsonl)",
    )
    parser.add_argument(
        "--data-url",
        default=DATA_URL,
        help=f"Datasette base URL (default: {DATA_URL}; env EVAL_DATA_URL also honored)",
    )
    parser.add_argument(
        "--classes",
        default="doctrinal,citation-lookup,case-name",
        help="Comma-separated subset of classes to build",
    )
    args = parser.parse_args()

    import os

    base_url = os.environ.get("EVAL_DATA_URL", args.data_url)
    wanted = {c.strip() for c in args.classes.split(",") if c.strip()}

    all_rows: list[dict] = []
    with httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": "zeeker-mcp-evals/0.1 (known-item builder)"},
    ) as client:
        if "doctrinal" in wanted:
            all_rows.extend(build_doctrinal(client))
            _sleep()
        if wanted & {"citation-lookup", "case-name"}:
            cite_rows, name_rows = build_from_judgments(client)
            if "citation-lookup" in wanted:
                all_rows.extend(cite_rows)
            if "case-name" in wanted:
                all_rows.extend(name_rows)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_class: dict[str, int] = {}
    for r in all_rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    print(f"wrote {len(all_rows)} queries to {out_path} — {by_class}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
