"""Multi-Domain Quantitative Scoring & 2-Trade-Per-Week Selection Funnel.

Implements the institutional quantitative scoring and exclusionary funnel:
1. Net Conviction Score S_{i,t} in [-100, +100]:
   S_{i,t} = w_M M_{i,t} + w_C C_{i,t} + w_G G_{i,t} + w_P P_{i,t}
   Weights: w_M = 0.35, w_C = 0.30, w_G = 0.20, w_P = 0.15
2. Active zones:
   - Zone 1: Active Long (|S| >= +45)
   - Zone 2: Active Short (|S| <= -45)
   - Neutral Zone (-45 < S < +45)
3. Exclusionary Funnel (Sunday evening scanning):
   - Filter 1: Score threshold (|S| >= 45)
   - Filter 2: Cross-Asset Alignment (rates/commodities confirmation)
   - Filter 3: De-Correlation Gate (|r| < 0.35 trailing 60-day)
   -> Exactly Top 2 Uncorrelated Trades (Alpha Trend + Beta Catalyst)
4. Master Playbook Suite (CB-DIV, STAG-SHOCK, SUPPLY-DISRUPT, LIQUIDITY-SQUEEZE, CARRY-UNWIND)

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib & NumPy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

EXECUTION_AUTHORITY = False
HURDLE_RATE = 45.0
MAX_CORRELATION = 0.35


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_net_conviction_score(
    macro_surprise_score: Any,
    cb_divergence_score: Any,
    geopolitical_shock_score: Any,
    cot_3y_z_score: Any,
    *,
    w_m: float = 0.35,
    w_c: float = 0.30,
    w_g: float = 0.20,
    w_p: float = 0.15,
) -> Dict[str, Any]:
    """Calculates S_{i,t} bounded in [-100, +100] across 4 fundamental domains."""
    m_raw = _safe_float(macro_surprise_score)
    c_raw = _safe_float(cb_divergence_score)
    g_raw = _safe_float(geopolitical_shock_score)
    z_cot = _safe_float(cot_3y_z_score)

    m_val = 0.0 if m_raw is None else float(np.tanh(m_raw) * 100.0)
    c_val = 0.0 if c_raw is None else float(np.tanh(c_raw) * 100.0)
    g_val = 0.0 if g_raw is None else float(np.tanh(g_raw) * 100.0)

    # Contrarian positioning modifier
    if z_cot is None:
        p_val = 0.0
    elif z_cot > 2.0:
        p_val = -100.0  # Crowded long -> penalize continuation / favor short
    elif z_cot < -2.0:
        p_val = 100.0   # Crowded short -> penalize selling / favor squeeze
    else:
        p_val = float(np.tanh(z_cot) * 50.0)

    score = (w_m * m_val) + (w_c * c_val) + (w_g * g_val) + (w_p * p_val)
    score = float(np.clip(score, -100.0, 100.0))

    if score >= HURDLE_RATE:
        zone = "ACTIVE_LONG"
    elif score <= -HURDLE_RATE:
        zone = "ACTIVE_SHORT"
    else:
        zone = "NEUTRAL_ZONE"

    return {
        "status": "READY_FOR_REVIEW",
        "net_conviction_score": round(score, 2),
        "actionable_zone": zone,
        "m_component": round(m_val, 2),
        "c_component": round(c_val, 2),
        "g_component": round(g_val, 2),
        "p_component": round(p_val, 2),
        "execution_authority": False,
    }


def classify_master_playbook(context: Dict[str, Any]) -> Dict[str, Any]:
    """Matches fundamental catalysts to one of the five Master Playbooks."""
    if not isinstance(context, dict):
        return {"status": "INVALID_INPUT", "playbook_code": "UNKNOWN", "execution_authority": False}

    # Playbook 1: CB-DIV
    if context.get("cb_guidance_divergence") or (_safe_float(context.get("rate_spread_expanding_bps")) or 0.0) >= 15.0:
        return {
            "status": "READY_FOR_REVIEW",
            "playbook_code": "CB-DIV",
            "playbook_name": "Central Bank Policy Divergence",
            "entry_trigger": "H4 retracement to 20 EMA",
            "tp1_atr": 1.5,
            "tp2_atr": 3.0,
            "execution_authority": False,
        }

    # Playbook 2: STAG-SHOCK
    if (_safe_float(context.get("core_cpi_surprise_pct")) or 0.0) >= 0.20 and context.get("employment_contracting"):
        return {
            "status": "READY_FOR_REVIEW",
            "playbook_code": "STAG-SHOCK",
            "playbook_name": "Macro Surprise Stagflation Re-Pricing",
            "entry_trigger": "Post-event T+30m after initial wick high/low",
            "tp1_atr": 1.5,
            "tp2_atr": 2.5,
            "execution_authority": False,
        }

    # Playbook 3: SUPPLY-DISRUPT
    if context.get("chokepoint_compromised") or context.get("crude_backwardation_steepening"):
        return {
            "status": "READY_FOR_REVIEW",
            "playbook_code": "SUPPLY-DISRUPT",
            "playbook_name": "Geopolitical Supply-Chokepoint Disruption",
            "entry_trigger": "Market execution post-spread normalization",
            "tp1_atr": 1.5,
            "tp2_atr": 3.0,
            "execution_authority": False,
        }

    # Playbook 4: LIQUIDITY-SQUEEZE
    if (_safe_float(context.get("tga_balance_building_billions")) or 0.0) >= 100.0 or context.get("sofr_above_iorb"):
        return {
            "status": "READY_FOR_REVIEW",
            "playbook_code": "LIQUIDITY-SQUEEZE",
            "playbook_name": "Sovereign Debt Issuance & Liquidity Squeeze",
            "entry_trigger": "Position Monday of auction week through settlement",
            "tp1_atr": 1.5,
            "tp2_atr": 2.5,
            "execution_authority": False,
        }

    # Playbook 5: CARRY-UNWIND
    if (_safe_float(context.get("vix_level")) or 0.0) >= 22.0 or (_safe_float(context.get("leveraged_carry_percentile")) or 0.0) >= 85.0:
        return {
            "status": "READY_FOR_REVIEW",
            "playbook_code": "CARRY-UNWIND",
            "playbook_name": "Cross-Asset Volatility Carry Unwind",
            "entry_trigger": "Breakdown of daily support with tightened trailing stop",
            "tp1_atr": 1.5,
            "tp2_atr": 3.0,
            "execution_authority": False,
        }

    return {
        "status": "READY_FOR_REVIEW",
        "playbook_code": "BASELINE_MONITORING",
        "playbook_name": "Macro Equilibrium",
        "entry_trigger": "None",
        "tp1_atr": 1.5,
        "tp2_atr": 2.5,
        "execution_authority": False,
    }


class MacroConvictionEngine:
    """Orchestrates candidate scoring, cross-asset verification, and de-correlation filtering."""

    def __init__(self, hurdle_rate: float = HURDLE_RATE, max_corr: float = MAX_CORRELATION) -> None:
        self.hurdle_rate = hurdle_rate
        self.max_corr = max_corr

    def _get_pairwise_corr(
        self, asset_a: str, asset_b: str, corr_matrix: Dict[Tuple[str, str], float]
    ) -> float:
        if asset_a == asset_b:
            return 1.0
        val = corr_matrix.get((asset_a, asset_b))
        if val is None:
            val = corr_matrix.get((asset_b, asset_a), 0.0)
        return float(val)

    def run_weekly_selection_funnel(
        self,
        candidate_scores: Dict[str, float],
        trailing_60d_correlations: Dict[Tuple[str, str], float],
    ) -> Dict[str, Any]:
        """Runs the Sunday evening exclusionary funnel isolating precisely Top 2 uncorrelated setups."""
        # Filter 1: Score Hurdle Cutoff
        qualified = [
            (asset, score)
            for asset, score in candidate_scores.items()
            if abs(score) >= self.hurdle_rate
        ]
        if not qualified:
            return {
                "status": "READY_FOR_REVIEW",
                "selected_trades": [],
                "reason": "No assets exceeded conviction hurdle (|S| >= 45)",
                "execution_authority": False,
            }

        # Sort by absolute conviction strength
        qualified.sort(key=lambda x: abs(x[1]), reverse=True)

        selected: List[Dict[str, Any]] = []

        # Trade 1: Highest conviction setup (Trade Alpha: Macro-Trend)
        first_asset, first_score = qualified[0]
        selected.append({
            "asset": first_asset,
            "role": "TRADE_ALPHA_MACRO_TREND",
            "score": first_score,
            "direction": "LONG" if first_score > 0 else "SHORT",
        })

        # Trade 2: Next highest conviction setup that passes de-correlation gate (|r| < 0.35)
        for cand_asset, cand_score in qualified[1:]:
            r = self._get_pairwise_corr(first_asset, cand_asset, trailing_60d_correlations)
            if abs(r) < self.max_corr:
                selected.append({
                    "asset": cand_asset,
                    "role": "TRADE_BETA_CATALYST_EVENT",
                    "score": cand_score,
                    "direction": "LONG" if cand_score > 0 else "SHORT",
                    "pairwise_correlation_with_alpha": round(r, 4),
                })
                break

        return {
            "status": "READY_FOR_REVIEW",
            "selected_trades": selected,
            "count": len(selected),
            "execution_authority": False,
        }


def evaluate_selection_funnel(
    candidate_scores: Dict[str, float],
    correlations: Dict[Tuple[str, str], float],
) -> Dict[str, Any]:
    engine = MacroConvictionEngine()
    return engine.run_weekly_selection_funnel(candidate_scores, correlations)
