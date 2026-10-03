"""Module 06: Econometric Cointegration Testing, Backtest Deflation & Geometric Compounding Governance Engine.

Implements institutional quantitative robustness, stationarity testing, and compounding governance:
1. Engle-Granger 2-step Ordinary Least Squares (OLS) and Augmented Dickey-Fuller (ADF) cointegration testing.
2. Out-of-sample degradation deflation hurdle:
       Sharpe_live >= delta_deflation * Sharpe_IS >= 1.00
3. Position band inertia filtering (deadband buffer +/- 10%) to eliminate turnover friction.
4. Multi-asset portfolio variance & variance drag suppression:
       sigma_p = sqrt(w^T Sigma w),  CAGR approx mu_p - 0.5 * sigma_p^2
5. Fractional growth-optimal Kelly criterion:
       Applied_Risk = lambda * f*,  where f* = (P_win * R - P_loss) / R
6. Multi-horizon drawdown circuit breakers:
       Level 1 (Session -3.0R freeze), Level 2 (Weekly -6.0R 50% scale-down), Level 3 (Peak-to-Trough >= 15% liquidation)
7. Dual-criteria alpha decay decommission gate:
       Sharpe_60d < 0.50  or  Drawdown_Duration > 1.5 * L_max
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd


def calculate_engle_granger_adf_cointegration(
    series_y: Union[List[float], np.ndarray, pd.Series],
    series_x: Union[List[float], np.ndarray, pd.Series],
) -> Dict[str, Any]:
    """Engle-Granger 2-step cointegration test via OLS residuals and ADF t-statistic.
    
    Step 1: y_t = alpha + beta * x_t + e_t
    Step 2: Delta e_t = gamma * e_{t-1} + u_t
    t_stat = gamma / SE(gamma) compared against critical surfaces (-3.34 for 5% significance).
    """
    y = np.asarray(series_y, dtype=float)
    x = np.asarray(series_x, dtype=float)
    if len(y) != len(x) or len(y) < 30:
        return {"is_cointegrated": False, "p_value": 1.0, "hedge_ratio": 1.0, "t_stat": 0.0}

    # Step 1: OLS regression y = alpha + beta * x
    mean_x = np.mean(x)
    mean_y = np.mean(y)
    var_x = np.sum((x - mean_x) ** 2)
    if var_x == 0:
        return {"is_cointegrated": False, "p_value": 1.0, "hedge_ratio": 1.0, "t_stat": 0.0}

    beta = np.sum((x - mean_x) * (y - mean_y)) / var_x
    alpha = mean_y - beta * mean_x
    residuals = y - (alpha + beta * x)

    # Step 2: ADF test on residuals
    diff_res = np.diff(residuals)
    lag_res = residuals[:-1]
    mean_lag = np.mean(lag_res)
    var_lag = np.sum((lag_res - mean_lag) ** 2)
    if var_lag == 0:
        return {"is_cointegrated": False, "p_value": 1.0, "hedge_ratio": float(beta), "t_stat": 0.0}

    gamma = np.sum((lag_res - mean_lag) * (diff_res - np.mean(diff_res))) / var_lag
    res_diff_error = (diff_res - np.mean(diff_res)) - gamma * (lag_res - mean_lag)
    se_gamma = math.sqrt(np.sum(res_diff_error ** 2) / (len(lag_res) - 2) / var_lag) if var_lag > 0 else 1.0
    t_stat = gamma / se_gamma if se_gamma > 0 else 0.0

    # Critical values for Engle-Granger: 1% = -3.90, 5% = -3.34, 10% = -3.04
    is_coint = t_stat < -3.34
    p_value = 0.001 if t_stat < -3.90 else (0.04 if t_stat < -3.34 else 0.25)

    return {
        "is_cointegrated": bool(is_coint),
        "p_value": p_value,
        "hedge_ratio": round(float(beta), 4),
        "alpha": round(float(alpha), 4),
        "t_stat": round(float(t_stat), 3),
        "critical_5pct": -3.34,
    }


def evaluate_out_of_sample_deflation_hurdle(
    in_sample_sharpe: Optional[float] = None,
    in_sample_cagr: Optional[float] = None,
    in_sample_max_dd: Optional[float] = None,
    deflation_factor: float = 0.50,
    expansion_factor: float = 1.50,
    min_sharpe_hurdle: float = 1.00,
    max_drawdown_ceiling: float = 0.20,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Applies out-of-sample performance degradation deflation to backtested performance.
    
    Calculates expected live performance under friction, quote degradation, and parameter estimation error:
        Sharpe_live = deflation_factor * Sharpe_IS
        CAGR_live = deflation_factor * CAGR_IS
        MaxDD_live = expansion_factor * MaxDD_IS
    """
    sharpe_val = in_sample_sharpe if in_sample_sharpe is not None else kwargs.get("backtest_sharpe", 0.0)
    cagr_val = in_sample_cagr if in_sample_cagr is not None else kwargs.get("backtest_cagr", 0.0)
    max_dd_val = in_sample_max_dd if in_sample_max_dd is not None else kwargs.get("backtest_max_dd", 0.0)

    expected_live_sharpe = float(sharpe_val) * deflation_factor
    expected_live_cagr = float(cagr_val) * deflation_factor
    expected_live_max_dd = min(1.0, float(max_dd_val) * expansion_factor)

    is_promotable = (expected_live_sharpe >= min_sharpe_hurdle) and (expected_live_max_dd <= max_drawdown_ceiling)

    return {
        "backtest_sharpe": round(float(sharpe_val), 2),
        "expected_live_sharpe": round(float(expected_live_sharpe), 2),
        "backtest_cagr": round(float(cagr_val), 4),
        "expected_live_cagr": round(float(expected_live_cagr), 4),
        "backtest_max_dd": round(float(max_dd_val), 4),
        "expected_live_max_dd": round(float(expected_live_max_dd), 4),
        "is_promotion_approved": is_promotable,
        "hurdle_sharpe": min_sharpe_hurdle,
    }


