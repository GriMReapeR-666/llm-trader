#!/usr/bin/env python3
"""Seed the triage labeled corpus from real production headlines.

Why this exists
---------------
The only accuracy figure in the repo for the Layer-0 triage heads was
``artifacts/laya_extreme_benchmark_report.json``: 52.17% over 12 hand-built
macro scenarios. That is a coin flip, and it says nothing about the six heads
that actually run on every incoming headline. Meanwhile 2,697 real triaged
articles sat in the production journal with **no ground truth at all** —
``llm_decisions`` has 2 outcome rows out of 527, and ``setup_labels`` /
``research_validation_runs`` contain no triage labels.

So no model swap can be justified on evidence. This script builds the missing
piece: a human-labeled corpus of real headlines.

Method
------
Reads triage verdicts back out of the production journal, selects a stratified
sample across the six triage heads and the interesting label values, and emits
JSONL rows with ``gold: null`` for a human to fill in. It never invents labels.
``--auto-label`` is available but only for the subset where the deterministic
lexicon is unambiguous AND the model agreed with it — those rows are marked
``source: "auto_consensus"`` so they can be filtered out of any headline
accuracy claim.

Usage
-----
    python scripts/build_triage_corpus.py --limit 400
    python scripts/build_triage_corpus.py --stats
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_JOURNAL = os.path.join(REPO_ROOT, "trading_state.db")
DEFAULT_OUT = os.path.join(REPO_ROOT, "research", "decision_models", "triage_corpus.jsonl")

# The six Layer-0 heads that run on every headline.
TRIAGE_FIELDS = ("d0_1", "d0_2", "d0_3", "d5a", "d9_kill_switch", "r1")

# Stratification targets. D5a/D9 are the safety-critical heads and get the
# largest allocation because they are also the ones that had fabricated
# escalations; R1 is next because it drives event routing.
STRATA = (
    ("d9_kill_switch", 60),
    ("d5a", 80),
    ("r1", 100),
    ("d0_1", 60),
    ("d0_2", 40),
    ("d0_3", 60),
)

# Labels where the deterministic lexical rule is unambiguous enough that
# auto-labeling is defensible. Deliberately excludes every escalated D5a band
# and EMERGENCY_HALT_ACTIVE — those are exactly the labels under dispute.
AUTO_LABEL_SAFE = {
    "d5a": {"INSUFFICIENT_DATA", "RHETORIC_ONLY"},
    "d9_kill_switch": {"NORMAL_OPERATION", "INSUFFICIENT_DATA"},
}


def _iter_journal_rows(db_path: str) -> Iterable[Dict[str, Any]]:
    """Yield triage records from the production journal.

    Reads ``macro_sources.payload`` — the table that holds every ingested
    headline with its triage verdict. Read-only URI so a scoring run can never
    mutate the live journal.
    """
    if not os.path.exists(db_path):
        raise SystemExit(f"journal not found: {db_path}")
    uri = "file:%s?mode=ro" % db_path.replace("\\", "/")
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute(
            "SELECT payload FROM macro_sources WHERE payload LIKE '%\"triage\"%'"
        )
        for (payload,) in cur:
            try:
                rec = json.loads(payload)
            except (TypeError, ValueError):
                continue
            triage = rec.get("triage")
            if not isinstance(triage, dict):
                continue
            yield {
                "summary": rec.get("summary") or rec.get("title") or "",
                "url": rec.get("url") or "",
                "publisher": rec.get("publisher") or "",
                "provider": rec.get("provider") or "",
                "tier": (rec.get("source_policy") or {}).get("tier") or "",
                "triage": triage,
            }
    finally:
        con.close()


def _stratified_sample(
    rows: List[Dict[str, Any]], limit: int, seed: int
) -> List[Dict[str, Any]]:
    """Sample proportionally across label values, prioritising safety heads."""
    rng = random.Random(seed)
    chosen: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    for field, quota in STRATA:
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            label = str(row["triage"].get(field) or "")
            if label:
                buckets[label].append(row)

        # Round-robin across labels so rare-but-critical values (e.g.
        # EMERGENCY_HALT_ACTIVE) are not crowded out by NOT_EVENT_NOISE.
        order = sorted(buckets, key=lambda k: (len(buckets[k]), k))
        taken = 0
        while taken < quota and any(buckets.values()):
            progressed = False
            for label in order:
                if taken >= quota:
                    break
                if not buckets[label]:
                    continue
                row = buckets[label].pop(rng.randrange(len(buckets[label])))
                key = row["url"] or row["summary"][:120]
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                chosen.append({"row": row, "stratum": field})
                taken += 1
                progressed = True
            if not progressed:
                break

    rng.shuffle(chosen)
    if limit and len(chosen) > limit:
        chosen = chosen[:limit]
    return chosen


def _auto_label(row: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Derive gold labels for rows where the lexicon is unambiguous.

    The persisted verdicts do NOT carry ``deterministic_labels`` (confirmed:
    0 of 2,697 journal rows have it), so the deterministic side is recomputed
    here from the headline text using the same pure-lexical functions triage
    uses at runtime. That is exact, not an approximation — ``_deterministic_d5a``
    and ``_deterministic_d9`` read nothing but title/summary.

    A label is emitted only when the recomputed deterministic label AGREES with
    the model's answer after vocabulary translation. A consensus row carries no
    information about which side is right, which is precisely why it is not
    ground truth — hence the ``auto_consensus`` tag and the scorer's exclusion
    of these rows from headline accuracy.

    Only the text-only heads qualify. D0_2 depends on the dedupe-set state at
    ingest time, which is not reconstructible from a journal row, so it is never
    auto-labeled.
    """
    from data.news_triage import _deterministic_d5a, _deterministic_d9, _map_model_label

    triage = row["triage"]
    model_answers = triage.get("laya_answers") or {}
    if not model_answers:
        return None

    article = {
        "title": row["summary"],
        "text_or_summary": row["summary"],
    }
    gold: Dict[str, str] = {}

    for field, head, fn in (
        ("d5a", "D5a_escalation_ladder", _deterministic_d5a),
        ("d9_kill_switch", "D9_emergency_kill_switch", _deterministic_d9),
    ):
        det_val = fn(article)
        if det_val not in AUTO_LABEL_SAFE.get(field, set()):
            continue
        model_val = _map_model_label(head, model_answers.get(head))
        if model_val and model_val != det_val:
            continue  # disagreement -> not consensus -> no auto label
        gold[field] = det_val

    return gold or None


