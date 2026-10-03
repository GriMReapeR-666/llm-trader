"""Progressive Gate System — three-stage quality gate for strategy promotion.

Stages: SCREENING → PAPER → DEPLOYMENT

Each stage enforces progressively stricter quality thresholds. A strategy
must clear the current stage's gate before advancing. The system is
fail-closed: any missing metric results in REJECT.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GateVerdict:
    """Result of evaluating a strategy against a progressive gate stage."""
    strategy_id: str
    stage: str
    passed: bool
    quality_score: float  # 0.0 - 1.0 composite
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    evaluated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "stage": self.stage,
            "passed": self.passed,
            "quality_score": round(self.quality_score, 4),
            "checks": self.checks,
            "reasons": self.reasons,
            "next_actions": self.next_actions,
            "evaluated_at": self.evaluated_at,
        }


# ---------------------------------------------------------------------------
# Gate table: each stage maps metric names to (min_threshold, weight)
# ---------------------------------------------------------------------------

GATE_TABLE: Dict[str, Dict[str, Any]] = {
    "SCREENING": {
        "min_trades": {"threshold": 30, "weight": 1.0},
        "win_rate": {"threshold": 45.0, "weight": 1.0},
        "profit_factor": {"threshold": 1.05, "weight": 1.0},
        "sharpe": {"threshold": 0.3, "weight": 0.8},
        "max_drawdown_pct": {"threshold": 25.0, "weight": 0.6, "invert": True},
    },
    "PAPER": {
        "min_trades": {"threshold": 100, "weight": 1.0},
        "win_rate": {"threshold": 48.0, "weight": 1.0},
        "profit_factor": {"threshold": 1.20, "weight": 1.0},
        "sharpe": {"threshold": 0.7, "weight": 1.0},
        "max_drawdown_pct": {"threshold": 18.0, "weight": 0.8, "invert": True},
        "walk_forward_efficiency": {"threshold": 0.4, "weight": 0.7},
    },
    "DEPLOYMENT": {
        "min_trades": {"threshold": 200, "weight": 1.0},
        "win_rate": {"threshold": 50.0, "weight": 1.0},
        "profit_factor": {"threshold": 1.30, "weight": 1.0},
        "sharpe": {"threshold": 0.9, "weight": 1.0},
        "max_drawdown_pct": {"threshold": 15.0, "weight": 0.9, "invert": True},
        "walk_forward_efficiency": {"threshold": 0.5, "weight": 0.9},
        "overfit_gate_pass": {"threshold": 1, "weight": 1.0},
    },
}

STAGE_ORDER = ["SCREENING", "PAPER", "DEPLOYMENT"]


class ProgressiveGateSystem:
    """Evaluates strategies through progressive quality gates."""

    def __init__(self, gate_table: Optional[Dict[str, Any]] = None):
        self.gate_table = gate_table or GATE_TABLE

    def evaluate(
        self,
        metrics: Dict[str, Any],
        current_stage: str = "SCREENING",
        strategy_id: str = "unknown",
    ) -> GateVerdict:
        """Evaluate a strategy's metrics against the gate for current_stage.

        Args:
            metrics: Dict with keys matching the gate table metric names.
                     Supported keys: min_trades (or trades), win_rate,
                     profit_factor, sharpe, max_drawdown_pct, max_dd,
                     walk_forward_efficiency, overfit_gate_pass.
            current_stage: One of SCREENING, PAPER, DEPLOYMENT.
            strategy_id: Identifier for logging.

        Returns:
            GateVerdict with pass/fail and per-check details.
        """
        if current_stage not in self.gate_table:
            return GateVerdict(
                strategy_id=strategy_id,
                stage=current_stage,
                passed=False,
                quality_score=0.0,
                reasons=[f"Unknown stage: {current_stage}"],
                evaluated_at=time.time(),
            )

        stage_gates = self.gate_table[current_stage]
        checks: Dict[str, Dict[str, Any]] = {}
        reasons: List[str] = []
        next_actions: List[str] = []
        scores: List[float] = []
        all_passed = True

        # Normalize metric aliases
        normalized = dict(metrics)
        if "trades" in normalized and "min_trades" not in normalized:
            normalized["min_trades"] = normalized["trades"]
        if "max_dd" in normalized and "max_drawdown_pct" not in normalized:
            normalized["max_drawdown_pct"] = normalized["max_dd"]

        for metric_name, spec in stage_gates.items():
            threshold = spec["threshold"]
            weight = spec.get("weight", 1.0)
            invert = spec.get("invert", False)

            value = normalized.get(metric_name)
            if value is None:
                check_passed = False
                score = 0.0
                reasons.append(f"{metric_name}: MISSING (required >= {threshold})")
                next_actions.append(f"Compute {metric_name} for this strategy")
            elif invert:
                # For inverted metrics (e.g., drawdown: lower is better)
                check_passed = value <= threshold
                score = weight * (1.0 if check_passed else max(0.0, 1.0 - (value - threshold) / threshold))
                if not check_passed:
                    reasons.append(f"{metric_name}: {value:.2f} exceeds threshold {threshold}")
                    next_actions.append(f"Reduce {metric_name} below {threshold}")
            else:
                check_passed = value >= threshold
                score = weight * (1.0 if check_passed else min(1.0, value / threshold if threshold > 0 else 0.0))
                if not check_passed:
                    reasons.append(f"{metric_name}: {value:.2f} below threshold {threshold}")
                    next_actions.append(f"Increase {metric_name} to >= {threshold}")

            if not check_passed:
                all_passed = False

            checks[metric_name] = {
                "value": value,
                "threshold": threshold,
                "passed": check_passed,
                "weight": weight,
                "score": round(score, 4),
            }
            scores.append(score)

        # Composite quality score: weighted average of individual scores
        quality_score = sum(scores) / len(scores) if scores else 0.0

        verdict = GateVerdict(
            strategy_id=strategy_id,
            stage=current_stage,
            passed=all_passed,
            quality_score=quality_score,
            checks=checks,
            reasons=reasons,
            next_actions=next_actions,
            evaluated_at=time.time(),
        )

        if all_passed:
            logger.info(
                "Gate PASS %s stage=%s quality=%.2f",
                strategy_id, current_stage, quality_score,
            )
        else:
            logger.info(
                "Gate REJECT %s stage=%s quality=%.2f reasons=%d",
                strategy_id, current_stage, quality_score, len(reasons),
            )

        return verdict

    def get_next_stage(self, current_stage: str) -> Optional[str]:
        """Return the next stage after current_stage, or None if DEPLOYMENT."""
        try:
            idx = STAGE_ORDER.index(current_stage)
            if idx + 1 < len(STAGE_ORDER):
                return STAGE_ORDER[idx + 1]
        except ValueError:
            pass
        return None

    def get_stage_requirements(self, stage: str) -> Dict[str, Any]:
        """Return the gate table for a given stage."""
        return dict(self.gate_table.get(stage, {}))
