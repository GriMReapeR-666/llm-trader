import numpy as np
import pytest
from core.growth_and_ruin_engine import (
    SizingMode,
    GrowthSimulationResult,
    SmallCapitalGrowthSimulator,
)

def test_small_capital_compounding_fixed_fractional():
    sim = SmallCapitalGrowthSimulator()
    # 10 positive R trades followed by 5 negative R trades
    trade_rs = np.array([1.5, 2.0, -1.0, 1.2, 2.5, -1.0, 1.8, -1.0, 1.5, 2.0, -1.0, -1.0, 1.5, 2.0, -1.0])
    res = sim.simulate_path(
        starting_capital=1000.0,
        trade_rs=trade_rs,
        sizing_mode=SizingMode.FIXED_FRACTIONAL,
        base_risk_pct=0.015,
        kill_dd_pct=0.15,
    )
    assert isinstance(res, GrowthSimulationResult)
    assert res.execution_authority is False
    assert res.terminal_equity > 1000.0
    assert res.max_drawdown < 0.15
    assert res.is_ruined is False
    d = res.to_dict()
    assert d["starting_capital"] == 1000.0
    assert d["execution_authority"] is False

def test_small_capital_compounding_kill_switch():
    sim = SmallCapitalGrowthSimulator()
    # Severe consecutive losses to trigger 15% kill switch
    trade_rs = np.array([-1.0] * 20)
    res = sim.simulate_path(
        starting_capital=1000.0,
        trade_rs=trade_rs,
        sizing_mode=SizingMode.FIXED_FRACTIONAL,
        base_risk_pct=0.02,
        kill_dd_pct=0.15,
    )
    assert res.is_ruined is True
    assert res.max_drawdown >= 0.15
    assert len(res.equity_path) < 21  # Halted early

def test_small_capital_compounding_other_sizing_modes():
    sim = SmallCapitalGrowthSimulator(seed=42)
    trade_rs = [1.0, -1.0, 2.0, -0.5] * 3

    # Half Kelly
    res_hk = sim.simulate_path(
        starting_capital=1000.0,
        trade_rs=trade_rs,
        sizing_mode=SizingMode.HALF_KELLY,
        base_risk_pct=0.02,
    )
    assert isinstance(res_hk, GrowthSimulationResult)

    # Volatility Targeting
    vol_multipliers = [1.2, 0.8, 1.5, 0.5] * 3
    res_vt = sim.simulate_path(
        starting_capital=1000.0,
        trade_rs=trade_rs,
        sizing_mode=SizingMode.VOLATILITY_TARGETING,
        vol_multipliers=vol_multipliers,
    )
    assert isinstance(res_vt, GrowthSimulationResult)

    # Dynamic Leverage
    res_dl = sim.simulate_path(
        starting_capital=1000.0,
        trade_rs=trade_rs,
        sizing_mode=SizingMode.DYNAMIC_LEVERAGE,
    )
    assert isinstance(res_dl, GrowthSimulationResult)

def test_sequence_of_returns_risk():
    sim = SmallCapitalGrowthSimulator(seed=42)
    trade_rs = np.array([2.0, -1.0, 1.5, -1.0, 2.5, -1.0, 1.0, -1.0, 3.0, -1.0] * 5)
    seq_res = sim.simulate_sequence_risk(starting_capital=1000.0, trade_rs=trade_rs, n_permutations=100)
    assert "median_terminal_equity" in seq_res
    assert "p5_terminal_equity" in seq_res
    assert "prob_drawdown_over_15" in seq_res

def test_equity_drift_detection():
    sim = SmallCapitalGrowthSimulator()
    # Steady early, decaying late
    pnls = [100.0] * 20 + [-50.0] * 20
    is_degraded, decay_pct = sim.detect_equity_drift(pnls, window=20, degrade_threshold=0.25)
    assert is_degraded is True
    assert decay_pct >= 0.25

    # Short pnls (< 2 * window)
    is_deg_short, decay_short = sim.detect_equity_drift([10.0] * 10, window=20)
    assert is_deg_short is False
    assert decay_short == 0.0

    # Non-positive prior
    is_deg_neg, decay_neg = sim.detect_equity_drift([-10.0] * 20 + [5.0] * 20, window=20)
    assert is_deg_neg is False
    assert decay_neg == 0.0

def test_advanced_monte_carlo_engine():
    from core.growth_and_ruin_engine import AdvancedMonteCarloEngine, MonteCarloRuinResult

    mc = AdvancedMonteCarloEngine(seed=42)
    # Define trades in 3 regimes: BULL (high positive expectation), BEAR (mild positive), SHOCK (fat tails)
    trades_by_regime = {
        "BULL": np.array([0.02, 0.015, -0.01, 0.025, 0.01, -0.01]),
        "BEAR": np.array([0.01, -0.01, 0.012, -0.01, -0.005]),
        "SHOCK": np.array([-0.03, -0.02, 0.04, -0.04, -0.01]),
    }

    res = mc.run_simulation(
        trades_by_regime=trades_by_regime,
        starting_capital=1000.0,
        n_trades=100,
        n_simulations=500,
        target_multiplier=2.0,
    )
    assert isinstance(res, MonteCarloRuinResult)
    assert res.execution_authority is False
    assert 0.0 <= res.probability_of_ruin <= 1.0
    assert 0.0 <= res.probability_dd_over_20 <= 1.0
    assert 0.0 <= res.confidence_target_flip <= 1.0
    assert res.median_terminal_equity > 0.0
    d = res.to_dict()
    assert d["starting_capital"] == 1000.0
    assert d["execution_authority"] is False


def test_account_flip_controller():
    from core.growth_and_ruin_engine import (
        AccountFlipController,
        AccountFlipState,
        RiskBucket,
    )

    ctrl = AccountFlipController(starting_capital=1000.0, flip_target_mult=2.0)
    assert ctrl.state.starting_capital == 1000.0
    assert ctrl.get_effective_risk_multiplier() == 1.0

    allocs = ctrl.get_bucket_allocations()
    assert allocs[RiskBucket.CORE] == 0.50
    assert allocs[RiskBucket.GROWTH] == 0.30
    assert allocs[RiskBucket.HEDGE] == 0.20

    # 10% Drawdown triggers 50% risk halving
    state = ctrl.update_equity(900.0)
    assert ctrl.get_effective_risk_multiplier() == 0.50
    assert state.active_drawdown >= 0.10

    # 15% Drawdown disables GROWTH bucket
    state = ctrl.update_equity(850.0)
    assert ctrl.get_effective_risk_multiplier() == 0.25
    allocs = ctrl.get_bucket_allocations()
    assert allocs[RiskBucket.GROWTH] == 0.0

    # 20% Drawdown triggers emergency halt
    state = ctrl.update_equity(790.0)
    assert state.is_halted is True

    # Equity Flip Trigger at 2x ($2,000)
    ctrl_flip = AccountFlipController(starting_capital=1000.0, flip_target_mult=2.0)
    state_flip = ctrl_flip.update_equity(2050.0)
    assert state_flip.flip_count == 1
    assert state_flip.starting_capital == 2050.0  # Reset baseline
    d = state_flip.to_dict()
    assert d["flip_count"] == 1
    assert d["execution_authority"] is False
