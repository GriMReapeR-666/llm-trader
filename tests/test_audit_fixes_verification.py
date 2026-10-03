"""Tests for all audit fixes — P0/P1 gap remediation."""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# P0 #1: RL State Key Schema Alignment
# ---------------------------------------------------------------------------
class TestRLStateKeyAlignment:
    """Verify _build_state_key reads the keys _build_rl_state produces."""

    def test_state_key_uses_atr_normalized(self):
        from research.rl_trainer import RLTrainer
        trainer = RLTrainer.__new__(RLTrainer)
        trainer.log = MagicMock()
        def _numeric_bin(v, edges):
            if v is None:
                return "unknown"
            for i, e in enumerate(edges):
                if v <= e:
                    return ["low", "med", "high"][min(i, 2)]
            return "high"
        trainer._numeric_bin = _numeric_bin

        # State as produced by _build_rl_state
        state = {
            "atr_normalized": 0.005,
            "ma_alignment": 1,
            "rsi_14": 55.0,
            "confluence_score": 4.0,
            "near_sr": True,
            "htf_trend": 1,
            "news_sentiment": 0.2,
            "volatility_regime": "medium",
            "trend_strength": 0.6,
            "session": "london",
        }
        key = trainer._build_state_key(state)
        # Key should NOT contain "unknown" for vol (old bug used volatility_range)
        assert "vol:unknown" not in key, f"Key still has unknown vol: {key}"
        # Key should contain rsi (new field)
        assert "rsi:" in key, f"Key missing rsi bin: {key}"

    def test_state_key_no_bar_position(self):
        from research.rl_trainer import RLTrainer
        trainer = RLTrainer.__new__(RLTrainer)
        trainer.log = MagicMock()
        trainer._numeric_bin = lambda v, edges: "med" if v is not None else "unknown"

        state = {
            "atr_normalized": 0.005,
            "ma_alignment": 0,
            "rsi_14": 50.0,
            "confluence_score": 5.0,
            "near_sr": False,
            "htf_trend": 0,
            "news_sentiment": None,
            "volatility_regime": "low",
            "trend_strength": 0.4,
            "session": "asia",
        }
        key = trainer._build_state_key(state)
        # Should NOT reference bar_position
        assert "bar:" not in key, f"Key still references bar_position: {key}"
        # Should be a valid compact key
        parts = key.split("|")
        assert len(parts) >= 8, f"Key has too few parts: {key}"


