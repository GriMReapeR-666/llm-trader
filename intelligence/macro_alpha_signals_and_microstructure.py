"""Module 07: Institutional Macro Alpha Signals, Microstructure & Non-Paid Open-Data Telemetry.

Extracts low-latency, tradeable alpha signals from open/free public data sources:
1. Macro Cointegration Lead-Lag Divergence Z-Score:
       epsilon_t = Y_t - (alpha + beta * X_t),  Z_res = (epsilon_t - mu_eps) / sigma_eps
2. Futures Term Structure & Annualized Roll Yield:
       S_roll = ln(F_1 / F_2) * (365 / delta_t)
3. VIX Term Structure Inversion Panic Ratio:
       Ratio_panic = VIX_9D / VIX_3M (Inversion > 1.15 signals imminent equity volatility shock)
4. 25-Delta Options Volatility Skew & Tail Hedging Proxy:
       Skew_25d = [(P_put_otm - P_call_otm) / P_atm] * 100
5. Volume-Synchronized Probability of Toxicity (VPIN & Microstructure Flow Imbalance):
       VPIN = (sum_{tau=1}^N |V_tau^B - V_tau^S|) / (N * V)
6. EIA / USDA Pre-Emptive Satellite & Telemetry Release Arbitrage:
       Delta_surprise = Delta_satellite - Delta_consensus
7. MacroAlphaSignalsEngine (Master Open-Data Signal Orchestrator)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np


def calculate_macro_cointegration_divergence_zscore(
    macro_driver_series: Union[Sequence[float], np.ndarray],
    asset_price_series: Union[Sequence[float], np.ndarray],
    window: int = 60,
) -> Dict[str, Any]:
    """Calculates dynamic cointegration lead-lag residual Z-score between a macro fundamental driver
    (e.g., Copper/Gold, Haber-Bosch Ammonia) and a tradeable asset (e.g., 10Y Yield, Corn Futures).
    epsilon_t = Y_t - (alpha + beta * X_t)
    Z = (epsilon_t - mean(epsilon)) / std(epsilon)
    """
    x = np.asarray(macro_driver_series, dtype=float)
    y = np.asarray(asset_price_series, dtype=float)

    n = min(len(x), len(y))
    if n < 10:
        return {
            "hedge_ratio_beta": 1.0,
            "intercept_alpha": 0.0,
            "latest_residual": 0.0,
            "residual_zscore": 0.0,
            "signal": "INSUFFICIENT_DATA",
            "trading_action": "HOLD",
        }

    x = x[-n:]
    y = y[-n:]
    w = min(n, max(10, int(window)))

    x_win = x[-w:]
    y_win = y[-w:]

    # OLS estimation of beta and alpha
    x_mean = float(np.mean(x_win))
    y_mean = float(np.mean(y_win))
    var_x = float(np.var(x_win))

    if var_x < 1e-12:
        beta = 1.0
    else:
        cov_xy = float(np.cov(x_win, y_win)[0, 1])
        beta = cov_xy / var_x

    alpha = y_mean - beta * x_mean

    residuals = y_win - (alpha + beta * x_win)
    res_mean = float(np.mean(residuals))
    res_std = max(1e-6, float(np.std(residuals)))

    latest_res = float(residuals[-1])
    z_score = (latest_res - res_mean) / res_std

    if z_score <= -2.0:
        sig = "LONG_ASSET_UNDERVALUED_RELATIVE_TO_MACRO"
        action = "BUY_ASSET_LONG_BASIS"
    elif z_score >= 2.0:
        sig = "SHORT_ASSET_OVERVALUED_RELATIVE_TO_MACRO"
        action = "SELL_ASSET_SHORT_BASIS"
    else:
        sig = "EQUILIBRIUM_COINTEGRATION_SYNC"
        action = "HOLD_NEUTRAL"

    return {
        "hedge_ratio_beta": round(float(beta), 4),
        "intercept_alpha": round(float(alpha), 4),
        "latest_residual": round(latest_res, 4),
        "residual_zscore": round(float(z_score), 3),
        "signal": sig,
        "trading_action": action,
    }


def calculate_futures_roll_yield_and_term_structure_slope(
    front_month_price_f1: float,
    second_month_price_f2: float,
    days_between_contracts: int = 30,
) -> Dict[str, Any]:
    """Calculates annualized roll yield and classifies physical futures term structure curve:
    S_roll = ln(F1 / F2) * (365 / delta_t)
    - S_roll > +15%: Strong Backwardation (Immediate physical scarcity / Long bias)
    - S_roll < -15%: Deep Contango (Storage saturation / Short / Bearish prompt)
    """
    f1 = max(1e-6, float(front_month_price_f1))
    f2 = max(1e-6, float(second_month_price_f2))
    dt = max(1, int(days_between_contracts))

    log_spread = math.log(f1 / f2)
    annualized_roll_yield_pct = log_spread * (365.0 / dt) * 100.0

    if annualized_roll_yield_pct >= 15.0:
        regime = "STRONG_BACKWARDATION"
        bias = "PHYSICAL_SCARCITY_POSITIVE_ROLL_YIELD_LONG"
    elif annualized_roll_yield_pct >= 2.0:
        regime = "MILD_BACKWARDATION"
        bias = "NORMAL_TIGHTNESS_MILD_POSITIVE_CARRY"
    elif annualized_roll_yield_pct > -5.0:
        regime = "PARITY_FLAT"
        bias = "NEUTRAL_CARRY"
    elif annualized_roll_yield_pct > -20.0:
        regime = "CONTANGO"
        bias = "NEGATIVE_ROLL_YIELD_STORAGE_NORMAL"
    else:
        regime = "DEEP_SUPER_CONTANGO"
        bias = "STORAGE_SATURATION_NEGATIVE_CARRY_SHORT"

    return {
        "front_month_f1": f1,
        "second_month_f2": f2,
        "calendar_spread_nominal": round(f1 - f2, 4),
        "annualized_roll_yield_pct": round(float(annualized_roll_yield_pct), 2),
        "term_structure_regime": regime,
        "carry_allocation_bias": bias,
    }


def calculate_vix_term_structure_volatility_panic_ratio(
    vix_9d: float,
    vix_spot: float,
    vix_3m: float,
) -> Dict[str, Any]:
    """Evaluates VIX Term Structure Inversion Panic Ratio:
    Ratio_panic = VIX_9D / VIX_3M
    Prompt volatility inversion (> 1.15) precedes major equity drawdowns by 24–72 hours.
    """
    v9 = max(1.0, float(vix_9d))
    vspot = max(1.0, float(vix_spot))
    v3m = max(1.0, float(vix_3m))

    panic_ratio = v9 / v3m
    spot_to_3m = vspot / v3m

    is_inverted = panic_ratio > 1.05
    is_panic = panic_ratio >= 1.15

    if is_panic:
        state = "ACUTE_VOLATILITY_INVERSION_PANIC"
        action = "EXECUTE_LONG_VOLATILITY_AND_TAIL_HEDGES"
    elif is_inverted:
        state = "MILD_TERM_STRUCTURE_INVERSION"
        action = "DEFENSIVE_REBALANCING"
    else:
        state = "NORMAL_CONTANGO_COMPLACENCY"
        action = "NORMAL_EQUITY_BETA"

    return {
        "vix_9d": v9,
        "vix_spot": vspot,
        "vix_3m": v3m,
        "panic_ratio_9d_3m": round(float(panic_ratio), 3),
        "spot_to_3m_ratio": round(float(spot_to_3m), 3),
        "is_inverted": bool(is_inverted),
        "volatility_regime": state,
        "recommended_action": action,
    }


def calculate_options_twenty_five_delta_skew_proxy(
    otm_put_price: float,
    otm_call_price: float,
    atm_price: float,
) -> Dict[str, Any]:
    """Calculates 25-Delta Options Volatility Skew / Risk Reversal Proxy:
    Skew = [(P_put_otm - P_call_otm) / P_atm] * 100
    - Skew > +10.0%: Heavy downside tail crash hedging (Bearish protection demand)
    - Skew < -5.0%: Heavy upside call skew (Aggressive upside speculation / Short squeeze risk)
    """
    put_p = max(0.0, float(otm_put_price))
    call_p = max(0.0, float(otm_call_price))
    atm_p = max(1e-6, float(atm_price))

    skew_pct = ((put_p - call_p) / atm_p) * 100.0

    if skew_pct >= 12.0:
        sentiment = "EXTREME_DOWNSIDE_CRASH_FEAR"
        bias = "LONG_TAIL_PUTS_OR_CASH_BUFFER"
    elif skew_pct >= 4.0:
        sentiment = "MODERATE_DOWNSIDE_HEDGING"
        bias = "BALANCED_DEFENSIVE"
    elif skew_pct >= -4.0:
        sentiment = "SYMMETRIC_DISTRIBUTION"
        bias = "NEUTRAL"
    else:
        sentiment = "AGGRESSIVE_UPSIDE_CALL_SKEW"
        bias = "LONG_CONVEX_UPSIDE"

    return {
        "otm_put_price": put_p,
        "otm_call_price": call_p,
        "atm_price": atm_p,
        "skew_percentage": round(float(skew_pct), 2),
        "tail_sentiment": sentiment,
        "volatility_positioning_bias": bias,
    }


def calculate_vpin_flow_toxicity(
    trade_volumes: Union[Sequence[float], np.ndarray],
    price_changes: Union[Sequence[float], np.ndarray],
    bucket_size_volume: float = 1000.0,
    num_buckets: int = 50,
) -> Dict[str, Any]:
    """Calculates Volume-Synchronized Probability of Toxicity (VPIN) based on Easley et al. (2012):
    VPIN = sum_{tau=1}^N |V_tau^B - V_tau^S| / (N * V)
    - VPIN >= 0.70: High toxicity / Informed institutional flow present
    - VPIN < 0.40: Benign retail / Noise flow
    """
    vols = np.asarray(trade_volumes, dtype=float)
    deltas = np.asarray(price_changes, dtype=float)

    n_trades = min(len(vols), len(deltas))
    if n_trades == 0:
        return {
            "vpin_toxicity": 0.0,
            "toxicity_regime": "NO_DATA",
            "is_informed_flow_present": False,
        }

    vols = vols[:n_trades]
    deltas = deltas[:n_trades]

    # Assign buy volume vs. sell volume using normal CDF probability of trade sign
    std_delta = max(1e-6, float(np.std(deltas)))
    z_deltas = deltas / std_delta

    # Prob buy = 0.5 * (1 + erf(z / sqrt(2)))
    buy_probs = 0.5 * (1.0 + np.vectorize(math.erf)(z_deltas / math.sqrt(2.0)))
    buy_vols = vols * buy_probs
    sell_vols = vols * (1.0 - buy_probs)

    # Accumulate into equal-volume buckets
    b_size = max(1.0, float(bucket_size_volume))
    n_buckets = max(5, int(num_buckets))

    bucket_imbalances = []
    cur_v_buy = 0.0
    cur_v_sell = 0.0
    cur_total = 0.0

    for b_v, s_v, v in zip(buy_vols, sell_vols, vols):
        cur_v_buy += b_v
        cur_v_sell += s_v
        cur_total += v

        if cur_total >= b_size:
            imbalance = abs(cur_v_buy - cur_v_sell)
            bucket_imbalances.append(imbalance)
            cur_v_buy = 0.0
            cur_v_sell = 0.0
            cur_total = 0.0

    if not bucket_imbalances:
        # Fallback to simple aggregate imbalance
        total_vol = max(1e-6, float(np.sum(vols)))
        vpin = abs(float(np.sum(buy_vols)) - float(np.sum(sell_vols))) / total_vol
    else:
        recent_buckets = bucket_imbalances[-n_buckets:]
        vpin = float(np.mean(recent_buckets)) / b_size

    vpin = float(min(1.0, max(0.0, vpin)))

    if vpin >= 0.70:
        regime = "TOXIC_INFORMED_INSTITUTIONAL_FLOW"
        is_toxic = True
    elif vpin >= 0.50:
        regime = "ELEVATED_ASYMMETRIC_FLOW"
        is_toxic = False
    else:
        regime = "BENIGN_NOISE_TRADING"
        is_toxic = False

    return {
        "vpin_toxicity": round(vpin, 4),
        "toxicity_regime": regime,
        "is_informed_flow_present": is_toxic,
        "execution_strategy": "AGGRESSIVE_CROSS_SPREAD" if is_toxic else "PASSIVE_LIMIT_MAKER",
    }


def evaluate_eia_usda_preemption_signal(
    satellite_inferred_inventory_delta: float,
    consensus_expected_inventory_delta: float,
    threshold_surprise_sigma: float = 1.5,
) -> Dict[str, Any]:
    """Quantifies pre-release inventory surprise from free Sentinel-1 SAR satellite tank observations
    ahead of scheduled EIA Weekly Petroleum Status (Wednesday 10:30 AM EST) or USDA WASDE releases.
    Surprise = Delta_satellite - Delta_consensus
    """
    sat_delta = float(satellite_inferred_inventory_delta)
    cons_delta = float(consensus_expected_inventory_delta)
    thresh = max(0.1, float(threshold_surprise_sigma))

    surprise_mbbl = sat_delta - cons_delta

    if surprise_mbbl <= -thresh:
        trade_bias = "BULLISH_SUPPLY_DEFICIT_ACCUMULATE_BEFORE_RELEASE"
        confidence = min(0.95, 0.65 + abs(surprise_mbbl) * 0.10)
    elif surprise_mbbl >= thresh:
        trade_bias = "BEARISH_STORAGE_BUILD_FADE_BEFORE_RELEASE"
        confidence = min(0.95, 0.65 + abs(surprise_mbbl) * 0.10)
    else:
        trade_bias = "INLINE_WITH_CONSENSUS_NO_PREEMPTION"
        confidence = 0.50

    return {
        "satellite_inventory_delta": sat_delta,
        "consensus_inventory_delta": cons_delta,
        "surprise_delta_mbbl": round(surprise_mbbl, 2),
        "preemption_trade_bias": trade_bias,
        "signal_confidence": round(float(confidence), 3),
        "execution_window": "24_HOURS_PRIOR_TO_10_30_AM_EST",
    }


class MacroAlphaSignalsEngine:
    """Master institutional quant alpha engine aggregating non-paid public feeds into high-conviction signals."""

    def __init__(self):
        pass

    def evaluate_live_market_alpha(
        self,
        macro_driver_series: np.ndarray,
        asset_price_series: np.ndarray,
        f1_price: float,
        f2_price: float,
        vix_9d: float,
        vix_spot: float,
        vix_3m: float,
        trade_vols: np.ndarray,
        price_deltas: np.ndarray,
    ) -> Dict[str, Any]:
        coint = calculate_macro_cointegration_divergence_zscore(macro_driver_series, asset_price_series)
        roll = calculate_futures_roll_yield_and_term_structure_slope(f1_price, f2_price)
        vix_panic = calculate_vix_term_structure_volatility_panic_ratio(vix_9d, vix_spot, vix_3m)
        vpin = calculate_vpin_flow_toxicity(trade_vols, price_deltas)

        # Composite Conviction Meta-Label
        score = 0.0
        if coint["residual_zscore"] <= -2.0:
            score += 0.35
        elif coint["residual_zscore"] >= 2.0:
            score -= 0.35

        if "BACKWARDATION" in roll["term_structure_regime"]:
            score += 0.25
        elif "CONTANGO" in roll["term_structure_regime"]:
            score -= 0.25

        if vix_panic["is_inverted"]:
            score -= 0.25

        if vpin["is_informed_flow_present"]:
            score = score * 1.25

        conviction = float(min(1.0, max(-1.0, score)))

        return {
            "cointegration_lead_lag": coint,
            "futures_roll_yield": roll,
            "vix_term_structure": vix_panic,
            "vpin_order_flow_toxicity": vpin,
            "composite_directional_conviction": round(conviction, 3),
            "meta_label_execution_authorized": abs(conviction) >= 0.50,
        }
