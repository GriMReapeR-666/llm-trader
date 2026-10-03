"""Information Coefficient (IC) Evaluator.

Calculates Information Coefficient for alpha expressions using Spearman rank
correlation between alpha values and forward returns. Implements the fitness
function for the genetic algorithm.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
    Grinold, R. C., & Kahn, R. N. (2000). "Active Portfolio Management."
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .expression_tree import AlphaExpression, evaluate_with_cache


# ---------------------------------------------------------------------------
# IC Quality Thresholds (Industry Standard)
# ---------------------------------------------------------------------------

IC_THRESHOLDS = {
    "significant": 0.02,   # |IC| > 0.02 is statistically significant
    "good": 0.05,          # |IC| > 0.05 is good
    "strong": 0.10,        # |IC| > 0.10 is strong
    "very_strong": 0.15,   # |IC| > 0.15 is very strong
}


# ---------------------------------------------------------------------------
# IC Result Data Class
# ---------------------------------------------------------------------------

@dataclass
class ICResult:
    """Result of Information Coefficient calculation."""
    ic_mean: float                    # Mean IC across time
    ic_std: float                     # Standard deviation of IC
    ic_ir: float                      # IC Information Ratio (mean/std)
    ic_positive_pct: float            # Percentage of positive IC periods
    ic_series: pd.Series              # Full IC time series
    n_observations: int               # Number of observations
    ic_quality: str                   # Quality classification
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ic_mean": round(self.ic_mean, 6),
            "ic_std": round(self.ic_std, 6),
            "ic_ir": round(self.ic_ir, 4),
            "ic_positive_pct": round(self.ic_positive_pct, 4),
            "n_observations": self.n_observations,
            "ic_quality": self.ic_quality,
        }


# ---------------------------------------------------------------------------
# ICEvaluator Class
# ---------------------------------------------------------------------------

class ICEvaluator:
    """Calculate Information Coefficient for alpha expressions."""
    
    def __init__(self, 
                 min_observations: int = 20,
                 forward_periods: int = 1,
                 ic_threshold: float = 0.02):
        """Initialize IC evaluator.
        
        Args:
            min_observations: Minimum observations required for valid IC
            forward_periods: Number of periods for forward returns
            ic_threshold: Minimum |IC| threshold for significance
        """
        self.min_observations = min_observations
        self.forward_periods = forward_periods
        self.ic_threshold = ic_threshold
    
    def calculate_ic(self, alpha_values: pd.Series, 
                     forward_returns: pd.Series) -> float:
        """Calculate Spearman rank correlation between alpha and forward returns.
        
        Args:
            alpha_values: Alpha signal values
            forward_returns: Forward return values
            
        Returns:
            Spearman IC value
        """
        # Align series
        alpha_clean = alpha_values.dropna()
        returns_clean = forward_returns.dropna()
        common_idx = alpha_clean.index.intersection(returns_clean.index)
        
        if len(common_idx) < self.min_observations:
            return 0.0
        
        alpha_aligned = alpha_clean.loc[common_idx].values
        returns_aligned = returns_clean.loc[common_idx].values
        
        # A constant window has no rank information — Spearman is undefined
        # (scipy would emit a ConstantInputWarning per call and return nan,
        # which we filter below anyway). Skip the call entirely: same result,
        # no log flooding, dramatically faster in GA mining where flat
        # candidate expressions are common.
        if np.ptp(alpha_aligned) == 0.0 or np.ptp(returns_aligned) == 0.0:
            return 0.0
        
        # Calculate Spearman rank correlation
        ic, _ = sp_stats.spearmanr(alpha_aligned, returns_aligned)
        
        return float(ic) if math.isfinite(ic) else 0.0
    
    def calculate_rolling_ic(self, alpha_values: pd.Series,
                            forward_returns: pd.Series,
                            window: int = 20) -> pd.Series:
        """Calculate rolling IC over specified window.
        
        Args:
            alpha_values: Alpha signal values
            forward_returns: Forward return values
            window: Rolling window size
            
        Returns:
            Series of rolling IC values
        """
        # Align series
        alpha_clean = alpha_values.dropna()
        returns_clean = forward_returns.dropna()
        common_idx = alpha_clean.index.intersection(returns_clean.index)

        n = len(common_idx)
        if n < window:
            return pd.Series(dtype=float)

        alpha_arr = alpha_clean.loc[common_idx].to_numpy(dtype=float)
        returns_arr = returns_clean.loc[common_idx].to_numpy(dtype=float)

        # Windows end at positions window..n-1 (the final full window is unused),
        # matching the previous per-window loop exactly.
        n_windows = n - window
        a_wins = np.lib.stride_tricks.sliding_window_view(alpha_arr, window)[:n_windows]
        r_wins = np.lib.stride_tricks.sliding_window_view(returns_arr, window)[:n_windows]

        # rankdata(axis=1) reproduces spearmanr's per-window average-rank tie
        # handling while vectorizing over all windows at once.
        a_ranks = sp_stats.rankdata(a_wins, axis=1)
        r_ranks = sp_stats.rankdata(r_wins, axis=1)

        a_c = a_ranks - a_ranks.mean(axis=1, keepdims=True)
        r_c = r_ranks - r_ranks.mean(axis=1, keepdims=True)
        num = (a_c * r_c).sum(axis=1)
        den = np.sqrt((a_c * a_c).sum(axis=1) * (r_c * r_c).sum(axis=1))

        # Constant windows carry no rank info — skip (see calculate_ic).
        constant = (np.ptp(a_wins, axis=1) == 0.0) | (np.ptp(r_wins, axis=1) == 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ic = np.where(constant | (den <= 0.0), np.nan, num / den)

        keep = np.isfinite(ic)
        indices = common_idx[window:]
        return pd.Series(ic[keep], index=indices[keep])
    
    def calculate_ic_ir(self, ic_series: pd.Series) -> float:
        """Calculate IC Information Ratio = mean(IC) / std(IC).
        
        Args:
            ic_series: Time series of IC values
            
        Returns:
            IC Information Ratio
        """
        if len(ic_series) < 2:
            return 0.0
        
        ic_mean = ic_series.mean()
        ic_std = ic_series.std()
        
        if ic_std < 1e-10:
            return 0.0
        
        return ic_mean / ic_std
    
    def calculate_ic_positive_pct(self, ic_series: pd.Series) -> float:
        """Calculate percentage of positive IC periods.
        
        Args:
            ic_series: Time series of IC values
            
        Returns:
            Percentage of positive IC (0-1)
        """
        if len(ic_series) == 0:
            return 0.0
        
        return (ic_series > 0).mean()
    
    def classify_ic_quality(self, ic_mean: float, ic_ir: Any = None) -> str:
        """Classify IC quality from magnitude *and* measured stability.

        Grading on |IC| alone let a single-window, zero-dispersion reading be
        called "strong". ``evaluate_alpha`` already computes a real ICIR from
        the rolling series, so pass it here: an unmeasured or non-positive IR
        caps the grade rather than being ignored.

        Args:
            ic_mean: Mean IC value
            ic_ir: Information ratio (mean IC / std IC). None when unmeasured.

        Returns:
            Quality classification string
        """
        abs_ic = abs(ic_mean)
        
        if abs_ic >= IC_THRESHOLDS["very_strong"]:
            grade = "very_strong"
        elif abs_ic >= IC_THRESHOLDS["strong"]:
            grade = "strong"
        elif abs_ic >= IC_THRESHOLDS["good"]:
            grade = "good"
        elif abs_ic >= IC_THRESHOLDS["significant"]:
            grade = "significant"
        else:
            grade = "weak"

        try:
            ir = float(ic_ir) if ic_ir is not None else None
        except (TypeError, ValueError, OverflowError):
            ir = None
        # Only an absent or exactly-zero IR means "never measured". A negative
        # IR is a *strong consistent* signal in the opposite direction, so
        # stability is verified whenever |IR| > 0.
        if ir is None or not math.isfinite(ir) or ir == 0.0:
            return f"{grade}_unverified_stability"
        return grade
    
    def evaluate_alpha(self, alpha_values: pd.Series,
                      forward_returns: pd.Series,
                      rolling_window: int = 20) -> ICResult:
        """Comprehensive IC evaluation for an alpha expression.
        
        Args:
            alpha_values: Alpha signal values
            forward_returns: Forward return values
            rolling_window: Window for rolling IC calculation
            
        Returns:
            ICResult with all metrics
        """
        # Calculate IC series
        ic_series = self.calculate_rolling_ic(alpha_values, forward_returns, rolling_window)
        
        if len(ic_series) == 0:
            return ICResult(
                ic_mean=0.0,
                ic_std=0.0,
                ic_ir=0.0,
                ic_positive_pct=0.0,
                ic_series=pd.Series(dtype=float),
                n_observations=0,
                ic_quality="weak"
            )
        
        # Calculate metrics
        ic_mean = ic_series.mean()
        ic_std = ic_series.std()
        ic_ir = self.calculate_ic_ir(ic_series)
        ic_positive_pct = self.calculate_ic_positive_pct(ic_series)
        ic_quality = self.classify_ic_quality(ic_mean, ic_ir)
        
        return ICResult(
            ic_mean=ic_mean,
            ic_std=ic_std,
            ic_ir=ic_ir,
            ic_positive_pct=ic_positive_pct,
            ic_series=ic_series,
            n_observations=len(ic_series),
            ic_quality=ic_quality
        )
    
    def evaluate_expression(self, expr: AlphaExpression,
                           ohlcv: pd.DataFrame,
                           forward_returns: pd.Series,
                           use_cache: bool = True) -> ICResult:
        """Evaluate IC for an alpha expression tree.
        
        Args:
            expr: Alpha expression to evaluate
            ohlcv: OHLCV data
            forward_returns: Forward returns
            use_cache: Whether to use expression cache
            
        Returns:
            ICResult with all metrics
        """
        try:
            if use_cache:
                alpha_values = evaluate_with_cache(expr, ohlcv)
            else:
                alpha_values = expr.evaluate(ohlcv)
            
            return self.evaluate_alpha(alpha_values, forward_returns)
        
        except Exception as e:
            # Return zero IC on evaluation failure
            return ICResult(
                ic_mean=0.0,
                ic_std=0.0,
                ic_ir=0.0,
                ic_positive_pct=0.0,
                ic_series=pd.Series(dtype=float),
                n_observations=0,
                ic_quality="weak"
            )
    
    def calculate_information_coefficient(self, alpha_values: pd.Series,
                                         forward_returns: pd.Series) -> float:
        """Alias for calculate_ic (backward compatibility)."""
        return self.calculate_ic(alpha_values, forward_returns)
    
    def calculate_mathematical_expectancy(self, win_rate: float,
                                         avg_win_r: float,
                                         avg_loss_r: float = 1.0) -> float:
        """Calculate mathematical expectancy.
        
        EV = (Win% × Avg Win) - (Loss% × Avg Loss)
        
        Args:
            win_rate: Win rate (0-1)
            avg_win_r: Average win in R-multiples
            avg_loss_r: Average loss in R-multiples (positive)
            
        Returns:
            Expected value per trade
        """
        return (win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r)
    
    def calculate_system_quality_number(self, ic_series: pd.Series,
                                       n_trades: int) -> float:
        """Calculate System Quality Number (SQN).
        
        SQN = sqrt(n) × mean(R) / std(R)
        
        Args:
            ic_series: IC values (used as proxy for R-multiples)
            n_trades: Number of trades
            
        Returns:
            SQN value
        """
        if len(ic_series) < 2 or n_trades < 1:
            return 0.0
        
        mean_r = ic_series.mean()
        std_r = ic_series.std()
        
        if std_r < 1e-10:
            return 0.0
        
        return math.sqrt(n_trades) * mean_r / std_r


# ---------------------------------------------------------------------------
# Batch IC Evaluation
# ---------------------------------------------------------------------------

def batch_evaluate_ic(expressions: List[AlphaExpression],
                     ohlcv: pd.DataFrame,
                     forward_returns: pd.Series,
                     evaluator: Optional[ICEvaluator] = None) -> Dict[str, ICResult]:
    """Evaluate IC for multiple expressions.
    
    Args:
        expressions: List of alpha expressions
        ohlcv: OHLCV data
        forward_returns: Forward returns
        evaluator: Optional ICEvaluator instance
        
    Returns:
        Dictionary mapping expression string to ICResult
    """
    if evaluator is None:
        evaluator = ICEvaluator()
    
    results = {}
    for i, expr in enumerate(expressions):
        try:
            result = evaluator.evaluate_expression(expr, ohlcv, forward_returns)
            results[expr.to_string()] = result
        except Exception as e:
            # Skip failed evaluations
            continue
    
    return results


def select_significant_alphas(ic_results: Dict[str, ICResult],
                             min_ic: float = 0.02) -> List[Tuple[str, ICResult]]:
    """Select alphas with |IC| > min_ic.
    
    Args:
        ic_results: Dictionary of IC results
        min_ic: Minimum IC threshold
        
    Returns:
        List of (expression_string, ICResult) tuples, sorted by |IC|
    """
    significant = []
    for expr_str, result in ic_results.items():
        if abs(result.ic_mean) >= min_ic:
            significant.append((expr_str, result))
    
    # Sort by absolute IC (descending)
    significant.sort(key=lambda x: abs(x[1].ic_mean), reverse=True)
    
    return significant


# ---------------------------------------------------------------------------
# Forward Returns Calculation
# ---------------------------------------------------------------------------

def calculate_forward_returns(prices: pd.Series, 
                             periods: int = 1,
                             method: str = "simple") -> pd.Series:
    """Calculate forward returns for IC calculation.
    
    Args:
        prices: Price series (typically close)
        periods: Number of periods forward
        method: "simple" or "log" returns
        
    Returns:
        Forward returns series
    """
    if method == "log":
        returns = np.log(prices / prices.shift(1))
    else:
        returns = prices.pct_change()
    
    # Shift backwards to get forward returns
    forward_returns = returns.shift(-periods)
    
    return forward_returns


def calculate_forward_returns_5day(prices: pd.Series) -> pd.Series:
    """Calculate 5-day forward returns (as used in the paper)."""
    return calculate_forward_returns(prices, periods=5, method="simple")
