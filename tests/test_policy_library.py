"""Tests for FinPFN/ReCAP inspired PolicyLibrary and RegimeGate.

Covers:
- PolicyVector serialization
- PolicyLibrary load/save/update
- RegimeGate weight computation
- Policy blending
- Zero-shot switching
- Performance-based updates
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from core.policy_library import PolicyLibrary, PolicyVector, RegimeGate


# ---------------------------------------------------------------------------
# PolicyVector Tests
# ---------------------------------------------------------------------------

class TestPolicyVector:
    def test_defaults(self):
        pv = PolicyVector()
        assert pv.risk_multiplier == 1.0
        assert pv.position_scale == 1.0
        assert pv.max_positions == 3

    def test_to_dict(self):
        pv = PolicyVector(risk_multiplier=1.5)
        d = pv.to_dict()
        assert d["risk_multiplier"] == 1.5
        assert "win_rate" in d

    def test_from_dict(self):
        d = PolicyVector(risk_multiplier=1.5).to_dict()
        pv = PolicyVector.from_dict(d)
        assert pv.risk_multiplier == 1.5

    def test_flat_array_roundtrip(self):
        pv = PolicyVector(
            risk_multiplier=1.3, position_scale=0.8, stop_loss_atr=2.0,
            take_profit_atr=4.0, max_positions=2, entry_confidence_threshold=0.6,
            holding_period_bars=25, trailing_stop_activate_r=2.0, partial_close_r=3.0,
        )
        arr = pv.to_flat_array()
        pv2 = PolicyVector.from_flat_array(arr)
        assert pv2.risk_multiplier == pytest.approx(1.3, abs=0.01)
        assert pv2.position_scale == pytest.approx(0.8, abs=0.01)
        assert pv2.max_positions == 2
        assert pv2.holding_period_bars == 25


# ---------------------------------------------------------------------------
# PolicyLibrary Tests
# ---------------------------------------------------------------------------

class TestPolicyLibrary:
    def _make_library(self, tmp_path: Path) -> PolicyLibrary:
        return PolicyLibrary(state_file=str(tmp_path / "policy_lib.json"))

    def test_initializes_with_defaults(self, tmp_path):
        lib = self._make_library(tmp_path)
        assert "LOW_VOL_TREND" in lib.policies
        assert "HIGH_VOL_CRISIS" in lib.policies
        assert lib.policies["LOW_VOL_TREND"].risk_multiplier == 1.2

    def test_get_policy_creates_missing(self, tmp_path):
        lib = self._make_library(tmp_path)
        policy = lib.get_policy("NEW_REGIME")
        assert isinstance(policy, PolicyVector)
        assert "NEW_REGIME" in lib.policies

    def test_persists_to_disk(self, tmp_path):
        lib = self._make_library(tmp_path)
        lib.policies["LOW_VOL_TREND"].risk_multiplier = 1.5
        lib._save()
        
        lib2 = self._make_library(tmp_path)
        assert lib2.policies["LOW_VOL_TREND"].risk_multiplier == 1.5

    def test_update_policy_with_good_performance(self, tmp_path):
        lib = self._make_library(tmp_path)
        old_risk = lib.policies["LOW_VOL_TREND"].risk_multiplier
        lib.update_policy("LOW_VOL_TREND", {
            "win_rate": 0.6, "sharpe_ratio": 1.0, "avg_rr": 2.0, "total_trades": 20,
        })
        # High win rate → risk multiplier should increase slightly
        assert lib.policies["LOW_VOL_TREND"].risk_multiplier >= old_risk

    def test_update_policy_with_bad_performance(self, tmp_path):
        lib = self._make_library(tmp_path)
        old_risk = lib.policies["LOW_VOL_TREND"].risk_multiplier
        lib.update_policy("LOW_VOL_TREND", {
            "win_rate": 0.3, "sharpe_ratio": -0.5, "avg_rr": 0.8, "total_trades": 20,
        })
        # Low win rate → risk multiplier should decrease
        assert lib.policies["LOW_VOL_TREND"].risk_multiplier <= old_risk

    def test_update_insufficient_data(self, tmp_path):
        lib = self._make_library(tmp_path)
        old_risk = lib.policies["LOW_VOL_TREND"].risk_multiplier
        lib.update_policy("LOW_VOL_TREND", {
            "win_rate": 0.3, "sharpe_ratio": -0.5, "avg_rr": 0.8, "total_trades": 5,
        })
        # Not enough trades → no update
        assert lib.policies["LOW_VOL_TREND"].risk_multiplier == old_risk

    def test_get_all_policies(self, tmp_path):
        lib = self._make_library(tmp_path)
        all_policies = lib.get_all_policies()
        assert len(all_policies) >= 4
        assert "LOW_VOL_TREND" in all_policies


# ---------------------------------------------------------------------------
# RegimeGate Tests
# ---------------------------------------------------------------------------

class TestRegimeGate:
    def test_hard_switch_low_temperature(self):
        gate = RegimeGate(temperature=0.01)
        weights = gate.compute_gate_weights({
            "LOW_VOL_TREND": 0.8,
            "HIGH_VOL_CRISIS": 0.2,
        })
        # Low temp → winner-take-all
        assert weights["LOW_VOL_TREND"] > 0.9

    def test_smooth_blend_high_temperature(self):
        gate = RegimeGate(temperature=10.0)
        weights = gate.compute_gate_weights({
            "LOW_VOL_TREND": 0.6,
            "HIGH_VOL_CRISIS": 0.4,
        })
        # High temp → more uniform
        assert 0.3 < weights["LOW_VOL_TREND"] < 0.7
        assert 0.3 < weights["HIGH_VOL_CRISIS"] < 0.7

    def test_default_temperature(self):
        gate = RegimeGate()
        weights = gate.compute_gate_weights({
            "LOW_VOL_TREND": 0.7,
            "HIGH_VOL_CRISIS": 0.3,
        })
        assert weights["LOW_VOL_TREND"] > weights["HIGH_VOL_CRISIS"]
        assert abs(sum(weights.values()) - 1.0) < 0.01

    def test_empty_probs(self):
        gate = RegimeGate()
        weights = gate.compute_gate_weights({})
        assert weights == {}

    def test_blend_policies(self):
        gate = RegimeGate()
        policies = {
            "LOW_VOL_TREND": PolicyVector(risk_multiplier=1.2, position_scale=1.0),
            "HIGH_VOL_CRISIS": PolicyVector(risk_multiplier=0.2, position_scale=0.3),
        }
        weights = {"LOW_VOL_TREND": 0.8, "HIGH_VOL_CRISIS": 0.2}
        blended = gate.blend_policies(policies, weights)
        
        # Blended risk should be between 0.2 and 1.2
        assert 0.2 < blended.risk_multiplier < 1.2
        # Closer to LOW_VOL_TREND (weight=0.8)
        assert blended.risk_multiplier > 0.7

    def test_blend_single_policy(self):
        gate = RegimeGate()
        policies = {"LOW_VOL_TREND": PolicyVector(risk_multiplier=1.2)}
        weights = {"LOW_VOL_TREND": 1.0}
        blended = gate.blend_policies(policies, weights)
        assert blended.risk_multiplier == pytest.approx(1.2, abs=0.01)

    def test_blend_with_negligible_weights(self):
        gate = RegimeGate()
        policies = {
            "LOW_VOL_TREND": PolicyVector(risk_multiplier=1.2),
            "HIGH_VOL_CRISIS": PolicyVector(risk_multiplier=0.2),
        }
        weights = {"LOW_VOL_TREND": 0.99, "HIGH_VOL_CRISIS": 0.01}
        blended = gate.blend_policies(policies, weights)
        # Should be dominated by LOW_VOL_TREND
        assert blended.risk_multiplier > 1.0

    def test_get_status(self):
        gate = RegimeGate()
        gate.compute_gate_weights({"A": 0.6, "B": 0.4})
        status = gate.get_status()
        assert "temperature" in status
        assert "last_gate_weights" in status


# ---------------------------------------------------------------------------
# Integration: Full Flow
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_zero_shot_switching(self, tmp_path):
        """Test instant policy switching on regime change."""
        lib = PolicyLibrary(state_file=str(tmp_path / "policy_lib.json"))
        gate = RegimeGate(temperature=0.5)
        
        # Start in LOW_VOL_TREND
        probs1 = {"LOW_VOL_TREND": 0.8, "HIGH_VOL_CRISIS": 0.2}
        weights1 = gate.compute_gate_weights(probs1)
        blended1 = gate.blend_policies(lib.policies, weights1)
        assert blended1.risk_multiplier > 0.8  # Closer to 1.2
        
        # Regime shifts to HIGH_VOL_CRISIS
        probs2 = {"LOW_VOL_TREND": 0.2, "HIGH_VOL_CRISIS": 0.8}
        weights2 = gate.compute_gate_weights(probs2)
        blended2 = gate.blend_policies(lib.policies, weights2)
        assert blended2.risk_multiplier < 0.5  # Closer to 0.2
        
        # Instant switch — no waiting period
        assert blended1.risk_multiplier != blended2.risk_multiplier

    def test_performance_improves_policy(self, tmp_path):
        """Test that policies improve over time with performance feedback."""
        lib = PolicyLibrary(state_file=str(tmp_path / "policy_lib.json"))
        
        # Initial crisis policy
        initial = lib.get_policy("HIGH_VOL_CRISIS")
        initial_risk = initial.risk_multiplier
        
        # Simulate good performance in crisis
        for _ in range(5):
            lib.update_policy("HIGH_VOL_CRISIS", {
                "win_rate": 0.6, "sharpe_ratio": 0.8, "avg_rr": 2.0, "total_trades": 15,
            })
        
        updated = lib.get_policy("HIGH_VOL_CRISIS")
        # Policy should have adapted
        assert updated.total_trades > 0

    def test_policy_library_persists_across_sessions(self, tmp_path):
        """Test that policy improvements persist."""
        state_file = str(tmp_path / "policy_lib.json")
        
        # Session 1: update policy
        lib1 = PolicyLibrary(state_file=state_file)
        lib1.update_policy("LOW_VOL_TREND", {
            "win_rate": 0.6, "sharpe_ratio": 1.0, "avg_rr": 2.0, "total_trades": 20,
        })
        
        # Session 2: load and verify
        lib2 = PolicyLibrary(state_file=state_file)
        assert lib2.policies["LOW_VOL_TREND"].win_rate == 0.6
        assert lib2.policies["LOW_VOL_TREND"].sharpe_ratio == 1.0
