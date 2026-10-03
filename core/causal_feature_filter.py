"""Causal Feature Filter — Spurious Correlation Detection & Removal.

Inspired by CRL-ABSO (2026) and CausalPPO (CRL Survey 2025), this module
identifies which features have genuine causal relationships with trade
outcomes vs which are spurious correlations.

Key problem solved:
    Standard RL agents learn P(R|S,A) which is biased by confounders U:
        E[R|S=s, A=a] = sum_u E[R|s,a,u] P(u|s,a)  ← BIASED
        E[R|S=s, do(A=a)] = sum_u E[R|s,a,u] P(u|s)  ← CAUSAL

    When P(u|s,a) ≠ P(u|s), standard RL yields biased value estimates.

Solution (CRL-ABSO approach):
    1. Build causal graph from time-series data using:
       - Granger causality (temporal precedence)
       - Partial correlation (conditional independence)
       - Mutual information (non-linear relationships)
    2. Identify confounders and spurious features
    3. Filter features to only causal predictors
    4. Score feature reliability for the RL agent

Architecture:
    ┌─────────────────────────────────────────────────────┐
    │           CAUSAL FEATURE FILTER                      │
    │                                                      │
    │  Input: Feature history + outcome history            │
    │      ↓                                               │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 1: Correlation Analysis          │           │
    │  │   Pearson correlation matrix          │           │
    │  │   Partial correlation (remove conf.)  │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 2: Granger Causality Test        │           │
    │  │   Temporal precedence check           │           │
    │  │   F-test for predictive power         │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 3: Mutual Information            │           │
    │  │   Non-linear dependency detection     │           │
    │  │   Binning-based MI estimation         │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 4: Causal Score Fusion           │           │
    │  │   Combine correlation + Granger + MI   │           │
    │  │   Rank features by causal strength     │           │
    │  │   Filter spurious (score < threshold)  │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  Output: CausalFeatureReport with ranked features    │
    └─────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_FILE = "causal_filter_state.json"
DEFAULT_MIN_SAMPLES = 30       # Minimum observations for causal analysis
DEFAULT_CAUSAL_THRESHOLD = 0.3  # Minimum causal score to keep a feature
DEFAULT_GRANGER_LAG = 3         # Lag for Granger causality test
DEFAULT_MI_BINS = 10            # Bins for mutual information estimation


# ---------------------------------------------------------------------------
# Causal Score
# ---------------------------------------------------------------------------

@dataclass
class CausalScore:
    """Causal strength score for a single feature."""
    feature_name: str
    correlation: float         # Pearson correlation with outcome
    partial_correlation: float # Partial correlation (confounders removed)
    granger_f: float           # Granger causality F-statistic
    granger_p: float           # Granger causality p-value
    mutual_info: float         # Normalized mutual information
    causal_score: float        # Fused causal score (0-1)
    is_causal: bool            # Whether feature passes causal threshold
    temporal_lag: int          # Optimal lag for Granger test
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature": self.feature_name,
            "correlation": round(self.correlation, 4),
            "partial_correlation": round(self.partial_correlation, 4),
            "granger_f": round(self.granger_f, 4),
            "granger_p": round(self.granger_p, 4),
            "mutual_info": round(self.mutual_info, 4),
            "causal_score": round(self.causal_score, 4),
            "is_causal": self.is_causal,
            "temporal_lag": self.temporal_lag,
        }


@dataclass
class CausalFeatureReport:
    """Complete causal analysis report for all features."""
    feature_scores: List[CausalScore]
    causal_features: List[str]
    spurious_features: List[str]
    confounders: List[str]
    overall_causal_density: float  # % of features that are causal
    timestamp: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "causal_features": self.causal_features,
            "spurious_features": self.spurious_features,
            "confounders": self.confounders,
            "causal_density": round(self.overall_causal_density, 4),
            "feature_count": len(self.feature_scores),
            "causal_count": len(self.causal_features),
            "spurious_count": len(self.spurious_features),
            "scores": [s.to_dict() for s in self.feature_scores],
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Granger Causality (lightweight implementation)
# ---------------------------------------------------------------------------

def _granger_causality_test(
    x: np.ndarray, y: np.ndarray, lag: int = DEFAULT_GRANGER_LAG,
) -> Tuple[float, float]:
    """Granger causality F-test: does x help predict y?
    
    H0: x does not Granger-cause y (restricted model = unrestricted)
    H1: x Granger-causes y
    
    Uses OLS regression with lagged values.
    
    Returns:
        (f_statistic, p_value) — lower p = stronger causal evidence
    """
    n = min(len(x), len(y))
    if n < lag + 5:
        return 0.0, 1.0
    
    x = x[-n:]
    y = y[-n:]
    
    # Build lagged matrices
    # Restricted model: y_t = sum(b_i * y_{t-i}) + error
    # Unrestricted model: y_t = sum(b_i * y_{t-i}) + sum(c_j * x_{t-j}) + error
    
    Y = y[lag:]
    X_restricted = np.column_stack([y[lag - i - 1:n - i - 1] for i in range(lag)])
    X_unrestricted = np.column_stack([
        *([y[lag - i - 1:n - i - 1] for i in range(lag)]),
        *([x[lag - i - 1:n - i - 1] for i in range(lag)]),
    ])
    
    # Add intercept
    X_restricted = np.column_stack([np.ones(len(Y)), X_restricted])
    X_unrestricted = np.column_stack([np.ones(len(Y)), X_unrestricted])
    
    n_obs = len(Y)
    k_r = X_restricted.shape[1]
    k_u = X_unrestricted.shape[1]
    
    if n_obs <= k_u:
        return 0.0, 1.0
    
    # OLS for restricted model
    try:
        beta_r, residuals_r, _, _ = np.linalg.lstsq(X_restricted, Y, rcond=None)
        rss_r = float(np.sum((Y - X_restricted @ beta_r) ** 2))
        
        # OLS for unrestricted model
        beta_u, residuals_u, _, _ = np.linalg.lstsq(X_unrestricted, Y, rcond=None)
        rss_u = float(np.sum((Y - X_unrestricted @ beta_u) ** 2))
        
        if rss_u <= 0:
            return 0.0, 1.0
        
        # F-statistic
        df_num = k_u - k_r
        df_den = n_obs - k_u
        
        if df_den <= 0 or rss_r <= 0:
            return 0.0, 1.0
        
        f_stat = ((rss_r - rss_u) / df_num) / (rss_u / df_den)
        
        # Approximate p-value using F-distribution approximation
        # For large df, F ≈ normal
        f_stat = max(0.0, float(f_stat))
        
        # Simple p-value approximation using the regularized incomplete beta function
        # For practical purposes, use a chi-squared approximation
        x_val = df_num * f_stat
        # Approximate p-value: P(F > f) ≈ exp(-f/2) for large f
        p_value = max(0.001, min(1.0, math.exp(-f_stat / 2.0)))
        
        return f_stat, p_value
    except Exception:
        return 0.0, 1.0


# ---------------------------------------------------------------------------
# Mutual Information (binning-based)
# ---------------------------------------------------------------------------

def _mutual_information(
    x: np.ndarray, y: np.ndarray, n_bins: int = DEFAULT_MI_BINS,
) -> float:
    """Estimate mutual information between x and y using binning.
    
    MI(X;Y) = sum_x sum_y p(x,y) * log(p(x,y) / (p(x) * p(y)))
    
    Normalized to [0, 1] by dividing by max possible MI.
    """
    n = min(len(x), len(y))
    if n < 10:
        return 0.0
    
    x = x[-n:]
    y = y[-n:]
    
    # Bin the data
    try:
        x_bins = np.digitize(x, np.histogram_bin_edges(x, bins=n_bins))
        y_bins = np.digitize(y, np.histogram_bin_edges(y, bins=n_bins))
    except Exception:
        return 0.0
    
    # Compute joint and marginal distributions
    n_bins_actual = n_bins + 1
    joint = np.zeros((n_bins_actual, n_bins_actual))
    marginal_x = np.zeros(n_bins_actual)
    marginal_y = np.zeros(n_bins_actual)
    
    for i in range(n):
        bx = min(x_bins[i], n_bins_actual - 1)
        by = min(y_bins[i], n_bins_actual - 1)
        joint[bx, by] += 1
        marginal_x[bx] += 1
        marginal_y[by] += 1
    
    joint /= n
    marginal_x /= n
    marginal_y /= n
    
    # Compute MI
    mi = 0.0
    for i in range(n_bins_actual):
        for j in range(n_bins_actual):
            if joint[i, j] > 0 and marginal_x[i] > 0 and marginal_y[j] > 0:
                mi += joint[i, j] * math.log(joint[i, j] / (marginal_x[i] * marginal_y[j]))
    
    # Normalize by max possible MI (log of min number of bins with data)
    active_bins = max(1, np.sum(marginal_x > 0) * np.sum(marginal_y > 0))
    max_mi = math.log(max(2, min(n_bins_actual, active_bins)))
    
    return min(1.0, max(0.0, mi / max_mi)) if max_mi > 0 else 0.0


# ---------------------------------------------------------------------------
# Partial Correlation
# ---------------------------------------------------------------------------

def _partial_correlation(
    x: np.ndarray, y: np.ndarray, z: np.ndarray,
) -> float:
    """Partial correlation between x and y controlling for z.
    
    Uses the formula: rho_xy.z = (rho_xy - rho_xz * rho_yz) / 
                                  sqrt((1 - rho_xz^2) * (1 - rho_yz^2))
    """
    n = min(len(x), len(y), len(z))
    if n < 10:
        return 0.0
    
    x, y, z = x[-n:], y[-n:], z[-n:]
    
    try:
        corr_xy = float(np.corrcoef(x, y)[0, 1])
        corr_xz = float(np.corrcoef(x, z)[0, 1])
        corr_yz = float(np.corrcoef(y, z)[0, 1])
    except Exception:
        return 0.0
    
    if not (math.isfinite(corr_xy) and math.isfinite(corr_xz) and math.isfinite(corr_yz)):
        return 0.0
    
    denom = math.sqrt(max(0, (1 - corr_xz ** 2) * (1 - corr_yz ** 2)))
    if denom < 1e-10:
        return 0.0
    
    return max(-1.0, min(1.0, (corr_xy - corr_xz * corr_yz) / denom))


# ---------------------------------------------------------------------------
# Main Causal Feature Filter
# ---------------------------------------------------------------------------

class CausalFeatureFilter(StoreBackedState):
    """Causal feature filter inspired by CRL-ABSO and CausalPPO.
    
    Identifies which features have genuine causal relationships with
    trade outcomes vs spurious correlations. Filters the feature set
    to only include causally relevant features.
    
    Usage:
        filter = CausalFeatureFilter()
        report = filter.analyze(feature_history, outcome_history)
        causal_features = report.causal_features
    """

    store_key = "causal_filter_state"
    
    def __init__(
        self,
        state_file: str = STATE_FILE,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        causal_threshold: float = DEFAULT_CAUSAL_THRESHOLD,
        granger_lag: int = DEFAULT_GRANGER_LAG,
        mi_bins: int = DEFAULT_MI_BINS,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.min_samples = min_samples
        self.causal_threshold = causal_threshold
        self.granger_lag = granger_lag
        self.mi_bins = mi_bins
        self.state = self._load_state()
        if store is not None:
            self.attach_store(store)
    
    def _load_state(self) -> Dict[str, Any]:
        try:
            data = self._load_state_dict()
            if isinstance(data, dict) and data:
                return data
        except Exception as exc:
            logger.warning("state load failed: %s", exc)
        return {"last_analysis": None, "history": []}
    
    def _save_state(self) -> None:
        try:
            # Keep only last 20 reports
            history = self.state.get("history", [])
            if len(history) > 20:
                self.state["history"] = history[-20:]
            self._save_state_dict(self.state)
        except Exception as exc:
            logger.debug("Failed to save causal filter state: %s", exc)
    
    def analyze(
        self,
        feature_history: Dict[str, List[float]],
        outcome_history: List[float],
    ) -> CausalFeatureReport:
        """Analyze causal relationships between features and outcomes.
        
        Args:
            feature_history: Dict mapping feature names to time-series values
            outcome_history: Time-series of trade outcomes (PnL, returns, etc.)
            
        Returns:
            CausalFeatureReport with ranked features and causal/spurious lists
        """
        n_samples = len(outcome_history)
        if n_samples < self.min_samples:
            return CausalFeatureReport(
                feature_scores=[],
                causal_features=[],
                spurious_features=list(feature_history.keys()),
                confounders=[],
                overall_causal_density=0.0,
                timestamp=time.time(),
            )
        
        outcome = np.array(outcome_history, dtype=float)
        scores: List[CausalScore] = []
        
        for feat_name, feat_values in feature_history.items():
            if len(feat_values) < n_samples:
                continue
            
            feat = np.array(feat_values[-n_samples:], dtype=float)
            
            # Filter out NaN/inf
            valid = np.isfinite(feat) & np.isfinite(outcome)
            if np.sum(valid) < self.min_samples:
                continue
            
            feat_clean = feat[valid]
            outcome_clean = outcome[valid]
            
            # 1. Pearson correlation
            try:
                corr = float(np.corrcoef(feat_clean, outcome_clean)[0, 1])
                if not math.isfinite(corr):
                    corr = 0.0
            except Exception:
                corr = 0.0
            
            # 2. Partial correlation (controlling for other features)
            # Use the first other feature as a proxy for confounders
            other_features = [v for k, v in feature_history.items() if k != feat_name]
            if other_features:
                confounder = np.array(other_features[0][-n_samples:], dtype=float)
                valid_conf = valid & np.isfinite(confounder)
                if np.sum(valid_conf) >= self.min_samples:
                    p_corr = _partial_correlation(
                        feat[valid_conf], outcome_clean[valid[:len(valid)] & np.isfinite(confounder)[:len(valid)]][:np.sum(valid_conf)],
                        confounder[valid_conf],
                    )
                else:
                    p_corr = corr
            else:
                p_corr = corr
            
            # 3. Granger causality
            best_f, best_p, best_lag = 0.0, 1.0, 1
            for lag in range(1, self.granger_lag + 1):
                f_stat, p_val = _granger_causality_test(feat_clean, outcome_clean, lag)
                if f_stat > best_f:
                    best_f, best_p, best_lag = f_stat, p_val, lag
            
            # 4. Mutual information
            mi = _mutual_information(feat_clean, outcome_clean, self.mi_bins)
            
            # 5. Fused causal score
            # Weight: Granger (temporal) > Partial corr (confounder-adjusted) > Correlation > MI
            causal_score = (
                0.35 * min(1.0, best_f / 10.0) +  # Granger F normalized
                0.30 * abs(p_corr) +                 # Partial correlation
                0.20 * abs(corr) +                   # Raw correlation
                0.15 * mi                             # Mutual information
            )
            causal_score = max(0.0, min(1.0, causal_score))
            
            is_causal = causal_score >= self.causal_threshold
            
            scores.append(CausalScore(
                feature_name=feat_name,
                correlation=corr,
                partial_correlation=p_corr,
                granger_f=best_f,
                granger_p=best_p,
                mutual_info=mi,
                causal_score=causal_score,
                is_causal=is_causal,
                temporal_lag=best_lag,
            ))
        
        # Sort by causal score descending
        scores.sort(key=lambda s: s.causal_score, reverse=True)
        
        causal_features = [s.feature_name for s in scores if s.is_causal]
        spurious_features = [s.feature_name for s in scores if not s.is_causal]
        
        # Detect confounders: features with high correlation but low partial correlation
        # (correlation is high because of a third variable, not direct causation)
        confounders = []
        for s in scores:
            if abs(s.correlation) > 0.5 and abs(s.partial_correlation) < 0.2:
                confounders.append(s.feature_name)
        
        causal_density = len(causal_features) / max(1, len(scores))
        
        report = CausalFeatureReport(
            feature_scores=scores,
            causal_features=causal_features,
            spurious_features=spurious_features,
            confounders=confounders,
            overall_causal_density=causal_density,
            timestamp=time.time(),
        )
        
        # Update state
        self.state["last_analysis"] = report.to_dict()
        self.state["history"].append(report.to_dict())
        self._save_state()
        
        return report
    
    def filter_features(
        self,
        features: Dict[str, float],
        report: Optional[CausalFeatureReport] = None,
    ) -> Dict[str, float]:
        """Filter a feature dict to only causal features.
        
        Args:
            features: Current feature values
            report: Optional pre-computed report (if None, returns all features)
            
        Returns:
            Filtered features with spurious ones removed
        """
        if report is None:
            return features
        
        causal_set = set(report.causal_features)
        return {k: v for k, v in features.items() if k in causal_set}
    
    def get_status(self) -> Dict[str, Any]:
        """Get current causal filter status."""
        last = self.state.get("last_analysis")
        if last is None:
            return {"status": "NO_DATA", "causal_features": [], "spurious_features": []}
        return {
            "status": "ACTIVE",
            "causal_count": last.get("causal_count", 0),
            "spurious_count": last.get("spurious_count", 0),
            "causal_density": last.get("causal_density", 0),
            "confounders": last.get("confounders", []),
            "analyzed_at": last.get("timestamp"),
        }
