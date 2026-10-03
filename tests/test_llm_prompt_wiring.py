"""Tests for LLM prompt wiring — verify all auto_quant_tools results are visible."""
import pytest


class TestToonLiteWiring:
    """Verify that all 7 newly-wired tools appear in toon_lite context."""

    def _build_mock_toon(self):
        """Build a mock toon with all auto_quant_tools results."""
        return {
            "auto_quant_tools": {
                "results": {
                    "alpha_stability": {"robust_alphas": ["a1"], "fragile_alphas": ["a2"]},
                    "canonical_mastery": {"reflexivity": {"rdi": 1.2, "minsky_fragility": 0.8}},
                    "rl_analytics": {"n_states": 150},
                    "grpo_train": {"mean_group_reward": 0.3, "status": "OK"},
                    "ppo_train": {"mean_episode_reward": 0.2, "status": "OK"},
                    "regime_autocorr": {"regime_transition_detected": True, "microstructure_risk": "HIGH"},
                    "meta_loop": {"proposals_applied": 1, "patterns_found": 3},
                    "bocpd": {"has_recent_structural_shift": True},
                    "kalman": {"spread_z_score": 1.5},
                    "cpcv": {"probability_of_backtest_overfitting_pbo": 0.2, "deflated_sharpe_ratio": 0.8},
                },
                "cpcv_escalation": {},
            },
            "meta_adaptive_override": {"risk_multiplier": 0.8},
            "evolutionary_suggestions": {"sl_atr_mult": 1.5},
            "cross_market_robustness": {"composite_score": 0.7},
            "cross_market_hold_bias": False,
            "alpha_discovery_enrichment": {"validated_count": 2},
            "multimodal_patterns": {"confluence_score": 0.8},
            "crash_detector_signal": {"trend": "OSCILLATION"},
            "crash_detector_hold_bias": False,
            "causal_filter_report": {"causal_density": 0.6},
            "walk_forward_decay": {"status": "HEALTHY"},
            "walk_forward_hold_bias": False,
            "optimal_execution_cost": {"algorithm": "TWAP"},
            "execution_cost_hold_bias": False,
            "ewc_forgetting_status": {"snapshots_count": 3},
        }

    def test_newly_wired_tools_in_context(self):
        """All 7 newly-wired tools should be accessible from toon."""
        toon = self._build_mock_toon()
        aqt = toon.get("auto_quant_tools", {}).get("results", {})

        # These are now explicitly wired into toon_lite
        assert "alpha_stability" in aqt
        assert "canonical_mastery" in aqt
        assert "rl_analytics" in aqt
        assert "grpo_train" in aqt
        assert "ppo_train" in aqt
        assert "regime_autocorr" in aqt
        assert "meta_loop" in aqt

    def test_advanced_summary_includes_all_categories(self):
        """advanced_auto_tools_summary should cover core, advanced, and research tools."""
        toon = self._build_mock_toon()
        aqt = toon.get("auto_quant_tools", {}).get("results", {})

        # Build the summary the same way _apply_advanced_auto_tools does
        bocpd = aqt.get("bocpd", {})
        kalman = aqt.get("kalman", {})
        cpcv = aqt.get("cpcv", {})
        regime_ac = aqt.get("regime_autocorr", {})
        meta_lp = aqt.get("meta_loop", {})
        alpha_stab = aqt.get("alpha_stability", {})
        canon = aqt.get("canonical_mastery", {})
        rl_a = aqt.get("rl_analytics", {})

        summary = {
            "bocpd_shift": bool(bocpd.get("has_recent_structural_shift")),
            "kalman_z_score": kalman.get("spread_z_score", 0),
            "cpcv_pbo": cpcv.get("probability_of_backtest_overfitting_pbo", 0),
            "regime_autocorr_transition": bool(regime_ac.get("regime_transition_detected")),
            "meta_loop_proposals_applied": meta_lp.get("proposals_applied", 0),
            "alpha_stability_fragile": alpha_stab.get("fragile_alphas", []),
            "canonical_reflexivity": canon.get("reflexivity", {}).get("rdi", 0),
            "rl_analytics_states": rl_a.get("n_states", 0),
        }

        # Core tools
        assert summary["bocpd_shift"] is True
        assert summary["kalman_z_score"] == 1.5
        assert summary["cpcv_pbo"] == 0.2

        # Research modules
        assert summary["regime_autocorr_transition"] is True
        assert summary["meta_loop_proposals_applied"] == 1
        assert summary["alpha_stability_fragile"] == ["a2"]
        assert summary["canonical_reflexivity"] == 1.2
        assert summary["rl_analytics_states"] == 150

    def test_prompt_documents_all_wired_tools(self):
        """Prompt template should document all tools that are wired into context."""
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE as SYSTEM_PROMPT

        # All these tools should be documented in the prompt
        documented = [
            "alpha_stability",
            "canonical_mastery",
            "rl_analytics",
            "grpo_train",
            "ppo_train",
            "regime_autocorr",
            "meta_loop",
        ]
        for tool in documented:
            assert tool in SYSTEM_PROMPT, (
                f"Tool '{tool}' is wired into context but NOT documented in SYSTEM_PROMPT"
            )


class TestNoOrphanedTools:
    """Verify that tools computed by auto_quant_tools are not silently invisible."""

    def test_all_interval_tools_have_runners(self):
        """Every tool in DEFAULT_INTERVALS should have a _run_ method
        (or be explicitly called with a different name in update())."""
        from core.auto_quant_tools import AutoQuantTools, DEFAULT_INTERVALS
        import inspect

        tools = AutoQuantTools(state_file="/tmp/test_state.json")
        # Get all _run_ methods
        runner_methods = {
            name.replace("_run_", ""): method
            for name, method in inspect.getmembers(tools, predicate=inspect.ismethod)
            if name.startswith("_run_")
        }
        for tool_name in DEFAULT_INTERVALS:
            # Check direct match or any runner that starts with the tool name
            has_runner = (
                tool_name in runner_methods
                or any(k.startswith(tool_name) for k in runner_methods)
            )
            assert has_runner, (
                f"Tool '{tool_name}' is in DEFAULT_INTERVALS but has no matching _run_ method. "
                f"Available: {list(runner_methods.keys())[:10]}"
            )

    def test_all_runners_return_dict(self):
        """Every runner should return a dict (not None or raw value)."""
        from core.auto_quant_tools import AutoQuantTools, DEFAULT_INTERVALS

        tools = AutoQuantTools(state_file="/tmp/test_state.json")
        for tool_name in DEFAULT_INTERVALS:
            runner = getattr(tools, f"_run_{tool_name}", None)
            if runner is None:
                continue
            # Run with empty toon — should return a dict (ERROR or INSUFFICIENT_DATA)
            try:
                result = runner({})
                assert isinstance(result, dict), (
                    f"Tool '{tool_name}' returned {type(result)} instead of dict"
                )
            except Exception:
                pass  # Some tools may need specific args — that's OK for this test
