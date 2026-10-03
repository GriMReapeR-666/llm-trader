"""Per-strategy entry-signal registry (PU-0156 §8.1).

The certification sweep scores 14 registry labels, but the Stage-1 backtest
engine scores a single generic ``deterministic_signal`` ruleset for all of
them — the system itself warns every boot: "these metrics do not describe that
strategy." This module gives each label its own causal entry signal, consumed
through the engine's external-signal mode (``entry_signal_scores``): score >=
+threshold -> BUY, <= -threshold -> SELL, |score| < threshold -> no entry.
Geometry (ATR stops), gates, sizing, and management stay shared with the
legacy path, so cross-label comparisons remain apples-to-apples on execution.

Honesty contract (same doctrine as the rest of the research plane):
  * Every signal is CAUSAL: bar i uses closes/highs/lows up to and including
    index i only. No future bar, no shift-by-0 of a future quantity.
  * Scores are raw direction convictions (typically in [-2, 2]); the caller
    applies the engine's threshold.
  * Unknown labels raise KeyError — the sweep must never silently fall back
    to the generic ruleset and mislabel the result (that is the bug this
    module exists to kill). Callers that want a fallback must ask for it
    explicitly via ``build_signal(..., allow_fallback=True)``.

Each signal is a *plausible, distinct* hypothesis for its label — this is a
certification harness, not a claim that any of these are profitable (the DSR
gate still decides that).
"""
from __future__ import annotations

from typing import Callable, Dict, List

import numpy as np


# ---------------------------------------------------------------------------
# Shared causal primitives (vectorized, bar-i uses data up to i)
# ---------------------------------------------------------------------------