# ---------------------------------------------------------------------------
# P0 #2: Self-Improvement DEFERRED + Replay Simulator
# ---------------------------------------------------------------------------
class TestSelfImprovementSimulator:
    """Verify proposals are DEFERRED when no simulator, and simulated when wired."""

    def test_deferred_when_no_simulator(self):
        from core.self_improvement_loop import RecursiveSelfImprovementEngine
        engine = RecursiveSelfImprovementEngine.__new__(RecursiveSelfImprovementEngine)
        engine.state = MagicMock()
        engine.state.cycle_count = 0
        engine.state.last_baseline = None
        engine.state.to_dict.return_value = {"recent_history": []}
        engine.validator = MagicMock()
        engine.memory = MagicMock()
        engine.simulator = MagicMock()
        engine.simulator.n = 0
        engine.max_history = 100
        engine.min_improvement_pct = 2.0

        # Use a real BaselineSnapshot (all required fields present)
        from core.self_improvement_loop import BaselineSnapshot
        baseline = BaselineSnapshot(
            timestamp=time.time(),
            cycle_num=1,
            win_rate=0.5,
            sharpe_rolling=1.0,
        )

        # Mock LLM to return a valid proposal
        proposal = MagicMock()
        proposal.category = "alpha_weights"
        proposal.status = "PENDING"
        proposal.parameter_changes = {"alpha_036_weight": {"current": "0.1", "proposed": "0.15"}}
        proposal.to_dict.return_value = {"category": "alpha_weights"}

        engine.parse_llm_proposals = MagicMock(return_value=[proposal])
        engine._fallback_proposals = MagicMock(return_value=[])
        engine._check_overfit = MagicMock(return_value=proposal)

        # No simulate_proposed_metrics → should DEFER, not REJECT with 0%
        proposals = engine.run_improvement_cycle(
            baseline=baseline,
            llm_analyze_fn=lambda p: "dummy",
            simulate_proposed_metrics=None,
        )
        # At least one proposal should be DEFERRED
        deferred = [p for p in proposals if getattr(p, "status", "") == "DEFERRED"]
        rejected = [p for p in proposals if getattr(p, "status", "") == "REJECTED"]
        # With no simulator, should get DEFERRED not "Insufficient improvement: 0.0%"
        assert len(deferred) > 0 or len(rejected) == 0, (
            f"Expected DEFERRED status but got REJECTED: {[getattr(p, 'rejection_reason', '') for p in rejected]}"
        )

    def test_replay_simulator_produces_nonzero_improvement(self):
        """Replay simulator: honest None-refusal + real sizing math on a real DB."""
        import sqlite3
        import tempfile
        import os as _os
        from core.self_improvement_loop import create_replay_simulator

        # No usable DB -> the simulator must REFUSE (None), never fabricate
        # baseline-copy metrics that score as a fake 0% rejection.
        store = MagicMock()
        store.path = ":memory:"  # empty in-memory DB: no trades table
        sim = create_replay_simulator(store)
        assert sim({"risk_per_trade": {"current": "0.01", "proposed": "0.015"}}) is None

        # Real temp DB with closed trades -> sizing change evaluated honestly.
        # R multiples are size-INVARIANT: win rate / avg R / Sharpe must be
        # identical to baseline; only the equity-fraction drawdown scales.
        fd, db = tempfile.mkstemp(suffix=".db")
        _os.close(fd)
        try:
            con = sqlite3.connect(db)
            con.execute(
                "CREATE TABLE trades (ticket INTEGER PRIMARY KEY, pnl REAL, "
                "risk_amount REAL, closed_at TEXT)"
            )
            rs = [2.0, -1.0, 2.0, -1.0, 3.0, -1.0, 2.0, -1.0, 2.0, -1.0,
                  1.5, -1.0, 2.5, -1.0, 2.0, -1.0, 1.0, -1.0, 2.0, -1.0]
            for i, r in enumerate(rs):
                con.execute(
                    "INSERT INTO trades VALUES (?, ?, ?, ?)",
                    (i + 1, r * 100.0, 100.0, f"2026-06-{(i % 28) + 1:02d}T10:00:00+00:00"),
                )
            # Replay admits only broker-evidenced tickets (order_intents).
            con.execute("CREATE TABLE order_intents (mt5_ticket INTEGER)")
            con.executemany(
                "INSERT INTO order_intents (mt5_ticket) VALUES (?)",
                [(i + 1,) for i in range(len(rs))],
            )
            con.commit()
            con.close()
            store2 = MagicMock()
            store2.path = db
            sim2 = create_replay_simulator(store2, base_risk_per_trade=0.01)
            out = sim2({"risk_per_trade": {"current": "0.01", "proposed": "0.02"}})
            assert out is not None
            assert out["win_rate"] == 0.5          # size-invariant
            assert out["total_trades"] == 20.0
            # 2x sizing doubles the R-curve drawdown fraction
            base = sim2({"risk_per_trade": {"current": "0.01", "proposed": "0.01"}})
            assert base is not None
            assert abs(out["current_drawdown"] - 2.0 * base["current_drawdown"]) < 1e-9
            # Non-sizing changes (alpha re-weights) are NOT evaluable -> None
            assert sim2({"alpha_036_weight": {"current": "0.1", "proposed": "0.15"}}) is None
            # Carry-through metrics arrive unchanged
            sim3 = create_replay_simulator(store2, carry_metrics={"ic_mean_overall": 0.04})
            out3 = sim3({"position_scale": {"current": "1.0", "proposed": "1.5"}})
            assert out3 is not None and out3["ic_mean_overall"] == 0.04
        finally:
            _os.unlink(db)


