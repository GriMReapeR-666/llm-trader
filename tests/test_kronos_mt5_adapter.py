"""Unit tests for kronos_mt5_adapter.py covering all 4 MT5 technical data tasks."""
import pytest
from kronos_mt5_adapter import KronosMT5Adapter, MT5KronosRLSimulator


def test_mt5_rates_normalization():
    """Verify normalization of MT5 rates tuples and dicts."""
    raw_tuples = [
        (1700000000, 1.0850, 1.0870, 1.0840, 1.0865, 1200, 15, 1800),
        (1700003600, 1.0865, 1.0890, 1.0860, 1.0880, 1400, 12, 2100),
    ]
    rates = KronosMT5Adapter.normalize_mt5_rates(raw_tuples)
    assert len(rates) == 2
    assert rates[0]["open"] == 1.0850
    assert rates[0]["close"] == 1.0865
    assert rates[0]["volume"] == 1200.0


def test_task_1_mt5_alpha_factor():
    """Verify Task 1: MT5 Alpha Factor incorporating DOM imbalance."""
    adapter = KronosMT5Adapter(force_heuristic=True)
    raw_tuples = [
        (1700000000 + i * 3600, 1.0800 + i * 0.001, 1.0820 + i * 0.001, 1.0790 + i * 0.001, 1.0815 + i * 0.001, 1000, 10, 1500)
        for i in range(20)
    ]
    dom = {"dom_depth": 10, "spread": 12.0, "imbalance_rate": 0.45}

    alpha = adapter.get_mt5_alpha_factor("EURUSD", raw_tuples, dom=dom)
    assert isinstance(alpha, dict)
    assert alpha["symbol"] == "EURUSD"
    assert -1.0 <= alpha["composite_alpha_score"] <= 1.0
    assert alpha["direction"] in ["BUY", "SELL", "NEUTRAL"]
    assert alpha["dom_imbalance_modifier"] > 0.0


def test_task_2_mt5_volatility_risk():
    """Verify Task 2: MT5 Volatility prediction and recommended lot size."""
    adapter = KronosMT5Adapter(force_heuristic=True)
    raw_tuples = [
        (1700000000 + i * 3600, 1.0800 + i * 0.001, 1.0850 + i * 0.001, 1.0750 - i * 0.001, 1.0820 + i * 0.001, 1000, 10, 1500)
        for i in range(20)
    ]

    vol_risk = adapter.get_mt5_volatility_risk("EURUSD", raw_tuples, account_balance=10000.0, risk_per_trade_pct=1.0)
    assert isinstance(vol_risk, dict)
    assert vol_risk["symbol"] == "EURUSD"
    assert vol_risk["predicted_volatility_5d"] > 0
    assert vol_risk["recommended_lots"] > 0.0


def test_task_3_synthetic_mt5_rates():
    """Verify Task 3: Synthetic MT5 rates generator matching copy_rates_from_pos schema."""
    adapter = KronosMT5Adapter(force_heuristic=True)
    mt5_rates = adapter.generate_synthetic_mt5_rates("GBPUSD", start_price=1.2600, length=25, regime="bull")

    assert len(mt5_rates) == 25
    assert "time" in mt5_rates[0]
    assert "open" in mt5_rates[0]
    assert "tick_volume" in mt5_rates[0]
    assert "spread" in mt5_rates[0]


def test_task_4_mt5_rl_simulator():
    """Verify Task 4: Interactive MT5 RL Simulator with DOM depth & spread."""
    sim = MT5KronosRLSimulator("EURUSD", initial_balance=10000.0, regime="volatile", max_steps=15)
    obs = sim.reset()

    assert obs["step"] == 0
    assert "dom" in obs
    assert obs["dom"]["dom_depth"] == 10

    obs, reward, done, info = sim.step("BUY")
    assert obs["step"] == 1
    assert obs["position"] == 1.0
    assert isinstance(reward, float)
