"""Smoke tests for offline PPO + GAE position-management trainer."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research.kronos_trading_integrator import MANAGEMENT_ACTIONS, KronosRLMarketSimulator
from research.ppo_trainer import PPOTrainer, compute_gae, train_ppo_offline


def test_management_actions_documented():
    assert MANAGEMENT_ACTIONS == ("HOLD", "TIGHTEN_SL", "TRAIL", "PARTIAL_CLOSE", "FULL_EXIT")


def test_simulator_management_actions_smoke():
    sim = KronosRLMarketSimulator(symbol="EURUSD", regime="range", max_steps=20, seed=3)
    obs = sim.open_management_episode(direction=1.0, seed=3)
    assert obs["position"] == 1.0
    assert obs["execution_authority"] is False
    assert obs["stop_loss"] is not None

    for action in ("HOLD", "TIGHTEN_SL", "TRAIL", "PARTIAL_CLOSE", "FULL_EXIT"):
        if abs(float(obs.get("position") or 0.0)) < 1e-9 and action != "FULL_EXIT":
            break
        before = abs(float(obs.get("position") or 0.0))
        trail_before = bool(obs.get("trail_active"))
        obs, reward, done, info = sim.step(action)
        assert isinstance(reward, float)
        assert info["execution_authority"] is False
        if action == "TRAIL" and before > 0 and not info.get("stop_hit"):
            assert obs["trail_active"] is True or trail_before
        if action == "PARTIAL_CLOSE" and before > 0 and not info.get("stop_hit"):
            assert abs(float(obs["position"])) <= before * 0.5 + 1e-9
        if action == "FULL_EXIT":
            assert abs(float(obs["position"])) < 1e-9

    # Force a fresh episode that survives to FULL_EXIT for the flatten guarantee.
    sim2 = KronosRLMarketSimulator(symbol="EURUSD", regime="range", max_steps=20, seed=99)
    obs = sim2.open_management_episode(direction=1.0, seed=99)
    obs, reward, done, info = sim2.step("FULL_EXIT")
    assert abs(obs["position"]) < 1e-9
    assert info["execution_authority"] is False
    assert done is True


def test_gae_shapes_and_bootstrap():
    rewards = [1.0, 0.0, -1.0]
    values = [0.5, 0.25, 0.0]
    dones = [False, False, True]
    adv, ret = compute_gae(rewards, values, dones, gamma=0.99, lam=0.95)
    assert adv.shape == (3,)
    assert ret.shape == (3,)
    assert np.isfinite(adv).all()
    assert np.isfinite(ret).all()


def test_train_ppo_offline_smoke_no_authority(tmp_path: Path):
    artifact = tmp_path / "ppo_smoke.json"
    report = train_ppo_offline(
        episodes=2,
        updates=1,
        max_steps=8,
        seed=11,
        artifact_path=str(artifact),
        epochs=1,
        batch_size=16,
    )
    assert report["status"] == "COMPLETED"
    assert report["execution_authority"] is False
    assert report["promotion_safe"] is False
    assert report["artifact_written"] is True
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["execution_authority"] is False
    assert payload["promotion_safe"] is False
    assert payload["actions"] == list(MANAGEMENT_ACTIONS)
    assert report["training_data_hash"]
    assert report["artifact_sha256"]
    assert report["session_rollup"]["execution_authority"] is False
    assert report["configuration"]["seed"] == 11


def test_train_ppo_offline_deterministic_seed(tmp_path: Path):
    kwargs = dict(
        episodes=2,
        updates=1,
        max_steps=6,
        seed=42,
        epochs=1,
        batch_size=8,
        use_torch=False,  # numpy path is fully RNG-controlled
    )
    a = train_ppo_offline(artifact_path=str(tmp_path / "a.json"), **kwargs)
    b = train_ppo_offline(artifact_path=str(tmp_path / "b.json"), **kwargs)
    assert a["training_data_hash"] == b["training_data_hash"]
    assert a["mean_episode_reward"] == b["mean_episode_reward"]
    rewards_a = [ep["reward"] for ep in a["episodes_detail"]]
    rewards_b = [ep["reward"] for ep in b["episodes_detail"]]
    assert rewards_a == rewards_b


def test_ppo_trainer_act_valid_action():
    trainer = PPOTrainer(obs_dim=9, seed=0, use_torch=False)
    action, log_prob, value = trainer.act([0.0] * 9)
    assert 0 <= action < len(MANAGEMENT_ACTIONS)
    assert np.isfinite(log_prob)
    assert np.isfinite(value)
