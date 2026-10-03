"""Resumable runner for macro-correct benchmark Parts 2-4 (temp diagnostics helper).

Part 1 already completed and is captured in artifacts/mcb_out.txt; this script
recomputes only the remaining parts, checkpointing every model call to JSON so
a restart can never lose more than one call (~90 s of work).
"""
import json
import os
import sys
import time
import traceback

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WORKSPACE_ROOT)

from research.decision_models.laya_model import get_laya_decision_model
from research.decision_models.macro_ontology import (
    EconomicSurprise,
    MacroContextFeatures,
    OpportunityArbitration,
    get_standard_macro_questions,
)
from research.decision_models.post_validator import DeterministicPostValidator
from research.decision_models.scenario_generator import (
    EVENT_BLUEPRINTS,
    create_scenario_triple,
)
from research.test_macro_correct_benchmark import RECONSTRUCTED_ORIGINAL_12

CKPT = os.path.join(WORKSPACE_ROOT, "artifacts", "mcb_resume_ckpt.json")
OUT = os.path.join(WORKSPACE_ROOT, "artifacts", "macro_correct_benchmark_report.json")


def load_ckpt():
    if os.path.exists(CKPT):
        with open(CKPT, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ckpt(state):
    tmp = CKPT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, CKPT)


def cached_decide(state, name, model, validator, prompt_state, questions, features=None, **vkw):
    """decide + validate once, caching the validated answers keyed by name."""
    done = state.get(f"done_{name}")
    if done is not None:
        print(f"  [ckpt] {name} restored", flush=True)
        return done
    res = model.decide(prompt_state, questions)
    if features is not None:
        val = validator.validate(res.answers, features, **vkw)
        answers = dict(val.validated_answers)
    else:
        answers = dict(res.answers)
    state[f"done_{name}"] = answers
    save_ckpt(state)
    print(f"  [ckpt] {name} saved", flush=True)
    return answers


