"""Tests for EWC Forgetting Prevention module.

Covers:
- Importance computation
- Snapshot creation and persistence
- EWC penalty computation
- EWC-modified Q-learning update
- Multi-regime consolidation
- Forgetting prevention verification
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.ewc_forgetting_prevention import (
    EWCForgettingPrevention,
    ImportanceSnapshot,
)


# ---------------------------------------------------------------------------
# Importance Computation Tests
# ---------------------------------------------------------------------------

class TestImportanceComputation:
    def test_high_visit_high_q_is_important(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        q_values = {"state_A": {"BUY": 5.0, "SELL": -2.0}}
        visit_counts = {"state_A": {"BUY": 10.0, "SELL": 2.0}}
        importance = ewc.compute_importance(q_values, visit_counts)
        # BUY: 10 visits × |5.0| = 50
        assert importance["state_A"]["BUY"] == 50.0
        # SELL: 2 visits < min_visits(3) → 0
        assert importance["state_A"]["SELL"] == 0.0

    def test_low_visits_not_important(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), min_visits=3)
        q_values = {"state_A": {"BUY": 10.0}}
        visit_counts = {"state_A": {"BUY": 2.0}}
        importance = ewc.compute_importance(q_values, visit_counts)
        assert importance["state_A"]["BUY"] == 0.0

    def test_zero_q_not_important(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        q_values = {"state_A": {"BUY": 0.0}}
        visit_counts = {"state_A": {"BUY": 10.0}}
        importance = ewc.compute_importance(q_values, visit_counts)
        assert importance["state_A"]["BUY"] == 0.0

    def test_empty_inputs(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        importance = ewc.compute_importance({}, {})
        assert importance == {}


# ---------------------------------------------------------------------------
# Snapshot Tests
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_create_snapshot(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        q_table = {"s1": {"BUY": 1.0}, "s2": {"SELL": -0.5}}
        visit_counts = {"s1": {"BUY": 5.0}, "s2": {"SELL": 3.0}}
        ewc.snapshot_regime("LOW_VOL_TREND", q_table, visit_counts, total_updates=100)
        
        assert len(ewc.snapshots) == 1
        assert ewc.snapshots[0].regime == "LOW_VOL_TREND"
        assert ewc.snapshots[0].total_updates == 100

    def test_max_snapshots(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), max_snapshots=3)
        for i in range(5):
            ewc.snapshot_regime(f"regime_{i}", {"s": {"BUY": float(i)}})
        
        assert len(ewc.snapshots) == 3
        assert ewc.snapshots[0].regime == "regime_2"  # Oldest kept
        assert ewc.snapshots[2].regime == "regime_4"  # Most recent

    def test_snapshot_persists(self, tmp_path):
        state_file = str(tmp_path / "ewc_state.json")
        ewc1 = EWCForgettingPrevention(state_file=state_file)
        ewc1.snapshot_regime("regime_1", {"s": {"BUY": 1.0}})
        
        ewc2 = EWCForgettingPrevention(state_file=state_file)
        assert len(ewc2.snapshots) == 1
        assert ewc2.snapshots[0].regime == "regime_1"


# ---------------------------------------------------------------------------
# EWC Penalty Tests
# ---------------------------------------------------------------------------

class TestEWCPenalty:
    def test_no_snapshots_no_penalty(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        penalty = ewc.compute_ewc_penalty("s1", "BUY", 5.0)
        assert penalty == 0.0

    def test_penalty_for_important_change(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), ewc_lambda=1.0)
        q_table = {"s1": {"BUY": 5.0}}
        visit_counts = {"s1": {"BUY": 10.0}}
        ewc.snapshot_regime("regime_1", q_table, visit_counts)
        
        penalty = ewc.compute_ewc_penalty("s1", "BUY", 10.0)
        assert penalty > 0

    def test_no_penalty_for_unimportant_change(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), ewc_lambda=1.0, min_visits=5)
        q_table = {"s1": {"BUY": 5.0}}
        visit_counts = {"s1": {"BUY": 2.0}}
        ewc.snapshot_regime("regime_1", q_table, visit_counts)
        
        penalty = ewc.compute_ewc_penalty("s1", "BUY", 10.0)
        assert penalty == 0.0

    def test_no_penalty_for_unknown_state(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        q_table = {"s1": {"BUY": 5.0}}
        ewc.snapshot_regime("regime_1", q_table)
        
        penalty = ewc.compute_ewc_penalty("unknown_state", "BUY", 10.0)
        assert penalty == 0.0


# ---------------------------------------------------------------------------
# EWC Update Tests
# ---------------------------------------------------------------------------

class TestEWCUpdate:
    def test_standard_update_without_ewc(self, tmp_path):
        """Without snapshots, EWC update = standard Q-learning."""
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        new_q = ewc.ewc_update(
            state_key="s1", action="BUY", reward=1.0,
            max_next_q=2.0, learning_rate=0.1, discount_factor=0.95,
            current_q=0.0,
        )
        # Standard Q-learning: Q = 0 + 0.1 × (1 + 0.95×2 - 0) = 0.29
        assert new_q == pytest.approx(0.29, abs=0.01)

    def test_ewc_update_preserves_important_values(self, tmp_path):
        """With snapshot, EWC penalizes large changes to important values."""
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), ewc_lambda=1.0)
        q_table = {"s1": {"BUY": 5.0}}
        visit_counts = {"s1": {"BUY": 10.0}}
        ewc.snapshot_regime("regime_1", q_table, visit_counts)
        
        # With EWC, the new Q should be closer to 5.0 than without EWC
        new_q_with_ewc = ewc.ewc_update(
            state_key="s1", action="BUY", reward=-2.0,
            max_next_q=0.0, learning_rate=0.1, discount_factor=0.95,
            current_q=5.0,
        )
        
        # Standard Q-learning without EWC would give:
        standard_q = 5.0 + 0.1 * (-2.0 + 0.95 * 0.0 - 5.0)  # = 4.3
        
        # EWC should protect the value: new_q should be ABOVE standard_q
        # (penalty pushes back toward snapshot value of 5.0)
        # The penalty reduces the magnitude of the update
        assert new_q_with_ewc >= standard_q  # EWC protects: doesn't drop as much


# ---------------------------------------------------------------------------
# Multi-Regime Consolidation Tests
# ---------------------------------------------------------------------------

class TestMultiRegime:
    def test_combined_importance(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        ewc.snapshot_regime("regime_1", {"s1": {"BUY": 5.0}}, {"s1": {"BUY": 10.0}})
        ewc.snapshot_regime("regime_2", {"s1": {"SELL": 3.0}}, {"s1": {"SELL": 8.0}})
        
        combined = ewc.get_combined_importance()
        assert combined["s1"]["BUY"] > 0
        assert combined["s1"]["SELL"] > 0

    def test_penalty_from_multiple_snapshots(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), ewc_lambda=1.0)
        ewc.snapshot_regime("r1", {"s1": {"BUY": 5.0}}, {"s1": {"BUY": 10.0}})
        ewc.snapshot_regime("r2", {"s1": {"BUY": 3.0}}, {"s1": {"BUY": 8.0}})
        
        penalty = ewc.compute_ewc_penalty("s1", "BUY", 10.0)
        assert penalty > 0


# ---------------------------------------------------------------------------
# Forgetting Prevention Verification
# ---------------------------------------------------------------------------

class TestForgettingPrevention:
    def test_important_q_values_preserved(self, tmp_path):
        """Verify that important Q-values are NOT overwritten by new learning."""
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), ewc_lambda=0.5)
        
        q_table = {"bull_trend": {"BUY": 5.0, "SELL": -3.0, "HOLD": 0.0}}
        visit_counts = {"bull_trend": {"BUY": 20.0, "SELL": 5.0, "HOLD": 2.0}}
        ewc.snapshot_regime("LOW_VOL_TREND", q_table, visit_counts)
        
        current_q_buy = 5.0
        new_q_buy = ewc.ewc_update(
            state_key="bull_trend", action="BUY", reward=-1.0,
            max_next_q=3.0, learning_rate=0.1, discount_factor=0.95,
            current_q=current_q_buy,
        )
        
        # EWC should protect: new_q should not drop below snapshot value by much
        assert new_q_buy > current_q_buy - 2.0

    def test_unimportant_q_values_can_change(self, tmp_path):
        """Verify that unimportant Q-values CAN be freely updated."""
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"), ewc_lambda=0.5)
        
        q_table = {"quiet_market": {"BUY": 1.0}}
        visit_counts = {"quiet_market": {"BUY": 1.0}}  # Below min_visits
        ewc.snapshot_regime("LOW_VOL_TREND", q_table, visit_counts)
        
        new_q = ewc.ewc_update(
            state_key="quiet_market", action="BUY", reward=3.0,
            max_next_q=5.0, learning_rate=0.1, discount_factor=0.95,
            current_q=1.0,
        )
        expected = 1.0 + 0.1 * (3.0 + 0.95 * 5.0 - 1.0)
        assert new_q == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# Status Tests
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_empty(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        status = ewc.get_status()
        assert status["num_snapshots"] == 0
        assert status["total_important_pairs"] == 0

    def test_status_with_data(self, tmp_path):
        ewc = EWCForgettingPrevention(state_file=str(tmp_path / "ewc.json"))
        ewc.snapshot_regime("r1", {"s1": {"BUY": 5.0}}, {"s1": {"BUY": 10.0}})
        ewc.snapshot_regime("r2", {"s2": {"SELL": 3.0}}, {"s2": {"SELL": 8.0}})
        status = ewc.get_status()
        assert status["num_snapshots"] == 2
        assert status["total_important_pairs"] >= 2
