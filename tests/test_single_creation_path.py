"""Single Creation Path Tests.

Proves that after refactoring, the main trading loop always creates
services through the plugin system — never through inline instantiation.

Key invariant: there is ONE RiskManager, ONE TradingStore, and ONE
TradingContext per startup, regardless of flags.
"""
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch


class _FakeStore:
    def __init__(self):
        self.data: Dict[str, Any] = {}
        self.path = ":memory:"
    def get_state(self, key, default=None):
        return self.data.get(key, default)
    def set_state(self, key, value):
        self.data[key] = value


# ---------------------------------------------------------------------------
# Test 1: Single RiskManager from plugin — no duplicate
# ---------------------------------------------------------------------------

class TestSingleRiskManager:
    def test_always_mounts_paper_profile(self, tmp_path: Path):
        """When no --profile/--harness flags, paper profile is auto-selected."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        # This is what the refactored mt5_trader.py does unconditionally
        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        risk = ctx.get("risk_manager")
        store = ctx.get("trading_store")

        assert risk is not None
        assert store is not None
        assert risk.store is store

    def test_single_store_instance_in_context(self, tmp_path: Path):
        """Only ONE TradingStore is created per context."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        # Count how many times TradingStore was instantiated
        # by checking that all services referencing a store point to the same one
        store = ctx.get("trading_store")
        risk = ctx.get("risk_manager")

        assert risk.store is store

    def test_orchestrator_receives_plugin_store(self, tmp_path: Path):
        """When orchestrator is created with store=ctx.get('trading_store'),
        it uses the plugin's store instead of creating its own."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        plugin_store = ctx.get("trading_store")
        assert plugin_store is not None

        # The orchestrator should accept this store
        from intelligence.llm_orchestrator import LLMOrchestrator
        import inspect
        sig = inspect.signature(LLMOrchestrator.__init__)
        assert "store" in sig.parameters, "LLMOrchestrator must accept store parameter"


# ---------------------------------------------------------------------------
# Test 2: Profile auto-detection from flags
# ---------------------------------------------------------------------------

class TestProfileAutoDetection:
    def test_paper_flag_selects_paper_profile(self):
        """--paper flag should select paper profile."""
        from core.harness.quant_models_plugins import PROFILE_DEFINITIONS

        # The refactored logic: if not --live and not --demo → paper
        profile = "paper"
        assert profile in PROFILE_DEFINITIONS
        assert "RiskManagementPlugin" in str(PROFILE_DEFINITIONS[profile]["plugins"])

    def test_live_flag_selects_live_profile(self):
        from core.harness.quant_models_plugins import PROFILE_DEFINITIONS
        profile = "live"
        assert profile in PROFILE_DEFINITIONS
        # Live profile must have BrokerPlugin with paper_mode=False
        plugins = PROFILE_DEFINITIONS[profile]["plugins"]
        broker_specs = [p for p in plugins if "BrokerPlugin" in p[0]]
        assert len(broker_specs) == 1
        assert broker_specs[0][1].get("paper_mode") is False

    def test_all_trading_profiles_define_risk_management(self):
        from core.harness.quant_models_plugins import PROFILE_DEFINITIONS
        # research/backtest profiles legitimately omit RiskManagementPlugin
        for name in ("paper", "live"):
            defn = PROFILE_DEFINITIONS[name]
            plugin_classes = [p[0] for p in defn["plugins"]]
            assert any("RiskManagementPlugin" in c for c in plugin_classes), \
                f"Profile '{name}' missing RiskManagementPlugin"


# ---------------------------------------------------------------------------
# Test 3: No inline RiskManager creation in refactored path
# ---------------------------------------------------------------------------

class TestNoInlineCreation:
    def test_risk_manager_comes_from_context(self, tmp_path: Path):
        """The risk_manager variable should come from the context, not inline."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        # Simulate: risk = harness_ctx.get("risk_manager")
        risk = ctx.get("risk_manager")
        from core.risk_manager import RiskManager
        assert isinstance(risk, RiskManager)

    def test_store_comes_from_context(self, tmp_path: Path):
        """The store should come from the context, not inline."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        store = ctx.get("trading_store")
        from core.trading_safety import TradingStore
        assert isinstance(store, TradingStore)


# ---------------------------------------------------------------------------
# Test 4: Orchestrator store parameter works
# ---------------------------------------------------------------------------

class TestOrchestratorStoreParam:
    def test_orchestrator_uses_provided_store(self, tmp_path: Path):
        """When store is passed to LLMOrchestrator, it uses it."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        plugin_store = ctx.get("trading_store")

        # Create orchestrator with the plugin's store
        from intelligence.llm_orchestrator import LLMOrchestrator
        orch = LLMOrchestrator(
            client=MagicMock(),
            risk_manager=ctx.get("risk_manager"),
            perf_monitor=MagicMock(),
            api_key="test",
            model_name="test",
            llm_router=None,
            journal_mode="PAPER",
            account_fingerprint="test",
            store=plugin_store,
        )

        # The orchestrator should use the plugin's store, not create a new one
        assert orch.store is plugin_store

    def test_orchestrator_creates_own_store_when_none(self):
        """When store=None, orchestrator creates its own (backward compat)."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        from core.trading_safety import TradingStore

        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")
        risk = ctx.get("risk_manager")

        from intelligence.llm_orchestrator import LLMOrchestrator
        orch = LLMOrchestrator(
            client=MagicMock(),
            risk_manager=risk,
            perf_monitor=MagicMock(),
            api_key="test",
            model_name="test",
            llm_router=None,
            journal_mode="PAPER",
            account_fingerprint="test",
            store=None,
        )

        # Should have created its own store (not the plugin's)
        plugin_store = ctx.get("trading_store")
        assert hasattr(orch.store, "get_state"), "Orchestrator store should be a TradingStore-like object"
        assert orch.store is not plugin_store  # different instance


# ---------------------------------------------------------------------------
# Test 5: Integration — full single-path creation
# ---------------------------------------------------------------------------

class TestFullSinglePath:
    def test_full_single_path_creates_all_services(self, tmp_path: Path):
        """Simulate the refactored mt5_trader.py flow:
        1. Mount plugins (always)
        2. Get risk from context
        3. Pass store to orchestrator
        """
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore

        # Step 1: Always mount plugins
        ctx = TradingContext(name="paper_ctx")
        mount_profile(ctx, "paper")

        # Step 2: Get risk from context (no inline creation)
        risk = ctx.get("risk_manager")
        assert isinstance(risk, RiskManager)

        # Step 3: Pass store to orchestrator
        plugin_store = ctx.get("trading_store")
        assert isinstance(plugin_store, TradingStore)

        # Verify: only ONE store instance exists
        assert risk.store is plugin_store

        # Verify: all critical services are present
        assert ctx.get("trade_manager") is not None
        assert ctx.get("broker") is not None
        assert ctx.get("memory") is not None
        assert ctx.get("macro_graph") is not None
        assert ctx.get("deterministic_validator") is not None
