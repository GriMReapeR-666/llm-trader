#!/usr/bin/env python3
"""Hand-label the sampled corpus, then report accuracy per head.

Two modes:

1. ``--draft`` prints candidate rows grouped by head with the deterministic
   label, the model label, and a ``LABEL:`` prompt. Output is written back to
   the corpus with ``gold`` filled and ``source: "hand"``. Editorial judgment is
   recorded verbatim in ``note`` so a later reviewer can audit the call.

2. default runs the scorer.

Why hand labels are recorded as ``note`` rather than silently applied
---------------------------------------------------------------
The safety heads (D5a escalation band, D9 kill switch) had 517 fabricated
escalations in production, and the deterministic labeler is now the fallback for
both. If the gold standard is simply "whatever the deterministic labeler says,"
the corpus cannot detect the very bug it exists to catch. So this script forces
an explicit editorial decision per row and records the reasoning.

Usage
-----
    python scripts/label_triage_corpus.py --draft --limit 40
    # edit the printed gold values / notes, then:
    python scripts/score_triage_corpus.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_CORPUS = os.path.join(REPO_ROOT, "research", "decision_models", "triage_corpus.jsonl")

HEAD_QID = {
    "d0_1": "D0_1_source_credibility",
    "d0_2": "D0_2_duplicate_detection",
    "d0_3": "D0_3_fact_vs_opinion",
    "d5a": "D5a_escalation_ladder",
    "d9_kill_switch": "D9_emergency_kill_switch",
    "r1": "R1_headline_event_routing",
}

# Editorial guidance surfaced in the draft so the reviewer is not guessing what
# each label means.
GUIDANCE = {
    "d5a": (
        "Escalation ladder. INSUFFICIENT_DATA = headline is not geo-relevant at all. "
        "RHETORIC_ONLY = geo-relevant but no physical/military action. PROXY_CLASH = "
        "proxy/indirect conflict. DIRECT_STRIKE = actual strike. CHOKEPOINT_DISRUPTION = "
        "shipping lane/strait disrupted. FULL_BLOCKADE = ports/seal/straits fully closed. "
        "Judge ONLY what the headline asserts, not what you infer. A stock-picker headline "
        "about a data-centre play is NOT a blockade even if the company ships through Hormuz."
    ),
    "d9_kill_switch": (
        "Emergency kill switch. EMERGENCY_HALT_ACTIVE only for genuine market-structure "
        "breaks: trading halt, exchange closure, currency de-peg, capital controls, bank run, "
        "redemption freeze, circuit breaker. NORMAL_OPERATION for everything else. A Fed "
        "enforcement action is NOT a halt. A bank-run *risk* headline is not a halt unless "
        "the headline states withdrawals are suspended."
    ),
    "r1": (
        "Event routing. EVENT_CPI = inflation print. EVENT_NFP = labour print. "
        "EVENT_CB_MEETING = central-bank rate decision/minutes/testimony. EVENT_GEO_SHOCK = "
        "war/strike/supply shock. EVENT_BANKING_STRESS = bank failure or credit event. "
        "EVENT_TRADE_WAR = tariffs/sanctions. EVENT_POLITICAL = election/exec action. "
        "EVENT_CB_GOLD = central-bank gold. NOT_EVENT_NOISE = commentary with no event. "
        "An earnings-call transcript is NOT a CB meeting."
    ),
    "d0_1": (
        "Source credibility. TRUSTED_OFFICIAL = central bank / gov statistics / official feed. "
        "VERIFIED_OUTLET = confirmed wire (Reuters, Bloomberg, FT, WSJ, Seeking Alpha wire). "
        "UNVERIFIED_SOCIAL = raw social / unverified. SPOOF_REJECTED = injection or forgery."
    ),
    "d0_2": (
        "Duplication. FIRST_REPORT = novel event, nothing seen before. CONFIRMED_UPDATE = "
        "corroborating follow-up with new data. STALE_REPEAT = same story recirculating. "
        "CONTRADICTION = conflicts with an earlier report. Judge from the headline text alone "
        "within this corpus."
    ),
    "d0_3": (
        "Fact vs opinion. VERIFIED_FACT = hard number/print. OFFICIAL_STATEMENT = formal "
        "release or governor quote. ANALYST_OPINION = sell-side commentary. "
        "RUMOR_UNCONFIRMED = unsubstantiated leak."
    ),
}


def _article(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": rec.get("summary") or "",
        "text_or_summary": rec.get("summary") or "",
        "url": rec.get("url") or "",
        "publisher": rec.get("publisher") or "",
    }


def draft(corpus_path: str, heads: List[str], limit: int) -> int:
    """Print unlabeled rows for the requested heads with all three views."""
    from data.news_triage import _deterministic_d5a, _deterministic_d9, _map_model_label

    rows: List[Dict[str, Any]] = []
    with open(corpus_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    for head in heads:
        todo = [
            r for r in rows
            if r.get("stratum") == head and not (r.get("gold") or {}).get(head)
        ]
        if not todo:
            continue
        print("=" * 78)
        print(f"HEAD: {head}   ({len(todo)} unlabeled)")
        print(f"QID : {HEAD_QID[head]}")
        print(GUIDANCE[head])
        print("=" * 78)

        for rec in todo[:limit]:
            art = _article(rec)
            det = {
                "d5a": _deterministic_d5a(art),
                "d9_kill_switch": _deterministic_d9(art),
            }.get(head)
            model = _map_model_label(
                HEAD_QID[head], (rec.get("laya_answers") or {}).get(HEAD_QID[head])
            )
            print(f"\nid       : {rec['id']}")
            print(f"headline : {(rec.get('summary') or '')[:220]}")
            print(f"publisher: {rec.get('publisher') or rec.get('provider') or '-'}")
            print(f"observed : {(rec.get('observed') or {}).get(head)}")
            print(f"det      : {det}")
            print(f"model    : {model or '(abstain)'}")
            print(f"LABEL    : ____________________")
            print(f"NOTE     : ____________________________________")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--draft", action="store_true", help="print unlabeled rows for review")
    ap.add_argument("--heads", default="d5a,d9_kill_switch",
                    help="comma-separated heads to draft")
    ap.add_argument("--limit", type=int, default=40, help="rows per head")
    args = ap.parse_args(argv)

    if args.draft:
        heads = [h.strip() for h in args.heads.split(",") if h.strip()]
        return draft(args.corpus, heads, args.limit)

    from scripts.score_triage_corpus import main as score_main

    return score_main([])


if __name__ == "__main__":
    sys.exit(main())