"""Smoke tests for offline PEARL-inspired / RL²-PPO Meta-RL trainer."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research.kronos_trading_integrator import MANAGEMENT_ACTIONS
from research.meta_rl_trainer import (
    DEFAULT_TASKS,
    MetaRLTrainer,
    task_key,
    train_meta_rl_offline,
)


def test_default_tasks_cover_regime_symbol_setup():
    assert len(DEFAULT_TASKS) >= 2
    keys = {task_key(t) for t in DEFAULT_TASKS}
    assert len(keys) == len(DEFAULT_TASKS)
    for task in DEFAULT_TASKS:
        assert task["symbol"]
        assert task["regime"]
        assert task["setup_id"]


def test_meta_rl_context_updates_without_authority():
    trainer = MetaRLTrainer(obs_dim=9, seed=1, use_torch=False)
    trainer.reset_context()
    assert np.allclose(trainer.context, 0.0)
    z = trainer.update_context([0.1] * 9, action=0, reward=0.5)
    assert z.shape == (trainer.latent_dim,)
    assert trainer._context_count == 1
    action, log_prob, value = trainer.act([0.0] * 9)
    assert 0 <= action < len(MANAGEMENT_ACTIONS)
    assert np.isfinite(log_prob)
    assert np.isfinite(value)


def test_train_meta_rl_offline_smoke_no_authority(tmp_path: Path):
    artifact = tmp_path / "meta_rl_smoke.json"
    # Tiny task set keeps the smoke test fast.
    tasks = [
        {"symbol": "EURUSD", "regime": "range", "setup_id": "mean_reversion_mgmt"},
        {"symbol": "GBPUSD", "regime": "volatile", "setup_id": "breakout_mgmt"},
    ]
    report = train_meta_rl_offline(
        tasks=tasks,
        updates=1,
        context_episodes=1,
        adapt_episodes=1,
        max_steps=6,
        seed=13,
        artifact_path=str(artifact),
        epochs=1,
        batch_size=16,
        use_torch=False,
    )
    assert report["status"] == "COMPLETED"
    assert report["execution_authority"] is False
    assert report["promotion_safe"] is False
    assert report["artifact_written"] is True
    assert report["training_data_hash"]
    assert report["artifact_sha256"]
    assert artifact.exists()
    assert len(report["task_rollup"]) == 2
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["execution_authority"] is False
    assert payload["promotion_safe"] is False
    assert payload["model_kind"].startswith("meta_rl_")
    assert payload["actions"] == list(MANAGEMENT_ACTIONS)


def test_train_meta_rl_offline_deterministic_seed(tmp_path: Path):
    tasks = [
        {"symbol": "EURUSD", "regime": "range", "setup_id": "smoke_setup"},
    ]
    kwargs = dict(
        tasks=tasks,
        updates=1,
        context_episodes=1,
        adapt_episodes=1,
        max_steps=5,
        seed=99,
        epochs=1,
        batch_size=8,
        use_torch=False,
    )
    a = train_meta_rl_offline(artifact_path=str(tmp_path / "a.json"), **kwargs)
    b = train_meta_rl_offline(artifact_path=str(tmp_path / "b.json"), **kwargs)
    assert a["training_data_hash"] == b["training_data_hash"]
    assert a["mean_adapt_reward"] == b["mean_adapt_reward"]
