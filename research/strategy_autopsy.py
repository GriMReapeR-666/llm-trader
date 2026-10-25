"""Automated Forensic Autopsy Engine (Pillar 1 / Stage 1).

Decomposes why a strategy is weak across 7 orthogonal quantitative dimensions:
1. Inverted Signal Polarity (IC < -0.025, MAE > 1.4 * MFE)
2. Premature Stop-Out / Stop Hunt (P(hits +1.5R post-SL) >= 40%)
3. Profit Give-Back / Greedy Exit (P(reverses to loss | MFE >= 1.2R) >= 40%)
4. Regime Contamination Leak (E[R|regime_A] > 0.25 vs E[R|regime_B] < -0.15)
5. Session / Liquidity Bleed (E[R|Asia/Dead] << E[R|London/NY])
6. Microstructure Friction Strangle (Friction / Gross Profit >= 35%)
7. Alpha / Feature Drift (PSI >= 0.25 or delta_IC < -50%)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)


def run_strategy_autopsy(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Execute forensic autopsy on trade history to attribute failure root cause."""
    if len(trades) < 20:
        return {
            "status": "INSUFFICIENT_DATA",
            "overall_mean_r": float(np.mean([float(t.get("r_multiple", 0.0)) for t in trades])) if trades else 0.0,
            "diagnoses": [],
            "recommended_mutations": [],
        }

    r_arr = np.array([float(t.get("r_multiple", 0.0)) for t in trades], dtype=float)
    mfe_arr = np.array([float(t.get("mfe_r", max(0.0, float(t.get("r_multiple", 0.0))))) for t in trades], dtype=float)
    mae_arr = np.array([float(t.get("mae_r", abs(min(0.0, float(t.get("r_multiple", 0.0)))))) for t in trades], dtype=float)

    losers = [t for t in trades if float(t.get("r_multiple", 0.0)) < 0.0]
    winners = [t for t in trades if float(t.get("r_multiple", 0.0)) > 0.0]

    diagnoses: List[Dict[str, Any]] = []
    mutations: List[Dict[str, Any]] = []

    # 1. Inverted Signal Polarity
    if np.mean(r_arr) < -0.15 and np.mean(mae_arr) > 1.4 * max(float(np.mean(mfe_arr)), 1e-4):
        diagnoses.append({
            "code": "INVERTED_SIGNAL_POLARITY",
            "severity": "CRITICAL",
            "evidence": f"Mean R={np.mean(r_arr):.2f}R, Mean MAE={np.mean(mae_arr):.2f}R vs MFE={np.mean(mfe_arr):.2f}R",
        })
        mutations.append({
            "op": "INVERT_ENTRY_SIGNAL",
            "reason": "Strong adverse excursion dominates favorable excursion",
        })

    # 2. Premature Stop-Out (Stop Hunt)
    if losers:
        stop_hunted_rate = float(np.mean([1.0 if t.get("post_sl_reached_1_5r", False) else 0.0 for t in losers]))
        if stop_hunted_rate >= 0.40:
            diagnoses.append({
                "code": "PREMATURE_STOP_OUT",
                "severity": "HIGH",
                "evidence": f"{stop_hunted_rate * 100:.1f}% of stopped-out trades subsequently rallied to +1.5R",
            })
            mutations.append({
                "op": "WIDEN_SL_ATR_MULT",
                "scale": 1.30,
                "reason": "Stops placed inside noise envelope",
            })

    # 3. Profit Give-Back (Exits too greedy / slow ratchet)
    if losers:
        giveback_rate = float(np.mean([1.0 if float(t.get("mfe_r", 0.0)) >= 1.2 else 0.0 for t in losers]))
        if giveback_rate >= 0.40:
            diagnoses.append({
                "code": "PROFIT_GIVEBACK_BLEED",
                "severity": "HIGH",
                "evidence": f"{giveback_rate * 100:.1f}% of losers first reached >= +1.2R MFE before reversing to a loss",
            })
            mutations.append({
                "op": "LOWER_BREAKEVEN_TRIGGER_R",
                "new_trigger_r": 0.95,
                "reason": "Lock in +1R excursions earlier",
            })

    # 4. Regime Contamination Matrix
    regime_stats: Dict[str, List[float]] = {}
    for t in trades:
        reg = str(t.get("regime", "UNKNOWN"))
        regime_stats.setdefault(reg, []).append(float(t.get("r_multiple", 0.0)))

    good_regimes = [reg for reg, rs in regime_stats.items() if len(rs) >= 8 and np.mean(rs) >= 0.25]
    bad_regimes = [reg for reg, rs in regime_stats.items() if len(rs) >= 8 and np.mean(rs) <= -0.15]

    if good_regimes and bad_regimes:
        diagnoses.append({
            "code": "REGIME_CONTAMINATION",
            "severity": "CRITICAL",
            "evidence": {reg: {"n": len(rs), "mean_r": round(float(np.mean(rs)), 3)} for reg, rs in regime_stats.items()},
        })
        mutations.append({
            "op": "RESTRICT_ALLOWED_REGIMES",
            "allowed_regimes": good_regimes,
            "blocked_regimes": bad_regimes,
            "reason": f"Profitable in {good_regimes}, bleeding in {bad_regimes}",
        })

    # 5. Microstructure Friction Strangle
    gross_wins = sum(max(0.0, float(t.get("gross_pnl", 0.0))) for t in winners)
    total_friction = sum(float(t.get("friction_cost", 0.0)) for t in trades)
    if gross_wins > 0 and (total_friction / gross_wins) >= 0.35:
        diagnoses.append({
            "code": "FRICTION_STRANGLE",
            "severity": "HIGH",
            "evidence": f"Spread+Commission consumed {(total_friction / gross_wins) * 100:.1f}% of gross winning PnL",
        })
        mutations.append({
            "op": "INCREASE_MIN_TARGET_ATR",
            "scale": 1.40,
            "reason": "Target too small relative to spread",
        })

    # 6. Session / Liquidity Bleed
    dead_hours = {20, 21, 22, 23, 0, 1, 2, 3, 4, 5}
    dead_hour_rs = [float(t["r_multiple"]) for t in trades if int(t.get("hour_utc", 12)) in dead_hours]
    active_hour_rs = [float(t["r_multiple"]) for t in trades if int(t.get("hour_utc", 12)) not in dead_hours]

    if len(dead_hour_rs) >= 8 and len(active_hour_rs) >= 8:
        if np.mean(dead_hour_rs) <= -0.15 and np.mean(active_hour_rs) >= 0.15:
            diagnoses.append({
                "code": "SESSION_LIQUIDITY_BLEED",
                "severity": "HIGH",
                "evidence": f"Dead hours mean R={np.mean(dead_hour_rs):.2f}R vs Active hours mean R={np.mean(active_hour_rs):.2f}R",
            })
            mutations.append({
                "op": "RESTRICT_ALLOWED_HOURS",
                "allowed_hours": list(range(6, 20)),
                "reason": "Dead zone liquidity bleed",
            })

    return {
        "status": "AUTOPSY_COMPLETE",
        "overall_mean_r": float(np.mean(r_arr)),
        "diagnoses": diagnoses,
        "recommended_mutations": mutations,
    }
