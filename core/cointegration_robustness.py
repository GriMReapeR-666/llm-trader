"""Module 06: Econometric Cointegration Testing, Backtest Deflation & Geometric Compounding Governance."""
from __future__ import annotations

from core.cointegration_and_compounding_governance import (
    calculate_engle_granger_adf_cointegration,
    evaluate_out_of_sample_deflation_hurdle,
    apply_position_band_inertia_filter,
    calculate_multi_asset_portfolio_variance,
    calculate_fractional_growth_optimal_kelly,
    evaluate_multi_horizon_drawdown_circuit_breakers,
    evaluate_alpha_decay_decommission_thresholds,
    # Aliases
    calculate_adf_cointegration,
    evaluate_50pct_haircut_rule,
    apply_carver_trade_buffering,
    calculate_portfolio_decorrelation_volatility,
    calculate_fractional_kelly_sizing,
    evaluate_3tier_circuit_breakers,
    evaluate_model_decommission_gate,
)

__all__ = [
    "calculate_engle_granger_adf_cointegration",
    "evaluate_out_of_sample_deflation_hurdle",
    "apply_position_band_inertia_filter",
    "calculate_multi_asset_portfolio_variance",
    "calculate_fractional_growth_optimal_kelly",
    "evaluate_multi_horizon_drawdown_circuit_breakers",
    "evaluate_alpha_decay_decommission_thresholds",
    "calculate_adf_cointegration",
    "evaluate_50pct_haircut_rule",
    "apply_carver_trade_buffering",
    "calculate_portfolio_decorrelation_volatility",
    "calculate_fractional_kelly_sizing",
    "evaluate_3tier_circuit_breakers",
    "evaluate_model_decommission_gate",
]
