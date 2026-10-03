"""Tests for the StateManager adapter layer.

Validates that:
1. TradingStore is wired into RiskManager at startup
2. migrate_json_states_to_store backfills JSON files idempotently
3. StoreBackedState engines read from store when available
4. State migration status reports correctly
5. Fallback to JSON when store is unavailable
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fake Store (matches TradingStore interface)
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self):
        self.data: Dict[str, Any] = {}
        self.path = ":memory:"

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update_state(self, key: str, mutator, default: Any = None) -> Any:
        current = self.data.get(key, default)
        result = mutator(current)
        self.data[key] = result
        return result


# ---------------------------------------------------------------------------
# Test 1: RiskManager accepts and stores the store reference
# ---------------------------------------------------------------------------

class TestRiskManagerStoreWiring:
    def test_risk_manager_stores_reference(self):
        from core.risk_manager import RiskManager
        store = _FakeStore()
        rm = RiskManager(balance=10000, risk_per_trade=0.01, store=store)
        assert rm.store is store
        assert rm.store.get_state("nonexistent") is None

    def test_risk_manager_without_store_defaults_to_none(self):
        from core.risk_manager import RiskManager
        rm = RiskManager(balance=10000, risk_per_trade=0.01)
        assert rm.store is None


# ---------------------------------------------------------------------------
# Test 2: Auto-migration from JSON files to store
# ---------------------------------------------------------------------------

class TestAutoMigration:
    def test_migrate_backfills_json_files(self, tmp_path: Path):
        from core.state_persistence import migrate_json_states_to_store

        # Create a fake store and a JSON state file
        store = _FakeStore()
        state_file = tmp_path / "auto_quant_tools_state.json"
        state_file.write_text(json.dumps({
            "cycle_count": 100,
            "last_results": {"bocpd": {"status": "OK"}},
        }))

        result = migrate_json_states_to_store(store, tmp_path)
        assert result["imported"] >= 1
        assert store.get_state("auto_quant_tools_state") is not None
        assert store.get_state("auto_quant_tools_state")["cycle_count"] == 100

    def test_migrate_is_idempotent(self, tmp_path: Path):
        from core.state_persistence import migrate_json_states_to_store

        store = _FakeStore()
        state_file = tmp_path / "ewc_state.json"
        state_file.write_text(json.dumps({"snapshots": []}))

        first = migrate_json_states_to_store(store, tmp_path)
        assert first["imported"] >= 1

        second = migrate_json_states_to_store(store, tmp_path)
        assert second["imported"] == 0  # already present
        assert second["skipped"] >= 1
        assert second["passed"] is True

    def test_migrate_does_not_overwrite_store(self, tmp_path: Path):
        from core.state_persistence import migrate_json_states_to_store

        store = _FakeStore()
        store.set_state("ewc_state", {"snapshots": [{"regime": "live"}]})

        state_file = tmp_path / "ewc_state.json"
        state_file.write_text(json.dumps({"snapshots": []}))

        result = migrate_json_states_to_store(store, tmp_path)
        assert result["keys"]["ewc_state"]["action"] == "skipped_store_present"
        assert store.get_state("ewc_state")["snapshots"][0]["regime"] == "live"

    def test_migrate_skips_missing_files(self, tmp_path: Path):
        from core.state_persistence import migrate_json_states_to_store

        store = _FakeStore()
        result = migrate_json_states_to_store(store, tmp_path)
        # All keys should be skipped (no files exist)
        assert all(
            v["action"] in ("skipped_no_file_data", "skipped_no_store")
            for v in result["keys"].values()
        )


# ---------------------------------------------------------------------------
# Test 3: State migration status
# ---------------------------------------------------------------------------

class TestMigrationStatus:
    def test_status_reports_file_and_store_presence(self, tmp_path: Path):
        from core.state_persistence import state_migration_status

        store = _FakeStore()
        # Create one file
        state_file = tmp_path / "meta_loop_state.json"
        state_file.write_text(json.dumps({"total_proposals": 6}))

        status = state_migration_status(store, tmp_path)
        assert status["total_keys"] > 0
        meta = status["keys"]["meta_loop_state"]
        assert meta["file_present"] is True
        assert meta["store_present"] is False
        assert status["fully_migrated"] is False

    def test_status_after_migration(self, tmp_path: Path):
        from core.state_persistence import migrate_json_states_to_store, state_migration_status

        store = _FakeStore()
        state_file = tmp_path / "meta_loop_state.json"
        state_file.write_text(json.dumps({"total_proposals": 6}))

        migrate_json_states_to_store(store, tmp_path)
        status = state_migration_status(store, tmp_path)
        meta = status["keys"]["meta_loop_state"]
        assert meta["store_present"] is True
        assert meta["in_sync"] is True

    def test_status_detects_drift(self, tmp_path: Path):
        from core.state_persistence import migrate_json_states_to_store, state_migration_status

        store = _FakeStore()
        state_file = tmp_path / "meta_loop_state.json"
        state_file.write_text(json.dumps({"total_proposals": 6}))

        migrate_json_states_to_store(store, tmp_path)
        # Mutate store
        store.set_state("meta_loop_state", {"total_proposals": 42})
        status = state_migration_status(store, tmp_path)
        assert status["keys"]["meta_loop_state"]["in_sync"] is False


# ---------------------------------------------------------------------------
# Test 4: StoreBackedState reads from store when attached
# ---------------------------------------------------------------------------

class TestStoreBackedStateIntegration:
    def test_auto_quant_tools_loads_from_store(self, tmp_path: Path):
        from core.auto_quant_tools import AutoQuantTools

        store = _FakeStore()
        store.set_state("auto_quant_tools_state", {
            "cycle_count": 999,
            "last_results": {},
            "alert_escalation": {},
        })

        engine = AutoQuantTools(
            state_file=str(tmp_path / "auto_quant_tools_state.json"),
            store=store,
        )
        assert engine.state.cycle_count == 999

    def test_auto_quant_tools_falls_back_to_json(self, tmp_path: Path):
        from core.auto_quant_tools import AutoQuantTools

        state_file = tmp_path / "auto_quant_tools_state.json"
        state_file.write_text(json.dumps({
            "cycle_count": 42,
            "last_results": {},
            "alert_escalation": {},
        }))

        engine = AutoQuantTools(state_file=str(state_file), store=None)
        assert engine.state.cycle_count == 42

    def test_walk_forward_loads_from_store(self, tmp_path: Path):
        from core.walk_forward_validator import WalkForwardValidator

        store = _FakeStore()
        store.set_state("walk_forward_state", {
            "ic_history": [0.1, 0.2],
            "status": "HEALTHY",
            "hold_bias": False,
            "last_update_cycle": 50,
            "decay_score": 0.0,
        })

        engine = WalkForwardValidator(
            state_file=str(tmp_path / "walk_forward_state.json"),
            store=store,
        )
        assert engine.state["ic_history"] == [0.1, 0.2]
        assert engine.state["status"] == "HEALTHY"

    def test_meta_loop_loads_from_store(self, tmp_path: Path):
        from core.meta_loop import MetaLoop

        store = _FakeStore()
        store.set_state("meta_loop_state", {
            "total_proposals": 10,
            "applied_changes": [],
        })

        engine = MetaLoop(
            state_path=str(tmp_path / "meta_loop_state.json"),
            store=store,
        )
        assert engine._state["total_proposals"] == 10


# ---------------------------------------------------------------------------
# Test 5: All STATE_REGISTRY keys are accessible
# ---------------------------------------------------------------------------

class TestStateRegistryCompleteness:
    def test_registry_has_18_canonical_keys(self):
        from core.state_persistence import STATE_REGISTRY
        # 18 canonical runtime states
        canonical = [
            "alpha_decay_state", "alpha_discovery_state", "auto_quant_tools_state",
            "autonomy_state", "causal_filter_state", "crash_detector_state",
            "cross_market_benchmark", "evolutionary_state", "ewc_state",
            "meta_loop_state", "multimodal_analysis_state", "self_improvement_state",
            "self_improvement_state_memory", "risk_manager_state",
            "trade_manager_state", "trade_manager_conditions", "supervisor_state",
            "walk_forward_state",
        ]
        for key in canonical:
            assert key in STATE_REGISTRY, f"Missing canonical key: {key}"

    def test_all_keys_have_json_filenames(self):
        from core.state_persistence import STATE_REGISTRY
        for key, filename in STATE_REGISTRY.items():
            assert filename.endswith(".json"), f"Key {key} maps to non-JSON: {filename}"


# ---------------------------------------------------------------------------
# Test 6: Fallback when store is unavailable
# ---------------------------------------------------------------------------

class TestFallbackBehavior:
    def test_engine_works_without_store(self, tmp_path: Path):
        """StoreBackedState should gracefully fall back to JSON when store=None."""
        from core.auto_quant_tools import AutoQuantTools

        state_file = tmp_path / "auto_quant_tools_state.json"
        state_file.write_text(json.dumps({
            "cycle_count": 7,
            "last_results": {},
            "alert_escalation": {},
        }))

        engine = AutoQuantTools(state_file=str(state_file), store=None)
        assert engine.state.cycle_count == 7
        # Save should write to JSON
        engine._save_state()
        reloaded = AutoQuantTools(state_file=str(state_file), store=None)
        assert reloaded.state.cycle_count == 7
