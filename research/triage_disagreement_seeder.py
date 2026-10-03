"""Offline disagreement harvest: triage corpus -> active-learning ledger.

Runs the full triage-related test corpus (string literals AST-harvested from
the triage test files — no test execution, no imports of the tests) through
the Layer-0 triage gate twice:

1. Deterministic pass (``laya=None``) — records the deterministic Layer-0
   labels for every corpus headline.
2. Perturbation pass — an injected stub Laya answers each triage question
   with the deterministic label most of the time but, with a fixed
   per-question probability, substitutes a *different known label* from the
   same enum. Every raw answer that differs from the deterministic rule is a
   disagreement, exactly as the live gate sees one, and flows through the
   normal post-validation doctrine (floors, monotone escalation, 0.5
   probability floor, R1 add-concrete-only) before landing.

Every (article, question) disagreement is logged via
``TriageDisagreementLogger.log_verdict`` into:

- ``artifacts/triage_disagreement_sweep.db``  (disagreement ledger, same
  schema as ``macro_active_learning_disagreements``)
- ``artifacts/rlcd_training_pool_sweep.db``   (contrastive RLCD pairs,
  SAFETY_NEGATIVE weighting per the logger's doctrine)

Isolation: sweep stores are separate from production
(``artifacts/llm_decisions.db`` / ``artifacts/rlcd_training_pool.db``) —
seeding the next training batch must not pollute live stores. FAST_TEST is
forced before any news_triage import so the stub harness never touches the
421M checkpoint. Report: ``artifacts/triage_disagreement_seeds.json``.
"""
from __future__ import annotations

import ast
import json
import os
import random
import sys

os.environ["FAST_TEST"] = "1"  # must precede news_triage import: keeps _get_laya() offline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("", "data", "intelligence"):
    sys.path.insert(0, os.path.join(ROOT, p) if p else ROOT)

from news_triage import (  # noqa: E402
    D0_1_SourceCredibility,
    D0_2_DuplicateDetection,
    D0_3_FactVsOpinion,
    D5a_EscalationLadder,
    D9_KILL_SWITCH_ACTIVE,
    _R1_CONCRETE,
    _deterministic_labels,
    assess_source,
    dedupe_key_for,
    triage_articles,
)
from research.learning.contrastive_pair_miner import ContrastivePairMiner  # noqa: E402
from research.learning.triage_ledger import TriageDisagreementLogger  # noqa: E402

TEST_FILES = [
    "tests/test_news_triage.py",
    "tests/test_d9_kill_switch_triage.py",
    "tests/test_d9_kill_switch_latch.py",
    "tests/test_d5a_triage.py",
    "tests/test_d12_r1_questions.py",
    "tests/test_realtime_news_ingest.py",
    "tests/test_triage_ledger.py",
    "tests/test_news_evidence_routing.py",
    "tests/test_macro_graph_hardening.py",
    "tests/test_triage_model_path.py",
    "tests/test_data_source_pipeline.py",
    "tests/test_r1_mirroring.py",
]

LEDGER_DB = os.path.join("artifacts", "triage_disagreement_sweep.db")
POOL_DB = os.path.join("artifacts", "rlcd_training_pool_sweep.db")
REPORT = os.path.join("artifacts", "triage_disagreement_seeds.json")

# Per-question probability that the stub substitutes a different known label
# (perturbation rate). Chosen small so the corpus keeps a realistic
# majority-agreement texture while yielding hundreds of seeds.
PERTURB_RATE = {
    "D0_1_source_credibility": 0.15,
    "D0_2_duplicate_detection": 0.15,
    "D0_3_fact_vs_opinion": 0.15,
    "D5a_escalation_ladder": 0.20,
    "D9_emergency_kill_switch": 0.10,
    "R1_headline_event_routing": 0.20,
}

R1_ALTERNATIVES = sorted(_R1_CONCRETE)


