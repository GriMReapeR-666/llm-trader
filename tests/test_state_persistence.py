"""Unit tests for the centralized StoreBackedState transport + migration pipeline."""
import json
from pathlib import Path

import pytest

from state_persistence import (
    STATE_REGISTRY,
    StoreBackedState,
    atomic_write_json,
    migrate_json_states_to_store,
    read_json_dict,
    state_migration_status,
)
from trading_safety import TradingStore


CANONICAL_FILES = [
    "alpha_decay_state.json", "alpha_discovery_state.json", "auto_quant_tools_state.json",
    "autonomy_state.json", "causal_filter_state.json", "crash_detector_state.json",
    "cross_market_benchmark.json", "evolutionary_state.json", "ewc_state.json",
    "meta_loop_state.json", "multimodal_analysis_state.json", "self_improvement_state.json",
    "self_improvement_state_memory.json", "risk_state.json", "trade_manager_state.json",
    "trade_conditions.json", "supervisor_state.json", "walk_forward_state.json",
]


class _Engine(StoreBackedState):
    store_key = "test_engine_state"

    def __init__(self, state_file="", store=None):
        self.state_file = state_file
        self.store = store
        self.state = self._load_state()
        if store is not None:
            self.attach_store(store)

    def _load_state(self):
        return self._load_state_dict({"count": 0})

    def _save_state(self):
        self._save_state_dict(self.state)


def _db(tmp_path: Path) -> TradingStore:
    return TradingStore(str(tmp_path / "state.db"))


def test_registry_covers_all_canonical_files():
    assert set(CANONICAL_FILES) <= set(STATE_REGISTRY.values())


def test_file_only_roundtrip_without_store(tmp_path: Path):
    path = str(tmp_path / "engine.json")
    first = _Engine(state_file=path)
    assert first.state == {"count": 0}
    first.state = {"count": 3}
    first._save_state()
    assert json.loads(Path(path).read_text()) == {"count": 3}
    assert _Engine(state_file=path).state == {"count": 3}


def test_store_is_primary_over_file(tmp_path: Path):
    path = str(tmp_path / "engine.json")
    Path(path).write_text(json.dumps({"count": 1}))
    store = _db(tmp_path)
    store.set_state("test_engine_state", {"count": 9})
    assert _Engine(state_file=path, store=store).state == {"count": 9}


def test_attach_migrates_file_into_empty_store(tmp_path: Path):
    path = str(tmp_path / "engine.json")
    Path(path).write_text(json.dumps({"count": 4}))
    store = _db(tmp_path)
    engine = _Engine(state_file=path)
    report = engine.attach_store(store)
    assert report["migrated"] is True
    assert store.get_state("test_engine_state") == {"count": 4}


def test_attach_never_overwrites_populated_store(tmp_path: Path):
    path = str(tmp_path / "engine.json")
    Path(path).write_text(json.dumps({"count": 1}))
    store = _db(tmp_path)
    store.set_state("test_engine_state", {"count": 7})
    engine = _Engine(state_file=path)
    report = engine.attach_store(store)
    assert report["migrated"] is False
    assert store.get_state("test_engine_state") == {"count": 7}


def test_save_dual_writes_store_and_mirror(tmp_path: Path):
    path = str(tmp_path / "engine.json")
    store = _db(tmp_path)
    engine = _Engine(state_file=path, store=store)
    engine.state = {"count": 5}
    engine._save_state()
    assert store.get_state("test_engine_state") == {"count": 5}
    assert json.loads(Path(path).read_text()) == {"count": 5}


def test_corrupt_file_falls_back_to_default(tmp_path: Path):
    path = str(tmp_path / "engine.json")
    Path(path).write_text("{not json")
    assert _Engine(state_file=path).state == {"count": 0}
    assert read_json_dict(path) is None
    assert read_json_dict(tmp_path / "missing.json") is None


def test_broken_store_never_raises(tmp_path: Path):
    class _Broken:
        def get_state(self, *a, **k):
            raise RuntimeError("db down")

        def set_state(self, *a, **k):
            raise RuntimeError("db down")

    path = str(tmp_path / "engine.json")
    Path(path).write_text(json.dumps({"count": 2}))
    engine = _Engine(state_file=path, store=_Broken())
    assert engine.state == {"count": 2}
    engine._save_state()  # mirror still written, no raise
    assert json.loads(Path(path).read_text()) == {"count": 2}


def test_bulk_migrate_imports_and_is_idempotent(tmp_path: Path):
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    (state_dir / "meta_loop_state.json").write_text(json.dumps({"total_proposals": 6}))
    (state_dir / "walk_forward_state.json").write_text(json.dumps({"ic_history": [1]}))
    store = _db(tmp_path)
    first = migrate_json_states_to_store(store, state_dir)
    assert first["keys"]["meta_loop_state"]["action"] == "imported"
    assert first["keys"]["walk_forward_state"]["action"] == "imported"
    assert store.get_state("meta_loop_state") == {"total_proposals": 6}
    second = migrate_json_states_to_store(store, state_dir)
    assert second["imported"] == 0
    assert second["keys"]["meta_loop_state"]["action"] == "skipped_store_present"
    assert second["passed"] is True


def test_bulk_migrate_never_touches_source_files(tmp_path: Path):
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    target = state_dir / "ewc_state.json"
    target.write_text(json.dumps({"a": 1}))
    before = target.read_text()
    migrate_json_states_to_store(_db(tmp_path), state_dir)
    assert target.read_text() == before


def test_migration_status_reports_presence_and_sync(tmp_path: Path):
    state_dir = tmp_path / "states"
    state_dir.mkdir()
    (state_dir / "meta_loop_state.json").write_text(json.dumps({"n": 1}))
    store = _db(tmp_path)
    status = state_migration_status(store, state_dir)
    assert status["keys"]["meta_loop_state"]["file_present"] is True
    assert status["keys"]["meta_loop_state"]["store_present"] is False
    assert status["fully_migrated"] is False
    migrate_json_states_to_store(store, state_dir)
    status = state_migration_status(store, state_dir)
    assert status["keys"]["meta_loop_state"]["store_present"] is True
    assert status["keys"]["meta_loop_state"]["in_sync"] is True
    store.set_state("meta_loop_state", {"n": 2})
    assert state_migration_status(store, state_dir)["keys"]["meta_loop_state"]["in_sync"] is False


def test_atomic_write_never_raises_on_bad_path():
    assert atomic_write_json("", {"a": 1}) is False
