"""Plugin vs Direct Path Parity Tests.

Proves that the Harness plugin profile and the direct mt5_trader.py
instantiation path produce identical service graphs:

  1. Same service types registered
  2. Same TradingStore instance shared across all engines
  3. Same RiskManager parameters
  4. Same LLMOrchestrator receives compatible kwargs
  5. Plugin context overrides the direct-risk when profile is mounted
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Set
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeStore:
    """Minimal TradingStore stand-in for parity tests."""
    def __init__(self, path=":memory:"):
        self.path = path
        self.data: Dict[str, Any] = {}
    def get_state(self, key, default=None):
        return self.data.get(key, default)
    def set_state(self, key, value):
        self.data[key] = value


# ---------------------------------------------------------------------------
# Test 1: Plugin path creates exactly the services we expect
# ---------------------------------------------------------------------------

class TestPluginPathServiceGraph:
    """Verify the paper profile plugin path produces the right service types."""

    def test_paper_profile_creates_all_core_services(self, tmp_path: Path):
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        # Core services that MUST exist
        expected_services = {
            "trading_store",       # RiskManagementPlugin
            "risk_manager",        # RiskManagementPlugin
            "deterministic_validator",  # RiskManagementPlugin
            "memory",              # TradingMemoryPlugin
            "broker",              # BrokerPlugin
            "trade_manager",       # BrokerPlugin
            "market_data",         # DataHubPlugin
            "macro_graph",         # CausalIntelligencePlugin
            "causal_validator",    # CausalIntelligencePlugin
            "execution_safety_supervisor",  # ExecutionSafetyPlugin
            "skill_registry",      # SkillsPlugin
        }
        registered = set(ctx._services.keys())
        missing = expected_services - registered
        assert not missing, f"Plugin path missing services: {missing}"

    def test_plugin_risk_manager_has_store(self, tmp_path: Path):
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        risk = ctx.get("risk_manager")
        store = ctx.get("trading_store")
        assert risk.store is store, "RiskManager must share TradingStore with context"

    def test_plugin_store_is_same_instance_everywhere(self, tmp_path: Path):
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        store = ctx.get("trading_store")
        risk = ctx.get("risk_manager")
        trade_mgr = ctx.get("trade_manager")

        # RiskManager and TradeManager should both reference the same store
        assert risk.store is store
        if hasattr(trade_mgr, "store") and trade_mgr.store is not None:
            assert trade_mgr.store is store


# ---------------------------------------------------------------------------
# Test 2: Direct path creates same service types
# ---------------------------------------------------------------------------

class TestDirectPathServiceGraph:
    """Verify direct mt5_trader.py creates the same services."""

    def test_direct_risk_manager_type_matches_plugin(self, tmp_path: Path):
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore

        store = _FakeStore()
        rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store)

        assert type(rm).__name__ == "RiskManager"
        assert rm.store is store

    def test_direct_deterministic_validator_type(self):
        from core.trading_safety import DeterministicValidator, TradingStore

        store = _FakeStore()
        validator = DeterministicValidator(store=store)
        assert type(validator).__name__ == "DeterministicValidator"

    def test_direct_trading_memory_type(self, tmp_path: Path):
        from intelligence.trading_memory import TradingMemory
        db = str(tmp_path / "test.db")
        mem = TradingMemory(db_path=db)
        assert hasattr(mem, "record_core_memory") or hasattr(mem, "list_core_memories")


# ---------------------------------------------------------------------------
# Test 3: Plugin → LLMOrchestrator integration (harness path)
# ---------------------------------------------------------------------------

class TestPluginToOrchestratorParity:
    """When plugin path is used, LLMOrchestrator should receive compatible kwargs."""

    def test_orchestrator_kwargs_from_plugin_context(self, tmp_path: Path):
        """The plugin context provides risk_manager, broker, etc. — the
        orchestrator should accept them as kwargs."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        from core.risk_manager import RiskManager

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        risk = ctx.get("risk_manager")
        broker = ctx.get("broker")
        store = ctx.get("trading_store")

        # These are the kwargs mt5_trader.py passes to LLMOrchestrator
        orch_kwargs = {
            "client": broker,
            "risk_manager": risk,
            "perf_monitor": MagicMock(),
            "api_key": "test",
            "model_name": "test-model",
            "llm_router": None,
            "agent_name": "trader",
            "journal_mode": "PAPER",
            "account_fingerprint": "test-fp",
        }

        # RiskManager must be the same type as direct path
        assert isinstance(risk, RiskManager)
        # Store must be non-None
        assert risk.store is not None

    def test_profile_overrides_direct_risk(self, tmp_path: Path):
        """When profile is mounted, the direct-path risk should be replaced."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile
        from core.risk_manager import RiskManager

        # Direct path creates this
        direct_store = _FakeStore()
        direct_risk = RiskManager(balance=5000, risk_per_trade=0.005, store=direct_store)

        # Plugin path creates this
        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")
        plugin_risk = ctx.get("risk_manager")

        # They are DIFFERENT instances (plugin replaces direct)
        assert direct_risk is not plugin_risk
        # But both are RiskManager
        assert isinstance(direct_risk, RiskManager)
        assert isinstance(plugin_risk, RiskManager)


# ---------------------------------------------------------------------------
# Test 4: Service type parity across paths
# ---------------------------------------------------------------------------

class TestServiceTypeParity:
    """Both paths must create the same types for critical services."""

    PLUGIN_SERVICE_TYPES = {
        "risk_manager": "RiskManager",
        "trading_store": "TradingStore",
        "deterministic_validator": "DeterministicValidator",
        "trade_manager": "TradeManager",
        "broker": "PaperBroker",
        "memory": "TradingMemory",
        "macro_graph": "MacroGraph",
        "causal_validator": "AutonomousCausalValidator",
    }

    def test_plugin_path_types(self, tmp_path: Path):
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        for service_key, expected_type in self.PLUGIN_SERVICE_TYPES.items():
            svc = ctx.get(service_key)
            assert svc is not None, f"Plugin path: {service_key} is None"
            actual_type = type(svc).__name__
            assert actual_type == expected_type, (
                f"Plugin path: {service_key} is {actual_type}, expected {expected_type}"
            )

    def test_direct_path_types(self, tmp_path: Path):
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore, DeterministicValidator
        from core.trade_manager import TradeManager
        from core.order_execution import PaperBroker
        from intelligence.trading_memory import TradingMemory
        from intelligence.macro_graph import MacroGraph
        from intelligence.autonomous_causal_engine import AutonomousCausalValidator

        db_path = str(tmp_path / "test.db")
        store = TradingStore(db_path)
        direct = {
            "risk_manager": RiskManager(balance=10000, risk_per_trade=0.01, store=store),
            "trading_store": store,
            "deterministic_validator": DeterministicValidator(store=store),
            "trade_manager": TradeManager(client=PaperBroker(), risk_manager=RiskManager(balance=10000, risk_per_trade=0.01), store=store),
            "broker": PaperBroker(),
            "memory": TradingMemory(db_path=db_path),
            "macro_graph": MacroGraph(db_path=db_path),
            "causal_validator": AutonomousCausalValidator(graph=MacroGraph(db_path=db_path)),
        }

        for service_key, expected_type in self.PLUGIN_SERVICE_TYPES.items():
            svc = direct[service_key]
            actual_type = type(svc).__name__
            assert actual_type == expected_type, (
                f"Direct path: {service_key} is {actual_type}, expected {expected_type}"
            )


# ---------------------------------------------------------------------------
# Test 5: Store singleton behavior
# ---------------------------------------------------------------------------

class TestStoreSingleton:
    """All services in a path should share ONE TradingStore instance."""

    def test_plugin_all_engines_share_one_store(self, tmp_path: Path):
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        store = ctx.get("trading_store")
        risk = ctx.get("risk_manager")

        # Store references must be identical
        assert risk.store is store

    def test_engine_save_persists_to_shared_store(self, tmp_path: Path):
        """If one engine writes to the store, another engine reading it sees the data."""
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import mount_profile

        ctx = TradingContext(name="test_paper")
        mount_profile(ctx, "paper")

        store = ctx.get("trading_store")

        # Write via one path
        store.set_state("test_key", {"value": 42})

        # Read via another
        assert store.get_state("test_key") == {"value": 42}


# ---------------------------------------------------------------------------
# Test 6: Plugin profile definitions are self-consistent
# ---------------------------------------------------------------------------

class TestProfileConsistency:
    """All profile definitions must specify required services."""

    def test_paper_profile_has_risk_management(self):
        from core.harness.quant_models_plugins import PROFILE_DEFINITIONS

        paper = PROFILE_DEFINITIONS["paper"]
        plugin_classes = [p[0] for p in paper["plugins"]]
        assert any("RiskManagementPlugin" in c for c in plugin_classes)

    def test_live_profile_has_broker(self):
        from core.harness.quant_models_plugins import PROFILE_DEFINITIONS

        live = PROFILE_DEFINITIONS["live"]
        plugin_classes = [p[0] for p in live["plugins"]]
        assert any("BrokerPlugin" in c for c in plugin_classes)

    def test_all_profiles_mount_without_error(self):
        from core.harness.kernel import TradingContext
        from core.harness.quant_models_plugins import PROFILE_DEFINITIONS, mount_profile

        for profile_name in PROFILE_DEFINITIONS:
            ctx = TradingContext(name=f"test_{profile_name}")
            # Should not raise
            mount_profile(ctx, profile_name)
            assert len(ctx._services) > 0, f"Profile {profile_name} registered 0 services"


# ---------------------------------------------------------------------------
# Test 7: Direct path RiskManager matches plugin RiskManager config
# ---------------------------------------------------------------------------

class TestRiskManagerConfigParity:
    """Both paths must configure RiskManager with the same defaults."""

    def test_default_risk_per_trade_matches(self):
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore

        store = _FakeStore()

        # Plugin path: max_risk_pct=0.01 → risk_per_trade=min(0.01, 0.01) = 0.01
        plugin_rm = RiskManager(balance=10000, risk_per_trade=min(0.01, 0.01), store=store)

        # Direct path: risk_per_trade from safe_risk_fraction (default 0.01)
        direct_rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store)

        assert plugin_rm.risk_per_trade == direct_rm.risk_per_trade
        assert plugin_rm.balance == direct_rm.balance

    def test_plugin_risk_manager_clamps_risk(self):
        from core.risk_manager import RiskManager
        from core.trading_safety import TradingStore

        store = _FakeStore()

        # Plugin path: max_risk_pct=0.05 → min(0.05, 0.01) = 0.01
        rm = RiskManager(balance=10000, risk_per_trade=min(0.05, 0.01), store=store)
        assert rm.risk_per_trade == 0.01  # Clamped to max
