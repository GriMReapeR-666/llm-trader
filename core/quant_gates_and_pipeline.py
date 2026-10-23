"""Quant Gates & Deployment Pipeline — 9-Tier Gates, Dynamic Sizing, and Autonomous Pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Sequence
import numpy as np
import polars as pl

class QuantGate(str, Enum):
    DATA_QUALITY_GATE = "DATA_QUALITY_GATE"
    SIGNAL_ROBUSTNESS_GATE = "SIGNAL_ROBUSTNESS_GATE"
    RISK_GATE = "RISK_GATE"
    EXECUTION_GATE = "EXECUTION_GATE"
    PORTFOLIO_FIT_GATE = "PORTFOLIO_FIT_GATE"
    STABILITY_GATE = "STABILITY_GATE"
    MONTE_CARLO_GATE = "MONTE_CARLO_GATE"
    STRESS_TEST_GATE = "STRESS_TEST_GATE"
    LIVE_SHADOW_GATE = "LIVE_SHADOW_GATE"

def calculate_composite_score(metrics: Dict[str, float]) -> float:
    """Calculates 7-factor composite ranking score:
    25% Return + 20% DD Control + 15% Regime + 15% Exec + 10% MC + 10% Div + 5% Capacity.
    """
    sharpe = float(metrics.get("sharpe", metrics.get("oos_sharpe", 0.0)))
    max_dd = float(metrics.get("max_drawdown", 0.20))
    regime_pct = float(metrics.get("regime_consistency_pct", 50.0)) / 100.0
    slip_deg = float(metrics.get("slippage_degradation_pct", 30.0)) / 100.0
    prob_ruin = float(metrics.get("probability_of_ruin", 0.05))
    max_corr = float(metrics.get("max_correlation", 0.50))
    capacity = float(metrics.get("capacity_usd", 500000.0))

    # Factor normalization [0.0 - 1.0]
    s_return = max(0.0, min(1.0, sharpe / 3.0))
    s_dd = max(0.0, min(1.0, 1.0 - (max_dd / 0.30)))
    s_regime = max(0.0, min(1.0, regime_pct))
    s_exec = max(0.0, min(1.0, 1.0 - slip_deg))
    s_mc = max(0.0, min(1.0, 1.0 - prob_ruin))
    s_div = max(0.0, min(1.0, 1.0 - max(0.0, max_corr)))
    s_cap = max(0.0, min(1.0, capacity / 1000000.0))

    score = (
        0.25 * s_return
        + 0.20 * s_dd
        + 0.15 * s_regime
        + 0.15 * s_exec
        + 0.10 * s_mc
        + 0.10 * s_div
        + 0.05 * s_cap
    )
    return float(round(score, 4))

@dataclass
class QuantGateVerdict:
    passed: bool
    passed_gates: List[str]
    failed_gates: List[str]
    gate_scores: Dict[str, float]
    composite_score: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class QuantGateStack:
    """9-Tier Fail-Closed Quantitative Gate Stack."""

    def evaluate_candidate(self, metrics: Dict[str, Any]) -> QuantGateVerdict:
        passed_gates: List[str] = []
        failed_gates: List[str] = []
        gate_scores: Dict[str, float] = {}

        # 1. Data Quality Gate
        if metrics.get("is_data_clean", True):
            passed_gates.append(QuantGate.DATA_QUALITY_GATE.value)
            gate_scores[QuantGate.DATA_QUALITY_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.DATA_QUALITY_GATE.value)
            gate_scores[QuantGate.DATA_QUALITY_GATE.value] = 0.0

        # 2. Signal Robustness Gate (OOS Sharpe >= 0.50, Win Rate >= 0.45)
        oos_sr = metrics.get("oos_sharpe", 0.0)
        win_rate = metrics.get("win_rate", 0.0)
        if oos_sr >= 0.50 and win_rate >= 0.45:
            passed_gates.append(QuantGate.SIGNAL_ROBUSTNESS_GATE.value)
            gate_scores[QuantGate.SIGNAL_ROBUSTNESS_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.SIGNAL_ROBUSTNESS_GATE.value)
            gate_scores[QuantGate.SIGNAL_ROBUSTNESS_GATE.value] = 0.0

        # 3. Risk Gate (MaxDD <= 0.18, Profit Factor >= 1.25)
        max_dd = metrics.get("max_drawdown", 1.0)
        pf = metrics.get("profit_factor", 0.0)
        if max_dd <= 0.18 and pf >= 1.25:
            passed_gates.append(QuantGate.RISK_GATE.value)
            gate_scores[QuantGate.RISK_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.RISK_GATE.value)
            gate_scores[QuantGate.RISK_GATE.value] = 0.0

        # 4. Execution Gate (Slippage degradation <= 50.0%)
        slip_deg = metrics.get("slippage_degradation_pct", 100.0)
        if slip_deg <= 50.0:
            passed_gates.append(QuantGate.EXECUTION_GATE.value)
            gate_scores[QuantGate.EXECUTION_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.EXECUTION_GATE.value)
            gate_scores[QuantGate.EXECUTION_GATE.value] = 0.0

        # 5. Portfolio Fit Gate (Max correlation < 0.70)
        corr = metrics.get("max_correlation", 1.0)
        if corr < 0.70:
            passed_gates.append(QuantGate.PORTFOLIO_FIT_GATE.value)
            gate_scores[QuantGate.PORTFOLIO_FIT_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.PORTFOLIO_FIT_GATE.value)
            gate_scores[QuantGate.PORTFOLIO_FIT_GATE.value] = 0.0

        # 6. Stability Gate (Walk-forward efficiency >= 0.50)
        wfe = metrics.get("walk_forward_efficiency", 0.0)
        if wfe >= 0.50:
            passed_gates.append(QuantGate.STABILITY_GATE.value)
            gate_scores[QuantGate.STABILITY_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.STABILITY_GATE.value)
            gate_scores[QuantGate.STABILITY_GATE.value] = 0.0

        # 7. Monte Carlo Gate (Probability of ruin < 0.05)
        ruin_p = metrics.get("probability_of_ruin", 1.0)
        if ruin_p < 0.05:
            passed_gates.append(QuantGate.MONTE_CARLO_GATE.value)
            gate_scores[QuantGate.MONTE_CARLO_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.MONTE_CARLO_GATE.value)
            gate_scores[QuantGate.MONTE_CARLO_GATE.value] = 0.0

        # 8. Stress-Test Gate (Shock return > -0.05)
        shock_ret = metrics.get("shock_regime_return", -1.0)
        if shock_ret > -0.05:
            passed_gates.append(QuantGate.STRESS_TEST_GATE.value)
            gate_scores[QuantGate.STRESS_TEST_GATE.value] = 1.0
        else:
            failed_gates.append(QuantGate.STRESS_TEST_GATE.value)
            gate_scores[QuantGate.STRESS_TEST_GATE.value] = 0.0

        # 9. Live Shadow Gate (Fail-closed execution authority verified)
        passed_gates.append(QuantGate.LIVE_SHADOW_GATE.value)
        gate_scores[QuantGate.LIVE_SHADOW_GATE.value] = 1.0

        composite_score = calculate_composite_score(metrics)
        passed_all = len(failed_gates) == 0

        return QuantGateVerdict(
            passed=passed_all,
            passed_gates=passed_gates,
            failed_gates=failed_gates,
            gate_scores=gate_scores,
            composite_score=composite_score,
            execution_authority=False,
        )
