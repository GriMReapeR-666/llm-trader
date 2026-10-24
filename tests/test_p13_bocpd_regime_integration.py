"""Tests for P1-3: BOCPD Changepoint Posterior Integration into RegimeDetectionEngine in core/hierarchical_portfolio.py."""
import pytest
import numpy as np

from core.hierarchical_portfolio import RegimeDetectionEngine, RegimeState


def test_regime_state_fields():
    engine = RegimeDetectionEngine()
    prices = [1.1000 + i * 0.0001 for i in range(50)]
    state = engine.detect_regime(
        prices=prices,
        current_adx=28.0,
        current_atr=0.0015,
        bocpd_changepoint_prob=0.15,
    )
    assert isinstance(state, RegimeState)
    assert hasattr(state, "bocpd_changepoint_prob")
    assert hasattr(state, "changepoint_detected")
    assert state.bocpd_changepoint_prob == 0.15
    assert state.changepoint_detected is False


def test_bocpd_changepoint_shifts_probabilities_under_volatility():
    engine = RegimeDetectionEngine()
    prices = [1.1000 + (i % 5) * 0.0005 for i in range(50)]

    # Normal state without changepoint (high vol)
    state_no_cp = engine.detect_regime(
        prices=prices,
        current_adx=26.0,
        atr_history=[0.0010] * 50,
        current_atr=0.0020,  # elevated ATR
        vix_or_vol_index=26.0,
        bocpd_changepoint_prob=0.05,
    )

    # State with active high changepoint probability
    state_cp = engine.detect_regime(
        prices=prices,
        current_adx=26.0,
        atr_history=[0.0010] * 50,
        current_atr=0.0020,
        vix_or_vol_index=26.0,
        bocpd_changepoint_prob=0.85,
    )

    assert state_cp.changepoint_detected is True
    # Crisis probability must be higher with changepoint active
    assert state_cp.probabilities["HIGH_VOL_CRISIS"] > state_no_cp.probabilities["HIGH_VOL_CRISIS"]


def test_auto_bocpd_detection_from_abrupt_price_shift():
    engine = RegimeDetectionEngine()
    # Baseline stable price series
    prices = [1.1000] * 30
    for p in prices:
        state = engine.detect_regime([p] * 10)
    
    # Abrupt regime shift (large shock)
    shock_prices = [1.1000] * 20 + [1.1400]
    shock_state = engine.detect_regime(shock_prices)
    assert shock_state.bocpd_changepoint_prob > 0.20
