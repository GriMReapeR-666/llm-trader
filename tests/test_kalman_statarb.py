"""Unit tests for Module 05: Dynamic Kalman Filter Statistical Arbitrage Alpha Engine."""
import numpy as np
import pandas as pd
import pytest

from core.kalman_statarb import (
    estimate_spread_mean_reversion_half_life,
    evaluate_statarb_state_machine,
    DynamicKalmanStatArbEngine,
    DynamicKalmanStatArb,
    KalmanState,
)


def test_estimate_spread_mean_reversion_half_life():
    np.random.seed(42)
    # Generate AR(1) mean-reverting residuals: e_t = 0.6 * e_{t-1} + noise
    # theta = 0.6 - 1.0 = -0.40 -> tau_1/2 = -ln(2) / -0.40 ~ 1.73 bars
    e = np.zeros(100)
    for t in range(1, 100):
        e[t] = 0.6 * e[t - 1] + np.random.normal(0, 0.1)

    hl = estimate_spread_mean_reversion_half_life(e)
    assert 1.0 <= hl <= 5.0


def test_evaluate_statarb_state_machine():
    # 1. Neutral to Long Entry
    sig, pos = evaluate_statarb_state_machine(
        z_score=-2.5,
        current_position=0,
        holding_bars=0,
        half_life_bars=10.0,
    )
    assert sig == "BUY_SPREAD"
    assert pos == 1

    # 2. Long Spread to Target Exit
    sig_exit, pos_exit = evaluate_statarb_state_machine(
        z_score=0.10,
        current_position=1,
        holding_bars=5,
        half_life_bars=10.0,
    )
    assert sig_exit == "EXIT_TARGET"
    assert pos_exit == 0

    # 3. Long Spread to Stop Loss
    sig_sl, pos_sl = evaluate_statarb_state_machine(
        z_score=-3.5,
        current_position=1,
        holding_bars=5,
        half_life_bars=10.0,
    )
    assert sig_sl == "STOP_LOSS"
    assert pos_sl == 0

    # 4. Long Spread to Half-Life Time Stop (holding > 2 * 10 = 20 bars)
    sig_time, pos_time = evaluate_statarb_state_machine(
        z_score=-1.0,
        current_position=1,
        holding_bars=25,
        half_life_bars=10.0,
    )
    assert sig_time == "EXIT_TIME_STOP"
    assert pos_time == 0


def test_kalman_statarb_state_update():
    engine = DynamicKalmanStatArbEngine(delta=1e-4, measurement_var=1e-2)
    # Synthetic cointegrated relationship: y = 5.0 + 1.5 * x + noise
    np.random.seed(42)
    for _ in range(50):
        x = np.random.uniform(10.0, 20.0)
        y = 5.0 + 1.5 * x + np.random.normal(0, 0.2)
        state = engine.update_state_space(y, x)

    assert isinstance(state, KalmanState)
    assert pytest.approx(state.beta, abs=0.2) == 1.5
    assert state.ou_half_life_bars > 0.0


def test_kalman_statarb_state_machine_signals():
    engine = DynamicKalmanStatArb(z_entry_threshold=2.0, z_exit_threshold=0.20, z_stop_threshold=3.0)
    # Feed baseline observations to initialize
    for _ in range(30):
        engine.update(100.0, 100.0)

    # Extreme positive divergence in Y -> Sell spread signal
    state_high = engine.update(150.0, 100.0)
    assert state_high.spread_z_score > 2.0
    assert state_high.state_machine_signal == "SELL_SPREAD"

    # Divergence returns to mean -> Target exit signal
    state_revert = engine.update(100.0, 100.0)
    assert state_revert.state_machine_signal in ("EXIT_TARGET", "FLAT")


def test_kalman_statarb_historical_backtest():
    np.random.seed(42)
    n = 150
    x = 100.0 + np.cumsum(np.random.normal(0, 1, n))
    e = np.zeros(n)
    for t in range(1, n):
        e[t] = 0.5 * e[t - 1] + np.random.normal(0, 0.5)
    y = 2.0 * x + e

    df_pairs = pd.DataFrame({"y": y, "x": x})
    engine = DynamicKalmanStatArbEngine()
    results = engine.execute_historical_backtest(df_pairs, initial_capital=100000.0)

    assert "total_trades" in results
    assert "win_rate" in results
    assert "expected_value_r" in results
    assert "sqn" in results
    assert results["final_equity"] > 0.0
