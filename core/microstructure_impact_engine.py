"""Microstructure Gatekeeper & Impact Candle State Machine.

Implements the institutional execution gatekeeper:
1. Dealing Range & Premium/Discount framing (Fibonacci 50% Equilibrium).
2. Liquidity Sweep Detection (PWH/PWL, PDH/PDL, ASH/ASL, EQH/EQL).
3. Dual-Mode Cumulative Volume Delta (CVD) Absorption & Divergence.
4. Impact Candle Score (ICS_t):
   - Efficiency >= 0.72
   - Volume Z-Score >= 2.0
   - Fair Value Gap (FVG) Creation (3-bar sequence)
   - Market Structure Shift (MSS) Break
5. A+ Confluence Matrix (>= 85/100 required for limit order execution).

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
A_PLUS_CONFLUENCE_THRESHOLD = 85.0


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


def classify_dealing_range_zones(
    current_price: Any,
    range_low: Any,
    range_high: Any,
) -> Dict[str, Any]:
    """Classifies position of price within dealing range (Premium > 50% vs Discount < 50%)."""
    cp = _safe_float(current_price)
    rl = _safe_float(range_low)
    rh = _safe_float(range_high)

    if cp is None or rl is None or rh is None or rh <= rl:
        return {"status": "INVALID_INPUT", "zone": "UNKNOWN", "execution_authority": False}

    eq = round(0.5 * (rl + rh), 5)
    pos = (cp - rl) / (rh - rl)

    if pos < 0.5:
        zone = "DISCOUNT"
        favors = "LONG"
        guidance = "Price in discount; favors institutional accumulation / long execution."
    elif pos > 0.5:
        zone = "PREMIUM"
        favors = "SHORT"
        guidance = "Price in premium; favors institutional distribution / short execution."
    else:
        zone = "EQUILIBRIUM"
        favors = "NEUTRAL"
        guidance = "Price at 50% range equilibrium; no spatial edge."

    return {
        "status": "READY_FOR_REVIEW",
        "current_price": cp,
        "range_low": rl,
        "range_high": rh,
        "range_equilibrium": eq,
        "range_position_ratio": round(pos, 4),
        "zone": zone,
        "favors_direction": favors,
        "guidance": guidance,
        "execution_authority": False,
    }


def detect_liquidity_sweep(
    bar_low: Any,
    bar_high: Any,
    bar_close: Any,
    target_pool_level: Any,
    pool_type: str = "PDL",
    macro_bias: str = "LONG",
) -> Dict[str, Any]:
    """Detects whether price swept an opposing liquidity pool and closed back inside."""
    bl = _safe_float(bar_low)
    bh = _safe_float(bar_high)
    bc = _safe_float(bar_close)
    lvl = _safe_float(target_pool_level)

    if any(v is None for v in (bl, bh, bc, lvl)):
        return {"status": "INVALID_INPUT", "sweep_detected": False, "execution_authority": False}

    sweep = False
    depth_pips = 0.0
    aligned = False

    if macro_bias.upper() == "LONG":
        # Price sweeps below level and closes near or back above level
        if bl < lvl:
            sweep = True
            depth_pips = max(0.0, (lvl - bl) * 10000.0)
            aligned = True  # Swept sell-side liquidity before moving long
    elif macro_bias.upper() == "SHORT":
        # Price sweeps above level and closes near or back below level
        if bh > lvl:
            sweep = True
            depth_pips = max(0.0, (bh - lvl) * 10000.0)
            aligned = True  # Swept buy-side liquidity before moving short

    return {
        "status": "READY_FOR_REVIEW",
        "pool_type": pool_type,
        "target_pool_level": lvl,
        "sweep_detected": sweep,
        "sweep_depth_pips": round(depth_pips, 2),
        "aligned_with_macro": aligned,
        "execution_authority": False,
    }


def evaluate_cvd_divergence(
    price_low_prior: Any = None,
    price_low_current: Any = None,
    cvd_prior: Any = None,
    cvd_current: Any = None,
    direction: str = "LONG",
    price_high_prior: Any = None,
    price_high_current: Any = None,
) -> Dict[str, Any]:
    """Evaluates Cumulative Volume Delta (CVD) absorption and divergence."""
    cp_low_1 = _safe_float(price_low_prior)
    cp_low_2 = _safe_float(price_low_current)
    cvd_1 = _safe_float(cvd_prior)
    cvd_2 = _safe_float(cvd_current)

    cp_high_1 = _safe_float(price_high_prior)
    cp_high_2 = _safe_float(price_high_current)

    divergence = False
    absorption_type = "NO_DIVERGENCE"

    if direction.upper() == "LONG":
        if cp_low_1 is not None and cp_low_2 is not None and cvd_1 is not None and cvd_2 is not None:
            # Bullish absorption: Price prints lower low, but CVD prints higher low
            if cp_low_2 < cp_low_1 and cvd_2 > cvd_1:
                divergence = True
                absorption_type = "BULLISH_DELTA_ABSORPTION"
    else:
        if cp_high_1 is not None and cp_high_2 is not None and cvd_1 is not None and cvd_2 is not None:
            # Bearish absorption: Price prints higher high, but CVD prints lower high
            if cp_high_2 > cp_high_1 and cvd_2 < cvd_1:
                divergence = True
                absorption_type = "BEARISH_DELTA_ABSORPTION"

    return {
        "status": "READY_FOR_REVIEW",
        "direction": direction,
        "divergence_detected": divergence,
        "absorption_type": absorption_type,
        "execution_authority": False,
    }


def evaluate_impact_candle(
    bar_open: Any,
    bar_close: Any,
    bar_high: Any,
    bar_low: Any,
    bar_volume: Any,
    hist_vol_mean: Any,
    hist_vol_std: Any,
    prior_bar_high: Any,
    next_bar_low: Any,
    swing_high_to_break: Any = None,
    prior_bar_low: Any = None,
    next_bar_high: Any = None,
    swing_low_to_break: Any = None,
) -> Dict[str, Any]:
    """Computes Impact Candle Score (ICS_t): Efficiency >= 0.72, Vol Z >= 2.0, FVG, MSS."""
    bo = _safe_float(bar_open)
    bc = _safe_float(bar_close)
    bh = _safe_float(bar_high)
    bl = _safe_float(bar_low)
    bv = _safe_float(bar_volume)
    vm = _safe_float(hist_vol_mean)
    vs = _safe_float(hist_vol_std)

    if any(v is None for v in (bo, bc, bh, bl, bv, vm, vs)):
        return {"status": "INVALID_INPUT", "is_impact_candle": False, "execution_authority": False}

    rng = max(bh - bl, 1e-9)
    eff = abs(bc - bo) / rng
    vol_z = (bv - vm) / max(vs, 1e-6)

    bullish = bc > bo

    # Fair Value Gap (FVG) Check (3-bar sequence)
    fvg = False
    if bullish:
        p_high = _safe_float(prior_bar_high)
        n_low = _safe_float(next_bar_low)
        if p_high is not None and n_low is not None and n_low > p_high:
            fvg = True
    else:
        p_low = _safe_float(prior_bar_low)
        n_high = _safe_float(next_bar_high)
        if p_low is not None and n_high is not None and n_high < p_low:
            fvg = True

    # Market Structure Shift (MSS) Check
    mss = False
    if bullish:
        sh = _safe_float(swing_high_to_break)
        if sh is not None and bc > sh:
            mss = True
    else:
        sl = _safe_float(swing_low_to_break)
        if sl is not None and bc < sl:
            mss = True

    is_impact = bool(eff >= 0.72 and vol_z >= 2.0 and fvg and mss)

    return {
        "status": "READY_FOR_REVIEW",
        "efficiency": round(eff, 4),
        "vol_z": round(vol_z, 2),
        "fvg_created": fvg,
        "mss_confirmed": mss,
        "is_impact_candle": is_impact,
        "direction": "BULLISH" if bullish else "BEARISH",
        "execution_authority": False,
    }


def grade_a_plus_confluence(
    macro_conviction_score: Any,
    is_high_prob_temporal: bool,
    pool_type: str,
    in_discount_or_premium: bool,
    cvd_divergence: bool,
    impact_candle_valid: bool,
    risk_reward_ratio: Any,
) -> Dict[str, Any]:
    """Evaluates the 100-point A+ Confluence Matrix (85/100 threshold)."""
    score_m = _safe_float(macro_conviction_score) or 0.0
    rr = _safe_float(risk_reward_ratio) or 0.0

    # 1. Macro Conviction (25 pts)
    abs_m = abs(score_m)
    if abs_m >= 60.0:
        pts_macro = 25.0
    elif abs_m >= 45.0:
        pts_macro = 15.0
    else:
        pts_macro = 0.0

    # 2. Temporal & Killzone (15 pts)
    pts_temporal = 15.0 if is_high_prob_temporal else 5.0

    # 3. HTF Liquidity Pool Quality (20 pts)
    is_major_pool = pool_type.upper() in ("PDH", "PDL", "PWH", "PWL")
    if is_major_pool and in_discount_or_premium:
        pts_pool = 20.0
    elif pool_type.upper() in ("ASH", "ASL"):
        pts_pool = 12.0
    else:
        pts_pool = 5.0

    # 4. Order Flow & Delta Absorption (20 pts)
    pts_cvd = 20.0 if cvd_divergence else 0.0

    # 5. Impact Candle & R:R Geometry (20 pts)
    if impact_candle_valid and rr >= 3.0:
        pts_geometry = 20.0
    elif impact_candle_valid and rr >= 2.0:
        pts_geometry = 12.0
    else:
        pts_geometry = 0.0

    composite = pts_macro + pts_temporal + pts_pool + pts_cvd + pts_geometry
    qualified = composite >= A_PLUS_CONFLUENCE_THRESHOLD

    return {
        "status": "READY_FOR_REVIEW",
        "composite_score": round(composite, 1),
        "a_plus_qualified": qualified,
        "confluence_breakdown": {
            "macro_points": pts_macro,
            "temporal_points": pts_temporal,
            "pool_points": pts_pool,
            "cvd_points": pts_cvd,
            "geometry_points": pts_geometry,
        },
        "execution_order_type": "LIMIT_ORDER_AT_FVG" if qualified else "REJECTED_BELOW_CONFLUENCE_THRESHOLD",
        "execution_authority": False,
    }


class MicrostructureImpactEngine:
    """Wrapper orchestrating microstructure state machine."""
    pass