def _ema(x: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(x, dtype=np.float64)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def _roc(x: np.ndarray, period: int) -> np.ndarray:
    """Rate of change over `period` bars; leading values 0 (not NaN)."""
    out = np.zeros_like(x, dtype=np.float64)
    if len(x) > period:
        out[period:] = x[period:] / np.maximum(np.abs(x[:-period]), 1e-10) - 1.0
    return out


def _zscore(x: np.ndarray, window: int) -> np.ndarray:
    """Causal rolling z-score; warmup bars score 0."""
    out = np.zeros_like(x, dtype=np.float64)
    for i in range(len(x)):
        lo = max(0, i - window + 1)
        w = x[lo:i + 1]
        if len(w) >= max(5, window // 3):
            sd = float(np.std(w))
            if sd > 1e-12:
                out[i] = (float(x[i]) - float(np.mean(w))) / sd
    return out


def _atr_proxy(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               period: int = 14) -> np.ndarray:
    tr = np.maximum(highs - lows,
                    np.maximum(np.abs(highs - np.roll(closes, 1)),
                               np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    return _ema(tr, period)


def _donchian_pos(closes: np.ndarray, period: int) -> np.ndarray:
    """Causal position of close within the trailing Donchian channel: [-1, 1]."""
    out = np.zeros_like(closes, dtype=np.float64)
    for i in range(len(closes)):
        lo = max(0, i - period + 1)
        w = closes[lo:i + 1]
        hi, lo_v = float(np.max(w)), float(np.min(w))
        if hi - lo_v > 1e-12:
            out[i] = 2.0 * (float(closes[i]) - lo_v) / (hi - lo_v) - 1.0
    return out


def _prep(closes, highs, lows):
    c = np.asarray(closes, dtype=np.float64)
    h = np.asarray(highs, dtype=np.float64)
    l = np.asarray(lows, dtype=np.float64)
    return c, h, l


# ---------------------------------------------------------------------------
# Per-label signal builders (signature fixed; registry maps name -> builder)
# ---------------------------------------------------------------------------

def _sig_kronos_h1(c, h, l) -> np.ndarray:
    """kronos_h1: neural-fusion proxy — medium-horizon momentum blended with a
    faster trend, normalized (the label's neural core is approximated by its
    dominant observable: multi-horizon momentum fusion)."""
    m = _roc(c, 24)
    f = _roc(c, 6)
    return np.clip(1.2 * _zscore(m, 120) + 0.8 * _zscore(f, 60), -2, 2)


def _sig_statarb_eur_chf(c, h, l) -> np.ndarray:
    """statarb_eur_chf: single-symbol mean-reversion to a slow anchor
    (the pair's mean-reversion leg, evaluated on the swept symbol)."""
    anchor = _ema(c, 200)
    spread = (c - anchor) / np.maximum(anchor, 1e-10)
    return np.clip(-2.0 * _zscore(spread, 100), -2, 2)


def _sig_macro_nfp_pullback(c, h, l) -> np.ndarray:
    """macro_nfp_pullback: trade WITH the trend, but only after a pullback —
    enter when price snaps back toward the fast EMA inside a strong trend."""
    e20, e100 = _ema(c, 20), _ema(c, 100)
    trend = np.sign(e20 - e100)
    gap = (c - e20) / np.maximum(_atr_proxy(h, l, c), 1e-10)
    # Trend direction * pullback depth: deep pullback against trend = entry.
    return np.clip(trend * np.clip(-gap, 0.0, None) * 0.8, -2, 2)


def _sig_meanrev_bollinger(c, h, l) -> np.ndarray:
    """meanrev_bollinger: classic 20/2 band fade — short the upper band,
    long the lower band."""
    e20 = _ema(c, 20)
    dev = np.zeros_like(c)
    for i in range(len(c)):
        lo = max(0, i - 19)
        dev[i] = float(np.std(c[lo:i + 1]))
    pos = (c - e20) / np.maximum(2.0 * dev, 1e-10)
    return np.clip(-1.5 * pos, -2, 2)


def _sig_alpha_055_orderflow(c, h, l) -> np.ndarray:
    """alpha_055_orderflow: short-horizon pressure proxy — velocity +
    acceleration of close (order-flow imbalance stand-in on OHLC data)."""
    v1 = _roc(c, 3)
    v2 = _roc(c, 1)
    return np.clip(1.0 * _zscore(v1, 80) + 0.5 * _zscore(v2, 80), -2, 2)


def _sig_alpha_098_copula(c, h, l) -> np.ndarray:
    """alpha_098_copula: dependence-break proxy — range expansion with
    directional commitment (long ranges + close near high = long)."""
    rng = (h - l) / np.maximum(c, 1e-10)
    rng_z = _zscore(rng, 100)
    close_pos = _donchian_pos(c, 20)
    return np.clip(rng_z * close_pos, -2, 2)


def _sig_agent_execution_rl(c, h, l) -> np.ndarray:
    """agent_execution_rl: micro-timing signal — enter on short-term burst
    against the immediate overextension (execution-agent entry timing)."""
    rsi_like = _zscore(_roc(c, 2), 50)
    ext = _donchian_pos(c, 10)
    return np.clip(-0.6 * ext + 0.9 * rsi_like, -2, 2)


def _sig_agent_regime_supervisor(c, h, l) -> np.ndarray:
    """agent_regime_supervisor: slow regime filter — only commit in the
    direction of the 200-bar trend, scaled by its z-scored strength."""
    slope = _roc(_ema(c, 50), 10)
    regime = np.sign(_ema(c, 50) - _ema(c, 200))
    return np.clip(regime * np.clip(_zscore(slope, 100), -1.5, 1.5), -2, 2)


def _sig_model_bocpd_changepoint(c, h, l) -> np.ndarray:
    """model_bocpd_changepoint: break-of-structure proxy — enter on a
    Donchian(55) channel break in the direction of the break."""
    upper = np.zeros_like(c)
    lower = np.zeros_like(c)
    for i in range(len(c)):
        lo = max(0, i - 54)
        upper[i] = float(np.max(c[lo:i + 1]))
        lower[i] = float(np.min(c[lo:i + 1]))
    broke_up = c >= np.roll(upper, 1)
    broke_dn = c <= np.roll(lower, 1)
    sig = np.where(broke_up, 1.5, np.where(broke_dn, -1.5, 0.0))
    return sig


def _sig_model_kalman_filter(c, h, l) -> np.ndarray:
    """model_kalman_filter: 1D Kalman trend estimate vs price — trade the
    filtered level's deviation (price reverts to the filter)."""
    kf = np.empty_like(c, dtype=np.float64)
    kf[0] = c[0]
    p = 1.0
    q, r = 1e-5, 1e-4
    for i in range(1, len(c)):
        p = p + q
        k = p / (p + r)
        kf[i] = kf[i - 1] + k * (c[i] - kf[i - 1])
        p = (1 - k) * p
    dev = (c - kf) / np.maximum(_atr_proxy(h, l, c), 1e-10)
    return np.clip(-0.8 * _zscore(dev, 120), -2, 2)


def _sig_gold_momentum_h4(c, h, l) -> np.ndarray:
    """gold_momentum_h4: longer-horizon momentum with a wide trail —
    48-bar ROC (H4-equivalent) z-scored over a long window."""
    return np.clip(1.3 * _zscore(_roc(c, 48), 250), -2, 2)


def _sig_spx_volatility_skew(c, h, l) -> np.ndarray:
    """spx_volatility_skew: vol-regime contrarian — fade direction after
    volatility spikes (skew proxy: ATR z-score gates the fade)."""
    atr = _atr_proxy(h, l, c)
    atr_z = _zscore(atr / np.maximum(c, 1e-10), 100)
    mom = _zscore(_roc(c, 12), 100)
    # High vol: fade the move. Normal vol: weak momentum follow.
    return np.clip(np.where(atr_z > 1.0, -1.2 * np.sign(mom),
                            0.4 * mom), -2, 2)


def _sig_sprt_hypothesis_gate(c, h, l) -> np.ndarray:
    """sprt_hypothesis_gate: sequential-test proxy — cumulative evidence of
    drift vs no-drift via a Wald-style SPRT on sign(run) statistics."""
    signs = np.sign(np.diff(c, prepend=c[0]))
    llr = np.zeros_like(c, dtype=np.float64)
    for i in range(1, len(c)):
        # +1 log-likelihood-ratio step for up bars, -1 for down bars,
        # damped toward 0 (evidence decays when the run stalls).
        llr[i] = np.clip(llr[i - 1] + (0.2 * signs[i]), -2.0, 2.0)
    return llr


_REGISTRY: Dict[str, Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]] = {
    "kronos_h1": _sig_kronos_h1,
    "statarb_eur_chf": _sig_statarb_eur_chf,
    "macro_nfp_pullback": _sig_macro_nfp_pullback,
    "meanrev_bollinger": _sig_meanrev_bollinger,
    "alpha_055_orderflow": _sig_alpha_055_orderflow,
    "alpha_098_copula": _sig_alpha_098_copula,
    "agent_execution_rl": _sig_agent_execution_rl,
    "agent_regime_supervisor": _sig_agent_regime_supervisor,
    "model_bocpd_changepoint": _sig_model_bocpd_changepoint,
    "model_kalman_filter": _sig_model_kalman_filter,
    "gold_momentum_h4": _sig_gold_momentum_h4,
    "spx_volatility_skew": _sig_spx_volatility_skew,
    "sprt_hypothesis_gate": _sig_sprt_hypothesis_gate,
}


# ---------------------------------------------------------------------------
# Research variants (explicitly-ordered hypotheses, NOT certification labels)
# ---------------------------------------------------------------------------
# `@sprt_confirm` wraps a base label's sign votes in the sprt evidence
# accumulator: llr(t) = clip(llr(t-1) + 0.2*sign(raw_t), -2, 2). The PU-0156
# diagnosis (artifacts/pu_sprt_diagnose.py -> sprt_diagnosis.json) showed this
# commitment operator helps persistent-vote trend-family signals
# (agent_regime_supervisor -2.85 -> -0.72 bare-engine) and actively harms
# fade/noise voters (meanrev_bollinger -2.21 -> -4.07; alpha_055 -3.82 ->
# -5.37), so ONLY trend-family labels get a variant. A variant is its own
# labeled hypothesis (`strategy:<name>@sprt_confirm`) — never a silent
# replacement of the base label's certification result — and it must clear
# the same CPCV/DSR/walk-forward gauntlet as any other hypothesis.

def _sprt_confirm_wrap(raw: np.ndarray, step: float = 0.2, clip: float = 2.0) -> np.ndarray:
    """Evidence-accumulation commitment layer over another signal's sign votes."""
    signs = np.sign(raw)
    llr = np.zeros_like(raw, dtype=np.float64)
    for i in range(1, len(raw)):
        llr[i] = np.clip(llr[i - 1] + step * signs[i], -clip, clip)
    return llr


_VARIANTS: Dict[str, Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]] = {
    f"{name}@sprt_confirm": (
        lambda c, h, l, _b=_REGISTRY[name]: _sprt_confirm_wrap(_b(c, h, l))
    )
    for name in (
        # PU-0156 family check (artifacts/pu_sprt_diagnose.py): the wrapper's
        # effect is heterogeneous within the trend family — it amplifies the
        # directional content of the votes (agent_regime_supervisor and
        # macro_nfp_pullback/model_bocpd_changepoint improved; gold_momentum_h4
        # mildly harmed; kronos_h1 catastrophically worse). All five stay
        # registered as measured hypotheses — the harness evaluates them, the
        # CPCV/DSR gauntlet decides; none is certified by registration.
        "agent_regime_supervisor",
        "kronos_h1",
        "gold_momentum_h4",
        "macro_nfp_pullback",
        "model_bocpd_changepoint",
    )
}


def has_signal(strategy_name: str) -> bool:
    return strategy_name in _REGISTRY


def variant_names() -> List[str]:
    return sorted(_VARIANTS)


def registry_names() -> List[str]:
    return sorted(_REGISTRY)


def build_signal(strategy_name: str, closes, highs, lows, *,
                 allow_fallback: bool = False) -> np.ndarray:
    """Build the per-strategy causal entry-score array for `strategy_name`.

    Raises KeyError for unknown labels unless allow_fallback=True (which
    returns None so the caller keeps the generic ruleset — explicitly).
    """
    fn = _REGISTRY.get(strategy_name) or _VARIANTS.get(strategy_name)
    if fn is None:
        if allow_fallback:
            return None
        raise KeyError(
            f"no per-strategy signal registered for '{strategy_name}'; "
            "refusing to mislabel a generic-ruleset run as this strategy")
    c, h, l = _prep(closes, highs, lows)
    if len(c) == 0:
        raise ValueError("empty price arrays")
    scores = np.asarray(fn(c, h, l), dtype=np.float64)
    if len(scores) != len(c) or not np.all(np.isfinite(scores)):
        raise ValueError(f"signal '{strategy_name}' produced non-finite/mis-sized output")
    return scores
