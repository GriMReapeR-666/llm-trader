#!/usr/bin/env python3
"""Score Layer-0 triage heads against a labeled corpus.

Answers the question the repo could not previously answer: **how accurate is
each head, per label, against real data with known-correct answers?**

Runs each of three systems over the corpus and reports per-head accuracy plus
a full confusion matrix:

- ``det``    — deterministic labeler (the baseline that must not be beaten)
- ``model``  — raw Laya answer, translated to gate vocabulary
- ``fused``  — the actual ``_pick_better`` output (what production uses)

The ``model`` row is the number that was previously missing. Note that
``fused >= det`` on safety heads is *by construction*, since escalation
authority was removed — so ``fused`` accuracy alone cannot detect a fabricated
escalation. Read the ``model`` confusion matrix for that; a head whose model
row shows large mass in an escalation column while ``det`` is flat is exactly
the D5a/D9 failure mode found in production.

``auto_consensus`` rows are EXCLUDED from the headline numbers by default. A
consensus label (deterministic and model agree) is not evidence about which is
right, so including it would inflate accuracy toward 100%. Use
``--include-auto`` only to sanity-check plumbing.

Usage
-----
    python scripts/score_triage_corpus.py
    python scripts/score_triage_corpus.py --corpus path.jsonl --json out.json
    python scripts/score_triage_corpus.py --min-examples 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_CORPUS = os.path.join(REPO_ROOT, "research", "decision_models", "triage_corpus.jsonl")

# Head field -> (question id used by _pick_better, enum class attr on news_triage)
HEADS = {
    "d0_1": "D0_1_source_credibility",
    "d0_2": "D0_2_duplicate_detection",
    "d0_3": "D0_3_fact_vs_opinion",
    "d5a": "D5a_escalation_ladder",
    "d9_kill_switch": "D9_emergency_kill_switch",
    "r1": "R1_headline_event_routing",
}

# Head -> labels that constitute a severity escalation. Used to report
# "escalation rate": a model that invents escalations shows up here even when
# overall accuracy looks fine.
ESCALATION_LABELS = {
    "d5a": {"PROXY_CLASH", "DIRECT_STRIKE", "CHOKEPOINT_DISRUPTION", "FULL_BLOCKADE"},
    "d9_kill_switch": {"EMERGENCY_HALT_ACTIVE"},
}


def _load(corpus_path: str, include_auto: bool) -> List[Dict[str, Any]]:
    if not os.path.exists(corpus_path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(corpus_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not include_auto and rec.get("source") == "auto_consensus":
                continue
            if not any((rec.get("gold") or {}).values()):
                continue
            rows.append(rec)
    return rows


def _article(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": rec.get("summary") or "",
        "text_or_summary": rec.get("summary") or "",
        "url": rec.get("url") or "",
        "publisher": rec.get("publisher") or "",
    }


def _det_labels(rec: Dict[str, Any]) -> Dict[str, str]:
    """Recompute the deterministic labeler for this headline.

    Dedupe-dependent heads are skipped: the journal does not retain the
    ingest-time ``seen_keys`` set, so D0_2 cannot be reproduced faithfully and
    is reported as absent rather than guessed.
    """
    from data.news_triage import _deterministic_d5a, _deterministic_d9

    art = _article(rec)
    return {
        "d5a": _deterministic_d5a(art),
        "d9_kill_switch": _deterministic_d9(art),
    }


def score(corpus_path: str, include_auto: bool, min_examples: int) -> Dict[str, Any]:
    from data.news_triage import _map_model_label, _pick_better

    rows = _load(corpus_path, include_auto)
    if not rows:
        return {
            "corpus": corpus_path,
            "included_auto_consensus": include_auto,
            "labeled_rows": 0,
            "labeled_per_head": {},
            "per_head": {},
            "note": (
                "No labeled rows (corpus missing or every gold field is null). "
                "Label the `gold` fields first — see scripts/label_triage_corpus.py."
            ),
        }

    per_head: Dict[str, Dict[str, Any]] = {}
    totals = Counter()

    for field, qid in HEADS.items():
        confusion: Dict[str, Dict[str, Counter]] = {
            sys_name: defaultdict(Counter) for sys_name in ("det", "model", "fused")
        }
        n = 0
        escalations = Counter()

        for rec in rows:
            gold = (rec.get("gold") or {}).get(field)
            if not gold:
                continue
            n += 1
            totals[field] += 1

            det = _det_labels(rec).get(field)
            model_raw = _map_model_label(qid, (rec.get("laya_answers") or {}).get(qid))
            if det is not None:
                confusion["det"][str(gold)][str(det)] += 1
            if model_raw:
                confusion["model"][str(gold)][model_raw] += 1
                if model_raw in ESCALATION_LABELS.get(field, set()):
                    escalations["model"] += 1
            if det is not None:
                fused = _pick_better(qid, (rec.get("laya_answers") or {}).get(qid), det, None)
                confusion["fused"][str(gold)][str(fused)] += 1
                if fused in ESCALATION_LABELS.get(field, set()):
                    escalations["fused"] += 1
            if det is not None and det in ESCALATION_LABELS.get(field, set()):
                escalations["det"] += 1

        if n == 0:
            continue

        accuracy = {}
        for sys_name in ("det", "model", "fused"):
            # Weight by cell count, not by distinct predicted labels — an
            # unweighted sum collapses 89 correct rows in one cell to 1.
            hit = sum(
                sum(c for pred, c in confusion[sys_name][g].items() if pred == g)
                for g in confusion[sys_name]
            )
            seen = sum(sum(confusion[sys_name][g].values()) for g in confusion[sys_name])
            accuracy[sys_name] = round(hit / seen, 4) if seen else None

        per_head[field] = {
            "question_id": qid,
            "examples": n,
            "sufficient_for_claim": n >= min_examples,
            "accuracy": accuracy,
            "escalation_rate": {
                k: round(v / n, 4) for k, v in escalations.items()
            },
            "confusion": {
                sys_name: {g: dict(preds) for g, preds in sorted(confusion[sys_name].items())}
                for sys_name in ("det", "model", "fused")
            },
        }

    return {
        "corpus": corpus_path,
        "included_auto_consensus": include_auto,
        "labeled_rows": len(rows),
        "labeled_per_head": dict(totals),
        "per_head": per_head,
        "reading_guide": (
            "`model` accuracy is the honest Laya figure. `fused` >= `det` on "
            "D5a/D9 is guaranteed by construction (escalation authority removed), "
            "so judge fabricated risk from `model` escalation_rate vs `det`, not "
            "from fused accuracy. Heads with sufficient_for_claim=false cannot "
            "support a model-selection decision."
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--json", dest="json_out", default="", help="also write the report here")
    ap.add_argument("--include-auto", action="store_true",
                    help="include auto_consensus rows (inflates accuracy; plumbing check only)")
    ap.add_argument("--min-examples", type=int, default=20,
                    help="labeled examples required before a head can support a claim")
    args = ap.parse_args(argv)

    report = score(args.corpus, args.include_auto, args.min_examples)

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"report written: {args.json_out}\n")

    print(f"corpus: {report['corpus']}")
    print(f"labeled rows: {report['labeled_rows']}")
    print()
    header = f"{'head':<16}{'n':>5}{'det':>9}{'model':>9}{'fused':>9}  {'claim?'}"
    print(header)
    print("-" * len(header))
    for field, data in report.get("per_head", {}).items():
        acc = data["accuracy"]
        def fmt(k):
            v = acc.get(k)
            return f"{v:.3f}" if isinstance(v, (int, float)) else "  -  "
        print(
            f"{field:<16}{data['examples']:>5}{fmt('det'):>9}{fmt('model'):>9}{fmt('fused'):>9}"
            f"  {'yes' if data['sufficient_for_claim'] else 'NO'}"
        )

    for field, data in report.get("per_head", {}).items():
        esc = data["escalation_rate"]
        if any(esc.values()):
            print(f"\n{field} escalation_rate: " + ", ".join(f"{k}={v:.3f}" for k, v in sorted(esc.items())))

    if not report.get("per_head"):
        print(report.get("note", ""))
    else:
        print("\n" + report["reading_guide"])
    return 0


if __name__ == "__main__":
    sys.exit(main())