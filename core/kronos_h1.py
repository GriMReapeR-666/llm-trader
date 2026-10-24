"""Kronos H1 + Ensemble AND-Gate and Disagreement Audit Engine.

Enforces strict consensus gating between deep autoregressive sequence models (Kronos)
and multi-factor statistical alpha ensembles (Alpha101 / IC-IR weighted).

Guarantees:
- Fail-closed AND-gate logic: Trades are vetoed unless both engines concur in direction
  and exceed minimum operational conviction thresholds.
- Pure deterministic metrics: Disagreement rate, directional divergence, and audit ledger.
- Zero execution authority leakage (advisory only).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple


def compute_and_gate_decision(
    kronos_signal: float,
    ensemble_signal: float,
    threshold: float = 0.20,
) -> Dict[str, Any]:
    """Evaluates consensus between Kronos deep model and Alpha Ensemble.

    Args:
        kronos_signal: Predicted directional alpha in [-1.0, 1.0].
        ensemble_signal: Multi-factor composite signal in [-1.0, 1.0].
        threshold: Minimum conviction magnitude required from BOTH engines.

    Returns:
        Dict containing decision, approval flag, conservative combined signal,
        and diagnostic conflict details.
    """
    k = float(kronos_signal) if math.isfinite(float(kronos_signal)) else 0.0
    e = float(ensemble_signal) if math.isfinite(float(ensemble_signal)) else 0.0
    th = max(0.0, float(threshold))

    # Case 1: Mutual BUY agreement above conviction threshold
    if k > th and e > th:
        return {
            "allowed": True,
            "decision": "BUY",
            "kronos_signal": round(k, 4),
            "ensemble_signal": round(e, 4),
            "combined_signal": round(min(k, e), 4),  # Conservative lower bound
            "agreement": True,
            "is_conflict": False,
            "disagreement_reason": None,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        }

    # Case 2: Mutual SELL agreement below negative conviction threshold
    if k < -th and e < -th:
        return {
            "allowed": True,
            "decision": "SELL",
            "kronos_signal": round(k, 4),
            "ensemble_signal": round(e, 4),
            "combined_signal": round(max(k, e), 4),  # Conservative closest to 0
            "agreement": True,
            "is_conflict": False,
            "disagreement_reason": None,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        }

    # Case 3: Directional conflict (One engine says BUY, the other says SELL)
    is_conflict = (k > th and e < -th) or (k < -th and e > th)
    if is_conflict:
        return {
            "allowed": False,
            "decision": "VETO_CONFLICT",
            "kronos_signal": round(k, 4),
            "ensemble_signal": round(e, 4),
            "combined_signal": 0.0,
            "agreement": False,
            "is_conflict": True,
            "disagreement_reason": f"Directional conflict: Kronos={k:.3f} vs Ensemble={e:.3f}",
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        }

    # Case 4: Sub-threshold / Insufficient Conviction
    return {
        "allowed": False,
        "decision": "VETO_THRESHOLD",
        "kronos_signal": round(k, 4),
        "ensemble_signal": round(e, 4),
        "combined_signal": 0.0,
        "agreement": False,
        "is_conflict": False,
        "disagreement_reason": f"Signal below threshold {th:.2f}: Kronos={k:.3f}, Ensemble={e:.3f}",
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
    }


def disagreement_rate(
    kronos_series: Sequence[float],
    ensemble_series: Sequence[float],
    threshold: float = 0.0,
) -> float:
    """Calculates directional disagreement rate between Kronos and Alpha Ensemble.

    Measures the proportion of active observations where Kronos and Ensemble
    disagree in sign / directional polarity.
    """
    if not kronos_series or not ensemble_series:
        return 0.0

    n = min(len(kronos_series), len(ensemble_series))
    if n == 0:
        return 0.0

    th = max(0.0, float(threshold))
    disagreements = 0
    active_count = 0

    for i in range(n):
        k = float(kronos_series[i])
        e = float(ensemble_series[i])

        if th <= 0.0:
            if k != 0.0 and e != 0.0:
                active_count += 1
                if (k > 0) != (e > 0):
                    disagreements += 1
        else:
            if abs(k) > th or abs(e) > th:
                active_count += 1
                if (k > th and e < -th) or (k < -th and e > th):
                    disagreements += 1

    if active_count == 0:
        return 0.0

    return round(disagreements / active_count, 4)


class KronosEnsembleAndGate:
    """Audits live streaming decisions and maintains disagreement telemetry."""

    def __init__(self, threshold: float = 0.20):
        self.threshold = max(0.0, float(threshold))
        self.history: List[Dict[str, Any]] = []

    def evaluate(self, kronos_signal: float, ensemble_signal: float) -> Dict[str, Any]:
        """Evaluate consensus and record into audit history."""
        decision = compute_and_gate_decision(
            kronos_signal=kronos_signal,
            ensemble_signal=ensemble_signal,
            threshold=self.threshold,
        )
        self.history.append(decision)
        return decision

    def audit_summary(self) -> Dict[str, Any]:
        """Summarizes consensus decisions, veto rates, and conflict frequency."""
        total = len(self.history)
        if total == 0:
            return {
                "total_evaluations": 0,
                "buy_count": 0,
                "sell_count": 0,
                "veto_conflict_count": 0,
                "veto_threshold_count": 0,
                "agreement_rate": 0.0,
                "conflict_rate": 0.0,
                "disagreement_rate": 0.0,
            }

        buy_count = sum(1 for d in self.history if d["decision"] == "BUY")
        sell_count = sum(1 for d in self.history if d["decision"] == "SELL")
        conflict_count = sum(1 for d in self.history if d["decision"] == "VETO_CONFLICT")
        threshold_count = sum(1 for d in self.history if d["decision"] == "VETO_THRESHOLD")
        agreement_count = buy_count + sell_count

        k_series = [d["kronos_signal"] for d in self.history]
        e_series = [d["ensemble_signal"] for d in self.history]
        disagree_metric = disagreement_rate(k_series, e_series, threshold=self.threshold)

        return {
            "total_evaluations": total,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "veto_conflict_count": conflict_count,
            "veto_threshold_count": threshold_count,
            "agreement_rate": round(agreement_count / total, 4),
            "conflict_rate": round(conflict_count / total, 4),
            "disagreement_rate": disagree_metric,
        }
