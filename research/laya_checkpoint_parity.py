"""Real-checkpoint parity run for the triage stub harness.

Replays the stub-parity scenarios (corpus articles from the disagreement
seeder, plus the spoof-hazard cases) once against the real 421M Laya
checkpoint (``models/laya`` via ``get_laya_decision_model``), and reports
every point where the stub's assumed output shape diverges from what the
real adapter actually returns:

- per-question answer TYPE (str enum vs noul bool vs None)
- answer VOCABULARY (gate enum vs ontology criteria vs unknown)
- post-validation outcome (accepted vs deferred/floored) per question
- probabilities/confidences shape sanity
- per-article latency and verdict-source distribution

Output: ``artifacts/laya_parity_report.json``. No edits to production
stores; this run only observes.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

# NOTE: FAST_TEST deliberately NOT set — this run needs the real checkpoint.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "data", "intelligence"):
    sys.path.insert(0, os.path.join(ROOT, p) if p else ROOT)

from news_triage import (  # noqa: E402
    D0_1_SourceCredibility,
    D0_2_DuplicateDetection,
    D0_3_FactVsOpinion,
    D5a_EscalationLadder,
    R1_HeadlineEventRouting,
    _deterministic_labels,
    _get_laya,
    _pick_better,
    assess_source,
    dedupe_key_for,
    triage_articles,
)
from research.decision_models.macro_ontology import get_standard_macro_questions  # noqa: E402

REPORT = os.path.join("artifacts", "laya_parity_report.json")
JSONL = os.path.join("artifacts", "laya_parity_rows.jsonl")
N_UNIQUE = int(sys.argv[1]) if len(sys.argv) > 1 else 20
N_SPOOF = int(sys.argv[2]) if len(sys.argv) > 2 else 11

GATE_ENUMS = {
    "D0_1_source_credibility": {m.value for m in D0_1_SourceCredibility},
    "D0_2_duplicate_detection": {m.value for m in D0_2_DuplicateDetection},
    "D0_3_fact_vs_opinion": {m.value for m in D0_3_FactVsOpinion},
    "D5a_escalation_ladder": {m.value for m in D5a_EscalationLadder},
    "R1_headline_event_routing": {m.value for m in R1_HeadlineEventRouting},
}


def build_corpus(n_unique: int, n_spoof: int = 11) -> list:
    """Reuse the seeder's corpus builder; cap unique articles and spoofs."""
    sys.argv = [sys.argv[0]]  # seeder reads argv[1] too
    from research.triage_disagreement_seeder import build_corpus as _bc

    corpus = _bc()
    seen, unique, spoofs = set(), [], []
    for a in corpus:
        if a["dedupe_key"] in seen:
            continue
        seen.add(a["dedupe_key"])
        det = _deterministic_labels(a, assess_source(a["url"], a["publisher"]), set())
        if det["D0_1_source_credibility"] == D0_1_SourceCredibility.SPOOF_REJECTED.value:
            spoofs.append(a)
        elif len(unique) < n_unique:
            unique.append(a)
    return unique + spoofs[:n_spoof]


def classify_vocab(qid: str, val) -> str:
    if val is None:
        return "none"
    s = str(val)
    if isinstance(val, bool):
        return "bool"
    if s in GATE_ENUMS.get(qid, set()):
        return "gate_enum"
    crit = (get_standard_macro_questions().get(qid) or {}).get("criteria") or {}
    if s in crit:
        return "ontology_criteria"
    if s in GATE_ENUMS.get("R1_headline_event_routing", set()):
        return "r1_enum"
    return "unknown"


