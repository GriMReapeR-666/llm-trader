"""Tests for the Hierarchical Meta-Portfolio Model & Dynamic Multi-Regime Architecture."""
import numpy as np
import pytest

from core.hierarchical_portfolio import (
    RegimeState,
    DynamicAllocationResult,
    RegimeDetectionEngine,
    DynamicStrategyRouter,
    HierarchicalMetaPortfolioEngine,
)


def test_regime_detection_engine():
    detector = RegimeDetectionEngine()
    
    # Low-Vol Trend
    np.random.seed(123)
    prices = 100.0 + np.cumsum(np.random.normal(0.15, 0.2, 100))
    regime = detector.detect_regime(
        prices=prices,
        current_adx=28.0,
        current_atr=0.0010,
        atr_history=[0.0010] * 50,
        vix_or_vol_index=15.0,
    )
    assert regime.primary_regime in ("LOW_VOL_TREND", "LOW_VOL_RANGE")
    assert not regime.is_crisis
    assert sum(regime.probabilities.values()) == pytest.approx(1.0, 1e-2)


def test_dynamic_strategy_router():
    router = DynamicStrategyRouter(min_master_ev=0.20, portfolio_heat_cap_r=4.0)
    
    # State with clear trend
    regime = RegimeState(
        primary_regime="LOW_VOL_TREND",
        probabilities={"LOW_VOL_TREND": 0.80, "LOW_VOL_RANGE": 0.10, "HIGH_VOL_TREND": 0.05, "HIGH_VOL_CRISIS": 0.05},
        hurst_exponent=0.62,
        adx=30.0,
        atr_percentile=40.0,
        is_crisis=False,
    )

    allocation = router.route(regime, active_strategy_type="TREND_MOMENTUM")
    assert allocation.is_tradable
    assert allocation.master_expectancy_r >= 0.20
    assert allocation.strategy_weights["TREND_MOMENTUM"] > 0.50
    assert allocation.recommended_risk_multiplier >= 0.8


def test_crisis_regime_discard():
    router = DynamicStrategyRouter(min_master_ev=0.20)
    
    # High-Vol Crisis State
    regime = RegimeState(
        primary_regime="HIGH_VOL_CRISIS",
        probabilities={"LOW_VOL_TREND": 0.0, "LOW_VOL_RANGE": 0.0, "HIGH_VOL_TREND": 0.20, "HIGH_VOL_CRISIS": 0.80},
        hurst_exponent=0.50,
        adx=15.0,
        atr_percentile=98.0,
        is_crisis=True,
    )

    allocation = router.route(regime, active_strategy_type="TREND_MOMENTUM")
    assert not allocation.is_tradable
    assert allocation.recommended_risk_multiplier == 0.0
    assert "HIGH_VOL_CRISIS_REGIME_ACTIVE" in allocation.rejection_reasons


def test_hierarchical_meta_portfolio_engine_evaluation():
    engine = HierarchicalMetaPortfolioEngine(base_risk_pct=0.01, min_master_ev=0.20)
    
    np.random.seed(42)
    prices = 1.0800 + np.cumsum(np.random.normal(0.0002, 0.0005, 100))
    
    # Under normal conditions and low heat
    res = engine.evaluate(
        prices=prices,
        current_adx=26.0,
        current_atr=0.0015,
        strategy_type="TREND_MOMENTUM",
        open_portfolio_r=1.0,
        cross_strategy_correlation=0.20,
    )
    assert res["approved"] is True
    assert res["effective_risk_pct"] > 0.0

    # Under heat cap breach (open risk >= 4.0R)
    res_heat = engine.evaluate(
        prices=prices,
        current_adx=26.0,
        current_atr=0.0015,
        strategy_type="TREND_MOMENTUM",
        open_portfolio_r=4.5,
        cross_strategy_correlation=0.20,
    )
    assert res_heat["approved"] is False
    assert any("PORTFOLIO_HEAT_CAP" in r for r in res_heat["reasons"])

    # Under high cross-strategy correlation (r > 0.60 -> halved sizing)
    res_corr = engine.evaluate(
        prices=prices,
        current_adx=26.0,
        current_atr=0.0015,
        strategy_type="TREND_MOMENTUM",
        open_portfolio_r=1.0,
        cross_strategy_correlation=0.75,
    )
    assert res_corr["approved"] is True
    assert any("CROSS_CORRELATION_HALVED" in r for r in res_corr["reasons"])
    assert res_corr["applied_risk_multiplier"] < res["applied_risk_multiplier"]
