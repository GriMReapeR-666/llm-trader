"""RL policy feedback: state-space alignment + bounded sizing influence.

The gap these tests cover: a Q-table trained on Kronos episodes used a
simulator-only schema (``volatility_range``, session ``sim``), so no live
state could ever resolve to a learned key and the policy was a write-only
artifact.  These tests pin the alignment/backoff contract and the
downsize-only sizing influence (including fail-closed LIVE gating).
"""
from __future__ import annotations

import json

import pytest

from research.rl_trainer import RLTrainer


def _fresh_trainer():
    trainer = RLTrainer()
    trainer.q_table.clear()
    trainer.total_updates = 0
    return trainer


LIVE_STATE = {
    "rsi_14": 62.0,
    "macd_histogram": 0.0012,
    "ma_alignment": 1,
    "atr_normalized": 0.005,
    "confluence_score": 5,
    "near_sr": False,
    "news_sentiment": 0.1,
    "volatility_regime": "medium",
    "trend_strength": 0.5,
    "htf_trend": 1,
    "session": "london",
}

SIM_STATE = {
    "rsi_14": 62.0,
    "volatility_range": 0.005,
    "direction": 1,
    "confluence_score": 5,
    "near_sr": False,
    "htf_trend": 1,
    "news_sentiment": 0.1,
    "volatility_regime": "medium",
    "trend_strength": 0.5,
    "session": "sim",
}


def test_simulator_and_live_states_share_a_learned_key():
    """Offline volatility/session features now map onto the live schema."""
    trainer = _fresh_trainer()
    live_key = trainer._build_state_key(LIVE_STATE)
    sim_key = trainer._build_state_key(SIM_STATE)
    # Simulation session is a placeholder -> canonical unknown bucket.
    assert "sess:unknown" in sim_key
    assert "sess:london" in live_key
    # Everything except the session dimension must agree (volatility bins
    # come from atr_normalized / volatility_range respectively).
    assert live_key.replace("sess:london", "sess:unknown") == sim_key


def test_q_values_backoff_masks_unseen_session():
    trainer = _fresh_trainer()
    sim_key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[sim_key] = {"BUY": 1.5, "SELL": -0.5, "HOLD": 0.0}
    trainer.total_updates = 100

    q, matched = trainer.q_values_for_state(LIVE_STATE)
    assert q == {"BUY": 1.5, "SELL": -0.5, "HOLD": 0.0}
    assert "sess:*" in matched

    q_none, key = trainer.q_values_for_state(
        {**LIVE_STATE, "rsi_14": 10.0, "atr_normalized": 0.05}, allow_backoff=False
    )
    assert q_none is None and "sess:london" in key


def test_policy_advantage_signs():
    trainer = _fresh_trainer()
    key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[key] = {"BUY": 2.0, "SELL": -1.0, "HOLD": 0.0}
    trainer.total_updates = 100

    agree = trainer.policy_advantage(LIVE_STATE, "BUY")
    disagree = trainer.policy_advantage(LIVE_STATE, "SELL")

    assert agree["advantage"] == pytest.approx(2.0)
    assert disagree["advantage"] == pytest.approx(-3.0)
    assert agree["updates"] == 100
    assert agree["execution_authority"] is False


def _make_orchestrator(trainer, paper_mode=True):
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.enable_rl = True
    orch.rl_trainer = trainer
    orch._research_influence_allowed = lambda *a, **k: True
    orch._rl_live_evidence_ok = lambda: True
    return orch


def test_sizing_multiplier_no_op_without_enough_updates():
    trainer = _fresh_trainer()
    key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[key] = {"BUY": 2.0, "SELL": -1.0, "HOLD": 0.0}
    trainer.total_updates = 5
    orch = _make_orchestrator(trainer)
    assert orch._rl_sizing_multiplier(LIVE_STATE, "SELL", paper_mode=True) == 1.0


def test_sizing_multiplier_no_op_on_flat_table():
    trainer = _fresh_trainer()
    key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[key] = {"BUY": 1e-6, "SELL": 0.0, "HOLD": 0.0}
    trainer.total_updates = 100
    orch = _make_orchestrator(trainer)
    assert orch._rl_sizing_multiplier(LIVE_STATE, "SELL", paper_mode=True) == 1.0


def test_sizing_multiplier_halves_on_disagreement_never_raises():
    trainer = _fresh_trainer()
    key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[key] = {"BUY": 2.0, "SELL": -1.0, "HOLD": 0.0}
    trainer.total_updates = 100
    orch = _make_orchestrator(trainer)

    assert orch._rl_sizing_multiplier(LIVE_STATE, "SELL", paper_mode=True) == 0.5
    # Agreement is a no-op, never a size increase.
    assert orch._rl_sizing_multiplier(LIVE_STATE, "BUY", paper_mode=True) == 1.0


def test_sizing_multiplier_live_fails_closed_without_opt_in(monkeypatch):
    trainer = _fresh_trainer()
    key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[key] = {"BUY": 2.0, "SELL": -1.0, "HOLD": 0.0}
    trainer.total_updates = 100
    orch = _make_orchestrator(trainer)

    monkeypatch.delenv("RL_POLICY_ENABLED", raising=False)
    assert orch._rl_sizing_multiplier(LIVE_STATE, "SELL", paper_mode=False) == 1.0

    monkeypatch.setenv("RL_POLICY_ENABLED", "1")
    assert orch._rl_sizing_multiplier(LIVE_STATE, "SELL", paper_mode=False) == 0.5

    orch._rl_live_evidence_ok = lambda: False
    assert orch._rl_sizing_multiplier(LIVE_STATE, "SELL", paper_mode=False) == 1.0


def test_live_evidence_gate_reads_pipeline_artifact(tmp_path):
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.repo_base_dir = str(tmp_path)
    artifacts = tmp_path / "artifacts" / "backtest_results"
    artifacts.mkdir(parents=True)
    path = artifacts / "pipeline_results.json"

    # Unknown evidence fails closed.
    assert orch._rl_live_evidence_ok() is False

    path.write_text(json.dumps([{
        "stages": {
            "cpcv": {"status": "INSUFFICIENT_SAMPLES_FOR_CPCV"},
            "dsr": {"passed": True},
        }
    }]), encoding="utf-8")
    assert orch._rl_live_evidence_ok() is False

    path.write_text(json.dumps([{
        "stages": {
            "cpcv": {"status": "CPCV_VALIDATED"},
            "dsr": {"passed": True},
        }
    }]), encoding="utf-8")
    assert orch._rl_live_evidence_ok() is True


def test_hierarchical_backoff_generalises_context_but_not_core():
    trainer = _fresh_trainer()
    sim_key = trainer._build_state_key(SIM_STATE)
    trainer.q_table[sim_key] = {"BUY": 2.0, "SELL": -1.0, "HOLD": 0.0}
    trainer.total_updates = 100

    # Same core setup (vol/rsi/confluence/regime), different context:
    # unknown news, stronger trend, opposite HTF and direction, live session.
    live = {
        **LIVE_STATE,
        "news_sentiment": None,
        "trend_strength": 0.9,
        "htf_trend": -1,
        "ma_alignment": -1,
    }
    q, matched = trainer.q_values_for_state(live)
    assert q == {"BUY": 2.0, "SELL": -1.0, "HOLD": 0.0}
    assert matched.count(":*") >= 5  # backed off over session/news/trend/htf/dir

    # A different core state (RSI oversold) has no learned neighbour.
    none, _ = trainer.q_values_for_state({**live, "rsi_14": 20.0})
    assert none is None
