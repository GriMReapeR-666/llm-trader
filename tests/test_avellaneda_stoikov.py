"""Tests for Avellaneda-Stoikov reservation pricing and spread optimization."""
import math
import pytest

from core.avellaneda_stoikov import (
    AvellanedaStoikovEngine,
    AvellanedaStoikovQuotes,
    calculate_optimal_spread,
    calculate_reservation_price,
)


def test_reservation_price_inventory_symmetry():
    """Reservation price should equal mid-price at zero inventory and skew symmetrically."""
    mid = 1.1000
    vol = 0.01  # 1% vol
    horizon = 900.0  # 15 mins (0.25h)
    gamma = 0.5

    # Neutral inventory
    r_neutral = calculate_reservation_price(mid, inventory_units=0.0, gamma=gamma, volatility=vol, horizon_seconds=horizon)
    assert r_neutral == mid

    # Long inventory (q = +2): reservation price must be lower than mid (discount to incentivize selling)
    r_long = calculate_reservation_price(mid, inventory_units=2.0, gamma=gamma, volatility=vol, horizon_seconds=horizon)
    assert r_long < mid

    # Short inventory (q = -2): reservation price must be higher than mid (premium to incentivize buying)
    r_short = calculate_reservation_price(mid, inventory_units=-2.0, gamma=gamma, volatility=vol, horizon_seconds=horizon)
    assert r_short > mid

    # Symmetric displacement around mid
    diff_long = mid - r_long
    diff_short = r_short - mid
    assert math.isclose(diff_long, diff_short, rel_tol=1e-7)


def test_optimal_half_spread_properties():
    """Optimal half-spread must be positive and monotonically increase with volatility."""
    vol_low = 0.005
    vol_high = 0.020
    horizon = 900.0
    gamma = 0.5
    kappa = 1.5

    spread_low = calculate_optimal_spread(gamma=gamma, volatility=vol_low, horizon_seconds=horizon, kappa=kappa)
    spread_high = calculate_optimal_spread(gamma=gamma, volatility=vol_high, horizon_seconds=horizon, kappa=kappa)

    assert spread_low > 0.0
    assert spread_high > spread_low
    assert math.isfinite(spread_low)
    assert math.isfinite(spread_high)


def test_optimal_quotes_skew():
    """Verify that inventory skew shifts both optimal bid and ask quotes appropriately."""
    engine = AvellanedaStoikovEngine(gamma=0.5, kappa=1.5, default_horizon_seconds=900.0)
    mid = 100.0
    vol = 0.02

    quotes_neutral = engine.compute_quotes(mid_price=mid, inventory_units=0.0, volatility=vol)
    quotes_long = engine.compute_quotes(mid_price=mid, inventory_units=3.0, volatility=vol)
    quotes_short = engine.compute_quotes(mid_price=mid, inventory_units=-3.0, volatility=vol)

    assert quotes_neutral.reservation_price == mid
    assert quotes_neutral.optimal_bid < mid < quotes_neutral.optimal_ask

    # When long, both quotes shift down to attract buyers and discourage more buying
    assert quotes_long.optimal_bid < quotes_neutral.optimal_bid
    assert quotes_long.optimal_ask < quotes_neutral.optimal_ask

    # When short, both quotes shift up to attract sellers and discourage more shorting
    assert quotes_short.optimal_bid > quotes_neutral.optimal_bid
    assert quotes_short.optimal_ask > quotes_neutral.optimal_ask


def test_micro_price_limit_recommendation():
    """Verify recommended scalp limit pricing for passive maker execution."""
    engine = AvellanedaStoikovEngine(gamma=0.5, kappa=1.5, default_horizon_seconds=900.0)
    mid = 1.2500
    vol = 0.01

    # Buy order when already long (q = 2.0) -> limit price should be more conservative/lower
    buy_limit_long = engine.recommend_scalp_limit_price(side="BUY", mid_price=mid, inventory_units=2.0, volatility=vol)
    buy_limit_neutral = engine.recommend_scalp_limit_price(side="BUY", mid_price=mid, inventory_units=0.0, volatility=vol)
    assert buy_limit_long < buy_limit_neutral <= mid

    # Sell order when already short (q = -2.0) -> limit price should be higher
    sell_limit_short = engine.recommend_scalp_limit_price(side="SELL", mid_price=mid, inventory_units=-2.0, volatility=vol)
    sell_limit_neutral = engine.recommend_scalp_limit_price(side="SELL", mid_price=mid, inventory_units=0.0, volatility=vol)
    assert sell_limit_short > sell_limit_neutral >= mid


def test_finite_guard_and_fallbacks():
    """Edge cases such as zero/negative inputs should yield safe finite defaults."""
    res = calculate_reservation_price(mid_price=0.0, inventory_units=0.0, gamma=0.5, volatility=0.0, horizon_seconds=0.0)
    assert res == 0.0

    spread = calculate_optimal_spread(gamma=0.0, volatility=0.0, horizon_seconds=0.0, kappa=0.0)
    assert spread >= 0.0
    assert math.isfinite(spread)


def test_quotes_to_dict():
    """Verify serialization to dictionary."""
    quotes = AvellanedaStoikovQuotes(
        mid_price=1.2000,
        reservation_price=1.1995,
        optimal_half_spread=0.0008,
        optimal_bid=1.1987,
        optimal_ask=1.2003,
        inventory_skew_discount=-0.0005,
    )
    d = quotes.to_dict()
    assert d["mid_price"] == 1.2
    assert d["reservation_price"] == 1.1995
    assert d["optimal_half_spread"] == 0.0008
    assert d["optimal_bid"] == 1.1987
    assert d["optimal_ask"] == 1.2003
    assert d["inventory_skew_discount"] == -0.0005
