"""Tests for quant_models_plugins, named profiles, and UrgencySOR wiring."""
import pytest
from unittest.mock import MagicMock, patch
import numpy as np


class TestDomainPlugins:
    """Test the retail quant bundle mounts every engine (leaves folded in)."""

    def _mounted(self):
        from core.harness.quant_models_plugins import RetailQuantBundlePlugin
        from core.harness.kernel import TradingContext
        ctx = TradingContext(name="test_bundle")
        ctx.plugin(RetailQuantBundlePlugin())
        return ctx

    def test_sprt_engine(self):
        with self._mounted() as ctx:
            sprt = ctx.get("sprt_test")
            assert sprt is not None
            assert sprt.p0 == 0.55

    def test_cusum_engine(self):
        with self._mounted() as ctx:
            monitor = ctx.get("cusum_monitor")
            assert monitor is not None
            assert monitor.threshold == 4.0
            assert ctx.get("cusum_structural_break") is not None

    def test_alpha_capacity_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("alpha_capacity_model") is not None

    def test_retail_friction_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("retail_friction_engine") is not None

    def test_bayesian_update_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("bayesian_updater") is not None

    def test_correlation_breakdown_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("corr_breakdown_detector") is not None

    def test_sentiment_veto_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("sentiment_veto") is not None

    def test_account_sizing_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("account_sizer") is not None

    def test_session_spread_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("session_spread_engine") is not None

    def test_ou_time_stop_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("ou_time_stop") is not None

    def test_portfolio_decorr_engine(self):
        with self._mounted() as ctx:
            assert ctx.get("portfolio_decorr") is not None

    def test_retail_quant_bundle(self):
        from core.harness.quant_models_plugins import RetailQuantBundlePlugin
        from core.harness.kernel import TradingContext
        with TradingContext(name="test_bundle") as ctx:
            ctx.plugin(RetailQuantBundlePlugin())
            # All 12 services should be available
            assert ctx.get("sprt_test") is not None
            assert ctx.get("cusum_monitor") is not None
            assert ctx.get("cusum_structural_break") is not None
            assert ctx.get("alpha_capacity_model") is not None
            assert ctx.get("retail_friction_engine") is not None
            assert ctx.get("bayesian_updater") is not None
            assert ctx.get("corr_breakdown_detector") is not None
            assert ctx.get("sentiment_veto") is not None
            assert ctx.get("account_sizer") is not None
            assert ctx.get("session_spread_engine") is not None
            assert ctx.get("ou_time_stop") is not None
            assert ctx.get("portfolio_decorr") is not None


class TestNamedProfiles:
    """Test named profile loading and mounting."""

    def test_list_profiles(self):
        from core.harness.quant_models_plugins import list_profiles
        profiles = list_profiles()
        assert "paper" in profiles
        assert "live" in profiles
        assert "research" in profiles
        assert "backtest" in profiles

    def test_load_paper_profile(self):
        from core.harness.quant_models_plugins import load_profile
        plugins = load_profile("paper")
        assert len(plugins) > 10
        assert all("class_path" in p for p in plugins)

    def test_load_invalid_profile_raises(self):
        from core.harness.quant_models_plugins import load_profile
        with pytest.raises(ValueError, match="Unknown profile"):
            load_profile("nonexistent")

    def test_paper_vs_live_differs(self):
        from core.harness.quant_models_plugins import load_profile
        paper = load_profile("paper")
        live = load_profile("live")
        # Live mounts the broker in live mode, paper in paper mode
        paper_classes = [p["class_path"] for p in paper]
        live_classes = [p["class_path"] for p in live]
        # Both route all data feeds through the unified DataHubPlugin
        assert "core.harness.plugins:DataHubPlugin" in live_classes
        assert "core.harness.plugins:DataHubPlugin" in paper_classes
        live_broker = [p for p in live if p["class_path"].endswith(":BrokerPlugin")]
        paper_broker = [p for p in paper if p["class_path"].endswith(":BrokerPlugin")]
        assert live_broker[0]["kwargs"].get("paper_mode") is False
        assert paper_broker[0]["kwargs"].get("paper_mode") is True
        # Live requires human approval on deliberative graph
        live_delib = [p for p in live if "DeliberativeGraphPlugin" in p["class_path"]]
        assert live_delib[0]["kwargs"].get("require_human_approval") is True

    def test_live_data_hub_provides_news_service(self):
        """DataHub covers the live news feed (previously RealtimeNewsPlugin)."""
        from core.harness.kernel import TradingContext
        from core.harness.plugins import DataHubPlugin
        with TradingContext(name="test_hub_news") as ctx:
            ctx.plugin(DataHubPlugin)
            assert ctx.has("news_ingest_fn")
            assert ctx.has("fred_fetch_fn")
            assert ctx.has("cot_provider")
            assert ctx.has("openbb_bridge")

    def test_research_profile_has_no_broker(self):
        from core.harness.quant_models_plugins import load_profile
        research = load_profile("research")
        classes = [p["class_path"] for p in research]
        assert not any("BrokerPlugin" in c for c in classes)

    def test_backtest_profile_has_no_broker(self):
        from core.harness.quant_models_plugins import load_profile
        backtest = load_profile("backtest")
        classes = [p["class_path"] for p in backtest]
        assert not any("BrokerPlugin" in c for c in classes)

    def test_all_profiles_include_retail_quant_bundle(self):
        from core.harness.quant_models_plugins import load_profile
        for name in ["paper", "live", "research", "backtest"]:
            plugins = load_profile(name)
            classes = [p["class_path"] for p in plugins]
            assert any("RetailQuantBundlePlugin" in c for c in classes), \
                f"Profile '{name}' missing RetailQuantBundlePlugin"


class TestUrgencySORWiring:
    """Test that UrgencySOR is wired into the orchestrator."""

    def test_import_wiring(self):
        """Verify place_urgency_routed_order is importable from orchestrator context."""
        from core.order_execution import place_urgency_routed_order
        assert callable(place_urgency_routed_order)

    def test_urgency_score_computation(self):
        """Verify urgency score is computed from spread and volume."""
        from core.urgency_sor import calculate_alpha_decay_urgency_score
        # Low spread, normal volume → low urgency
        score = calculate_alpha_decay_urgency_score(
            instantaneous_volatility=0.0001,
            order_flow_imbalance=1.0,
            order_book_depth=100.0,
        )
        assert 0.0 <= score <= 1.0

        # High spread → higher urgency
        score_high = calculate_alpha_decay_urgency_score(
            instantaneous_volatility=0.01,
            order_flow_imbalance=5.0,
            order_book_depth=20.0,
        )
        assert score_high > score

    def test_paper_mode_falls_back_to_market(self):
        """In paper mode, urgency routing should fall back to standard market order."""
        from core.order_execution import place_urgency_routed_order, PaperBroker
        broker = PaperBroker()
        # Should not raise, should fall back to market order
        res = place_urgency_routed_order(
            symbol="EURUSD", side="BUY", volume=0.01,
            sl=None, tp=None, comment="test",
            paper=broker,
        )
        assert res is not None

    def test_urgency_routing_in_orchestrator_code(self):
        """Verify the orchestrator source contains urgency routing logic."""
        import ast
        with open("intelligence/llm_orchestrator.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "place_urgency_routed_order" in source
        assert "calculate_alpha_decay_urgency_score" in source
        assert "_urgency_score" in source
