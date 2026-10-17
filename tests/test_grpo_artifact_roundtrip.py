"""GRPO artifact round-trip: a trained proposer must be usable at inference.

Regression guard for GRPO having no loader at all - save_artifact() wrote
artifacts/grpo_proposer.json on every training run and nothing ever read it
back, so the trained proposer never influenced a later decision.
"""
import json

import numpy as np
import pytest

from research.grpo_proposer import (
    GRPOProposerTrainer,
    NumpyProposerPolicy,
    PROPOSER_ACTIONS,
    context_vector,
    synthesize_contexts,
)

CTX = {"macro_surprise": 0.4, "causal_strength": 0.7, "candidate_score": 1.2,
       "candidate_side": "BUY", "confluence": 5.0, "blackout": False,
       "kill_switch": False, "session_liquidity": 0.8, "spread_r": 0.2}


def _trained(tmp_path):
    trainer = GRPOProposerTrainer(seed=3)
    ctxs = synthesize_contexts(4, seed=3)
    groups = [trainer.collect_group(c, group_size=4) for c in ctxs]
    trainer.update(groups)
    path = str(tmp_path / "grpo.json")
    trainer.save_artifact(path)
    return trainer, path


def test_artifact_round_trip_preserves_policy(tmp_path):
    trained, path = _trained(tmp_path)
    fresh = GRPOProposerTrainer(seed=999)
    probe = context_vector(CTX)

    assert not np.allclose(fresh.policy.logits(probe), trained.policy.logits(probe)), \
        "fresh policy already matches; test would be vacuous"

    fresh.load_artifact(path)
    assert np.allclose(fresh.policy.logits(probe), trained.policy.logits(probe))


def test_greedy_action_is_deterministic(tmp_path):
    _, path = _trained(tmp_path)
    trainer = GRPOProposerTrainer(seed=1)
    trainer.load_artifact(path)

    picks = {trainer.greedy_action(CTX)["action"] for _ in range(20)}
    assert len(picks) == 1, f"greedy_action is not deterministic: {picks}"


def test_greedy_matches_argmax(tmp_path):
    _, path = _trained(tmp_path)
    trainer = GRPOProposerTrainer(seed=1)
    trainer.load_artifact(path)
    probs = trainer.policy.probs(context_vector(CTX))
    assert trainer.greedy_action(CTX)["action_index"] == int(np.argmax(probs))


def test_greedy_still_applies_frozen_risk_gate(tmp_path):
    """The frozen RiskController must veto BUY during a blackout.

    This is the important one: inference must not become a way to route around
    the non-trainable risk gate just because we stopped sampling.
    """
    _, path = _trained(tmp_path)
    trainer = GRPOProposerTrainer(seed=1)
    trainer.load_artifact(path)

    blackout = {**CTX, "blackout": True}
    result = trainer.greedy_action(blackout)
    # HOLD always passes; any BUY/SELL during blackout must be rejected.
    if result["action"] in {"BUY", "SELL"}:
        assert result["gate"]["approved"] is False
        assert "event_blackout" in result["gate"]["reasons"]
    else:
        assert result["action"] == "HOLD"


def test_kill_switch_blocks_directional_action(tmp_path):
    _, path = _trained(tmp_path)
    trainer = GRPOProposerTrainer(seed=1)
    trainer.load_artifact(path)
    result = trainer.greedy_action({**CTX, "kill_switch": True})
    if result["action"] in {"BUY", "SELL"}:
        assert result["gate"]["approved"] is False
        assert "kill_switch" in result["gate"]["reasons"]


def test_shape_mismatch_is_refused(tmp_path):
    _, path = _trained(tmp_path)
    payload = json.loads(open(path, encoding="utf-8").read())
    payload["policy"]["obs_dim"] = 99
    bad = str(tmp_path / "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)

    trainer = GRPOProposerTrainer(seed=1)
    with pytest.raises(ValueError, match="shape"):
        trainer.load_artifact(bad)


def test_safety_flags_are_never_overridden(tmp_path):
    _, path = _trained(tmp_path)
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["execution_authority"] is False
    assert payload["promotion_safe"] is False
    assert payload["risk_controller_frozen"] is True


def test_greedy_result_is_advisory_only(tmp_path):
    _, path = _trained(tmp_path)
    trainer = GRPOProposerTrainer(seed=1)
    trainer.load_artifact(path)
    result = trainer.greedy_action(CTX)
    assert result["execution_authority"] is False
    assert result["advisory_only"] is True
    assert result["action"] in PROPOSER_ACTIONS