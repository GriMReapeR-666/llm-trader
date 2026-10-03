"""Tests for GRPO proposer and PPO trainer wired into auto_quant_tools."""
import json
import os
import tempfile
import pytest


class TestGRPOWiring:
    """Test grpo_proposer wired into auto_quant_tools."""

    def test_grpo_runner_exists(self):
        from core.auto_quant_tools import AutoQuantTools
        assert hasattr(AutoQuantTools, '_run_grpo_train')

    def test_grpo_interval_registered(self):
        from core.auto_quant_tools import DEFAULT_INTERVALS
        assert "grpo_train" in DEFAULT_INTERVALS
        assert DEFAULT_INTERVALS["grpo_train"] == 4032  # bi-weekly

    def test_grpo_runner_produces_report(self):
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"grpo_train": 1}, state_file=state_file)
            result = tools._run_grpo_train({})
            assert result["status"] == "OK"
            assert result["execution_authority"] is False
            assert result["promotion_safe"] is False
            assert result["risk_controller_frozen"] is True
            assert "mean_group_reward" in result
            assert "artifact_path" in result
        finally:
            os.unlink(state_file)

    def test_grpo_in_update(self):
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"grpo_train": 1}, state_file=state_file)
            result = tools.update(1, {})
            aqt = result.get("auto_quant_tools", {}).get("results", {})
            assert "grpo_train" in aqt
        finally:
            os.unlink(state_file)

    def test_grpo_artifact_written(self):
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"grpo_train": 1}, state_file=state_file)
            result = tools._run_grpo_train({})
            artifact_path = result.get("artifact_path", "")
            assert os.path.exists(artifact_path), f"Artifact not found: {artifact_path}"
            with open(artifact_path) as f:
                data = json.load(f)
            assert "proposer_policy" in data or "model_kind" in data or "q_table" in data
        finally:
            os.unlink(state_file)


class TestPPOWiring:
    """Test ppo_trainer wired into auto_quant_tools."""

    def test_ppo_runner_exists(self):
        from core.auto_quant_tools import AutoQuantTools
        assert hasattr(AutoQuantTools, '_run_ppo_train')

    def test_ppo_interval_registered(self):
        from core.auto_quant_tools import DEFAULT_INTERVALS
        assert "ppo_train" in DEFAULT_INTERVALS
        assert DEFAULT_INTERVALS["ppo_train"] == 4032  # bi-weekly

    def test_ppo_runner_produces_report(self):
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"ppo_train": 1}, state_file=state_file)
            result = tools._run_ppo_train({})
            assert result["status"] == "OK"
            assert result["execution_authority"] is False
            assert result["promotion_safe"] is False
            assert "mean_episode_reward" in result
            assert "artifact_path" in result
            assert "regime" in result
        finally:
            os.unlink(state_file)

    def test_ppo_in_update(self):
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"ppo_train": 1}, state_file=state_file)
            result = tools.update(1, {})
            aqt = result.get("auto_quant_tools", {}).get("results", {})
            assert "ppo_train" in aqt
        finally:
            os.unlink(state_file)

    def test_ppo_artifact_written(self):
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"ppo_train": 1}, state_file=state_file)
            result = tools._run_ppo_train({})
            artifact_path = result.get("artifact_path", "")
            assert os.path.exists(artifact_path), f"Artifact not found: {artifact_path}"
            with open(artifact_path) as f:
                data = json.load(f)
            assert "mean_episode_reward" in data or "policy" in data or "model_kind" in data
        finally:
            os.unlink(state_file)

    def test_ppo_regime_detection(self):
        """PPO should detect regime from OHLC data."""
        from core.auto_quant_tools import AutoQuantTools
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name
        try:
            tools = AutoQuantTools(intervals={"ppo_train": 1}, state_file=state_file)
            # High volatility OHLC
            ohlc = [{"close": 1.1000 + i * 0.001 * ((-1)**i) * 2} for i in range(60)]
            result = tools._run_ppo_train({"ohlc": ohlc})
            assert result["status"] == "OK"
            assert result["regime"] in ("volatile", "trend_up", "trend_down", "range")
        finally:
            os.unlink(state_file)


class TestPromptsIncludeGRPOPPO:
    """Verify LLM prompt includes GRPO and PPO advisory context."""

    def test_prompt_includes_grpo_ppo(self):
        import pathlib
        src = pathlib.Path("intelligence/llm_orchestrator.py").read_text(encoding="utf-8")
        assert "grpo_train" in src
        assert "ppo_train" in src
        assert "GRPO proposer" in src or "grpo_train" in src
        assert "PPO position-management" in src or "ppo_train" in src
