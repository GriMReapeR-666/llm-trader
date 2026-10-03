"""
End-to-end paper mode cycle test.
Verifies all tier 1-3 modules are wired and can be invoked.
"""
from __future__ import annotations

import os
import sys
import logging
import pytest

# Ensure project root is on path
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ["core", "intelligence", "operations", "research", "data", "ui"]:
    _p = os.path.join(_root, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
if _root not in sys.path:
    sys.path.insert(0, _root)


class TestEndToEndPaperCycle:
    """Test that a full paper cycle runs without import/wiring errors."""

    def test_all_modules_importable(self):
        """Verify every tier 1-3 module can be imported."""
        # Tier 1
        from core.crash_detector import CrashDetector
        from core.causal_feature_filter import CausalFeatureFilter
        from core.trading_constraints import TradingConstraintEngine

        # Tier 2
        from core.policy_library import PolicyLibrary
        from core.ewc_forgetting_prevention import EWCForgettingPrevention

        # Tier 3
        from core.optimal_execution import OptimalExecutionScheduler
        from core.walk_forward_validator import WalkForwardValidator

        # Alpha system
        from core.alpha_ensemble import combine_alphas, compute_ic_weights
        from core.alpha_discovery import AlphaDiscoveryEngine
        from core.alpha_rotation import AlphaRotationEngine
        from core.alpha_decay_monitor import AlphaDecayMonitor

        # Adaptive system
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        from core.self_improvement_loop import RecursiveSelfImprovementEngine
        from core.evolutionary_optimizer import EvolutionaryOptimizer
        from core.cross_market_benchmark import CrossMarketBenchmark

        # Multimodal
        from core.multimodal_analysis import MultimodalAnalysisEngine

        # Auto tools
        from core.auto_quant_tools import AutoQuantTools

        # Orchestrator
        from intelligence.llm_orchestrator import LLMOrchestrator

    def test_auto_quant_tools_all_tools_registered(self):
        """Verify AutoQuantTools has all 16 tools registered."""
        from core.auto_quant_tools import AutoQuantTools
        tools = AutoQuantTools()
        tool_names = list(tools.intervals.keys())
        assert len(tool_names) >= 16, f"Expected >=16 tools, got {len(tool_names)}: {tool_names}"

        expected = [
            "cpcv", "alpha_discovery", "meta_adaptive",
            "self_improvement", "evolutionary", "cross_market", "multimodal",
            "crash_detector", "causal_filter", "walk_forward",
            "optimal_execution", "ewc_status",
        ]
        for tool in expected:
            assert tool in tool_names, f"Missing tool: {tool}"

    def test_advanced_auto_tools_wiring_fields(self):
        """Verify _apply_advanced_auto_tools covers all tier 1-3 modules."""
        from intelligence.llm_orchestrator import LLMOrchestrator
        import inspect

        src = inspect.getsource(LLMOrchestrator._apply_advanced_auto_tools)

        required_references = [
            "crash_detector",
            "causal_filter",
            "walk_forward",
            "optimal_execution",
            "ewc",
            "meta_adaptive",
            "self_improvement",
        ]
        for ref in required_references:
            assert ref in src.lower(), f"_apply_advanced_auto_tools missing reference to {ref}"

    def test_validate_proposal_gates(self):
        """Verify _validate_proposal has all deterministic gates."""
        from intelligence.llm_orchestrator import LLMOrchestrator
        import inspect

        src = inspect.getsource(LLMOrchestrator._validate_proposal)

        gate_keywords = [
            "walk_forward",
            "optimal_execution",
            "crash_detector",
            "trading_constraints",
        ]
        for gate in gate_keywords:
            assert gate in src.lower(), f"_validate_proposal missing gate: {gate}"

    def test_prompt_template_has_tier1_3_sections(self):
        """Verify prompt template references all tier 1-3 signals."""
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE

        assert "walk_forward" in SIGNAL_SYSTEM_PROMPT_TEMPLATE.lower()
        assert "optimal_execution" in SIGNAL_SYSTEM_PROMPT_TEMPLATE.lower() or "execution_cost" in SIGNAL_SYSTEM_PROMPT_TEMPLATE.lower()
        assert "ewc" in SIGNAL_SYSTEM_PROMPT_TEMPLATE.lower()
        assert "crash_detector" in SIGNAL_SYSTEM_PROMPT_TEMPLATE.lower()
        assert "causal_filter" in SIGNAL_SYSTEM_PROMPT_TEMPLATE.lower()

    def test_toon_lite_has_all_advanced_fields(self):
        """Verify toon_lite dict includes all tier 1-3 fields."""
        from intelligence.llm_orchestrator import LLMOrchestrator
        import inspect

        src_apply = inspect.getsource(LLMOrchestrator._apply_advanced_auto_tools)

        required_fields = [
            "crash_detector_signal",
            "crash_detector_hold_bias",
            "causal_filter_report",
            "walk_forward_decay",
            "walk_forward_hold_bias",
            "optimal_execution_cost",
            "execution_cost_hold_bias",
            "ewc_forgetting_status",
        ]
        for field in required_fields:
            assert field in src_apply, f"_apply_advanced_auto_tools missing field: {field}"

    def test_rl_trainer_ewc_integration(self):
        """Verify RL trainer has EWC integration with correct keyword args."""
        from research.rl_trainer import RLTrainer
        trainer = RLTrainer()

        assert hasattr(trainer, 'snapshot_for_ewc'), "Missing snapshot_for_ewc"
        assert hasattr(trainer, 'get_ewc_status'), "Missing get_ewc_status"

        if trainer._ewc:
            q_dict = {("s1", "a1"): {"BUY": 0.5, "SELL": -0.3}}
            trainer._ewc.snapshot_regime(
                regime="test_regime",
                q_table=q_dict,
                visit_counts={},
                total_updates=10,
            )
            status = trainer._ewc.get_status()
            assert "num_snapshots" in status
            assert status["num_snapshots"] >= 1

    def test_walk_forward_validator_independence(self):
        """Verify walk-forward validator works standalone."""
        from core.walk_forward_validator import WalkForwardValidator
        import numpy as np

        validator = WalkForwardValidator()
        np.random.seed(42)
        predicted = np.random.randn(50)
        actual = predicted * 0.5 + np.random.randn(50) * 0.5

        report = validator.detect_decay(predicted, actual)
        assert hasattr(report, 'status')
        assert report.status in ("HEALTHY", "WATCH", "DECAYING", "DEAD")

    def test_optimal_execution_independence(self):
        """Verify optimal execution scheduler works standalone."""
        from core.optimal_execution import OptimalExecutionScheduler

        scheduler = OptimalExecutionScheduler()
        schedule = scheduler.generate_twap_schedule(
            quantity=1000.0,
            price=1.0850,
            num_slices=5,
            daily_volume=100000.0,
            daily_volatility=0.005,
            bar_volume=5000.0,
            urgency=0.5,
        )
        assert schedule.total_quantity > 0
        assert schedule.algorithm in ("TWAP", "VWAP")
        assert len(schedule.slices) > 0

    def test_crash_detector_independence(self):
        """Verify crash detector works standalone."""
        from core.crash_detector import CrashDetector

        detector = CrashDetector()
        prices = [1.0900 + i * 0.0001 for i in range(30)]
        result = detector.evaluate(prices)
        assert hasattr(result, 'trend')
        assert result.trend.value in ("RAPID_DESCEND", "STEADY_DESCEND", "RAPID_ASCEND", "STEADY_ASCEND", "OSCILLATION")

    def test_alpha_ensemble_independence(self):
        """Verify alpha ensemble functions work standalone."""
        from core.alpha_ensemble import combine_alphas
        import pandas as pd
        import numpy as np

        np.random.seed(42)
        n = 100
        closes = 1.0850 + np.cumsum(np.random.randn(n) * 0.0002)
        alphas = pd.DataFrame({
            "close": closes,
            "alpha_012": np.random.randn(n),
            "alpha_053": np.random.randn(n),
            "alpha_034": np.random.randn(n),
        })

        result = combine_alphas(alphas, ["alpha_012", "alpha_053", "alpha_034"])
        assert hasattr(result, 'composite')
        assert hasattr(result, 'alpha_weights')
        assert hasattr(result, 'n_alphas_used')

    def test_causal_filter_independence(self):
        """Verify causal filter works standalone."""
        from core.causal_feature_filter import CausalFeatureFilter
        import numpy as np

        f = CausalFeatureFilter()
        features = {
            "rsi_14": np.random.randn(50),
            "macd_hist": np.random.randn(50),
            "hma_direction": np.random.randn(50),
        }
        targets = np.random.randn(50)
        report = f.analyze(features, targets)
        assert hasattr(report, 'ranked_features') or hasattr(report, 'spurious_features')

    def test_ewc_forgetting_independence(self):
        """Verify EWC snapshot manager works standalone."""
        import tempfile, os
        from core.ewc_forgetting_prevention import EWCForgettingPrevention

        # Use temp file to avoid interference with persistent state
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            mgr = EWCForgettingPrevention(state_file=tmp)
            q_table = {("s1", "a1"): {"BUY": 0.5, "SELL": -0.3}}
            visit_counts = {("s1", "a1"): {"BUY": 10, "SELL": 3}}

            assert mgr.get_status()["num_snapshots"] == 0
            mgr.snapshot_regime("test_independence", q_table, visit_counts)
            status = mgr.get_status()
            assert status["num_snapshots"] == 1
        finally:
            os.unlink(tmp)

        importance = mgr.get_combined_importance()
        assert isinstance(importance, dict)

    def test_policy_library_independence(self):
        """Verify policy library works standalone."""
        from core.policy_library import PolicyLibrary

        lib = PolicyLibrary()
        policy = lib.get_policy("TRENDING")
        assert hasattr(policy, 'to_dict')

    def test_meta_adaptive_independence(self):
        """Verify meta-adaptive engine works standalone."""
        from core.meta_adaptive_engine import MetaAdaptiveEngine

        engine = MetaAdaptiveEngine()
        profile = engine.get_active_profile()
        assert hasattr(profile, 'to_dict')
        ctx = engine.get_llm_context()
        assert isinstance(ctx, dict)

    def test_full_auto_quant_tools_update(self):
        """Verify AutoQuantTools.update() runs all tools without errors."""
        from core.auto_quant_tools import AutoQuantTools

        tools = AutoQuantTools()
        toon = {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "close": 1.0850,
        }

        ctx = tools.update(cycle_num=1, toon=toon)

        assert "auto_quant_tools" in ctx
        assert isinstance(ctx["auto_quant_tools"], dict)

    def test_trading_constraints_independence(self):
        """Verify trading constraint engine works standalone."""
        from core.trading_constraints import TradingConstraintEngine

        tc = TradingConstraintEngine()
        proposal = {
            "action": "BUY",
            "entry": 1.0850,
            "sl": 1.0830,
            "tp": 1.0890,
            "lots": 0.1,
        }
        context = {
            "balance": 10000,
            "equity": 10000,
            "open_positions": 0,
            "price": 1.0850,
            "spread_pips": 1.2,
        }
        result = tc.evaluate(proposal, context)
        d = result.to_dict()
        assert "overall_verdict" in d
        assert d["overall_verdict"] in ("APPROVE", "REDUCE", "REJECT")
