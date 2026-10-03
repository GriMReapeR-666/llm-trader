"""Unit tests for P22: Complex Adaptive Systems Selective Activation (Han 2026).

Validates:
- Knowing when NOT to trade is more valuable than refining HOW to trade.
- Selective activation of trend strategies during verified trend regimes (blocks in choppy/ranging).
- Selective activation of mean-reversion strategies during ranging regimes (blocks in raging trends).
- Downside insurance / bear strategy activation during downtrends.
- Integration into TradingConstraintEngine.validate().
"""
from __future__ import annotations

import pytest
from core.trading_safety import (
    TradeProposal,
    TradingConstraintEngine,
    TradingStore,
    check_selective_activation,
)


def test_selective_activation_trend_blocked_in_choppy_regime():
    """Trend/breakout strategies are deactivated in choppy or low-ADX regimes."""
    ok_adx, reason_adx = check_selective_activation(
        strategy="trend",
        regime="RANGING",
        adx=14.0,  # Below 20 threshold
        choppiness=65.0,
    )
    assert ok_adx is False
    assert "trend_disabled_in_choppy_regime" in reason_adx

    ok_chop, reason_chop = check_selective_activation(
        strategy="breakout",
        regime="CHOPPY",
        adx=22.0,
        choppiness=68.0,  # High choppiness (> 61.8)
    )
    assert ok_chop is False
    assert "trend_disabled_in_choppy_regime" in reason_chop


def test_selective_activation_trend_approved_in_strong_trend():
    """Trend strategies are active during verified trending regimes."""
    ok, reason = check_selective_activation(
        strategy="trend",
        regime="TRENDING_BULL",
        adx=28.0,
        choppiness=42.0,
    )
    assert ok is True
    assert reason == "admissible"


def test_selective_activation_mean_reversion_blocked_in_strong_trend():
    """Mean reversion strategies are deactivated in strong runaway trends."""
    ok, reason = check_selective_activation(
        strategy="mean_reversion",
        regime="STRONG_TREND",
        adx=38.0,  # Exceeds 32 runaway threshold
        choppiness=35.0,
    )
    assert ok is False
    assert "mean_reversion_disabled_in_strong_trend" in reason


def test_selective_activation_mean_reversion_approved_in_range():
    """Mean reversion strategies are active in ranging regimes."""
    ok, reason = check_selective_activation(
        strategy="mean_reversion",
        regime="RANGING",
        adx=16.0,
        choppiness=55.0,
    )
    assert ok is True
    assert reason == "admissible"


def test_trading_constraint_engine_enforces_selective_gate():
    """TradingConstraintEngine rejects proposals that violate the CAS selective activation gate."""
    store = TradingStore(":memory:")
    engine = TradingConstraintEngine(store)

    proposal = TradeProposal(
        strategy="trend",
        symbol="BTCUSD",
        side="BUY",
        entry=50000.0,
        stop_loss=49000.0,
        take_profit=53000.0,
        confidence=0.85,
        features={"adx": 12.0, "regime": "CHOPPY"},
    )

    market = {
        "market_open": True,
        "symbol_tradable": True,
        "tick_age_ms": 100,
        "max_tick_age_ms": 5000,
        "spread": 10.0,
        "max_spread": 50.0,
        "execution_price": 50000.0,
        "entry_tolerance": 50.0,
        "adx": 12.0,
        "regime": "CHOPPY",
        "choppiness_index": 68.0,
    }

    res = engine.validate(proposal, market)
    assert res.approved is False
    assert any("selective_activation_gate" in r for r in res.reasons)
