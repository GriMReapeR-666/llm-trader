"""Module 02: Binomial Fractional Differentiation & Inefficiency Discovery Engine.

Applies binomial fractional differentiation d in [0, 1] to financial time series to
preserve long-range memory while achieving covariance stationarity (Author-C):
    (1 - B)^d X_t = sum_{k=0}^infty (-1)^k binom(d, k) X_{t-k}
    where w_0 = 1,  w_k = -w_{k-1} * (d - k + 1) / k

Also implements:
- Augmented Dickey-Fuller stationarity testing
- Minimum optimal order d* optimization
- Ornstein-Uhlenbeck mean-reversion half-life estimation (tau_1/2 = -ln(2) / theta)
- Bouchaud square-root market capacity impact modeling (I = Y * sigma * sqrt(Q / V))
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


def compute_binomial_fractional_weights(
    order_d: float,
    window_size: int = 50,
    convergence_threshold: float = 1e-3,
) -> np.ndarray:
    """Generate memory-preserving fractional binomial expansion weights.
    
    Formula:
        w_0 = 1.0
        w_k = -w_{k-1} / k * (d - k + 1)
    """
    d = float(order_d)
    size = int(window_size)
    threshold = float(convergence_threshold)

    w = [1.0]
    for k in range(1, size):
        w_k = -w[-1] / k * (d - k + 1)
        if abs(w_k) < threshold:
            break
        w.append(w_k)
    return np.array(w[::-1], dtype=float)  # Ascending order for dot-product convolution


def apply_fractional_differentiation(
    series: Union[pd.Series, np.ndarray, List[float]],
    order_d: float = 0.40,
    max_window: int = 40,
    threshold: float = 1e-3,
) -> pd.Series:
    """Apply fractional differentiation of order d to a price series.
    
    Preserves multi-period memory (unlike 1st differences) while eliminating non-stationarity.
    """
    d = float(order_d)
    if isinstance(series, (list, tuple)):
        s = pd.Series(series, dtype=float)
    elif isinstance(series, np.ndarray):
        s = pd.Series(series.flatten(), dtype=float)
    else:
        s = series.copy().astype(float)

    if d == 0.0:
        return s
    if d == 1.0:
        return s.diff().dropna()

    win_len = min(len(s), max_window)
    weights = compute_binomial_fractional_weights(d, window_size=win_len, convergence_threshold=threshold)
    width = len(weights)
    if width > len(s) or width == 0:
        return pd.Series(dtype=float)

    res = {}
    for i in range(width - 1, len(s)):
        window = s.iloc[i - width + 1 : i + 1].values
        val = float(np.dot(weights, window))
        res[s.index[i]] = val

    return pd.Series(res)


def estimate_augmented_dickey_fuller_stationarity(
    series: Union[pd.Series, np.ndarray, List[float]],
) -> float:
    """Estimates Augmented Dickey-Fuller (ADF) regression t-statistic:
    Delta y_t = alpha + beta * y_{t-1} + epsilon_t
    t_beta = beta / SE(beta). Values < -2.86 indicate stationarity at 95% confidence.
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 20:
        return 0.0
    diff = np.diff(arr)
    lag = arr[:-1]
    mean_lag = np.mean(lag)
    mean_diff = np.mean(diff)
    var_lag = np.sum((lag - mean_lag) ** 2)
    if var_lag == 0:
        return 0.0
    beta = np.sum((lag - mean_lag) * (diff - mean_diff)) / var_lag
    residuals = (diff - mean_diff) - beta * (lag - mean_lag)
    se_beta = math.sqrt(np.sum(residuals ** 2) / (len(lag) - 2) / var_lag) if var_lag > 0 else 1.0
    t_stat = beta / se_beta if se_beta > 0 else 0.0
    return float(t_stat)


def optimize_stationarity_memory_order_d(
    series: Union[pd.Series, np.ndarray, List[float]],
    d_search_range: Tuple[float, float] = (0.1, 1.0),
    step_size: float = 0.05,
    adf_critical_threshold: float = -2.86,  # 95% critical value
) -> Dict[str, Any]:
    """Finds the minimum order d* that achieves stationarity while maximizing memory retention."""
    s = pd.Series(series).dropna()
    results = []
    optimal_d = 1.0
    found = False

    for d_val in np.arange(d_search_range[0], d_search_range[1] + step_size, step_size):
        d_val = round(float(d_val), 2)
        fd = apply_fractional_differentiation(s, order_d=d_val)
        if len(fd) < 20:
            continue
        t_stat = estimate_augmented_dickey_fuller_stationarity(fd)
        is_stationary = t_stat < adf_critical_threshold
        corr = float(s.iloc[-len(fd):].corr(fd)) if len(fd) > 1 else 0.0
        results.append({
            "d": d_val,
            "adf_stat": round(t_stat, 3),
            "is_stationary": is_stationary,
            "correlation_with_raw": round(corr, 4),
        })
        if is_stationary and not found:
            optimal_d = d_val
            found = True

    return {
        "optimal_d": optimal_d,
        "is_stationary": found,
        "evaluations": results,
    }


def estimate_ornstein_uhlenbeck_half_life(
    series: Union[pd.Series, np.ndarray, List[float]],
) -> float:
    """Estimates Ornstein-Uhlenbeck (OU) mean-reversion half-life (tau_1/2):
    Delta y_t = theta * (mu - y_{t-1}) Delta t + epsilon_t
    tau_1/2 = -ln(2) / theta
    """
    arr = np.asarray(series, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) < 20:
        return 0.0
    diff = np.diff(arr)
    lag = arr[:-1]
    mean_lag = np.mean(lag)
    var_lag = np.sum((lag - mean_lag) ** 2)
    if var_lag == 0:
        return 0.0
    theta = np.sum((lag - mean_lag) * (diff - np.mean(diff))) / var_lag
    if theta >= 0.0:
        # Non-mean-reverting or explosive series
        return float("inf")
    half_life = -math.log(2.0) / theta
    return float(max(0.5, half_life))


def calculate_bouchaud_square_root_capacity(
    daily_volatility: float,
    order_size_shares: float,
    average_daily_volume_shares: float,
    impact_constant_y: float = 1.0,
) -> float:
    """Calculates Bouchaud Square-Root Law Market Impact & Strategy Capacity:
    I_total = Y * sigma_daily * sqrt(Q / V_ADV)
    """
    sigma = float(daily_volatility)
    q = float(order_size_shares)
    v = float(average_daily_volume_shares)
    y = float(impact_constant_y)

    if not all(math.isfinite(x) for x in (sigma, q, v, y)) or v <= 0.0 or q <= 0.0:
        return 0.0
    participation = q / v
    return float(y * sigma * math.sqrt(participation))


def fractional_diff(
    series: Union[pd.Series, np.ndarray, List[float]],
    d: float = 0.40,
    order_d: Optional[float] = None,
    max_window: int = 40,
    threshold: float = 1e-3,
) -> np.ndarray:
    """Memory-preserving fractional differentiation wrapper."""
    final_d = float(order_d) if order_d is not None else float(d)
    return apply_fractional_differentiation(series, order_d=final_d, max_window=max_window, threshold=threshold)


get_fractional_weights = compute_binomial_fractional_weights
adf_stationarity_score = estimate_augmented_dickey_fuller_stationarity
find_optimal_d = optimize_stationarity_memory_order_d

