"""Optimal Dynamic Strategy Statistics.

Implements the theoretical results from Firoozye & Koshiyama (2023)
"Optimal Dynamic Strategies on Gaussian Returns" for evaluating and
optimizing dynamic trading strategies.

Key Results:
    - Maximum Sharpe ratio for linear Gaussian strategies: SR_max = 1/sqrt(2)
    - Closed-form expressions for Sharpe, skewness, and kurtosis
    - Refined standard errors tighter than Lo (2002)
    - Total Least Squares (TLS) outperforms OLS for Sharpe maximization

References:
    Firoozye, N. & Koshiyama, A. (2023). "Optimal Dynamic Strategies on
    Gaussian Returns." AIP/123-QED.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Theoretical Bounds (Theorem 2.3)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TheoreticalBounds:
    """Theoretical bounds for linear Gaussian strategies."""
    SR_MAX: float = 1.0 / np.sqrt(2)  # ≈ 0.707 per period
    SKEWNESS_MAX: float = 2.0 * np.sqrt(2)  # ≈ 2.828
    KURTOSIS_MAX: float = 15.0
    KURTOSIS_MIN: float = 9.0  # Excess kurtosis = 6


# ---------------------------------------------------------------------------
# Strategy Statistics
# ---------------------------------------------------------------------------

@dataclass
class StrategyStatistics:
    """Statistical properties of a dynamic trading strategy."""
    sharpe_ratio: float
    skewness: float
    kurtosis: float
    excess_kurtosis: float
    
    # Standard errors
    sharpe_std_err: float
    skewness_std_err: float
    kurtosis_std_err: float
    
    # Confidence intervals (95%)
    sharpe_ci_lower: float
    sharpe_ci_upper: float
    
    # IC (signal-return correlation)
    ic: float
    ic_squared: float
    
    # Normality test
    jarque_bera_stat: float
    is_normal: bool
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
            "excess_kurtosis": round(self.excess_kurtosis, 4),
            "sharpe_std_err": round(self.sharpe_std_err, 4),
            "skewness_std_err": round(self.skewness_std_err, 4),
            "kurtosis_std_err": round(self.kurtosis_std_err, 4),
            "sharpe_ci_95": [round(self.sharpe_ci_lower, 4), round(self.sharpe_ci_upper, 4)],
            "ic": round(self.ic, 4),
            "ic_squared": round(self.ic_squared, 4),
            "jarque_bera_stat": round(self.jarque_bera_stat, 2),
            "is_normal": self.is_normal,
        }


# ---------------------------------------------------------------------------
# Optimal Strategy Evaluator
# ---------------------------------------------------------------------------

class OptimalStrategyEvaluator:
    """Evaluate dynamic trading strategy statistics.
    
    Implements Theorem 2.3 and Corollaries 3.1-3.2 from Firoozye & Koshiyama.
    """
    
    def __init__(self):
        self.bounds = TheoreticalBounds()
    
    def compute_statistics(self, strategy_returns: np.ndarray,
                          signal_returns: np.ndarray,
                          sample_size: Optional[int] = None) -> StrategyStatistics:
        """Compute complete strategy statistics.
        
        Args:
            strategy_returns: Strategy returns (S_t = X_t * R_t)
            signal_returns: Signal values or signal*return product
            sample_size: Sample size (defaults to len(strategy_returns))
        
        Returns:
            StrategyStatistics with all computed metrics
        """
        T = sample_size or len(strategy_returns)
        
        # Compute correlation (IC)
        if len(signal_returns) == len(strategy_returns):
            ic, _ = sp_stats.pearsonr(signal_returns, strategy_returns)
        else:
            # If signal not provided, estimate from returns
            ic = self._estimate_ic_from_returns(strategy_returns)
        
        ic_squared = ic ** 2
        
        # Compute moments from Theorem 2.3
        sharpe = self._compute_sharpe(ic)
        skewness = self._compute_skewness(ic)
        kurtosis = self._compute_kurtosis(ic)
        excess_kurtosis = kurtosis - 3.0
        
        # Standard errors (Corollary 3.1-3.2)
        sharpe_stderr = self._compute_sharpe_std_err(ic, T)
        skewness_stderr = self._compute_skewness_std_err(ic, T)
        kurtosis_stderr = self._compute_kurtosis_std_err(ic, T)
        
        # 95% confidence interval for Sharpe
        z_95 = 1.96
        sharpe_ci_lower = sharpe - z_95 * sharpe_stderr
        sharpe_ci_upper = sharpe + z_95 * sharpe_stderr
        
        # Jarque-Bera test for normality
        jb_stat = self._compute_jarque_bera(strategy_returns)
        is_normal = jb_stat < 9.21  # chi2(2) at 99% confidence
        
        return StrategyStatistics(
            sharpe_ratio=sharpe,
            skewness=skewness,
            kurtosis=kurtosis,
            excess_kurtosis=excess_kurtosis,
            sharpe_std_err=sharpe_stderr,
            skewness_std_err=skewness_stderr,
            kurtosis_std_err=kurtosis_stderr,
            sharpe_ci_lower=sharpe_ci_lower,
            sharpe_ci_upper=sharpe_ci_upper,
            ic=ic,
            ic_squared=ic_squared,
            jarque_bera_stat=jb_stat,
            is_normal=is_normal,
        )
    
    def _compute_sharpe(self, rho: float) -> float:
        """Theorem 2.3: SR = rho / sqrt(1 + rho^2)"""
        return rho / np.sqrt(1 + rho**2)
    
    def _compute_skewness(self, rho: float) -> float:
        """Theorem 2.3: skewness = 2*rho*(3 + rho^2) / (1 + rho^2)^(3/2)"""
        return 2 * rho * (3 + rho**2) / (1 + rho**2)**1.5
    
    def _compute_kurtosis(self, rho: float) -> float:
        """Theorem 2.3: kurtosis = 3*(3 + 14*rho^2 + 3*rho^4) / (1 + rho^2)^2"""
        return 3 * (3 + 14 * rho**2 + 3 * rho**4) / (1 + rho**2)**2
    
    def _compute_sharpe_std_err(self, rho: float, T: int) -> float:
        """Corollary 3.1: stderr = sqrt(1/(rho^2+1)^(3/2) * (1-rho^2)/(T-2))"""
        if abs(rho) >= 1.0 or T <= 2:
            return 0.0
        
        rho_hat_sq = rho**2
        numerator = 1 - rho_hat_sq
        denominator = (rho_hat_sq + 1)**1.5 * (T - 2)
        
        return np.sqrt(max(0, numerator / denominator))
    
    def _compute_skewness_std_err(self, rho: float, T: int) -> float:
        """Standard error for skewness (approximation)."""
        # From Joanes & Gill (1998) adapted for dynamic strategies
        skewness = self._compute_skewness(rho)
        return np.sqrt(6 * (T - 2) / ((T + 1) * (T + 3)))
    
    def _compute_kurtosis_std_err(self, rho: float, T: int) -> float:
        """Standard error for kurtosis (approximation)."""
        # From Joanes & Gill (1998) adapted for dynamic strategies
        return np.sqrt(24 * T * (T - 2) * (T - 3) / ((T + 1)**2 * (T + 3) * (T + 5)))
    
    def _compute_jarque_bera(self, returns: np.ndarray) -> float:
        """Jarque-Bera test statistic."""
        n = len(returns)
        if n < 8:
            return 0.0
        
        skewness = sp_stats.skew(returns)
        kurtosis = sp_stats.kurtosis(returns, fisher=False)  # Raw kurtosis
        
        jb = (n / 6) * (skewness**2 + (kurtosis - 3)**2 / 4)
        return jb
    
    def _estimate_ic_from_returns(self, strategy_returns: np.ndarray) -> float:
        """Estimate IC from strategy returns alone.
        
        Uses the relationship: Var(S) = sigma_X^2 * sigma_R^2 * (1 + rho^2)
        """
        # Simple estimation assuming unit variances
        # This is a rough approximation
        returns_var = np.var(strategy_returns)
        
        # For a rough IC estimate, use the fact that
        # SR = mu/sigma and SR ≈ rho for small rho
        if returns_var > 0:
            sr = np.mean(strategy_returns) / np.sqrt(returns_var)
            # Invert SR = rho/sqrt(1+rho^2) to get rho
            if abs(sr) < 1.0:
                rho = sr / np.sqrt(1 - sr**2)
            else:
                rho = np.sign(sr) * 0.99
            return rho
        return 0.0
    
    def compute_ic_squared(self, ic: float) -> float:
        """Compute IC^2 for information ratio calculations."""
        return ic ** 2
    
    def compute_information_ratio(self, active_returns: np.ndarray) -> float:
        """Compute information ratio from active returns."""
        if len(active_returns) == 0:
            return 0.0
        
        tracking_error = np.std(active_returns)
        if tracking_error < 1e-10:
            return 0.0
        
        return np.mean(active_returns) / tracking_error
    
    def max_sharpe_from_ic(self, ic: float) -> float:
        """Compute maximum achievable Sharpe from IC."""
        return self._compute_sharpe(ic)
    
    def ic_required_for_sharpe(self, target_sharpe: float) -> float:
        """Compute IC required to achieve target Sharpe ratio.
        
        Inverts: SR = rho / sqrt(1 + rho^2)
        """
        if abs(target_sharpe) >= 1.0:
            return np.sign(target_sharpe) * 0.999
        
        return target_sharpe / np.sqrt(1 - target_sharpe**2)
    
    def annualize_sharpe(self, period_sharpe: float,
                        periods_per_year: int = 252) -> float:
        """Annualize Sharpe ratio from single-period estimate."""
        return period_sharpe * np.sqrt(periods_per_year)
    
    def deannualize_sharpe(self, annual_sharpe: float,
                          periods_per_year: int = 252) -> float:
        """Convert annualized Sharpe to single-period estimate."""
        return annual_sharpe / np.sqrt(periods_per_year)


# ---------------------------------------------------------------------------
# Total Least Squares Optimizer
# ---------------------------------------------------------------------------

class TLSOptimizer:
    """Total Least Squares optimizer for Sharpe maximization.
    
    From the paper: TLS maximizes correlation (and thus Sharpe) rather
    than minimizing MSE, which is more appropriate for trading strategies.
    """
    
    def __init__(self):
        pass
    
    def fit(self, X: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
        """Fit TLS model.
        
        Args:
            X: Feature matrix (n_samples, n_features)
            y: Target vector (n_samples,)
        
        Returns:
            Dictionary with coefficients and diagnostics
        """
        n, k = X.shape
        
        # Augment matrix with target
        Z = np.column_stack([X, y])
        
        # SVD for TLS
        U, S, Vt = np.linalg.svd(Z, full_matrices=False)
        
        # TLS coefficients: last row of Vt scaled by smallest singular value
        # Beta_TLS = -Vt[-1, :-1] / Vt[-1, -1]
        beta_tls = -Vt[-1, :-1] / Vt[-1, -1] if abs(Vt[-1, -1]) > 1e-10 else np.zeros(k)
        
        # Compute fitted values and residuals
        y_hat = X @ beta_tls
        residuals = y - y_hat
        
        # Compute R^2
        ss_res = np.sum(residuals**2)
        ss_tot = np.sum((y - np.mean(y))**2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
        
        # Compute correlation
        correlation = np.corrcoef(y_hat, y)[0, 1] if len(y) > 1 else 0.0
        
        return {
            "coefficients": beta_tls,
            "r_squared": r_squared,
            "correlation": correlation,
            "smallest_singular_value": S[-1] if len(S) > 0 else 0.0,
            "residuals": residuals,
        }
    
    def predict(self, X: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
        """Make predictions using TLS coefficients."""
        return X @ coefficients


# ---------------------------------------------------------------------------
# Integration with Alpha Evaluator
# ---------------------------------------------------------------------------

def compute_strategy_quality(strategy_returns: np.ndarray,
                            signal_returns: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """Compute comprehensive strategy quality metrics.
    
    Uses Theorem 2.3 and Corollaries from Firoozye & Koshiyama.
    """
    evaluator = OptimalStrategyEvaluator()
    stats = evaluator.compute_statistics(strategy_returns, signal_returns)
    
    return stats.to_dict()


def compute_optimal_ic_target(target_sharpe: float) -> float:
    """Compute the IC needed to achieve a target Sharpe ratio."""
    evaluator = OptimalStrategyEvaluator()
    return evaluator.ic_required_for_sharpe(target_sharpe)
