"""Tests for offline GRPO MarketModel proposer (Phase E)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research.grpo_proposer import (
    FROZEN_AGENT,
    PROPOSER_ACTIONS,
    TRAINABLE_AGENT,
    FrozenRiskGate,
    GRPOProposerTrainer,
    group_relative_advantages,
    score_trajectory,
    synthesize_contexts,
    train_grpo_offline,
)


def test_group_relative_advantages_zero_mean():
    adv = group_relative_advantages([1.0, 2.0, 3.0])
    assert abs(float(adv.mean())) < 1e-9
    assert np.isfinite(adv).all()


def test_frozen_gate_blocks_blackout_and_never_submits():
    gate = FrozenRiskGate()
    assert gate.trainable is False
    out = gate.evaluate({"blackout": True, "confluence": 9}, "BUY")
    assert out["approved"] is False
    assert "event_blackout" in out["reasons"]
    assert out["order_submitted"] is False
    assert out["execution_authority"] is False
    hold = gate.evaluate({"blackout": True, "confluence": 9}, "HOLD")
    assert hold["approved"] is True


def test_blackout_hold_never_penalized():
    gate = FrozenRiskGate()
    ctx = {"blackout": True, "latent_edge_r": 2.0, "latent_edge_dir": 1}
    scored = score_trajectory(ctx, "HOLD", gate.evaluate(ctx, "HOLD"))
    assert scored["reward"] == 0.0
    assert scored["kind"] == "blackout_hold"


def test_collect_group_marks_risk_frozen():
    trainer = GRPOProposerTrainer(seed=1)
    ctx = synthesize_contexts(1, seed=1)[0]
    group = trainer.collect_group(ctx, group_size=3)
    assert group["risk_controller_frozen"] is True
    assert group["execution_authority"] is False
    assert len(group["samples"]) == 3
    assert all(s["agent"] == TRAINABLE_AGENT for s in group["samples"])
    assert all(s["gate"]["trainable"] is False for s in group["samples"])


def test_train_grpo_offline_smoke(tmp_path: Path):
    artifact = tmp_path / "grpo.json"
    report = train_grpo_offline(
        contexts=4,
        group_size=3,
        updates=1,
        epochs=1,
        seed=5,
        artifact_path=str(artifact),
    )
    assert report["status"] == "COMPLETED"
    assert report["execution_authority"] is False
    assert report["promotion_safe"] is False
    assert report["risk_controller_frozen"] is True
    assert report["trainable_agent"] == TRAINABLE_AGENT
    assert report["frozen_agent"] == FROZEN_AGENT
    assert report["artifact_written"] is True
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["execution_authority"] is False
    assert payload["risk_controller_frozen"] is True
    assert payload["actions"] == list(PROPOSER_ACTIONS)
    assert payload["trainable_agent"] == "MarketModel"
    assert "W" in payload["policy"]


def test_train_grpo_deterministic_seed(tmp_path: Path):
    kwargs = dict(contexts=3, group_size=2, updates=1, epochs=1, seed=42)
    a = train_grpo_offline(artifact_path=str(tmp_path / "a.json"), **kwargs)
    b = train_grpo_offline(artifact_path=str(tmp_path / "b.json"), **kwargs)
    assert a["training_data_hash"] == b["training_data_hash"]
    assert a["mean_group_reward"] == b["mean_group_reward"]
