"""Tests for Meta-Adaptive Regime Engine."""
from __future__ import annotations

import json
import os
import tempfile

import numpy as np
import pytest


# ===========================================================================
# 1. Strategy Profiles
# ===========================================================================

class TestStrategyProfiles:
    """Test regime-specific strategy profiles."""

    def test_default_profiles_exist(self):
        from core.meta_adaptive_engine import DEFAULT_PROFILES
        assert "LOW_VOL_TREND" in DEFAULT_PROFILES
        assert "LOW_VOL_RANGE" in DEFAULT_PROFILES
        assert "HIGH_VOL_TREND" in DEFAULT_PROFILES
        assert "HIGH_VOL_CRISIS" in DEFAULT_PROFILES

    def test_crisis_profile_conservative(self):
        from core.meta_adaptive_engine import DEFAULT_PROFILES
        crisis = DEFAULT_PROFILES["HIGH_VOL_CRISIS"]
        trend = DEFAULT_PROFILES["LOW_VOL_TREND"]
        assert crisis.risk_multiplier < trend.risk_multiplier
        assert crisis.position_scale < trend.position_scale
        assert crisis.max_positions <= trend.max_positions

    def test_profile_serialization(self):
        from core.meta_adaptive_engine import StrategyProfile
        profile = StrategyProfile(regime="TEST", risk_multiplier=0.5)
        d = profile.to_dict()
        assert d["regime"] == "TEST"
        assert d["risk_multiplier"] == 0.5


# ===========================================================================
# 2. Regime Transition Detector
# ===========================================================================

class TestRegimeTransitionDetector:
    """Test regime transition detection."""

    def test_no_transition_same_regime(self):
        from core.meta_adaptive_engine import RegimeTransitionDetector
        detector = RegimeTransitionDetector(min_stability_bars=1)
        probs = {"LOW_VOL_TREND": 0.7, "LOW_VOL_RANGE": 0.2, "HIGH_VOL_TREND": 0.1}
        t = detector.update(probs, 1)
        assert t is None
        t = detector.update(probs, 2)
        assert t is None

    def test_transition_detected(self):
        from core.meta_adaptive_engine import RegimeTransitionDetector
        detector = RegimeTransitionDetector(min_stability_bars=2, cooldown_bars=0)
        # Establish initial regime
        detector.update({"LOW_VOL_TREND": 0.7, "LOW_VOL_RANGE": 0.2}, 1)
        # Shift to different regime
        t = detector.update({"LOW_VOL_RANGE": 0.7, "LOW_VOL_TREND": 0.2}, 2)
        assert t is None  # Not enough bars yet
        t = detector.update({"LOW_VOL_RANGE": 0.7, "LOW_VOL_TREND": 0.2}, 3)
        assert t is not None
        assert t.from_regime == "LOW_VOL_TREND"
        assert t.to_regime == "LOW_VOL_RANGE"

    def test_cooldown_prevents_rapid_transitions(self):
        from core.meta_adaptive_engine import RegimeTransitionDetector
        detector = RegimeTransitionDetector(min_stability_bars=1, cooldown_bars=5)
        # First transition
        detector.update({"LOW_VOL_TREND": 0.7}, 1)
        t1 = detector.update({"HIGH_VOL_TREND": 0.7}, 2)
        assert t1 is not None
        # Second transition within cooldown — should be blocked
        t2 = detector.update({"LOW_VOL_RANGE": 0.7}, 3)
        assert t2 is None

    def test_low_confidence_blocks_transition(self):
        from core.meta_adaptive_engine import RegimeTransitionDetector
        detector = RegimeTransitionDetector(min_stability_bars=1, confidence_threshold=0.5)
        detector.update({"LOW_VOL_TREND": 0.7}, 1)
        # Low confidence shift
        t = detector.update({"LOW_VOL_RANGE": 0.3, "LOW_VOL_TREND": 0.35}, 2)
        assert t is None


# ===========================================================================
# 3. Few-Shot Adapter
# ===========================================================================

