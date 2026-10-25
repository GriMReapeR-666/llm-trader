"""Hybrid Causal Strategy Reasoner & Alpha-Grafting Engine (Pillar 2 / Stage 2).

Translates forensic autopsy telemetry into targeted genome mutations and grafts
orthogonal, validated FactorMiner alphas when core strategy signals fail.
"""
from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class StrategyReasoner:
    """Proposes structural mutations and alpha graftings to repair underperforming strategies."""

    def __init__(self, llm_router: Optional[Any] = None):
        self.llm_router = llm_router

    def propose_improvement_branches(
        self,
        strategy_id: str,
        parent_genome: Dict[str, Any],
        autopsy_report: Dict[str, Any],
        failed_commit_diffs: List[Dict[str, Any]],
        validated_alphas_pool: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Return candidate mutation branches based on autopsy evidence and alpha pool."""
        candidates: List[Dict[str, Any]] = []
        base = copy.deepcopy(parent_genome)
        recommended = autopsy_report.get("recommended_mutations", [])

        # 1. Deterministic Structural Repair from Forensic Autopsy
        if recommended:
            mutated = copy.deepcopy(base)
            applied_ops: List[str] = []

            for rec in recommended:
                op = rec.get("op")
                if op == "INVERT_ENTRY_SIGNAL":
                    mutated["signal_polarity"] = -1 * int(mutated.get("signal_polarity", 1))
                    applied_ops.append("invert-polarity")

                elif op == "WIDEN_SL_ATR_MULT":
                    old_sl = float(mutated.get("sl_atr_mult", 1.5))
                    mutated["sl_atr_mult"] = round(min(old_sl * float(rec.get("scale", 1.3)), 3.5), 2)
                    applied_ops.append("widen-sl")

                elif op == "LOWER_BREAKEVEN_TRIGGER_R":
                    mutated["breakeven_trigger_r"] = float(rec.get("new_trigger_r", 0.95))
                    applied_ops.append("early-be-ratchet")

                elif op == "RESTRICT_ALLOWED_REGIMES":
                    mutated["allowed_regimes"] = list(rec.get("allowed_regimes", []))
                    applied_ops.append("regime-lock")

                elif op == "INCREASE_MIN_TARGET_ATR":
                    old_tp = float(mutated.get("tp_atr_mult", 2.5))
                    mutated["tp_atr_mult"] = round(min(old_tp * float(rec.get("scale", 1.4)), 6.0), 2)
                    applied_ops.append("widen-tp-friction")

                elif op == "RESTRICT_ALLOWED_HOURS":
                    mutated["allowed_hours"] = list(rec.get("allowed_hours", []))
                    applied_ops.append("hours-lock")

            if applied_ops and mutated != base:
                branch_slug = "-".join(applied_ops[:2])
                candidates.append({
                    "branch_name": f"shadow/fix-{branch_slug}",
                    "mutated_genome": mutated,
                    "commit_message": f"Autopsy repair [{', '.join(applied_ops)}] on {strategy_id}",
                })

        # 2. Structural Alpha-Grafting (when core signal is weak across ALL regimes)
        has_regime_rescue = any(r.get("op") == "RESTRICT_ALLOWED_REGIMES" for r in recommended)
        if (not has_regime_rescue or float(autopsy_report.get("overall_mean_r", 0.0)) < 0.05) and validated_alphas_pool:
            # Sort validated alphas by highest IC_IR and low turnover
            ranked_alphas = sorted(
                validated_alphas_pool,
                key=lambda a: float(a.get("ic_ir", 0.0)) * (1.0 - 0.3 * float(a.get("turnover", 0.5))),
                reverse=True,
            )
            for idx, best_alpha in enumerate(ranked_alphas[:2]):
                graft_genome = copy.deepcopy(base)
                graft_genome["primary_alpha_dsl"] = best_alpha["expression"]
                graft_genome["signal_polarity"] = 1
                candidates.append({
                    "branch_name": f"shadow/graft-alpha-{idx + 1}",
                    "mutated_genome": graft_genome,
                    "commit_message": (
                        f"Grafted FactorMiner alpha (IC_IR={best_alpha.get('ic_ir', 0):.2f}): "
                        f"{best_alpha['expression'][:45]}"
                    ),
                })

        # 3. Optional LLM Causal Refinement Branch (if LLMRouter is online)
        if self.llm_router is not None:
            llm_branch = self._query_llm_mutation(strategy_id, base, autopsy_report, failed_commit_diffs)
            if llm_branch is not None:
                candidates.append(llm_branch)

        return candidates[:3]

    def _query_llm_mutation(
        self,
        strategy_id: str,
        parent_genome: Dict[str, Any],
        autopsy_report: Dict[str, Any],
        failed_commit_diffs: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        prompt = (
            f"Strategy '{strategy_id}' failed validation.\n"
            f"Current Genome: {json.dumps(parent_genome)}\n"
            f"Forensic Autopsy: {json.dumps(autopsy_report)}\n"
            f"Previously Rejected Diffs (DO NOT REPEAT): {json.dumps(failed_commit_diffs[-5:])}\n"
            "Return ONLY a JSON object with keys: 'mutated_genome' (matching genome schema) and 'commit_message'."
        )
        try:
            if hasattr(self.llm_router, "generate_json"):
                resp = self.llm_router.generate_json(agent="trader", prompt=prompt)
            else:
                resp_text = self.llm_router.generate(agent="trader", messages=[{"role": "user", "content": prompt}])
                resp = json.loads(str(resp_text))

            if isinstance(resp, dict) and "mutated_genome" in resp and resp["mutated_genome"] != parent_genome:
                return {
                    "branch_name": "shadow/llm-causal-patch",
                    "mutated_genome": resp["mutated_genome"],
                    "commit_message": str(resp.get("commit_message", "LLM causal genome patch"))[:120],
                }
        except Exception as exc:
            logger.debug("LLM mutation query fallback: %s", exc)
        return None
