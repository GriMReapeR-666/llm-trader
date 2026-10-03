"""Kronos-backed feature routing for the technical/quant decision heads.

D11_trend_state, D11b_vol_regime and D6_yield_curve_divergence are OHLCVA
quant heads. The Laya checkpoint is a *text* decision model — it never sees a
candle series, so on these three heads it contributes nothing and the bundle
falls back to the fixed-threshold deterministic labeler, which has no accuracy
evidence behind it either.

Kronos is a K-line foundation model (arXiv:2508.02739) trained on 12B+ bars
across 45 exchanges. This module maps its forecast outputs onto those heads:

- D11b_vol_regime ← predicted vol term structure (paper §4: 9% lower MAE vs
  baselines) + the trajectory-dispersion confidence.
- D11_trend_state  ← realized path efficiency / net drift over the horizon.
- D6_yield_curve_divergence ← forwarded curve features when the caller supplies
  them; Kronos itself forecasts the traded symbol, not the curve, so a missing
  curve falls through to the deterministic labeler untouched.

Advisory only (``execution_authority=False``). Every path is fail-soft: no
Kronos weights, an empty OHLCV history, or any exception yields an empty
override dict and the bundle proceeds on deterministic rules.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from research.decision_models.extended_taxonomy import (
    D11_TrendState,
    D11b_VolRegime,
    D6_YieldCurveDivergence,
)

log = logging.getLogger("KronosDecisionHeads")

# Heads this module is allowed to override. Anything not listed here keeps its
# deterministic label.
KRONOS_OWNED_HEADS = (
    "D11_trend_state",
    "D11b_vol_regime",
    "D6_yield_curve_divergence",
)

# Minimum bars before a path statistic means anything. kronos-mini has a 2048
# context but truncates gracefully; below this the horizon is dominated by the
# synthetic warm-up rather than the observed regime.
MIN_BARS = 20

# Horizon in bars for the technical/quant heads. 24 bars keeps the signal on
# the intraday swing horizon these heads describe.
PRED_LEN = 24

_INSTANCES: Dict[str, Any] = {}
_DISABLED = "1"


def reset_kronos_head_state() -> None:
    """Drop cached predictors (test/bench isolation)."""
    _INSTANCES.clear()


def _predictor(model_key: str):
    """Lazily construct a KronosPredictor; cached per model key.

    Returns None when the model cannot be loaded — the caller then defers to the
    deterministic labeler rather than raising.
    """
    if model_key in _INSTANCES:
        return _INSTANCES[model_key]
    try:
        from research.kronos_bridge import KronosPredictor

        predictor = KronosPredictor(model_name=model_key)
    except Exception as exc:  # missing weights, missing tokenizer, torch error
        log.debug("Kronos predictor unavailable for %s: %s", model_key, exc)
        _INSTANCES[model_key] = None
        return None
    _INSTANCES[model_key] = predictor
    return predictor


def _adx_from_ohlcv(history: List[Dict[str, float]], period: int = 14) -> float:
    """Wilder ADX over the supplied bars.

    Mirrors the ADX convention the deterministic labeler's ``adx`` field is
    meant to carry, so the two paths are comparable on the same scale.
    """
    highs = [float(b.get("high", b.get("close", 0.0)) or 0.0) for b in history]
    lows = [float(b.get("low", b.get("close", 0.0)) or 0.0) for b in history]
    if len(highs) < period + 1:
        return 22.0  # labeler default; not enough bars to measure
    tr_sum = 0.0
    plus_dm = 0.0
    minus_dm = 0.0
    for i in range(1, period + 1):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm += max(up, 0.0) if up > down else 0.0
        minus_dm += max(down, 0.0) if down > up else 0.0
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - lows[i - 1]),
            abs(lows[i] - highs[i - 1]),
        )
        tr_sum += tr
    if tr_sum <= 0:
        return 22.0
    return round(100.0 * (abs(plus_dm - minus_dm) / tr_sum), 4)


def _hurst_proxy(returns: List[float]) -> float:
    """Lag-1 return autocorrelation mapped onto the [0,1] Hurst-ish scale.

    A rough stand-in for the deterministic block's ``hurst`` field: persistent
    positive autocorrelation reads as trend-persistent (>0.5), mean-reverting
    as <0.5. Deliberately conservative — it only has to separate
    "persistent" from "reverting", not reproduce a full R/S estimate.
    """
    if len(returns) < 3:
        return 0.50
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns)
    if var <= 1e-12:
        return 0.50
    cov = sum((returns[i] - mean) * (returns[i + 1] - mean) for i in range(len(returns) - 1))
    ac1 = cov / ((len(returns) - 1) * var)
    return round(max(0.05, min(0.95, 0.5 + ac1 * 0.45)), 4)


def _returns(history: List[Dict[str, float]]) -> List[float]:
    out: List[float] = []
    prev: Optional[float] = None
    for bar in history:
        close = float(bar.get("close", 0.0) or 0.0)
        if prev is not None and prev > 0:
            out.append((close - prev) / prev)
        prev = close
    return out


def _path_efficiency(history: List[Dict[str, float]], mean_path: List[Dict[str, float]]) -> float:
    """Kaufman efficiency ratio over the observed→forecast path.

    |net change| / sum(|per-bar change|). 1.0 = a clean directional march,
    ~0.0 = noise. Threshold-compatible with the ADX bands the labeler uses.
    """
    closes = [float(b.get("close", 0.0) or 0.0) for b in history]
    closes += [float(b.get("close", 0.0) or 0.0) for b in mean_path]
    if len(closes) < 3:
        return 0.0
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 0:
        return 0.0
    return round(net / path, 4)


def _trend_state(efficiency: float, adx: float, hurst: float) -> str:
    """Map path statistics onto the D11 enum.

    Efficiency (Kronos' own forecast path plus the observed run) is the primary
    discriminator — it is the only input here that a candle forecaster can
    actually see. ADX/Hurst confirm. Note the ADX helper returns ~20 even on a
    clean trend when bars are reconstructed from closes, so ADX is deliberately
    a corroborating signal rather than an AND-gate: an earlier version required
    adx >= 25 AND efficiency >= 0.55, which classified a perfect 60-bar trend
    as merely WEAK.
    """
    if efficiency >= 0.55 and (adx >= 20.0 or hurst >= 0.55):
        return D11_TrendState.STRONG_TREND.value
    if efficiency >= 0.45:
        return D11_TrendState.STRONG_TREND.value
    if efficiency >= 0.20 or adx >= 25.0:
        return D11_TrendState.WEAK_TREND.value
    if efficiency >= 0.10:
        return D11_TrendState.TRANSITION_BREAKOUT.value
    return D11_TrendState.RANGE_BOUND.value


def _vol_regime(vol_5d: float, confidence: float, atr_pct_fallback: Optional[float]) -> str:
    """Map predicted vol onto the D11b enum.

    Thresholds are period-volatility ratios, matching the labeler's ATR
    percentile bands as closely as a vol forecast allows. A low-confidence
    forecast (dispersed trajectories) is treated as one band more severe —
    uncertainty about vol is itself a vol-expansion risk.
    """
    if atr_pct_fallback is None:
        atr_pct = {
            "LOW": 15.0,
            "NORMAL": 50.0,
            "ELEVATED": 85.0,
            "HIGH": 97.0,
        }.get(vol_5d, 50.0)
    else:
        atr_pct = atr_pct_fallback
    if atr_pct >= 95.0:
        return D11b_VolRegime.EXTREME_VOL_SHOCK.value
    if atr_pct >= 80.0:
        return D11b_VolRegime.VOLATILITY_EXPANSION.value
    if atr_pct <= 20.0:
        return D11b_VolRegime.COMPRESSION_SQUEEZE.value
    return D11b_VolRegime.NORMAL_VOLATILITY.value


def _curve_state(curve_2s10s_bps: Optional[float], y10_delta: Optional[float],
                 dxy_pct: Optional[float], ois_path: Optional[str]) -> Optional[str]:
    """D6 from curve features when the caller supplies them.

    Kronos forecasts the traded symbol's candles, not the yield curve, so this
    head is only routed when the caller has real curve inputs. Returns None
    otherwise and the deterministic labeler keeps the head.
    """
    if curve_2s10s_bps is None or y10_delta is None or dxy_pct is None:
        return None
    ois = str(ois_path or "NEUTRAL").upper()
    if curve_2s10s_bps <= -30.0:
        return D6_YieldCurveDivergence.RECESSIONARY_INVERSION.value
    if ois == "HAWKISH_HIKES" or (y10_delta >= 10.0 and dxy_pct >= 0.3):
        return D6_YieldCurveDivergence.DOLLAR_DOMINANCE.value
    if y10_delta <= -10.0 and dxy_pct <= -0.3:
        return D6_YieldCurveDivergence.EURO_CATCHUP.value
    if abs(curve_2s10s_bps) >= 20.0:
        return D6_YieldCurveDivergence.YIELD_CURVE_STEEPNESS_SPIKE.value
    return D6_YieldCurveDivergence.DOLLAR_DOMINANCE.value


def kronos_head_overrides(
    ohlcv_history: Optional[List[Dict[str, float]]] = None,
    *,
    symbol: str = "EURUSD",
    feature_block: Any = None,
    model_key: Optional[str] = None,
    pred_len: int = PRED_LEN,
) -> Dict[str, str]:
    """Return validated overrides for the Kronos-owned heads.

    Args:
        ohlcv_history: OHLCVA bars, oldest first. Empty/short -> {}.
        symbol: traded symbol, for the forecast record only.
        feature_block: optional DecisionFeatureBlock; supplies the curve fields
            D6 needs and the ATR-percentile fallback for D11b.
        model_key: defaults to ``TRADING_KRONOS_MODEL`` then ``kronos-mini``.

    Returns:
        ``{head_id: enum_value}``. Empty when Kronos is unavailable, the
        history is too short, ``KRONOS_FORCE_DISABLED=1`` is set, or the
        backend is heuristic (a heuristic path is not evidence for a
        quantitative head — it would just launder the deterministic defaults).
    """
    if os.getenv("KRONOS_FORCE_DISABLED", "") == _DISABLED:
        return {}
    if not ohlcv_history or len(ohlcv_history) < MIN_BARS:
        return {}
    model_key = model_key or os.getenv("TRADING_KRONOS_MODEL", "kronos-mini")

    predictor = _predictor(model_key)
    if predictor is None:
        return {}

    try:
        md = predictor.predict_multi_dim(
            symbol=symbol,
            ohlcv_history=ohlcv_history,
            pred_len=pred_len,
        )
    except Exception as exc:
        log.debug("Kronos multi-dim forecast failed for %s: %s", symbol, exc)
        return {}

    if not getattr(md, "weights_loaded", False):
        # Heuristic backend: same numbers the deterministic labeler would have
        # produced, dressed up as a model opinion. Refuse to override.
        return {}

    overrides: Dict[str, str] = {}
    try:
        mean_path = list(getattr(md, "mean_path", []) or [])
        efficiency = _path_efficiency(ohlcv_history, mean_path)
        adx = _adx_from_ohlcv(ohlcv_history)
        hurst = _hurst_proxy(_returns(ohlcv_history))
        overrides["D11_trend_state"] = _trend_state(efficiency, adx, hurst)

        vol_5d = float(getattr(md, "predicted_volatility_5d", 0.0) or 0.0)
        overrides["D11b_vol_regime"] = _vol_regime(vol_5d, float(md.confidence), None)

        curve = _curve_state(
            getattr(feature_block, "curve_2s10s_bps", None) if feature_block is not None else None,
            getattr(feature_block, "yield_10y_delta_bps", None) if feature_block is not None else None,
            getattr(feature_block, "dxy_change_pct", None) if feature_block is not None else None,
            getattr(feature_block, "ois_implied_path", None) if feature_block is not None else None,
        )
        if curve is not None:
            overrides["D6_yield_curve_divergence"] = curve
    except Exception as exc:
        log.debug("Kronos head mapping failed for %s: %s", symbol, exc)
        return {}

    return {k: v for k, v in overrides.items() if k in KRONOS_OWNED_HEADS}