def main():
    print("=" * 80, flush=True)
    print("   MACRO-CORRECT BENCHMARK RESUME: Parts 2-4 (Part 1 in mcb_out.txt)", flush=True)
    print("=" * 80, flush=True)
    t0 = time.perf_counter()
    model = get_laya_decision_model("models/laya")
    validator = DeterministicPostValidator()
    state = load_ckpt()

    # -------- Part 2: Boundary Precision on Contrastive Triples --------
    print("\n--- Part 2: Evaluating Boundary Precision on Near-Miss Triples (±0.35σ) ---", flush=True)
    cpi_bp = EVENT_BLUEPRINTS[0]  # CPI
    pos_scen, neg_scen, conf_scen = create_scenario_triple(
        cpi_bp, target_z=2.1, regime_type="NORMAL", category="CANONICAL", triple_index=1
    )
    a_pos = cached_decide(state, "p2_pos", model, validator,
                          pos_scen.features.to_prompt_state(), pos_scen.questions, pos_scen.features)
    a_neg = cached_decide(state, "p2_neg", model, validator,
                          neg_scen.features.to_prompt_state(), neg_scen.questions, neg_scen.features)
    a_conf = cached_decide(state, "p2_conf", model, validator,
                           conf_scen.features.to_prompt_state(), conf_scen.questions, conf_scen.features)

    boundary_pass = (
        a_pos.get("D1_economic_surprise") == EconomicSurprise.EXTREME_HAWKISH.value
        and a_neg.get("D1_economic_surprise") == EconomicSurprise.MODERATE_HAWKISH.value
    )
    print(f"  Positive Z=+2.10 -> {a_pos.get('D1_economic_surprise')} (Target: EXTREME_HAWKISH)", flush=True)
    print(f"  Near-Miss Z=+1.75 -> {a_neg.get('D1_economic_surprise')} (Target: MODERATE_HAWKISH)", flush=True)
    print(f"  Confounder (garbled, Z=+2.10) -> {a_conf.get('D1_economic_surprise')}", flush=True)
    print(f"  Boundary Discrimination Result: {'PASSED' if boundary_pass else 'FAILED'}", flush=True)

    # -------- Part 3: Crisis Veto & Freeze Recall --------
    print("\n--- Part 3: Crisis Veto & Freeze Recall Verification (100% Target) ---", flush=True)
    crisis_scenarios = [
        s for s in RECONSTRUCTED_ORIGINAL_12
        if s["features"].vix >= 30.0 or s["features"].minutes_to_release <= 2.0 or s["features"].is_adversarial
    ]
    total_crisis = len(crisis_scenarios)
    freeze_recalled = 0
    crisis_detail = []
    for i, cs in enumerate(crisis_scenarios):
        ans = cached_decide(state, f"p3_{i}", model, validator,
                            cs["features"].to_prompt_state(), get_standard_macro_questions(),
                            cs["features"])
        recalled = ans.get("blackout_freeze_boolean") is True
        freeze_recalled += int(recalled)
        crisis_detail.append({"id": cs["id"], "freeze_recalled": recalled})
        print(f"  {cs['id']:<32} freeze={'RECALLED' if recalled else 'MISSED'}", flush=True)
    freeze_recall_pct = round(freeze_recalled / total_crisis * 100, 1)
    print(f"  Crisis Scenarios Tested : {total_crisis}", flush=True)
    print(f"  Blackout Freezes Recalled: {freeze_recalled} / {total_crisis}", flush=True)
    print(f"  Freeze Safety Recall     : {freeze_recall_pct}% (Production Target: 100.0%)", flush=True)

    # -------- Part 4: Cascade Consistency --------
    print("\n--- Part 4: Cascade Cross-Decision Consistency Audit ---", flush=True)
    feat_shock = MacroContextFeatures(
        event_name="US CPI Extreme Inflation Shock",
        tier="HIGH", actual=4.5, consensus=3.2, sigma_hist=0.2, z_score=6.5,
        vix=26.0, minutes_to_release=0.0,
    )
    a_cas = cached_decide(state, "p4_cascade", model, validator,
                          feat_shock.to_prompt_state(), get_standard_macro_questions(),
                          feat_shock, proposed_direction="BUY", symbol="EURUSD")
    arb = a_cas.get("D8_opportunity_arbitration")
    cascade_consistent = arb in (
        OpportunityArbitration.REJECT_MACRO_CONFLICT.value,
        OpportunityArbitration.BLACKOUT_POSTPONE.value,
    )
    print("  Shock Event           : US CPI +6.5σ Beat", flush=True)
    print("  Proposed Trade Signal : BUY EURUSD (Pro-cyclical Long)", flush=True)
    print(f"  Arbitration Verdict   : {arb}", flush=True)
    print(f"  Cascade Consistency   : {'PASSED (Zero Contradictions)' if cascade_consistent else 'FAILED'}", flush=True)

    # -------- Report --------
    report = {
        "benchmark_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_under_test": "Laya (ModernBERT-421M) + Deterministic Post-Validator",
        "note": "Parts 2-4 from checkpointed resume (research/_mcb_resume.py); Part 1 captured in artifacts/mcb_out.txt",
        "boundary_precision": {
            "test_pair": "+2.10σ vs +1.75σ",
            "passed": boundary_pass,
            "pos_answer": a_pos.get("D1_economic_surprise"),
            "neg_answer": a_neg.get("D1_economic_surprise"),
            "confounder_answer": a_conf.get("D1_economic_surprise"),
        },
        "crisis_freeze_recall": {
            "tested_scenarios": total_crisis,
            "recalled_freezes": freeze_recalled,
            "recall_pct": freeze_recall_pct,
            "target_met": (freeze_recall_pct == 100.0),
            "detail": crisis_detail,
        },
        "cascade_consistency": {
            "tested_link": "D1 Surprise -> D6 Yield -> D8 Arbitration",
            "passed": cascade_consistent,
            "arbitration_verdict": arb,
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written: {OUT}", flush=True)
    print(f"Resume wall time: {round(time.perf_counter() - t0, 1)}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        with open(os.path.join(WORKSPACE_ROOT, "artifacts", "mcb_resume_crash.txt"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
