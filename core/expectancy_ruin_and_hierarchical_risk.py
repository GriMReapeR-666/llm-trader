"""Module 01: Quantitative Expectancy, Ruin Theory, Kelly Sizing & Hierarchical Risk Engine.

Exact Quant & Mathematical Processes Implemented:
1. Stage 1: Micro-Standardization (R-multiples, 1R Dollar Risk, Structural Invalidation Distance)
2. Stage 2: Performance & Validation (Expected Value EV, SQN, Walk-Forward Efficiency WFE, Deflated Sharpe Ratio DSR)
3. Stage 3: Survival & Ruin Theory (Ralph Vince / Balsara Ruin Probability, Asymmetric Loss Recovery, Run Theory Streak Bounds)
4. Stage 4: Optimal Risk Sizing (Quarter-Kelly K*, Continuous Kelly, Volatility Parity Allocator)
5. Stage 5: Non-Stationary Regime Routing (Rescaled Range Hurst Exponent H, Empirical ATR Percentile)
6. Stage 6: Compounding Governance (CAGR, Downside Deviation Sortino, Calmar Ratio)
7. Stateful Engines: QuantitativeMetricsEngine, MetricsTracker, HierarchicalMetaPortfolioEngine
"""
from __future__ import annotations

from core.metrics import (
    # Stage 1: Micro Standardization
    calculate_stop_distance,
    calculate_1r_dollar_risk,
    calculate_normalized_lot_size,
    calculate_realized_r_multiple,
    calculate_structural_invalidation_distance,
    calculate_fixed_fractional_dollar_risk,
    calculate_discrete_lot_allocation,
    calculate_dimensionless_realized_r_multiple,
    # Stage 2: Validation Engine
    calculate_expected_value,
    calculate_sqn,
    calculate_wfe,
    calculate_deflated_sharpe_ratio,
    calculate_information_coefficient,
    calculate_mathematical_expectancy,
    calculate_system_quality_number,
    calculate_walk_forward_efficiency,
    # Stage 3: Survival & Run Theory
    calculate_risk_of_ruin,
    calculate_ruin_probability_balsara,
    calculate_asymmetric_loss_recovery_fraction,
    calculate_asymmetric_loss_recovery,
    calculate_discrete_lottery_expectancy,
    calculate_lottery_expected_value,
    calculate_streak_probability,
    calculate_run_theory_streak_probability,
    calculate_expected_max_losing_streak,
    calculate_run_theory_expected_max_streak,
    calculate_calmar_ratio,
    calculate_calmar_drawdown_ratio,
    # Stage 4: Sizing
    calculate_kelly_criterion,
    calculate_kelly_optimal_fraction,
    calculate_quarter_kelly_sizing,
    calculate_fractional_kelly_allocation,
    calculate_volatility_parity_size,
    calculate_volatility_parity_allocation,
    # Stage 5: Regime Detection & Dynamic Routing
    calculate_hurst_exponent,
    calculate_rescaled_range_hurst_exponent,
    calculate_atr_percentile,
    calculate_empirical_atr_percentile,
    calculate_master_expectancy,
    calculate_regime_conditioned_master_expectancy,
    # Stage 6: Compounding & Macro Performance
    calculate_cagr,
    calculate_compound_annual_growth_rate,
    calculate_sharpe_ratio,
    calculate_annualized_sharpe_ratio,
    calculate_sortino_ratio,
    calculate_downside_deviation_sortino_ratio,
    # Stateful Engines
    QuantitativeMetricsEngine,
    MetricsTracker,
)
from core.hierarchical_portfolio import (
    RegimeState,
    DynamicAllocationResult,
    RegimeDetectionEngine,
    DynamicStrategyRouter,
    MasterRiskEngine,
    HierarchicalMetaPortfolioEngine,
)

# Canonical and backward-compatibility aliases
calculate_ruin_probability = calculate_ruin_probability_balsara
calculate_quarter_kelly_size = calculate_quarter_kelly_sizing
calculate_max_consecutive_losses = calculate_expected_max_losing_streak

__all__ = [
    "calculate_stop_distance",
    "calculate_1r_dollar_risk",
    "calculate_normalized_lot_size",
    "calculate_realized_r_multiple",
    "calculate_structural_invalidation_distance",
    "calculate_fixed_fractional_dollar_risk",
    "calculate_discrete_lot_allocation",
    "calculate_dimensionless_realized_r_multiple",
    "calculate_expected_value",
    "calculate_sqn",
    "calculate_wfe",
    "calculate_deflated_sharpe_ratio",
    "calculate_information_coefficient",
    "calculate_mathematical_expectancy",
    "calculate_system_quality_number",
    "calculate_walk_forward_efficiency",
    "calculate_risk_of_ruin",
    "calculate_ruin_probability_balsara",
    "calculate_ruin_probability",
    "calculate_asymmetric_loss_recovery_fraction",
    "calculate_asymmetric_loss_recovery",
    "calculate_discrete_lottery_expectancy",
    "calculate_lottery_expected_value",
    "calculate_streak_probability",
    "calculate_run_theory_streak_probability",
    "calculate_expected_max_losing_streak",
    "calculate_run_theory_expected_max_streak",
    "calculate_max_consecutive_losses",
    "calculate_calmar_ratio",
    "calculate_calmar_drawdown_ratio",
    "calculate_kelly_criterion",
    "calculate_kelly_optimal_fraction",
    "calculate_quarter_kelly_sizing",
    "calculate_quarter_kelly_size",
    "calculate_fractional_kelly_allocation",
    "calculate_volatility_parity_size",
    "calculate_volatility_parity_allocation",
    "calculate_hurst_exponent",
    "calculate_rescaled_range_hurst_exponent",
    "calculate_atr_percentile",
    "calculate_empirical_atr_percentile",
    "calculate_master_expectancy",
    "calculate_regime_conditioned_master_expectancy",
    "calculate_cagr",
    "calculate_compound_annual_growth_rate",
    "calculate_sharpe_ratio",
    "calculate_annualized_sharpe_ratio",
    "calculate_sortino_ratio",
    "calculate_downside_deviation_sortino_ratio",
    "QuantitativeMetricsEngine",
    "MetricsTracker",
    "RegimeState",
    "DynamicAllocationResult",
    "RegimeDetectionEngine",
    "DynamicStrategyRouter",
    "MasterRiskEngine",
    "HierarchicalMetaPortfolioEngine",
]
