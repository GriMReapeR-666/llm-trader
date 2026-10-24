import numpy as np
import polars as pl
import pytest

from core.constrained_rl_policy import (
    RLStateFeatureExtractor,
    SafetyInvariantsGuard,
    RLAction,
    compute_risk_adjusted_reward,
    ConstrainedPolicyAgent,
    DeterministicBaselineStrategy,
    ConstrainedRLPipeline,
    ConstrainedRLReport,
)


def test_rl_state_feature_extractor():
    np.random.seed(42)
    n = 100
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = pl.DataFrame({
        "close": prices,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "volume": np.full(n, 1000.0),
    })

    extractor = RLStateFeatureExtractor()
    states = extractor.extract(df)

    assert isinstance(states, np.ndarray)
    assert states.shape == (n, 8)
    assert not np.isnan(states).any()
    assert not np.isinf(states).any()


def test_safety_invariants_guard_limits_and_veto():
    guard = SafetyInvariantsGuard(max_drawdown_limit=0.10, max_exposure_cap=1.0)

    # 1. Normal state: allow position sizing up to 1.0
    safe_act, safe_exp = guard.apply_guard(
        raw_action=RLAction.ADD_EXPOSURE,
        current_exposure=0.5,
        current_drawdown=0.04,
    )
    assert safe_act == RLAction.ADD_EXPOSURE
    assert safe_exp == 0.75

    # 2. Exposure cap: trying to exceed 1.0 gets clamped
    safe_act_cap, safe_exp_cap = guard.apply_guard(
        raw_action=RLAction.ADD_EXPOSURE,
        current_exposure=0.9,
        current_drawdown=0.02,
    )
    assert safe_exp_cap == 1.0

    # 3. Drawdown breach (> 10% limit): forced FLATTEN
    safe_act_dd, safe_exp_dd = guard.apply_guard(
        raw_action=RLAction.ADD_EXPOSURE,
        current_exposure=0.75,
        current_drawdown=0.12,  # Breached 10% limit
    )
    assert safe_act_dd == RLAction.FLATTEN
    assert safe_exp_dd == 0.0


def test_risk_adjusted_reward_shaping():
    # Scenario A: Steady positive return with no drawdown expansion
    reward_a = compute_risk_adjusted_reward(
        period_return=0.01,
        drawdown_expansion=0.0,
        turnover=0.0,
        rolling_volatility=0.01,
    )
    assert reward_a > 0.0

    # Scenario B: Positive nominal return but severe drawdown expansion (-8%) and high turnover
    reward_b = compute_risk_adjusted_reward(
        period_return=0.005,
        drawdown_expansion=0.08,
        turnover=1.0,
        rolling_volatility=0.03,
    )
    # The penalty for drawdown expansion and turnover friction must turn net reward negative
    assert reward_b < 0.0
    assert reward_a > reward_b


def test_constrained_rl_pipeline_and_baseline_comparison():
    np.random.seed(42)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = pl.DataFrame({
        "close": prices,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "volume": np.full(n, 1000.0),
    })

    pipeline = ConstrainedRLPipeline(emit_telemetry=True)
    report = pipeline.evaluate_policy(df, symbol="EURUSD", timeframe="H1")

    assert isinstance(report, ConstrainedRLReport)
    assert report.symbol == "EURUSD"
    assert report.timeframe == "H1"
    assert report.total_bars == n
    assert report.execution_authority is False
    assert hasattr(report, "rl_metrics")
    assert hasattr(report, "baseline_metrics")
    assert hasattr(report, "sharpe_outperformance")
    assert hasattr(report, "drawdown_reduction_pct")
    assert hasattr(report, "gate_passed")
    assert isinstance(report.gate_passed, bool)
    assert isinstance(report.rejection_reasons, list)