def main() -> None:
    model = _get_laya()
    if model is None:
        # Allow offline report regeneration when the incremental JSONL already
        # covers the requested corpus (no new inference needed).
        already = set()
        if os.path.exists(JSONL):
            with open(JSONL, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        already.add(json.loads(line)["dedupe_key"])
                    except Exception:
                        pass
        probe = build_corpus(N_UNIQUE, N_SPOOF)
        if not probe or not all(a["dedupe_key"] in already for a in probe):
            print("FATAL: real Laya checkpoint unavailable (models/laya missing or load failed)")
            sys.exit(2)
        print("checkpoint unavailable — regenerating report from completed JSONL rows only")
    else:
        print(f"checkpoint loaded: {type(model).__name__}")

    corpus = build_corpus(N_UNIQUE, N_SPOOF)
    print(f"corpus: {len(corpus)} unique articles (incl. spoofs)", flush=True)

    # Incremental run: one triage_article call per row, each result flushed to
    # JSONL keyed by dedupe_key, so a killed run resumes without re-spending
    # the ~19s/article CPU inference.
    done = set()
    if os.path.exists(JSONL):
        with open(JSONL, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["dedupe_key"])
                except Exception:
                    pass
    from news_triage import triage_article

    with open(JSONL, "a", encoding="utf-8") as out:
        for art in corpus:
            if art["dedupe_key"] in done:
                continue
            row = dict(art)
            v = triage_article(row, laya=model)
            out.write(json.dumps({"dedupe_key": row["dedupe_key"], "triage": v.to_dict()}, default=str) + "\n")
            out.flush()
            print(f"done {len(done) + 1}/{len(corpus)} src={v.source} {v.latency_ms:.0f}ms", flush=True)
            done.add(row["dedupe_key"])


    tri_rows: dict = {}
    with open(JSONL, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                tri_rows[rec["dedupe_key"]] = rec["triage"]
            except Exception:
                pass
    counts = Counter((t or {}).get("action") for t in tri_rows.values())
    report = {"counts": dict(counts), "mode": "laya_checkpoint"}
    rows = [dict(a) for a in corpus]

    # to_dict() drops deterministic_labels/laya_probabilities/laya_confidences,
    # so det baselines are recomputed here rather than read from the dicts.
    det_by_key: dict = {}
    for art in rows:
        pol = assess_source(art["url"], art["publisher"])
        det_by_key[art["dedupe_key"]] = _deterministic_labels(art, pol, set())

    # ---- divergence instrumentation ------------------------------------
    answer_types: Counter = Counter()
    vocab: dict = {}
    prob_issues: list = []
    conf_issues: list = []
    post_val = Counter()          # (qid, outcome) counts
    latencies: list = []
    sources: Counter = Counter()
    raw_examples: dict = {}       # qid -> example raw answers

    for art in rows:
        v = tri_rows.get(art["dedupe_key"]) or {}
        sources[v.get("source")] += 1
        if v.get("source") == "laya":
            latencies.append(float(v.get("latency_ms") or 0.0))
        answers = dict(v.get("laya_answers") or {})
        probs = dict(v.get("laya_probabilities") or {})
        confs_raw = v.get("laya_confidences")
        det = det_by_key.get(art["dedupe_key"]) or {}

        for qid, raw in answers.items():
            answer_types[f"{qid}:{type(raw).__name__}"] += 1
            vocab.setdefault(qid, Counter())[classify_vocab(qid, raw)] += 1
            raw_examples.setdefault(qid, raw)
            # Replay post-validation to classify the outcome.
            final_map = {
                "D0_1_source_credibility": v.get("d0_1"),
                "D0_2_duplicate_detection": v.get("d0_2"),
                "D0_3_fact_vs_opinion": v.get("d0_3"),
                "D5a_escalation_ladder": v.get("d5a"),
                "D9_emergency_kill_switch": v.get("d9_kill_switch"),
                "R1_headline_event_routing": v.get("r1"),
            }
            det_label = det.get(qid, "")
            final = final_map.get(qid)
            norm = str(raw or "").strip().upper()
            if not norm or norm == "NONE":
                post_val[f"{qid}:abstain"] += 1
            elif norm == str(det_label).upper():
                post_val[f"{qid}:parity"] += 1
            elif norm == str(final).upper():
                post_val[f"{qid}:model_accepted"] += 1
            else:
                post_val[f"{qid}:deferred_or_floored"] += 1
            # Probabilities shape
            p = probs.get(qid)
            if not isinstance(p, dict) or not p:
                prob_issues.append(f"{qid}:{type(p).__name__}:{p!r}"[:80])
            else:
                bad = [k for k, x in p.items() if not isinstance(x, (int, float))]
                if bad:
                    prob_issues.append(f"{qid}:nonfloat:{bad[:3]}")
            # Confidences shape
            c = (confs_raw or {}).get(qid) if isinstance(confs_raw, dict) else None
            if not isinstance(c, (int, float)) or not (0.0 <= float(c) <= 1.0):
                conf_issues.append(f"{qid}:{c!r}"[:60])

    lat_sorted = sorted(latencies)
    summary = {
        "checkpoint": "models/laya",
        "articles": len(corpus),
        "gate_counts": report.get("counts"),
        "mode": report.get("mode"),
        "verdict_sources": dict(sources),
        "latency_ms": {
            "mean": round(sum(latencies) / max(1, len(latencies)), 1),
            "p50": lat_sorted[len(lat_sorted) // 2] if lat_sorted else None,
            "max": max(latencies) if latencies else None,
        },
        "answer_types": dict(answer_types),
        "answer_vocab": {q: dict(c) for q, c in sorted(vocab.items())},
        "post_validation_outcomes": dict(post_val),
        "probability_shape_issues": prob_issues[:12],
        "confidence_shape_issues": conf_issues[:12],
        "raw_example_answers": raw_examples,
        "divergences_from_stub_assumptions": [],
    }

    # Named divergences, only when observed:
    divs = summary["divergences_from_stub_assumptions"]
    lat_mean = summary["latency_ms"]["mean"] or 0
    if lat_mean > 30000:
        divs.append(
            f"Latency: real checkpoint costs ~{lat_mean/1000:.0f}s/article (code comment claims ~19s) — "
            "per-article live triage on CPU is operationally infeasible for a real feed; the stub's 1.0ms hides this entirely."
        )
    d9_vocab = (vocab.get("D9_emergency_kill_switch") or {}).get("unknown", 0)
    if d9_vocab:
        divs.append(
            "D9_emergency_kill_switch is a noul question: the adapter returns a BOOL (probe: answer=False, probs={'false':0.899,'true':0.101}); "
            "str(False or '') serializes to '' in laya_answers, so the live model never escalates D9 (abstain). "
            "Stub emits enum strings ('EMERGENCY_HALT_ACTIVE') — not shape-faithful."
        )
    if any(k.endswith(":bool") for k in answer_types):
        divs.append("D9_emergency_kill_switch returns noul bool from the real adapter; stub emits enum strings. Gate absorbs it via _D9_TRUTHY ('TRUE'), but the stub is not shape-faithful.")
    for qid in ("D0_1_source_credibility", "D0_2_duplicate_detection"):
        c = vocab.get(qid, {})
        if c and all(k in ("ontology_criteria", "unknown", "none") for k in c):
            divs.append(f"{qid}: real model answers use ontology-criteria vocabulary disjoint from the gate enum — every answer defers to deterministic (model path inert on this question).")
    if any(k.endswith(":none") for k in answer_types):
        divs.append("Some answers arrive as None (adapter emits no choice); stub never emits None.")
    if any(k.endswith(":model_accepted") for k in post_val):
        divs.append("Real model produced at least one accepted override — the stub's parity assumption (always mirroring det) hides live disagreement texture.")
    if prob_issues:
        divs.append("to_dict() omits laya_probabilities/laya_confidences/deterministic_labels entirely — dict consumers (sweep aggregation, cycle summaries) cannot see model uncertainty; only the verdict OBJECT carries them.")
    if not divs:
        divs.append("No shape divergences observed — stub is shape-faithful on this corpus.")

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
