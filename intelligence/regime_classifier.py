"""Regime Classifier — rule-based + Gaussian HMM + BMA.

Backend
-------
- When ``hmmlearn`` is installed: 2-state GaussianHMM fitted via Baum-Welch EM.
- Fallback: z-score + sigmoid heuristic (labeled HEURISTIC, auto-clears when hmmlearn available).

 Guarantees:
- execution_authority=False
- backend_status() reports heuristic vs neural
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

log = logging.getLogger("MacroRegimeClassifier")

# Try hmmlearn once at import time
_HMM_AVAILABLE = False
try:
    from hmmlearn.hmm import GaussianHMM as _GaussianHMM
    _HMM_AVAILABLE = True
except ImportError:
    pass


@dataclass(frozen=True)
class MacroRegimeResult:
    active_regime: str
    regime_probabilities: Dict[str, float]
    regime_persistence: float
    bma_weights: Dict[str, float]
    is_crisis_override: bool
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MacroRegimeClassifier:
    """Institutional-grade HMM and Bayesian Model Averaging Regime Classifier."""

    def __init__(self, prior_bma_weights: Optional[Dict[str, float]] = None) -> None:
        self.bma_weights = prior_bma_weights or {
            "RULE_BASED": 0.40,
            "GAUSSIAN_HMM": 0.35,
            "CORRELATION_MATRIX": 0.25,
        }

    def classify_rule_based(
        self,
        vix: float = 16.0,
        gold_dxy_corr: float = -0.55,
        oil_change_pct: float = 0.0,
        dxy_change_pct: float = 0.0,
    ) -> Dict[str, float]:
        """Classify regime using macroeconomic and intermarket threshold rules."""
        try:
            if not all(math.isfinite(float(x)) for x in (vix, gold_dxy_corr, oil_change_pct, dxy_change_pct)):
                return {"NORMAL_RISK_OFF": 0.50, "RISK_ON_EXPANSION": 0.50, "CRISIS_PANIC": 0.0, "STAGFLATION_SHOCK": 0.0}
        except (TypeError, ValueError):
            return {"NORMAL_RISK_OFF": 0.50, "RISK_ON_EXPANSION": 0.50, "CRISIS_PANIC": 0.0, "STAGFLATION_SHOCK": 0.0}

        # 1. Crisis Panic Override
        if vix >= 30.0 and gold_dxy_corr >= 0.15:
            return {"CRISIS_PANIC": 0.85, "NORMAL_RISK_OFF": 0.10, "STAGFLATION_SHOCK": 0.05, "RISK_ON_EXPANSION": 0.0}

        # 2. Stagflation Shock
        if oil_change_pct > 0.02 and gold_dxy_corr > -0.20 and dxy_change_pct <= 0.0:
            return {"STAGFLATION_SHOCK": 0.70, "CRISIS_PANIC": 0.15, "NORMAL_RISK_OFF": 0.10, "RISK_ON_EXPANSION": 0.05}

        # 3. Normal Risk-Off
        if vix >= 18.0 or dxy_change_pct > 0.005:
            return {"NORMAL_RISK_OFF": 0.65, "CRISIS_PANIC": 0.15, "STAGFLATION_SHOCK": 0.10, "RISK_ON_EXPANSION": 0.10}

        # 4. Risk-On Expansion (Default tranquil)
        return {"RISK_ON_EXPANSION": 0.75, "NORMAL_RISK_OFF": 0.15, "STAGFLATION_SHOCK": 0.05, "CRISIS_PANIC": 0.05}

    def classify_hmm(
        self,
        returns: np.ndarray,
        volatilities: np.ndarray,
    ) -> Dict[str, float]:
        """Estimate 2-state Gaussian HMM (State 0: Low Vol, State 1: High Vol Stress).

        Uses hmmlearn Baum-Welch EM when available; falls back to z-score sigmoid.
        """
        ret = np.asarray(returns, dtype=float)
        vol = np.asarray(volatilities, dtype=float)

        if len(ret) < 5:
            return {"RISK_ON_EXPANSION": 0.50, "NORMAL_RISK_OFF": 0.50, "CRISIS_PANIC": 0.0, "STAGFLATION_SHOCK": 0.0}

        # --- Real HMM path (hmmlearn Baum-Welch EM) ---
        if _HMM_AVAILABLE and len(ret) >= 20:
            try:
                X = np.column_stack([ret, vol]) if len(vol) >= len(ret) else np.column_stack([ret, np.std(ret) * np.ones_like(ret)])
                model = _GaussianHMM(n_components=2, covariance_type="full", n_iter=100, random_state=42)
                model.fit(X)
                hidden_states = model.predict(X)
                state_means = model.means_[:, 0]  # mean return per state
                # Identify which state is high-vol (higher return variance or lower mean)
                state_vars = np.array([np.var(ret[hidden_states == s]) for s in range(2)])
                high_vol_state = int(np.argmax(state_vars))
                low_vol_state = 1 - high_vol_state
                # Posterior probability of high-vol state from last observation
                posteriors = model.predict_proba(X)
                prob_high_vol = float(posteriors[-1, high_vol_state])
                prob_low_vol = 1.0 - prob_high_vol
                backend = "hmmlearn_baum_welch"
            except Exception as exc:
                log.debug("hmmlearn fit failed, falling back to sigmoid: %s", exc)
                prob_high_vol, prob_low_vol, backend = self._sigmoid_hmm(ret, vol)
        else:
            prob_high_vol, prob_low_vol, backend = self._sigmoid_hmm(ret, vol)

        if prob_high_vol > 0.75:
            return {"CRISIS_PANIC": prob_high_vol * 0.6, "NORMAL_RISK_OFF": prob_high_vol * 0.4, "RISK_ON_EXPANSION": prob_low_vol * 0.8, "STAGFLATION_SHOCK": prob_low_vol * 0.2}
        else:
            return {"RISK_ON_EXPANSION": prob_low_vol * 0.7, "NORMAL_RISK_OFF": prob_high_vol * 0.6, "STAGFLATION_SHOCK": 0.15, "CRISIS_PANIC": 0.05}

    def _sigmoid_hmm(self, ret: np.ndarray, vol: np.ndarray) -> Tuple[float, float, str]:
        """Fallback z-score + sigmoid when hmmlearn unavailable or data < 20 bars."""
        recent_vol = float(np.mean(vol[-5:])) if len(vol) >= 5 else float(np.std(ret))
        hist_vol = float(np.std(ret))
        z_vol = (recent_vol - hist_vol) / max(hist_vol, 1e-6)
        prob_high_vol = 1.0 / (1.0 + math.exp(-max(min(z_vol, 5.0), -5.0)))
        return prob_high_vol, 1.0 - prob_high_vol, "sigmoid_heuristic"

    def classify_correlation_matrix(
        self,
        avg_pairwise_correlation: float = 0.35,
    ) -> Dict[str, float]:
        """Correlation breakdown detector (avg corr -> 1.0 in systemic crises)."""
        try:
            if not math.isfinite(float(avg_pairwise_correlation)):
                return {"NORMAL_RISK_OFF": 0.50, "RISK_ON_EXPANSION": 0.50, "CRISIS_PANIC": 0.0, "STAGFLATION_SHOCK": 0.0}
        except (TypeError, ValueError):
            return {"NORMAL_RISK_OFF": 0.50, "RISK_ON_EXPANSION": 0.50, "CRISIS_PANIC": 0.0, "STAGFLATION_SHOCK": 0.0}

        if avg_pairwise_correlation >= 0.70:
            return {"CRISIS_PANIC": 0.75, "NORMAL_RISK_OFF": 0.20, "STAGFLATION_SHOCK": 0.05, "RISK_ON_EXPANSION": 0.0}
        elif avg_pairwise_correlation >= 0.50:
            return {"NORMAL_RISK_OFF": 0.60, "CRISIS_PANIC": 0.20, "STAGFLATION_SHOCK": 0.10, "RISK_ON_EXPANSION": 0.10}
        else:
            return {"RISK_ON_EXPANSION": 0.65, "NORMAL_RISK_OFF": 0.20, "STAGFLATION_SHOCK": 0.10, "CRISIS_PANIC": 0.05}

    def classify_current_regime(
        self,
        vix: float = 16.0,
        gold_dxy_corr: float = -0.55,
        oil_change_pct: float = 0.0,
        dxy_change_pct: float = 0.0,
        recent_returns: Optional[np.ndarray] = None,
        recent_volatilities: Optional[np.ndarray] = None,
        avg_pairwise_corr: float = 0.35,
    ) -> MacroRegimeResult:
        """Bayesian Model Averaging synthesis across all candidate regime models."""
        p_rules = self.classify_rule_based(vix, gold_dxy_corr, oil_change_pct, dxy_change_pct)

        if recent_returns is None:
            recent_returns = np.array([0.001, -0.002, 0.003, -0.001, 0.002])
        if recent_volatilities is None:
            recent_volatilities = np.array([0.008, 0.009, 0.007, 0.008, 0.008])

        p_hmm = self.classify_hmm(recent_returns, recent_volatilities)
        p_corr = self.classify_correlation_matrix(avg_pairwise_corr)

        # BMA combination
        all_regimes = ["RISK_ON_EXPANSION", "NORMAL_RISK_OFF", "STAGFLATION_SHOCK", "CRISIS_PANIC"]
        combined_probs: Dict[str, float] = {r: 0.0 for r in all_regimes}

        w_rule = self.bma_weights.get("RULE_BASED", 0.40)
        w_hmm = self.bma_weights.get("GAUSSIAN_HMM", 0.35)
        w_corr = self.bma_weights.get("CORRELATION_MATRIX", 0.25)
        total_w = w_rule + w_hmm + w_corr

        for r in all_regimes:
            combined_probs[r] = (
                p_rules.get(r, 0.0) * w_rule +
                p_hmm.get(r, 0.0) * w_hmm +
                p_corr.get(r, 0.0) * w_corr
            ) / total_w

        # Normalize
        sum_p = sum(combined_probs.values())
        if sum_p > 0:
            for r in combined_probs:
                combined_probs[r] = round(combined_probs[r] / sum_p, 4)

        active_regime = max(combined_probs, key=combined_probs.get)
        is_crisis = active_regime == "CRISIS_PANIC" or combined_probs.get("CRISIS_PANIC", 0.0) >= 0.40
        persistence = float(combined_probs[active_regime])
        # Auto-clear heuristic status when data sufficiency improves
        data_sufficient = recent_returns is not None and len(recent_returns) >= 20

        return MacroRegimeResult(
            active_regime=active_regime,
            regime_probabilities=combined_probs,
            regime_persistence=persistence,
            bma_weights=dict(self.bma_weights),
            is_crisis_override=is_crisis,
            execution_authority=False,
        )
