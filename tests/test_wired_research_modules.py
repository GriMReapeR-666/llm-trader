"""Tests for wired research modules: alpha_stability, canonical_mastery, rl_analytics."""
import json
import os
import pytest
import numpy as np


class TestAlphaStabilityWiring:
    """Test alpha_ensemble_stability wired into auto_quant_tools."""

    def test_alpha_stability_runner_exists(self):
        from core.auto_quant_tools import AutoQuantTools
        assert hasattr(AutoQuantTools, '_run_alpha_stability')

    def test_alpha_stability_interval_registered(self):
        from core.auto_quant_tools import DEFAULT_INTERVALS
        assert "alpha_stability" in DEFAULT_INTERVALS
        assert DEFAULT_INTERVALS["alpha_stability"] == 2016

    def test_alpha_stability_runner_with_synthetic(self):
        from core.auto_quant_tools import AutoQuantTools
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"alpha_stability": 1}, state_file=state_file)
            result = tools._run_alpha_stability({"ohlc": []})
            assert result["status"] in ("OK", "INSUFFICIENT_DATA", "ERROR")
            if result["status"] == "OK":
                assert "robust_alphas" in result
                assert "fragile_alphas" in result
                assert "results" in result
        finally:
            os.unlink(state_file)

    def test_alpha_stability_in_update(self):
        from core.auto_quant_tools import AutoQuantTools
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"alpha_stability": 1}, state_file=state_file)
            result = tools.update(1, {"ohlc": []})
            aqt = result.get("auto_quant_tools", {}).get("results", {})
            assert "alpha_stability" in aqt
        finally:
            os.unlink(state_file)


class TestCanonicalMasteryWiring:
    """Test canonical_literary_mastery_engine wired into auto_quant_tools."""

    def test_canonical_mastery_runner_exists(self):
        from core.auto_quant_tools import AutoQuantTools
        assert hasattr(AutoQuantTools, '_run_canonical_mastery')

    def test_canonical_mastery_interval_registered(self):
        from core.auto_quant_tools import DEFAULT_INTERVALS
        assert "canonical_mastery" in DEFAULT_INTERVALS
        assert DEFAULT_INTERVALS["canonical_mastery"] == 200

    def test_canonical_mastery_runner(self):
        from core.auto_quant_tools import AutoQuantTools
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"canonical_mastery": 1}, state_file=state_file)
            result = tools._run_canonical_mastery({"ohlc": [{"close": 1.1000}]})
            assert result["status"] == "OK"
            assert "reflexivity" in result
            assert "all_weather" in result
            assert "antifragility" in result
            assert "rdi" in result["reflexivity"]
            assert "minsky_fragility" in result["reflexivity"]
            assert "phase" in result["reflexivity"]
            assert "allocation" in result["all_weather"]
            assert "empire_stage" in result["all_weather"]
            assert "ferguson_breached" in result["all_weather"]
            assert "convexity" in result["antifragility"]
        finally:
            os.unlink(state_file)

    def test_canonical_mastery_in_update(self):
        from core.auto_quant_tools import AutoQuantTools
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"canonical_mastery": 1}, state_file=state_file)
            result = tools.update(1, {"ohlc": []})
            aqt = result.get("auto_quant_tools", {}).get("results", {})
            assert "canonical_mastery" in aqt
        finally:
            os.unlink(state_file)


class TestRLAnalyticsWiring:
    """Test rl_analytics wired into auto_quant_tools."""

    def test_rl_analytics_runner_exists(self):
        from core.auto_quant_tools import AutoQuantTools
        assert hasattr(AutoQuantTools, '_run_rl_analytics')

    def test_rl_analytics_interval_registered(self):
        from core.auto_quant_tools import DEFAULT_INTERVALS
        assert "rl_analytics" in DEFAULT_INTERVALS
        assert DEFAULT_INTERVALS["rl_analytics"] == 500

    def test_rl_analytics_runner_no_model(self):
        from core.auto_quant_tools import AutoQuantTools
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"rl_analytics": 1}, state_file=state_file)
            result = tools._run_rl_analytics({})
            assert result["status"] in ("NO_MODEL", "OK", "ERROR")
        finally:
            os.unlink(state_file)

    def test_rl_analytics_in_update(self):
        from core.auto_quant_tools import AutoQuantTools
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"rl_analytics": 1}, state_file=state_file)
            result = tools.update(1, {})
            aqt = result.get("auto_quant_tools", {}).get("results", {})
            assert "rl_analytics" in aqt
        finally:
            os.unlink(state_file)


class TestPromptsIncludeNewModules:
    """Verify the LLM prompt template includes the new wired modules."""

    def test_prompt_includes_new_modules(self):
        import pathlib
        src = pathlib.Path("intelligence/llm_orchestrator.py").read_text(encoding="utf-8")
        assert "alpha_stability" in src
        assert "canonical_mastery" in src
        assert "rl_analytics" in src
