"""PPO artifact round-trip: a trained policy must be usable at inference.

Regression guard for the gap where PPOTrainer.load_artifact() existed but had
no production caller, so every training run wrote artifacts/ppo_position_mgmt.json
that nothing ever read back. Training was therefore a pure write-only sink.
"""
import json

import numpy as np
import pytest

from research.ppo_trainer import PPOTrainer, MANAGEMENT_ACTIONS, OBS_DIM_KRONOS


def _trained_artifact(tmp_path, obs_dim=OBS_DIM_KRONOS):
    """Train briefly and return a saved artifact path with a KNOWN policy."""
    trainer = PPOTrainer(obs_dim=obs_dim, use_torch=False, seed=7)
    rng = np.random.default_rng(0)
    obs = rng.normal(size=(32, obs_dim))
    actions = rng.integers(0, len(MANAGEMENT_ACTIONS), size=32)
    # Hand-set advantages so the gradient step has a consistent direction.
    advantages = np.linspace(0.2, -0.2, 32)
    returns = advantages + 0.1
    trainer.update({
        "obs": obs,
        "actions": actions,
        "log_probs": np.full(32, -1.0),
        "advantages": advantages,
        "returns": returns,
    })
    path = str(tmp_path / "ppo.json")
    trainer.save_artifact(path)
    return trainer, path


def test_artifact_round_trip_preserves_policy(tmp_path):
    """A loaded trainer must reproduce the saved trainer's decisions."""
    trained, path = _trained_artifact(tmp_path)

    fresh = PPOTrainer(obs_dim=OBS_DIM_KRONOS, use_torch=False, seed=999)
    # Sanity: the fresh model must NOT already match, else the test is vacuous.
    probe = np.linspace(-1, 1, OBS_DIM_KRONOS)
    assert not np.allclose(
        fresh.numpy_model.logits_value(probe)[0],
        trained.numpy_model.logits_value(probe)[0],
    ), "fresh trainer already matches trained policy; test would be vacuous"

    fresh.load_artifact(path)
    assert np.allclose(
        fresh.numpy_model.logits_value(probe)[0],
        trained.numpy_model.logits_value(probe)[0],
    ), "load_artifact did not restore the saved policy"


def test_loaded_policy_is_deterministic_under_sampling(tmp_path):
    """Greedy selection from a loaded policy must not drift between calls."""
    _, path = _trained_artifact(tmp_path)
    trainer = PPOTrainer(obs_dim=OBS_DIM_KRONOS, use_torch=False, seed=1)
    trainer.load_artifact(path)

    obs = np.linspace(-0.5, 0.5, OBS_DIM_KRONOS)
    picks = {trainer.greedy_action(obs) for _ in range(20)}
    assert len(picks) == 1, f"greedy_action is not deterministic: {picks}"


def test_greedy_action_matches_argmax_of_logits(tmp_path):
    """greedy_action must be argmax, not a sample from the distribution."""
    _, path = _trained_artifact(tmp_path)
    trainer = PPOTrainer(obs_dim=OBS_DIM_KRONOS, use_torch=False, seed=1)
    trainer.load_artifact(path)

    obs = np.linspace(-0.3, 0.9, OBS_DIM_KRONOS)
    logits, _ = trainer.numpy_model.logits_value(obs)
    assert trainer.greedy_action(obs) == int(np.argmax(logits))


def test_missing_artifact_fails_closed(tmp_path):
    """A missing/corrupt artifact must not raise into the decision path."""
    from intelligence.llm_orchestrator import LLMOrchestrator

    obj = object.__new__(LLMOrchestrator)
    result = LLMOrchestrator._ppo_advice(
        obj, toon={"indicators": {}, "ohlc": []}, artifact_path=str(tmp_path / "nope.json")
    )
    assert result == {} or result.get("action") is None


def test_promotion_flags_are_never_overridden(tmp_path):
    """A loaded policy stays advisory: execution_authority must stay False."""
    _, path = _trained_artifact(tmp_path)
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["execution_authority"] is False
    assert payload["promotion_safe"] is False


def test_obs_dim_mismatch_is_refused(tmp_path):
    """A width-mismatched artifact must be refused, not silently mis-loaded.

    W_pi is (obs_dim x n_actions); loading a 9-wide policy into an 11-wide head
    previously produced a matmul error from deep inside logits_value(), which
    said nothing about the real cause.
    """
    _, path = _trained_artifact(tmp_path)
    trainer = PPOTrainer(obs_dim=11, use_torch=False, seed=2)
    with pytest.raises(ValueError, match="obs_dim"):
        trainer.load_artifact(path)


def test_backend_mismatch_is_refused(tmp_path):
    """A torch artifact must not be loaded into the numpy head."""
    _, path = _trained_artifact(tmp_path)
    trainer = PPOTrainer(obs_dim=OBS_DIM_KRONOS, use_torch=False, seed=2)
    # Corrupt the recorded backend to simulate a cross-backend load.
    payload = json.loads(open(path, encoding="utf-8").read())
    payload["backend"] = "torch"
    bad = str(tmp_path / "bad_backend.json")
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    with pytest.raises(ValueError, match="backend"):
        trainer.load_artifact(bad)


def test_act_still_pads_short_observations():
    """act() keeps its pad/truncate behaviour for rollout collection."""
    trainer = PPOTrainer(obs_dim=OBS_DIM_KRONOS, use_torch=False, seed=2)
    idx, logp, val = trainer.act(np.ones(7))
    assert 0 <= idx < len(MANAGEMENT_ACTIONS)