def build(db_path: str, out_path: str, limit: int, seed: int, auto_label: bool) -> Dict[str, Any]:
    rows = list(_iter_journal_rows(db_path))
    if not rows:
        raise SystemExit("no triage records found in journal")

    sampled = _stratified_sample(rows, limit, seed)
    written = 0
    auto_written = 0

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for item in sampled:
            row = item["row"]
            triage = row["triage"]
            record: Dict[str, Any] = {
                "id": f"{item['stratum']}-{written:05d}",
                "stratum": item["stratum"],
                "summary": row["summary"],
                "url": row["url"],
                "publisher": row["publisher"],
                "provider": row["provider"],
                "tier": row["tier"],
                "observed": {f: triage.get(f) for f in TRIAGE_FIELDS},
                "laya_answers": triage.get("laya_answers") or {},
                "gold": {f: None for f in TRIAGE_FIELDS},
                "source": "unlabeled",
            }
            if auto_label:
                derived = _auto_label(row)
                if derived:
                    for k, v in derived.items():
                        record["gold"][k] = v
                    record["source"] = "auto_consensus"
                    auto_written += 1
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    return {
        "journal": db_path,
        "output": out_path,
        "journal_records": len(rows),
        "written": written,
        "auto_labeled": auto_written,
        "seed": seed,
    }


def stats(out_path: str) -> Dict[str, Any]:
    """Report corpus coverage and label balance."""
    if not os.path.exists(out_path):
        raise SystemExit(f"corpus not found: {out_path} (run without --stats first)")

    per_field = {f: Counter() for f in TRIAGE_FIELDS}
    labeled = Counter()
    sources = Counter()
    total = 0

    with open(out_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            total += 1
            sources[rec.get("source", "unlabeled")] += 1
            gold = rec.get("gold") or {}
            for field in TRIAGE_FIELDS:
                if gold.get(field):
                    labeled[field] += 1
                    per_field[field][str(gold[field])] += 1

    return {
        "path": out_path,
        "rows": total,
        "sources": dict(sources),
        "labeled_per_field": dict(labeled),
        "label_distribution": {f: dict(c) for f, c in per_field.items()},
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", default=DEFAULT_JOURNAL, help="production journal db")
    ap.add_argument("--out", default=DEFAULT_OUT, help="corpus JSONL output path")
    ap.add_argument("--limit", type=int, default=400, help="max rows (0 = all sampled)")
    ap.add_argument("--seed", type=int, default=17, help="sampling seed")
    ap.add_argument("--auto-label", action="store_true",
                    help="fill consensus-only labels (marked auto_consensus, exclude from headline accuracy)")
    ap.add_argument("--stats", action="store_true", help="report coverage of an existing corpus")
    args = ap.parse_args(argv)

    if args.stats:
        print(json.dumps(stats(args.out), indent=2))
        return 0

    summary = build(args.journal, args.out, args.limit, args.seed, args.auto_label)
    print(json.dumps(summary, indent=2))
    print("\nNext: label the `gold` fields, then score with scripts/score_triage_corpus.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())