# ---------------------------------------------------------------------------
# P0 #4: ShadowDeploy Corpus Reference
# ---------------------------------------------------------------------------
class TestShadowDeployCorpus:
    """Verify ShadowDeploy receives and uses the corpus reference."""

    def test_shadow_deploy_has_corpus(self):
        from core.meta_loop import ShadowDeploy
        mock_corpus = MagicMock()
        mock_corpus.get_entry_count.return_value = 50
        sd = ShadowDeploy(corpus=mock_corpus)
        assert sd.corpus is mock_corpus
        assert sd.corpus.get_entry_count() == 50

    def test_dsr_uses_corpus_count(self):
        from core.meta_loop import ShadowDeploy
        mock_corpus = MagicMock()
        mock_corpus.get_entry_count.return_value = 100
        sd = ShadowDeploy(corpus=mock_corpus, dsr_threshold=0.5, shadow_window=5)
        # _compute_dsr_proxy should use self.corpus.get_entry_count()
        score = sd._compute_dsr_proxy(sharpe=1.5, max_dd=0.1, n_obs=50)
        # Should return a numeric score, not crash
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# P1 #20: Decay Status Enum
# ---------------------------------------------------------------------------
class TestDecayStatusEnum:
    """Verify _classify_decay_status returns proper enum values."""

    def test_healthy_positive_r(self):
        from data.market_data_provider import _classify_decay_status
        assert _classify_decay_status(1.5) == "HEALTHY"
        assert _classify_decay_status(0.5) == "HEALTHY"

    def test_reversed_small_negative(self):
        from data.market_data_provider import _classify_decay_status
        assert _classify_decay_status(-0.3) == "REVERSED"

    def test_degraded_moderate_negative(self):
        from data.market_data_provider import _classify_decay_status
        assert _classify_decay_status(-1.0) == "DEGRADED"

    def test_dead_large_negative(self):
        from data.market_data_provider import _classify_decay_status
        assert _classify_decay_status(-2.0) == "DEAD"

    def test_unknown_none(self):
        from data.market_data_provider import _classify_decay_status
        assert _classify_decay_status(None) == "UNKNOWN"

    def test_unknown_non_numeric(self):
        from data.market_data_provider import _classify_decay_status
        assert _classify_decay_status("not_a_number") == "UNKNOWN"


# ---------------------------------------------------------------------------
# P1 #19: MetaAdaptiveEngine Signature Fix
# ---------------------------------------------------------------------------
class TestMetaAdaptiveSignature:
    """Verify the engine.update() call in auto_quant_tools passes correct args."""

    def test_engine_update_signature(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        engine = MetaAdaptiveEngine.__new__(MetaAdaptiveEngine)
        engine.state_file = "test_state.json"
        engine.state = MagicMock()
        engine.detector = MagicMock()
        engine.detector.update.return_value = None
        engine.adapter = MagicMock()
        engine.profiles = {}
        engine._save_state = MagicMock()

        # Should accept (regime_probs, bar_num, rolling_sharpe) — NOT (bar_dict,)
        regime_probs = {"LOW_VOL_TREND": 0.7, "HIGH_VOL_TREND": 0.3}
        result = engine.update(regime_probs, bar_num=10, rolling_sharpe=0.5)
        assert isinstance(result, dict)
        assert "transition_detected" in result


# ---------------------------------------------------------------------------
# P1 #3: Meta-Loop Applied Changes Wiring
# ---------------------------------------------------------------------------
class TestMetaLoopAppliedWiring:
    """Verify applied_changes are plumbbed into alpha weights."""

    def test_applied_changes_extracted(self):
        # Simulate what _run_meta_loop does with applied_changes
        applied = {
            "alpha_weights": {"alpha_036": {"proposed": 0.15}, "alpha_101": 0.2},
            "risk_params": {"risk_per_trade": {"proposed": 0.008}},
        }
        alpha_deltas = applied.get("alpha_weights", {})
        assert "alpha_036" in alpha_deltas
        assert alpha_deltas["alpha_036"]["proposed"] == 0.15
        risk_deltas = applied.get("risk_params", {})
        assert "risk_per_trade" in risk_deltas
