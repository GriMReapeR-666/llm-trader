"""Unit tests for kronos_trading_integrator.py covering all 4 live trading tasks."""
import pytest
from kronos_trading_integrator import KronosTradingIntegrator, KronosRLMarketSimulator
from rl_trainer import RLTrainer


def test_task_1_alpha_factor():
    """Verify Task 1: Alpha Factor calculation for multi-factor ensemble."""
    integrator = KronosTradingIntegrator(force_heuristic=True)
    ohlcv = [
        {"open": 100.0 + i, "high": 102.0 + i, "low": 99.0 + i, "close": 101.5 + i, "volume": 1000.0}
        for i in range(20)
    ]
    alpha = integrator.get_alpha_factor(symbol="AAPL", ohlcv_history=ohlcv, pred_len=12)

    assert isinstance(alpha, dict)
    assert alpha["symbol"] == "AAPL"
    assert -1.0 <= alpha["alpha_score"] <= 1.0
    assert 0.0 <= alpha["confidence"] <= 1.0
    assert alpha["direction"] in ["BUY", "SELL", "NEUTRAL"]
    assert alpha["execution_authority"] is False
    assert alpha["backend"] == "heuristic"


def test_task_2_volatility_input_for_risk():
    """Verify Task 2: Volatility prediction for RiskManager."""
    integrator = KronosTradingIntegrator(force_heuristic=True)
    ohlcv = [
        {"open": 2000.0 + i * 2, "high": 2010.0 + i * 2, "low": 1990.0 - i, "close": 2005.0 + i, "volume": 500.0}
        for i in range(20)
    ]
    vol_input = integrator.get_volatility_input(symbol="XAUUSD", ohlcv_history=ohlcv)

    assert isinstance(vol_input, dict)
    assert vol_input["symbol"] == "XAUUSD"
    assert vol_input["predicted_volatility_5d"] > 0
    assert vol_input["volatility_regime"] in ["LOW", "NORMAL", "ELEVATED", "HIGH"]
    assert 0.5 <= vol_input["risk_adjustment_factor"] <= 1.0
    assert vol_input["execution_authority"] is False


def test_task_3_synthetic_robustness_suite():
    """Verify Task 3: Synthetic data generator for robustness testing."""
    integrator = KronosTradingIntegrator(force_heuristic=True)
    suite = integrator.generate_robustness_suite(symbol="BTCUSD", current_price=50000.0, length=30)

    assert isinstance(suite, dict)
    assert "bull" in suite
    assert "bear" in suite
    assert "volatile" in suite
    assert "range" in suite
    assert len(suite["bull"]) == 30


def test_task_4_rl_market_simulator():
    """Verify Task 4: Interactive Market Simulator for RL Agent training."""
    sim = KronosRLMarketSimulator(symbol="EURUSD", regime="volatile", max_steps=20)
    obs = sim.reset()

    assert obs["step"] == 0
    assert obs["balance"] == 10000.0
    assert obs["position"] == 0.0
    assert obs["execution_authority"] is False

    # Step BUY
    obs, reward, done, info = sim.step("BUY")
    assert obs["step"] == 1
    assert obs["position"] == 1.0
    assert isinstance(reward, float)
    assert done is False
    assert info["execution_authority"] is False

    # Step HOLD
    obs, reward, done, info = sim.step("HOLD")
    assert obs["step"] == 2
    assert done is False

    state = sim.observation_to_rl_state(obs)
    assert "rsi_14" in state
    assert state["execution_authority"] is False


def test_rl_trainer_offline_kronos_hook(tmp_path):
    """RLTrainer must train offline on KronosRLMarketSimulator without execution authority."""
    model_path = tmp_path / "kronos_rl.json"
    trainer = RLTrainer(model_path=str(model_path))
    result = trainer.train_on_kronos_simulator(episodes=2, max_steps=8, explore=True, save=False)
    assert result["status"] == "COMPLETED"
    assert result["episodes"] == 2
    assert result["execution_authority"] is False
    assert result["stats"]["total_updates"] >= 2
    assert trainer.total_updates >= 2


def test_advisory_bundle_never_has_execution_authority():
    integrator = KronosTradingIntegrator(force_heuristic=True)
    ohlcv = [
        {"open": 1.0 + i * 0.01, "high": 1.02 + i * 0.01, "low": 0.99 + i * 0.01,
         "close": 1.01 + i * 0.01, "volume": 100.0}
        for i in range(16)
    ]
    bundle = integrator.advisory_bundle("EURUSD", ohlcv, pred_len=6)
    assert bundle["execution_authority"] is False
    assert "alpha" in bundle and "volatility" in bundle
