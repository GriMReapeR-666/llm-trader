"""Unit tests for Module 07: Institutional Macro Alpha Signals, Microstructure & Non-Paid Open Data Telemetry."""
import math
import numpy as np
import pytest

from intelligence.macro_alpha_signals_and_microstructure import (
    calculate_macro_cointegration_divergence_zscore,
    calculate_futures_roll_yield_and_term_structure_slope,
    calculate_vix_term_structure_volatility_panic_ratio,
    calculate_options_twenty_five_delta_skew_proxy,
    calculate_vpin_flow_toxicity,
    evaluate_eia_usda_preemption_signal,
    MacroAlphaSignalsEngine,
)


def test_macro_cointegration_divergence_zscore():
    # Synthetic cointegrated series where asset temporarily decouples downwards
    np.random.seed(42)
    t = np.linspace(0, 10, 100)
    macro_driver = 100.0 + 2.0 * t + np.random.normal(0, 1.0, 100)
    # Asset tracks 1.5 * driver, but at the end drops sharply by 15 points
    asset_price = 1.5 * macro_driver + np.random.normal(0, 0.5, 100)
    asset_price[-1] -= 15.0

    res = calculate_macro_cointegration_divergence_zscore(macro_driver, asset_price, window=60)
    assert res["hedge_ratio_beta"] > 1.2
    assert res["residual_zscore"] <= -2.0
    assert "UNDERVALUED" in res["signal"]
    assert res["trading_action"] == "BUY_ASSET_LONG_BASIS"


def test_futures_roll_yield_and_term_structure_slope():
    # Backwardation: F1 = $80.00, F2 = $78.00 over 30 days
    # S_roll = ln(80/78) * (365/30) * 100 = 0.0253 * 12.167 * 100 = +30.8%
    res_back = calculate_futures_roll_yield_and_term_structure_slope(
        front_month_price_f1=80.0,
        second_month_price_f2=78.0,
        days_between_contracts=30,
    )
    assert res_back["annualized_roll_yield_pct"] > 25.0
    assert res_back["term_structure_regime"] == "STRONG_BACKWARDATION"

    # Contango: F1 = $70.00, F2 = $73.00 over 30 days
    res_cont = calculate_futures_roll_yield_and_term_structure_slope(
        front_month_price_f1=70.0,
        second_month_price_f2=73.0,
        days_between_contracts=30,
    )
    assert res_cont["annualized_roll_yield_pct"] < -20.0
    assert res_cont["term_structure_regime"] == "DEEP_SUPER_CONTANGO"


def test_vix_term_structure_volatility_panic_ratio():
    # Panic Inversion: VIX9D = 28.0, VIX Spot = 25.0, VIX3M = 20.0 -> Ratio = 28 / 20 = 1.40 > 1.15
    res_panic = calculate_vix_term_structure_volatility_panic_ratio(
        vix_9d=28.0,
        vix_spot=25.0,
        vix_3m=20.0,
    )
    assert res_panic["panic_ratio_9d_3m"] == 1.40
    assert res_panic["is_inverted"] is True
    assert res_panic["volatility_regime"] == "ACUTE_VOLATILITY_INVERSION_PANIC"
    assert "LONG_VOLATILITY" in res_panic["recommended_action"]

    # Normal Complacency: VIX9D = 13.0, VIX Spot = 14.0, VIX3M = 17.0 -> Ratio = 0.76
    res_norm = calculate_vix_term_structure_volatility_panic_ratio(
        vix_9d=13.0,
        vix_spot=14.0,
        vix_3m=17.0,
    )
    assert res_norm["is_inverted"] is False
    assert res_norm["volatility_regime"] == "NORMAL_CONTANGO_COMPLACENCY"


def test_options_twenty_five_delta_skew_proxy():
    # Put price $8.00, Call price $2.00, ATM price $50.00 -> Skew = (8 - 2)/50 * 100 = 12%
    res_crash = calculate_options_twenty_five_delta_skew_proxy(
        otm_put_price=8.0,
        otm_call_price=2.0,
        atm_price=50.0,
    )
    assert res_crash["skew_percentage"] == 12.0
    assert res_crash["tail_sentiment"] == "EXTREME_DOWNSIDE_CRASH_FEAR"


def test_vpin_flow_toxicity():
    # Toxic one-sided selling: Large volumes with consistent negative price changes
    np.random.seed(42)
    n_trades = 200
    vols = np.random.uniform(500.0, 1500.0, n_trades)
    # Strongly negative price deltas
    deltas = np.random.normal(-2.0, 0.5, n_trades)

    res_toxic = calculate_vpin_flow_toxicity(
        trade_volumes=vols,
        price_changes=deltas,
        bucket_size_volume=2000.0,
        num_buckets=20,
    )
    assert res_toxic["vpin_toxicity"] >= 0.70
    assert res_toxic["is_informed_flow_present"] is True
    assert res_toxic["execution_strategy"] == "AGGRESSIVE_CROSS_SPREAD"


def test_evaluate_eia_usda_preemption_signal():
    # Satellite observed -4.0M bbl draw, consensus expects +1.0M bbl build -> Surprise = -5.0M bbl
    res = evaluate_eia_usda_preemption_signal(
        satellite_inferred_inventory_delta=-4.0,
        consensus_expected_inventory_delta=1.0,
        threshold_surprise_sigma=1.5,
    )
    assert res["surprise_delta_mbbl"] == -5.0
    assert "BULLISH" in res["preemption_trade_bias"]
    assert res["signal_confidence"] >= 0.85


def test_macro_alpha_signals_engine():
    engine = MacroAlphaSignalsEngine()
    x = np.linspace(100, 120, 60)
    y = 1.2 * x
    vols = np.ones(50) * 100.0
    deltas = np.ones(50) * 0.1

    alpha = engine.evaluate_live_market_alpha(
        macro_driver_series=x,
        asset_price_series=y,
        f1_price=80.0,
        f2_price=78.0,
        vix_9d=14.0,
        vix_spot=15.0,
        vix_3m=18.0,
        trade_vols=vols,
        price_deltas=deltas,
    )
    assert "composite_directional_conviction" in alpha
    assert "meta_label_execution_authorized" in alpha
