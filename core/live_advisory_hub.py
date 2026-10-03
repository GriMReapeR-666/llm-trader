"""Live Advisory Hub — wires UNUSED quant engines as advisory overlays.

Takes the 4 RED systems (Hierarchical, Meta-Labeling, Copula, CPCV) and exposes
one pure function that LLMOrchestrator calls every cycle. Never grants
execution_authority; only returns advisory dict that is journaled in toon_lite
and may suggest risk multiplier / HOLD reason.

Feature flags (all advisory, fail-closed):
  HIERARCHICAL_ADVISORY=1/0  (default 1 advisory)
  COPULA_TAIL_RISK_ADVISORY=1/0
  META_LABELING_ADVISORY=1/0
  CPCV_PROMOTION_GATE_ONLY=1 (always promotion-only, never per-tick)

Execution authority remains with DeterministicValidator + RiskManager only.
"""
from __future__ import annotations
import os
import math
import logging
from typing import Any, Dict, List, Optional, Sequence
import numpy as np

log = logging.getLogger(__name__)

def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in {"1","true","yes","on"}

# ----------------------------------------------------------------------
# Hierarchical Meta-Portfolio
# ----------------------------------------------------------------------
def hierarchical_advisory(
    prices: Sequence[float],
    current_adx: float = 25.0,
    atr_history: Optional[Sequence[float]] = None,
    current_atr: float = 0.0010,
    strategy_type: str = "TREND_MOMENTUM",
    open_portfolio_r: float = 0.0,
    cross_strategy_correlation: float = 0.0,
    base_risk_pct: float = 0.01,
) -> Dict[str, Any]:
    if not _flag("HIERARCHICAL_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False}
    try:
        from core.hierarchical_portfolio import HierarchicalMetaPortfolioEngine
        eng = HierarchicalMetaPortfolioEngine(base_risk_pct=base_risk_pct)
        res = eng.evaluate(
            prices=prices,
            current_adx=current_adx,
            atr_history=atr_history,
            current_atr=current_atr,
            strategy_type=strategy_type,
            open_portfolio_r=open_portfolio_r,
            cross_strategy_correlation=cross_strategy_correlation,
        )
        # res is already advisory
        res["enabled"] = True
        res["execution_authority"] = False
        res["advisory_only"] = True
        return res
    except Exception as e:
        log.debug("hierarchical_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "approved": False, "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# Copula Tail Risk
# ----------------------------------------------------------------------
def copula_advisory(
    returns_matrix: Optional[np.ndarray] = None,
    normal_heat_budget_r: float = 4.0,
    returns_by_asset: Optional[Dict[str, List[float]]] = None,
) -> Dict[str, Any]:
    if not _flag("COPULA_TAIL_RISK_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False}
    try:
        from core.copula_tail_risk import clayton_copula_tail_risk
        if returns_matrix is None and returns_by_asset:
            # build matrix from dict
            assets = list(returns_by_asset.keys())
            mat = np.column_stack([np.asarray(returns_by_asset[k], dtype=float) for k in assets])
            returns_matrix = mat
        if returns_matrix is None:
            return {"enabled": True, "tail_risk_state": "NO_DATA", "adjusted_heat_budget_r": normal_heat_budget_r, "execution_authority": False, "advisory_only": True}
        res = clayton_copula_tail_risk(
            returns_matrix=np.asarray(returns_matrix, dtype=float),
            normal_heat_budget_r=normal_heat_budget_r,
        )
        res["enabled"] = True
        res["execution_authority"] = False
        res["advisory_only"] = True
        return res
    except Exception as e:
        log.debug("copula_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# Meta-Labeling (Triple-Barrier + P filter) — now live, not stub
# ----------------------------------------------------------------------
def meta_label_advisory(
    prices: Sequence[float],
    event_indices: Optional[List[int]] = None,
    event_sides: Optional[Sequence[str]] = None,
    upper_r: float = 1.5,
    lower_r: float = 1.0,
    vertical_bars: int = 24,
    volatility: Optional[Sequence[float]] = None,
    feature_matrix: Optional[np.ndarray] = None,
    confidence_threshold: float = 0.60,
) -> Dict[str, Any]:
    if not _flag("META_LABELING_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False}
    try:
        from core.meta_labeling import apply_triple_barrier, MetaLabelingProbabilityClassifier
        # --- Auto-detect swing events if not provided (support/resistance extrema) ---
        _auto_sides: List[str] = []
        if event_indices is None:
            event_indices = []
            n = len(prices)
            if n >= 22:
                # Local extremum over 10-bar window: same logic as MarketDataProvider._identify_support_resistance
                for i in range(10, n - 1):
                    window = prices[max(0, i - 10): min(n, i + 11)]
                    if len(window) < 5:
                        continue
                    # Direction follows the extremum type: a local maximum is a
                    # natural short entry, a local minimum a natural long entry.
                    # (Ties satisfy both; the maximum wins so the side is never
                    # ambiguous.) Without this, apply_triple_barrier labels every
                    # event long, which inverts every short-side outcome.
                    if prices[i] == max(window):
                        event_indices.append(i)
                        _auto_sides.append("short")
                    elif prices[i] == min(window):
                        event_indices.append(i)
                        _auto_sides.append("long")
                # Keep last 12 events, exclude forming bar (n-1)
                event_indices = [e for e in event_indices if e < n - 1][-12:]
                _auto_sides = _auto_sides[-len(event_indices):] if len(event_indices) > 0 else []
                # Fallback: last 3 completed bars if no extrema found
                if not event_indices and n >= 4:
                    event_indices = list(range(max(0, n - 4), n - 1))
            elif n > 1:
                event_indices = [n - 2]
        # --- Volatility proxy if not provided: rolling ATR-like std ---
        if volatility is None:
            try:
                rets = np.diff(np.log(np.asarray(prices, dtype=float)))
                rets = rets[np.isfinite(rets)]
                if len(rets) >= 14:
                    # Simple ATR proxy: rolling std of returns * price
                    vol = [float(np.std(rets[max(0, i - 14): i + 1]) * prices[min(i + 1, len(prices) - 1)]) for i in range(len(prices))]
                    volatility = vol
            except Exception:
                volatility = None
        barriers = apply_triple_barrier(prices, event_indices, upper_r=upper_r, lower_r=lower_r, vertical_barrier_bars=vertical_bars, volatility=volatility, sides=(event_sides if event_sides is not None else (_auto_sides or None)))
        # --- Build feature matrix if not provided: use price-derived features aligned to events ---
        if feature_matrix is None and len(barriers) > 0:
            try:
                feats = []
                for b in barriers:
                    idx = b.entry_index
                    # Features: return momentum, distance to recent high/low, volatility
                    p = float(prices[idx])
                    p5 = float(prices[max(0, idx - 5)])
                    p20 = float(prices[max(0, idx - 20)])
                    mom5 = (p - p5) / p if p != 0 else 0.0
                    mom20 = (p - p20) / p if p != 0 else 0.0
                    vol_feat = float(volatility[idx]) if volatility and idx < len(volatility) else 0.001
                    high20 = max(prices[max(0, idx - 20): idx + 1]) if idx >= 0 else p
                    low20 = min(prices[max(0, idx - 20): idx + 1]) if idx >= 0 else p
                    dist_high = (p - high20) / p if p != 0 else 0.0
                    dist_low = (p - low20) / p if p != 0 else 0.0
                    feats.append([mom5, mom20, vol_feat, dist_high, dist_low])
                feature_matrix = np.asarray(feats, dtype=float)
            except Exception:
                feature_matrix = None
        # --- Train meta classifier if we have labels, else untrained 0.5 ---
        clf = MetaLabelingProbabilityClassifier(confidence_threshold=confidence_threshold)
        if feature_matrix is not None and len(barriers) > 0 and len(feature_matrix) == len(barriers):
            try:
                # Labels: 1 if barrier hit TP (positive R), 0 if SL or time
                y = np.array([1 if b.label == 1 else 0 for b in barriers], dtype=float)
                # Need at least 2 classes and 6 samples to fit
                if len(set(y.tolist())) >= 2 and len(y) >= 6:
                    clf.fit(np.asarray(feature_matrix), y)
                    proba = clf.predict_proba(np.asarray(feature_matrix))
                    passed = clf.filter_signals(np.asarray(feature_matrix))
                else:
                    # Not enough to train — use expanding win-rate as proba proxy
                    win_rate = float(np.mean(y)) if len(y) > 0 else 0.5
                    proba = np.array([win_rate] * len(barriers))
                    passed = proba >= confidence_threshold
            except Exception as _fit_exc:
                log.debug("meta_label fit failed: %s", _fit_exc)
                # Fallback: use win-rate proxy so filter isn't always on
                _wr = float(np.mean(y)) if len(y) > 0 else 0.5
                proba = np.array([_wr] * len(barriers))
                passed = proba >= confidence_threshold
        else:
            # No feature matrix or insufficient barriers — use win-rate proxy
            _wr = 0.5
            if barriers:
                _labels = [1 if b.label == 1 else 0 for b in barriers]
                _wr = float(np.mean(_labels)) if _labels else 0.5
            proba = np.array([_wr] * len(barriers))
            passed = proba >= confidence_threshold
        return {
            "enabled": True,
            "events": len(event_indices),
            "event_indices": event_indices[-5:],
            "barriers": [{"entry": b.entry_index, "exit": b.exit_index, "touch": b.touch_type, "r": b.realized_r, "label": b.label} for b in barriers[:5]],
            "proba": [round(float(x), 3) for x in proba[:5]],
            "passed": [bool(x) for x in passed[:5]],
            "filter_threshold": confidence_threshold,
            "should_filter": bool(len(passed) > 0 and not passed[-1]) if len(passed) > 0 else False,
            "win_rate_proxy": round(float(np.mean([1 if b.label == 1 else 0 for b in barriers])) if barriers else 0.5, 3),
            "execution_authority": False,
            "advisory_only": True,
        }
    except Exception as e:
        log.debug("meta_label_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# BOCPD — Bayesian Online Changepoint (per-cycle, fail-closed)
# ----------------------------------------------------------------------
def bocpd_advisory(
    prices: Sequence[float],
    hazard_lambda: float = 30.0,
    threshold: float = 0.10,
) -> Dict[str, Any]:
    if not _flag("BOCPD_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False}
    try:
        from core.bocpd import BayesianOnlineChangepointDetector, detect_changepoints_offline
        if len(prices) < 12:
            return {"enabled": True, "status": "INSUFFICIENT_DATA", "bars": len(prices), "execution_authority": False, "advisory_only": True}
        # Online posterior on log-returns *20 to amplify FX small returns (0.0005 -> 0.010) for detector sensitivity
        rets = np.diff(np.log(np.asarray(prices, dtype=float))) * 20.0
        rets = rets[np.isfinite(rets)]
        if len(rets) < 10:
            return {"enabled": True, "status": "INSUFFICIENT_DATA", "returns": len(rets), "execution_authority": False, "advisory_only": True}
        detector = BayesianOnlineChangepointDetector(hazard_lambda=hazard_lambda)
        prob = 0.0
        for r in rets[-30:]:
            prob = detector.update(float(r))
        # Offline for recent history panel (lower threshold 0.15 vs 0.30)
        cps = detect_changepoints_offline(rets.tolist(), hazard_lambda=hazard_lambda, threshold=threshold)
        has_recent = bool(cps and cps[-1].get("index", -999) >= len(rets) - 10)
        return {
            "enabled": True,
            "changepoint_probability": round(float(prob), 4),
            "has_recent_shift": has_recent,
            "detected_count": len(cps),
            "changepoints": cps[-3:],
            "hazard_lambda": hazard_lambda,
            "threshold": threshold,
            "should_hold": bool(prob > 0.30 or has_recent),
            "execution_authority": False,
            "advisory_only": True,
        }
    except Exception as e:
        log.debug("bocpd_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# Fractional Differentiation — stationarity with memory preservation
# ----------------------------------------------------------------------
def fractional_diff_advisory(
    prices: Sequence[float],
    d: float = 0.40,
) -> Dict[str, Any]:
    if not _flag("FRACTIONAL_DIFF_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False}
    try:
        from core.fractional_diff import fractional_diff, adf_stationarity_score
        if len(prices) < 30:
            return {"enabled": True, "status": "INSUFFICIENT_DATA", "bars": len(prices), "execution_authority": False, "advisory_only": True}
        fd = fractional_diff(list(prices), d=d)
        t_stat = adf_stationarity_score(fd) if len(fd) > 0 else 0.0
        is_stat = bool(t_stat < -2.86)
        return {
            "enabled": True,
            "d": d,
            "adf_t_stat": round(float(t_stat), 3),
            "is_stationary": is_stat,
            "stationary_length": len(fd),
            "latest_value": round(float(fd.iloc[-1]), 6) if len(fd) > 0 else None,
            "memory_preserved": is_stat and d < 1.0,
            "execution_authority": False,
            "advisory_only": True,
        }
    except Exception as e:
        log.debug("fractional_diff_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# Kalman StatArb — dynamic beta + OU half-life (per-cycle)
# ----------------------------------------------------------------------
def kalman_advisory(
    prices: Sequence[float],
    hedge_prices: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    if not _flag("KALMAN_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False}
    try:
        from core.kalman_statarb import DynamicKalmanStatArb
        if len(prices) < 30:
            return {"enabled": True, "status": "INSUFFICIENT_DATA", "bars": len(prices), "execution_authority": False, "advisory_only": True}
        # Self-pairs with 1-bar lag if no hedge
        y = list(prices)
        if hedge_prices is None or len(hedge_prices) < len(y):
            x = y[:-1] + [y[-1]]
        else:
            x = list(hedge_prices)[-len(y):]
        eng = DynamicKalmanStatArb()
        last = None
        for yi, xi in zip(y, x):
            last = eng.update(float(yi), float(xi))
        if last is None:
            return {"enabled": True, "status": "EMPTY_SERIES", "execution_authority": False, "advisory_only": True}
        # Normalize: |z|>2 = entry, >3 = stop
        z = float(last.spread_z_score)
        if abs(z) >= 3.0:
            rec = "STOP"
        elif z < -2.0:
            rec = "BUY_SPREAD"
        elif z > 2.0:
            rec = "SELL_SPREAD"
        elif abs(z) <= 0.2:
            rec = "EXIT"
        else:
            rec = "HOLD"
        return {
            "enabled": True,
            "dynamic_beta": round(float(last.beta), 4),
            "alpha": round(float(last.alpha), 6),
            "spread_z_score": round(z, 3),
            "ou_half_life_bars": round(float(last.ou_half_life_bars), 1) if last.ou_half_life_bars else None,
            "signal": str(last.state_machine_signal),
            "recommendation": rec,
            "execution_authority": False,
            "advisory_only": True,
        }
    except Exception as e:
        log.debug("kalman_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# CPCV promotion gate (never per-tick)
# ----------------------------------------------------------------------
def cpcv_promotion_check(
    returns_series: Sequence[float],
    n_splits: int = 6,
    n_test_groups: int = 2,
) -> Dict[str, Any]:
    # always promotion-only, never blocks live cycle
    try:
        from core.cpcv_backtester import run_combinatorial_purged_backtest
        res = run_combinatorial_purged_backtest(
            returns_series=np.asarray(returns_series, dtype=float),
            n_splits=n_splits,
            n_test_groups=n_test_groups,
        )
        res["enabled"] = True
        res["execution_authority"] = False
        res["advisory_only"] = True
        res["gate"] = "PROMOTION_ONLY"
        return res
    except Exception as e:
        log.debug("cpcv_promotion_check failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "gate": "PROMOTION_ONLY"}

# ----------------------------------------------------------------------
# Alternative Unstructured NLP Alpha Advisory
# ----------------------------------------------------------------------
def alternative_nlp_advisory(
    transcript_text: Optional[str] = None,
    prior_10k_text: Optional[str] = None,
    current_10k_text: Optional[str] = None,
    current_job_openings: Optional[int] = None,
    prior_job_openings: Optional[int] = None,
    current_commits: Optional[int] = None,
    prior_commits: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluates 10-K revision index, earnings call hedging, and operational activity."""
    if not _flag("ALTERNATIVE_NLP_ADVISORY", "1"):
        return {"enabled": False, "execution_authority": False, "advisory_only": True}
    try:
        from intelligence.unstructured_alternative_alpha import (
            AlternativeActivityProxyScorer,
            EarningsCallHedgingAnalyzer,
            Form10KModificationAnalyzer,
        )

        mod_res = None
        if prior_10k_text and current_10k_text:
            mod_res = Form10KModificationAnalyzer.compute_modification_index(prior_10k_text, current_10k_text)

        hedging_res = None
        if transcript_text:
            hedging_res = EarningsCallHedgingAnalyzer.analyze_transcript(transcript_text)

        jobs_res = None
        if current_job_openings is not None and prior_job_openings is not None:
            jobs_res = AlternativeActivityProxyScorer.score_job_postings_momentum(current_job_openings, prior_job_openings)

        dev_res = None
        if current_commits is not None and prior_commits is not None:
            dev_res = AlternativeActivityProxyScorer.score_developer_velocity(current_commits, prior_commits)

        synth = AlternativeActivityProxyScorer.synthesize_alternative_alpha_signal(
            modification_index_result=mod_res,
            transcript_hedging_result=hedging_res,
            job_postings_result=jobs_res,
            developer_velocity_result=dev_res,
        )
        synth["enabled"] = True
        synth["execution_authority"] = False
        synth["advisory_only"] = True
        synth["modification_index"] = mod_res
        synth["hedging_analysis"] = hedging_res
        return synth
    except Exception as e:
        log.debug("alternative_nlp_advisory failed: %s", e)
        return {"enabled": True, "error": str(e)[:200], "execution_authority": False, "advisory_only": True}

# ----------------------------------------------------------------------
# Aggregated hub
# ----------------------------------------------------------------------
def build_live_advisory_snapshot(
    prices: Sequence[float],
    current_adx: float = 25.0,
    atr_history: Optional[Sequence[float]] = None,
    current_atr: float = 0.0010,
    strategy_type: str = "TREND_MOMENTUM",
    open_portfolio_r: float = 0.0,
    cross_strategy_correlation: float = 0.0,
    base_risk_pct: float = 0.01,
    returns_matrix: Optional[np.ndarray] = None,
    returns_by_asset: Optional[Dict[str, List[float]]] = None,
    meta_event_indices: Optional[List[int]] = None,
    cpcv_returns: Optional[Sequence[float]] = None,
    hedge_prices: Optional[Sequence[float]] = None,
    transcript_text: Optional[str] = None,
    prior_10k_text: Optional[str] = None,
    current_10k_text: Optional[str] = None,
) -> Dict[str, Any]:
    """One call for LLMOrchestrator to enrich toon_lite — now per-cycle (not 100-cycle stale).

    Adds per-cycle BOCPD, fractional_diff, Kalman (previously only auto_quant_tools batch).
    """
    return {
        "hierarchical": hierarchical_advisory(
            prices=prices, current_adx=current_adx, atr_history=atr_history,
            current_atr=current_atr, strategy_type=strategy_type,
            open_portfolio_r=open_portfolio_r, cross_strategy_correlation=cross_strategy_correlation,
            base_risk_pct=base_risk_pct,
        ),
        "copula_tail": copula_advisory(returns_matrix=returns_matrix, returns_by_asset=returns_by_asset),
        "meta_labeling": meta_label_advisory(prices=prices, event_indices=meta_event_indices),
        "bocpd": bocpd_advisory(prices=prices),
        "fractional_diff": fractional_diff_advisory(prices=prices),
        "kalman": kalman_advisory(prices=prices, hedge_prices=hedge_prices),
        "cpcv_promotion": cpcv_promotion_check(cpcv_returns or []) if cpcv_returns is not None else {"enabled": False, "gate": "PROMOTION_ONLY", "execution_authority": False},
        "alternative_nlp": alternative_nlp_advisory(transcript_text=transcript_text, prior_10k_text=prior_10k_text, current_10k_text=current_10k_text),
        "quant_workflow": {"enabled": _flag("PIPELINE_V2","0"), "mode": "PIPELINE_V2" if _flag("PIPELINE_V2","0") else "DISABLED", "execution_authority": False},
        "kronos": {"enabled": _flag("KRONOS_ADVISORY","1"), "mode": "ADVISORY_ONLY", "execution_authority": False},
        "execution_authority": False,
        "advisory_only": True,
    }