def apply_position_band_inertia_filter(
    target_position: float,
    current_position: float,
    deadband_buffer_fraction: Optional[float] = None,
    **kwargs: Any,
) -> Tuple[float, bool]:
    """Position Band Inertia Filtering (Deadband +/- 10%).
    
    If the target position change relative to current position is within the deadband buffer,
    suppress the rebalancing order to eliminate turnover friction.
    
    Returns:
        (executed_position, order_executed_boolean)
    """
    target = float(target_position)
    current = float(current_position)
    
    if deadband_buffer_fraction is not None:
        buffer_pct = float(deadband_buffer_fraction)
    elif "buffer_pct" in kwargs:
        buffer_pct = float(kwargs["buffer_pct"])
    else:
        buffer_pct = 0.10

    if abs(current) < 1e-6:
        # If flat, always execute target
        return target, True

    diff = abs(target - current)
    threshold = abs(current) * buffer_pct

    if diff <= threshold:
        # Within deadband buffer -> suppress order
        return current, False
    else:
        # Outside deadband buffer -> execute rebalance
        return target, True



def calculate_multi_asset_portfolio_variance(
    sleeve_volatilities: List[float],
    correlation_matrix: np.ndarray,
    weights: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Calculates multi-asset annualized portfolio variance under de-correlation.
    
    Formula:
        sigma_port = sqrt(w^T Sigma w)
        CAGR approx mu_p - 0.5 * sigma_port^2
    """
    vols = np.asarray(sleeve_volatilities, dtype=float)
    n = len(vols)
    if weights is None:
        w = np.ones(n) / n
    else:
        w = np.asarray(weights, dtype=float)
        total_w = np.sum(w)
        w = w / total_w if total_w > 0 else np.ones(n) / n

    corr = np.asarray(correlation_matrix, dtype=float)
    cov = np.outer(vols, vols) * corr
    port_var = float(np.dot(w, np.dot(cov, w)))
    port_vol = math.sqrt(max(0.0, port_var))
    weighted_avg_vol = float(np.sum(w * vols))
    vol_reduction_pct = ((weighted_avg_vol - port_vol) / weighted_avg_vol) * 100.0 if weighted_avg_vol > 0 else 0.0

    return {
        "portfolio_volatility": round(float(port_vol), 4),
        "weighted_average_individual_vol": round(float(weighted_avg_vol), 4),
        "volatility_reduction_percent": round(float(vol_reduction_pct), 2),
        "diversification_ratio": round(float(weighted_avg_vol / port_vol), 3) if port_vol > 0 else 1.0,
    }


def calculate_fractional_growth_optimal_kelly(
    win_rate: float,
    payoff_ratio: float,
    fractional_multiplier: float = 0.25,
    max_risk_cap: float = 0.02,
) -> float:
    """Fractional Growth-Optimal Kelly Allocation.
    
    Applied Risk % = min(fractional_multiplier * K*, max_risk_cap)
    where K* = (P_win * R - (1 - P_win)) / R
    """
    p = float(win_rate)
    r = float(payoff_ratio)
    lam = float(fractional_multiplier)
    cap = float(max_risk_cap)

    if not all(math.isfinite(x) for x in (p, r, lam, cap)) or r <= 0 or p <= 0 or p >= 1:
        return 0.0

    full_k = (p * r - (1.0 - p)) / r
    if full_k <= 0:
        return 0.0

    applied = lam * full_k
    return float(min(applied, cap))


def evaluate_multi_horizon_drawdown_circuit_breakers(
    session_realized_r: float,
    weekly_realized_r: float,
    peak_to_trough_drawdown_fraction: float,
    session_halt_r_threshold: float = -3.0,
    weekly_scale_down_r_threshold: float = -6.0,
    portfolio_liquidation_dd_threshold: float = 0.15,
) -> Dict[str, Any]:
    """Evaluates the 3-tier drawdown circuit breaker state machine:
    Level 1: Session loss <= -3.0R -> 24-hour order execution halt
    Level 2: Weekly loss <= -6.0R -> 50% position sizing scale-down
    Level 3: Peak-to-trough equity drawdown >= 15% -> Liquidate all open trades to cash
    """
    s_r = float(session_realized_r)
    w_r = float(weekly_realized_r)
    dd = float(peak_to_trough_drawdown_fraction)

    is_portfolio_liquidate = dd >= portfolio_liquidation_dd_threshold
    is_session_halted = s_r <= session_halt_r_threshold
    is_weekly_scale_down = w_r <= weekly_scale_down_r_threshold

    sizing_multiplier = 1.0
    if is_portfolio_liquidate:
        state = "LEVEL_3_PORTFOLIO_CASH_LIQUIDATION"
        sizing_multiplier = 0.0
    elif is_session_halted:
        state = "LEVEL_1_SESSION_HALT"
        sizing_multiplier = 0.0
    elif is_weekly_scale_down:
        state = "LEVEL_2_WEEKLY_SCALE_DOWN"
        sizing_multiplier = 0.50
    else:
        state = "NORMAL_EXECUTION"
        sizing_multiplier = 1.0

    return {
        "state": state,
        "is_trading_allowed": sizing_multiplier > 0.0,
        "sizing_multiplier": sizing_multiplier,
        "is_session_halted": is_session_halted,
        "is_weekly_scale_down": is_weekly_scale_down,
        "is_portfolio_liquidated": is_portfolio_liquidate,
        "session_realized_r": s_r,
        "weekly_realized_r": w_r,
        "drawdown_fraction": dd,
    }


def evaluate_alpha_decay_decommission_thresholds(
    rolling_sharpe_60d: float,
    current_drawdown_duration_bars: int,
    expected_max_losing_streak_l_max: float,
    min_rolling_sharpe_floor: float = 0.50,
    max_drawdown_duration_multiplier: float = 1.50,
) -> Dict[str, Any]:
    """Audits alpha decay health and determines if strategy must be decommissioned."""
    sharpe = float(rolling_sharpe_60d)
    dd_bars = int(current_drawdown_duration_bars)
    l_max = float(expected_max_losing_streak_l_max)

    max_allowed_dd_bars = max_drawdown_duration_multiplier * l_max

    sharpe_breached = sharpe < min_rolling_sharpe_floor
    duration_breached = dd_bars > max_allowed_dd_bars

    should_decommission = sharpe_breached or duration_breached

    return {
        "should_decommission": should_decommission,
        "rolling_sharpe_60d": round(sharpe, 2),
        "sharpe_floor": min_rolling_sharpe_floor,
        "sharpe_breached": sharpe_breached,
        "drawdown_duration_bars": dd_bars,
        "max_allowed_drawdown_bars": round(max_allowed_dd_bars, 1),
        "duration_breached": duration_breached,
    }


# ---------------------------------------------------------------------------
# Exact Aliases for Backwards Compatibility
# ---------------------------------------------------------------------------
calculate_adf_cointegration = calculate_engle_granger_adf_cointegration
evaluate_50pct_haircut_rule = evaluate_out_of_sample_deflation_hurdle
apply_carver_trade_buffering = apply_position_band_inertia_filter
calculate_portfolio_decorrelation_volatility = calculate_multi_asset_portfolio_variance
calculate_fractional_kelly_sizing = calculate_fractional_growth_optimal_kelly
evaluate_3tier_circuit_breakers = evaluate_multi_horizon_drawdown_circuit_breakers
evaluate_model_decommission_gate = evaluate_alpha_decay_decommission_thresholds
