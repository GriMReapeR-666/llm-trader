"""Unit tests for Module 04: Transaction Cost Engine and Market Impact."""
import pytest
from core.transaction_cost_engine import (
    calculate_volume_weighted_micro_price,
    calculate_endogenous_dynamic_spread,
    calculate_bouchaud_square_root_market_impact,
    calculate_almgren_chriss_price_impact,
    calculate_explicit_regulatory_and_broker_fees,
    calculate_implementation_shortfall_breakdown,
    TransactionCostEngine,
    OrderContext,
    MarketContext,
    CostBreakdown,
)


def test_transaction_cost_engine_micro_price():
    # Bid 100 with depth 300, Ask 101 with depth 100 -> weighted closer to 101: (300*101 + 100*100) / 400 = 100.75
    micro_p = calculate_volume_weighted_micro_price(best_bid=100.0, best_ask=101.0, bid_depth=300.0, ask_depth=100.0)
    assert micro_p == 100.75

    engine = TransactionCostEngine()
    assert engine.calculate_micro_price(100.0, 101.0, 300.0, 100.0) == 100.75


def test_endogenous_dynamic_spread():
    # Baseline calm spread: $0.02
    # Current ATR = 2.0 (vs baseline 1.0 -> 100% vol spike)
    # Current Volume = 500 (vs baseline 1000 -> 50% liquidity depletion)
    s_normal = calculate_endogenous_dynamic_spread(
        base_spread=0.02,
        current_volatility=1.0,
        baseline_volatility=1.0,
        current_volume=1000.0,
        baseline_volume=1000.0,
    )
    assert pytest.approx(s_normal, 1e-4) == 0.02

    s_shock = calculate_endogenous_dynamic_spread(
        base_spread=0.02,
        current_volatility=2.0,
        baseline_volatility=1.0,
        current_volume=500.0,
        baseline_volume=1000.0,
        is_high_impact_event=True,
    )
    # Spreads widen significantly under shock + event blackout
    assert s_shock > s_normal * 2.0


def test_square_root_law_market_impact():
    impact = calculate_bouchaud_square_root_market_impact(
        daily_volatility=2.5,
        order_size_shares=1000.0,
        average_daily_volume_shares=100000.0,
        market_structure_constant_y=0.50,
    )
    assert impact > 0.0
    # Quadrupling volume doubles the square-root impact
    impact_4x = calculate_bouchaud_square_root_market_impact(
        daily_volatility=2.5,
        order_size_shares=4000.0,
        average_daily_volume_shares=100000.0,
        market_structure_constant_y=0.50,
    )
    assert pytest.approx(impact_4x, rel=1e-2) == impact * 2.0


def test_almgren_chriss_impact():
    perm, temp = calculate_almgren_chriss_price_impact(
        daily_volatility=0.02,
        arrival_price=100.0,
        order_size_shares=1000.0,
        average_daily_volume_shares=100000.0,
        bar_volume_shares=10000.0,
        execution_horizon_bars=1,
    )
    assert perm > 0.0
    assert temp > 0.0


def test_explicit_regulatory_and_broker_fees():
    # Buy 100 shares at $100 -> $10,000 notional: commission = 100 * 0.0035 = $0.35
    buy_fees = calculate_explicit_regulatory_and_broker_fees(
        order_size_shares=100.0,
        execution_notional=10000.0,
        side="BUY",
        asset_class="EQUITY",
    )
    assert pytest.approx(buy_fees, 1e-4) == 0.35

    # Sell 100 shares at $100 -> SEC fee + FINRA TAF added
    sell_fees = calculate_explicit_regulatory_and_broker_fees(
        order_size_shares=100.0,
        execution_notional=10000.0,
        side="SELL",
        asset_class="EQUITY",
    )
    assert sell_fees > buy_fees


def test_cost_breakdown_estimation():
    engine = TransactionCostEngine()
    order = OrderContext(
        symbol="XAUUSD",
        side="BUY",
        order_type="MARKET",
        shares=10.0,
        decision_price=2630.0,
    )
    market = MarketContext(
        best_bid=2629.80,
        best_ask=2630.20,
        bid_depth=50.0,
        ask_depth=50.0,
        daily_volume=500000.0,
        daily_volatility=15.0,
    )
    costs = engine.estimate_costs(order, market, one_r_dollar=100.0)
    assert isinstance(costs, CostBreakdown)
    assert costs.quoted_spread == 0.40
    assert costs.total_cost_dollar > 0.0
    assert costs.expected_execution_price > order.decision_price  # Buy market pays spread/impact


def test_implementation_shortfall():
    shortfall = calculate_implementation_shortfall_breakdown(
        decision_price=100.0,
        execution_price=100.15,
        order_size_shares=100.0,
        side="BUY",
        explicit_fees=2.0,
    )
    assert shortfall["slippage_dollar"] == 15.0
    assert shortfall["total_shortfall_dollar"] == 17.0
    assert shortfall["shortfall_basis_points"] == 17.0