class TestFewShotAdapter:
    """Test MAML-inspired parameter adaptation."""

    def test_default_alpha(self):
        from core.meta_adaptive_engine import FewShotAdapter, DEFAULT_PROFILES
        adapter = FewShotAdapter(min_samples=0)
        alpha = adapter.compute_adaptation_alpha("LOW_VOL_TREND")
        assert 0.0 <= alpha <= 1.0

    def test_high_performance_increases_alpha(self):
        from core.meta_adaptive_engine import FewShotAdapter
        adapter = FewShotAdapter(min_samples=3)
        # Record good performance
        for _ in range(10):
            adapter.record_performance("LOW_VOL_TREND", 1.5)
        alpha = adapter.compute_adaptation_alpha("LOW_VOL_TREND")
        assert alpha > 0.5  # Should trust regime profile more

    def test_low_performance_decreases_alpha(self):
        from core.meta_adaptive_engine import FewShotAdapter
        adapter = FewShotAdapter(min_samples=3)
        # Record poor performance
        for _ in range(10):
            adapter.record_performance("LOW_VOL_TREND", -1.0)
        alpha = adapter.compute_adaptation_alpha("LOW_VOL_TREND")
        assert alpha < 0.5  # Should trust base profile more

    def test_adapt_profile_blends(self):
        from core.meta_adaptive_engine import FewShotAdapter, DEFAULT_PROFILES, StrategyProfile
        adapter = FewShotAdapter(min_samples=0)
        base = StrategyProfile(regime="TEST", risk_multiplier=1.0, position_scale=1.0)
        adapted = adapter.adapt_profile(base, "LOW_VOL_TREND")
        # Should be between base and regime profile
        assert 0.0 < adapted.risk_multiplier < 2.0


# ===========================================================================
# 4. Meta-Adaptive Engine (Full Integration)
# ===========================================================================

class TestMetaAdaptiveEngine:
    """Test the full meta-adaptive engine."""

    def test_engine_init(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = MetaAdaptiveEngine(state_file=state_file)
            profile = engine.get_active_profile()
            assert profile.regime == "LOW_VOL_TREND"
        finally:
            os.unlink(state_file)

    def test_update_without_transition(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = MetaAdaptiveEngine(state_file=state_file)
            result = engine.update(
                {"LOW_VOL_TREND": 0.7, "LOW_VOL_RANGE": 0.2},
                bar_num=1,
            )
            assert not result["transition_detected"]
        finally:
            os.unlink(state_file)

    def test_update_with_transition(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = MetaAdaptiveEngine(state_file=state_file)
            # Establish initial regime with enough bars
            for i in range(10):
                engine.update({"LOW_VOL_TREND": 0.7}, bar_num=1 + i)
            # Force transition to different regime — check ALL results
            transition_found = False
            for i in range(10):
                result = engine.update(
                    {"HIGH_VOL_TREND": 0.7, "LOW_VOL_TREND": 0.1},
                    bar_num=11 + i,
                )
                if result["transition_detected"]:
                    transition_found = True
                    assert result["to_regime"] == "HIGH_VOL_TREND"
                    break
            assert transition_found, "No transition detected after 10 bars of HIGH_VOL_TREND"
        finally:
            os.unlink(state_file)

    def test_llm_context(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = MetaAdaptiveEngine(state_file=state_file)
            ctx = engine.get_llm_context()
            assert "regime" in ctx
            assert "risk_multiplier" in ctx
            assert "preferred_alphas" in ctx
            assert "disfavored_alphas" in ctx
        finally:
            os.unlink(state_file)

    def test_state_persistence(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine1 = MetaAdaptiveEngine(state_file=state_file)
            engine1.update({"LOW_VOL_TREND": 0.7}, bar_num=1)
            # Force a transition
            for i in range(5):
                engine1.update({"HIGH_VOL_CRISIS": 0.8}, bar_num=2 + i)

            # Load in new instance
            engine2 = MetaAdaptiveEngine(state_file=state_file)
            assert engine2.state.total_transitions >= 1
        finally:
            os.unlink(state_file)

    def test_performance_tracking(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = MetaAdaptiveEngine(state_file=state_file)
            engine.update({"LOW_VOL_TREND": 0.7}, bar_num=1, rolling_sharpe=1.5)
            engine.update({"LOW_VOL_TREND": 0.7}, bar_num=2, rolling_sharpe=1.2)
            ctx = engine.get_llm_context()
            assert "performance_summary" in ctx
        finally:
            os.unlink(state_file)

    def test_get_status(self):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            engine = MetaAdaptiveEngine(state_file=state_file)
            status = engine.get_status()
            assert "meta_adaptive" in status
            assert "detector" in status
            assert "adapter" in status
        finally:
            os.unlink(state_file)
