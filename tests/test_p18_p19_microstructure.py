"""Unit tests for P18 & P19: Microstructure, Phantom Liquidity Guard & Price Impact Model.

References:
- P18: Fuli Yang, Shiyu Lin, Jing Huang (2026), "Artificial Intelligence, Algorithmic Trading,
  and Financial Market Microstructure: A Systematic Literature Review" (GARCH Institute / Harvard).
- P19: Snehal Banerjee & Martin Szydlowski (2026), "Algorithmic Trading Architecture and
  Price Informativeness."

Validates:
1. Phantom liquidity detection: Identifies quote-stuffing and sudden depth evaporation.
2. Price impact estimation: Square-root and Kyle lambda impact formulation.
3. Order size throttling: Caps participation rate to protect against adverse selection.
4. Algorithmic herding circuit breaker: Detects synchronized order clustering.
"""
from __future__ import annotations

import pytest

from core.market_structure_edges import (
    HerdingAlertResult,
    PhantomLiquidityResult,
    PriceImpactResult,
    check_herding_circuit_breaker,
    detect_phantom_liquidity,
    estimate_price_impact_and_throttle,
)


def test_phantom_liquidity_guard_normal_conditions():
    """Verify normal order book conditions pass phantom liquidity guard."""
    res = detect_phantom_liquidity(
        quote_count=150,
        trade_count=20,
        cancellation_count=40,
        depth_top5_bids=500.0,
        depth_top5_asks=480.0,
        prev_depth_top5=490.0,
    )
    assert res.is_phantom is False
    assert res.safe_to_execute is True
    assert res.phantom_risk_level == "LOW"
    assert res.quote_to_trade_ratio == 7.5


def test_phantom_liquidity_guard_detects_evaporation():
    """Verify quote-stuffing or depth collapse triggers phantom liquidity block."""
    # Scenario A: Extreme quote-to-trade ratio (quote stuffing)
    res_stuffing = detect_phantom_liquidity(
        quote_count=2500,
        trade_count=10,
        cancellation_count=2400,
        depth_top5_bids=300.0,
        depth_top5_asks=300.0,
        prev_depth_top5=310.0,
    )
    assert res_stuffing.is_phantom is True
    assert res_stuffing.safe_to_execute is False
    assert res_stuffing.quote_to_trade_ratio == 250.0
    assert "quote_stuffing" in res_stuffing.reasons

    # Scenario B: Sudden depth collapse (> 60% evaporation)
    res_collapse = detect_phantom_liquidity(
        quote_count=100,
        trade_count=30,
        cancellation_count=20,
        depth_top5_bids=80.0,
        depth_top5_asks=80.0,
        prev_depth_top5=500.0,  # Depth evaporated from 500 to 160
    )
    assert res_collapse.is_phantom is True
    assert res_collapse.safe_to_execute is False
    assert "depth_evaporation" in res_collapse.reasons


def test_price_impact_model_within_tolerance():
    """Verify normal order size incurs minimal impact and is approved without throttling."""
    res = estimate_price_impact_and_throttle(
        proposed_size=1.0,         # 1 lot
        bar_volume_adv=1000.0,     # ADV 1,000 lots -> 0.1% participation
        spot_price=2000.0,
        daily_volatility=0.015,
        max_slippage_points=2.0,
        max_participation_rate=0.02, # 2% cap
    )
    assert res.approved is True
    assert res.throttled_size == pytest.approx(1.0)
    assert res.expected_impact_points < 2.0
    assert res.participation_rate == pytest.approx(0.001)


def test_price_impact_model_throttles_oversized_order():
    """Verify oversized orders exceeding participation or slippage limit are throttled."""
    # Scenario A: Participation rate binds (max 2% of 1,000 = 20.0 lots)
    res_part = estimate_price_impact_and_throttle(
        proposed_size=50.0,        # 50 lots
        bar_volume_adv=1000.0,     # 5% participation > 2% max cap
        spot_price=2000.0,
        daily_volatility=0.02,
        max_slippage_points=5.0,   # Generous slippage budget so participation cap binds
        max_participation_rate=0.02, # Max 20 lots allowed
    )
    assert res_part.throttled_size == pytest.approx(20.0)
    assert res_part.is_throttled is True
    assert "participation_rate_exceeded" in res_part.reasons

    # Scenario B: Slippage budget binds (tight slippage 1.5 points limits size to 5.625 lots)
    res_slip = estimate_price_impact_and_throttle(
        proposed_size=50.0,
        bar_volume_adv=1000.0,
        spot_price=2000.0,
        daily_volatility=0.02,
        max_slippage_points=1.5,
        max_participation_rate=0.05,
    )
    assert res_slip.throttled_size == pytest.approx(5.625)
    assert res_slip.is_throttled is True
    assert "price_impact_exceeds_slippage_cap" in res_slip.reasons


def test_herding_circuit_breaker():
    """Verify correlated directional flow triggers herding circuit breaker."""
    # Normal mixed flow
    res_normal = check_herding_circuit_breaker(
        recent_order_directions=["BUY", "SELL", "BUY", "SELL", "BUY"],
        order_arrival_intervals_ms=[1200, 950, 1500, 1100],
    )
    assert res_normal.circuit_breaker_active is False
    assert res_normal.herding_score < 0.70

    # Toxic synchronized buy clustering (all BUY within sub-100ms intervals)
    res_herding = check_herding_circuit_breaker(
        recent_order_directions=["BUY", "BUY", "BUY", "BUY", "BUY", "BUY"],
        order_arrival_intervals_ms=[45, 30, 50, 25, 40],
    )
    assert res_herding.circuit_breaker_active is True
    assert res_herding.herding_score >= 0.80
    assert "directional_clustering" in res_herding.alert_reasons
