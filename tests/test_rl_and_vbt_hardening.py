import math
import numpy as np
import pandas as pd
import pytest

from research.vbt_engine import VectorizedBacktestEngine, BacktestMetrics
from research.grpo_proposer import GRPOProposerTrainer, synthesize_contexts
from operations.reward_sustainability import cvar, cvar_mixture_objective


def test_vbt_advanced_metrics_calculation():
    # Construct 100 bars of trending price series with simulated pullbacks
    np.random.seed(42)
    t = np.linspace(0, 10, 100)
    prices = 100.0 + np.sin(t) * 3.0 + np.linspace(0, 20, 100)

    df = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=100, freq="15min"),
        "close": prices,
    })

    engine = VectorizedBacktestEngine(
        initial_cash=10_000.0,
        commission_pct=0.0002,
        annual_risk_free_rate=0.04,
    )

    metrics = engine.run_crossover_strategy(
        df=df,
        fast_period=5,
        slow_period=15,
        symbol="EURUSD",
        timeframe="M15",
    )

    assert isinstance(metrics, BacktestMetrics)
    d = metrics.to_dict()

    # Verify advanced institutional metrics are present and finite
    assert "var_95_pct" in d
    assert "cvar_95_pct" in d
    assert "ulcer_index" in d
    assert "upi_martin_ratio" in d
    assert "gain_to_pain_ratio" in d
    assert "tail_ratio" in d

    assert math.isfinite(d["var_95_pct"])
    assert math.isfinite(d["cvar_95_pct"])
    assert d["cvar_95_pct"] >= d["var_95_pct"] or d["cvar_95_pct"] == 0.0
    assert d["ulcer_index"] >= 0.0
    assert math.isfinite(d["upi_martin_ratio"])
    assert d["gain_to_pain_ratio"] >= 0.0
    assert d["tail_ratio"] > 0.0


def test_grpo_trainer_group_advantages_and_update():
    trainer = GRPOProposerTrainer(lr=1e-2, seed=42)
    contexts = synthesize_contexts(4, seed=42)

    # Collect groups of trajectories
    groups = [trainer.collect_group(ctx, group_size=4) for ctx in contexts]
    assert len(groups) == 4
    for g in groups:
        assert len(g["samples"]) == 4
        assert len(g["advantages"]) == 4
        assert g["execution_authority"] is False
        assert g["risk_controller_frozen"] is True

    # Execute policy gradient update
    metrics = trainer.update(groups, epochs=2)
    assert "policy_loss" in metrics
    assert "entropy" in metrics
    assert math.isfinite(metrics["policy_loss"])
    assert math.isfinite(metrics["entropy"])
    assert metrics["n"] == 16.0


def test_cvar_sustainability_objective_finite_safeguards():
    # Empty rewards list
    res_empty = cvar([])
    assert res_empty["status"] == "INSUFFICIENT_DATA"
    assert res_empty["cvar"] is None

    # Invalid alpha
    res_invalid = cvar([1.0, 2.0], alpha=-0.5)
    assert res_invalid["status"] == "INVALID"

    # Finite mix
    rewards = [1.5, -1.0, 2.0, -0.5, 3.0, -2.5, 0.8, -1.2, 1.1, -0.4]
    mix = cvar_mixture_objective(rewards, alpha=0.10, mean_weight=0.5)
    assert mix["status"] == "OK"
    assert math.isfinite(mix["objective"])
    assert mix["execution_authority"] is False