def harvest_strings(path: str) -> list:
    """Pull string literals that plausibly carry news text from a test file."""
    try:
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError):
        return []
    found = []

    def visit(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if 8 <= len(s) <= 400 and sum(c.isalpha() for c in s) >= 6:
                found.append(s)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def build_corpus() -> list:
    """Deduped corpus articles; the first 25 titles re-appended as same-batch
    duplicates so D0_2 STALE_REPEAT disagreement paths get real texture."""
    titles = list(dict.fromkeys(
        s for tf in TEST_FILES for s in harvest_strings(tf)
    ))
    corpus = []
    for i, title in enumerate(titles):
        corpus.append({
            "title": title,
            "text_or_summary": title,
            "url": f"https://www.reuters.com/world/us/sweep-{i}",
            "publisher": "Reuters",
            "provider": "test_corpus_sweep",
            "dedupe_key": dedupe_key_for(f"https://www.reuters.com/world/us/sweep-{i}", title),
        })
    for i, art in enumerate(corpus[:25]):
        corpus.append(dict(art))  # identical url+title -> same dedupe key
    return corpus


class PerturbLaya:
    """Stub Laya: deterministic-parity answers with controlled perturbations.

    ``decide`` recovers the source headline from the prompt state, looks up
    its deterministic labels (recorded in the pass-1 sweep), and perturbs a
    per-question random subset with alternative known labels at high
    confidence — so every floored override the live doctrine allows is
    exercised, and every raw divergence becomes a ledger disagreement.
    """

    def __init__(self, det_by_title: dict, seed: int = 20260923) -> None:
        self._det = det_by_title
        self._rng = random.Random(seed)
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        title = str((state or {}).get("event", {}).get("name", ""))
        det = dict(self._det.get(title) or {})
        answers, probabilities = {}, {}
        for qid in questions:
            label = str(det.get(qid, "") or "")
            alt_pool = _alternatives(qid, label)
            if alt_pool and self._rng.random() < PERTURB_RATE.get(qid, 0.0):
                label = self._rng.choice(alt_pool)
            answers[qid] = label
            probabilities[qid] = {label.lower(): 0.9}
        return _StubResult(answers, probabilities)


def _alternatives(qid: str, det_label: str) -> list:
    """Known labels for ``qid`` other than the deterministic one."""
    pools = {
        "D0_1_source_credibility": [m.value for m in D0_1_SourceCredibility],
        "D0_2_duplicate_detection": [m.value for m in D0_2_DuplicateDetection],
        "D0_3_fact_vs_opinion": [m.value for m in D0_3_FactVsOpinion],
        "D5a_escalation_ladder": [m.value for m in D5a_EscalationLadder],
        "D9_emergency_kill_switch": [D9_KILL_SWITCH_ACTIVE, "NORMAL_OPERATION"],
        "R1_headline_event_routing": R1_ALTERNATIVES + ["NOT_EVENT_NOISE"],
    }
    return [v for v in pools.get(qid, []) if v and v != det_label]


class _StubResult:
    def __init__(self, answers, probabilities):
        self.answers = dict(answers)
        self.probabilities = {q: dict(probabilities.get(q, {})) for q in answers}
        self.confidences = {q: 0.9 for q in answers}
        self.latency_ms = 1.0
        self.input_tokens = 10
        self.output_tokens = 0


class SpoofHazardLaya:
    """Focused hazard stub for det-spoof articles: answers the model-hazard
    label (VERIFIED_OUTLET — spam that looks legitimate) on D0_1 and abstains
    elsewhere. Doctrine check on every run: the REJECT floor must hold in the
    gate while the hazard pairs seed at 5x weight."""

    def decide(self, state, questions):
        answers, probabilities = {}, {}
        for qid in questions:
            answers[qid] = "VERIFIED_OUTLET" if qid == "D0_1_source_credibility" else "INSUFFICIENT_DATA"
            probabilities[qid] = {answers[qid].lower(): 0.9}
        return _StubResult(answers, probabilities)


def seed_spoof_hazards(corpus: list, logger: TriageDisagreementLogger) -> dict:
    """Guaranteed D0_1 safety-pair coverage.

    The perturbation draw cannot promise the rare det-spoof corpus articles
    get their D0_1 answer perturbed, so this focused pass always exercises
    the model-tries-to-lax-a-spoof hazard. Runs through the same gate and
    ledger sink; REJECT enforcement is asserted, not assumed.
    """
    seen, spoof = set(), []
    for a in corpus:
        if a["dedupe_key"] in seen:
            continue
        seen.add(a["dedupe_key"])
        det = _deterministic_labels(a, assess_source(a["url"], a["publisher"]), set())
        if det["D0_1_source_credibility"] == D0_1_SourceCredibility.SPOOF_REJECTED.value:
            spoof.append(a)
    rows = [dict(a) for a in spoof]
    report = triage_articles(rows, laya=SpoofHazardLaya(), on_verdict=logger.log_verdict)
    return {
        "spoof_articles": len(spoof),
        "gate_counts": report["counts"],
        "ledger_rows": logger._session_ledger_rows,
        "pairs": logger._session_pairs,
    }


def main() -> None:
    corpus = build_corpus()
    print(f"corpus: {len(corpus)} articles from {len(TEST_FILES)} test files")

    # Pass 1 — deterministic labels per headline.
    det_rows = [dict(a) for a in corpus]
    triage_articles(det_rows, laya=None)
    det_by_title = {
        str(r["title"]): dict((r.get("triage") or {}).get("deterministic_labels") or {})
        for r in det_rows
    }
    det_mode = (det_rows[0].get("triage") or {}).get("source")
    print(f"pass 1: deterministic labels recorded (source={det_mode!r})")

    # Pass 2 — perturbed stub through the gate with the ledger sink attached.
    logger = TriageDisagreementLogger(db_path=LEDGER_DB, miner=ContrastivePairMiner(db_path=POOL_DB))
    stub = PerturbLaya(det_by_title)
    rows = [dict(a) for a in corpus]
    report = triage_articles(rows, laya=stub, on_verdict=logger.log_verdict)

    # Aggregate the disagreements actually logged (source=laya rows only).
    by_q, by_type, safety = {}, {}, 0
    for r in rows:
        v = r.get("triage") or {}
        if v.get("source") != "laya":
            continue
        for qid in v.get("disagreements") or []:
            by_q[qid] = by_q.get(qid, 0) + 1
    spoof_pass = seed_spoof_hazards(corpus, logger)

    import sqlite3

    pool_total = safety_total = 0
    try:
        with sqlite3.connect(POOL_DB) as conn:
            pool_total = conn.execute("SELECT COUNT(*) FROM contrastive_pairs").fetchone()[0]
            safety_total = conn.execute(
                "SELECT COUNT(*) FROM contrastive_pairs WHERE pair_type='SAFETY_NEGATIVE'"
            ).fetchone()[0]
    except sqlite3.Error:
        pass

    summary = {
        "corpus_articles": len(corpus),
        "stub_calls": stub.calls,
        "mode": report.get("mode"),
        "gate_counts": report.get("counts"),
        "disagreements_by_question": by_q,
        "disagreements_total": sum(by_q.values()),
        "spoof_hazard_pass": spoof_pass,
        "pool_total_pairs": pool_total,
        "pool_safety_pairs": safety_total,
        "ledger_db": LEDGER_DB,
        "pair_pool_db": POOL_DB,
        "ledger_rows_logged": logger._session_ledger_rows,
        "pairs_persisted": logger._session_pairs,
        "seed": 20260923,
        "perturb_rate": PERTURB_RATE,
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
