"""StatefulModule base class for JSON state persistence.

Eliminates the duplicated _load_state/_save_state pattern across 14+ modules.
Supports both dataclass (.to_dict()/from_dict()) and raw-dict state types.

Store-backed persistence
------------------------
``StoreBackedState`` routes the same payload through the authoritative
``TradingStore`` SQLite ``state`` KV table (single source of truth) while
keeping an atomic JSON mirror on disk during the transition.  Read order is
store -> file (one-time file-to-store migration) -> default, so attaching a
store can never silently discard newer file state, and detaching a store
falls back to the last mirrored file.

``STATE_REGISTRY`` maps every known store key to its legacy JSON filename so
``migrate_json_states_to_store()`` can backfill all 18+ state files in one
idempotent pass (source files are never modified).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger("StatePersistence")

T = TypeVar("T")

#: Authoritative store key -> legacy JSON filename.  The 18 canonical runtime
#: states first, then known auxiliary states (unmaterialized until first use).
STATE_REGISTRY: Dict[str, str] = {
    # --- 18 canonical runtime states (on-disk today) ---
    "alpha_decay_state": "alpha_decay_state.json",
    "alpha_discovery_state": "alpha_discovery_state.json",
    "auto_quant_tools_state": "auto_quant_tools_state.json",
    "autonomy_state": "autonomy_state.json",
    "causal_filter_state": "causal_filter_state.json",
    "crash_detector_state": "crash_detector_state.json",
    "cross_market_benchmark": "cross_market_benchmark.json",
    "evolutionary_state": "evolutionary_state.json",
    "ewc_state": "ewc_state.json",
    "meta_loop_state": "meta_loop_state.json",
    "multimodal_analysis_state": "multimodal_analysis_state.json",
    "self_improvement_state": "self_improvement_state.json",
    "self_improvement_state_memory": "self_improvement_state_memory.json",
    "risk_manager_state": "risk_state.json",
    "trade_manager_state": "trade_manager_state.json",
    "trade_manager_conditions": "trade_conditions.json",
    "supervisor_state": "supervisor_state.json",
    "walk_forward_state": "walk_forward_state.json",
    # --- auxiliary states (defaults exist in code, files appear on first save) ---
    "policy_library_state": "policy_library.json",
    "meta_adaptive_state": "meta_adaptive_state.json",
    "overfit_gate_state": "overfit_gate_state.json",
    "regime_duration_state": "regime_duration_state.json",
    "alpha_rotation_state": "alpha_rotation_state.json",
    "code_version_state": "ζ_code_versions.json",
}


def read_json_dict(path: Any) -> Optional[Dict[str, Any]]:
    """Best-effort read of a JSON object file; None when missing/corrupt/non-dict."""
    try:
        raw_path = Path(str(path)) if path else None
        if raw_path is None or not raw_path.exists():
            if raw_path is not None and not raw_path.is_absolute():
                alt_path = Path("data") / "state" / raw_path.name
                if alt_path.exists():
                    raw_path = alt_path
                else:
                    return None
            else:
                return None
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("state file read failed for %s: %s", path, exc)
        return None


def atomic_write_json(path: Any, data: Any) -> bool:
    """Best-effort atomic JSON write (tmp + os.replace); never raises."""
    try:
        target = Path(str(path))
        if not target.is_absolute():
            state_alt = Path("data") / "state" / target.name
            if state_alt.exists() or ((Path("data") / "state").is_dir() and not target.exists()):
                target = state_alt
        if target.parent and str(target.parent) not in ("", "."):
            target.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(target) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, default=str, indent=2)
        os.replace(tmp, str(target))
        return True
    except Exception as exc:
        logger.debug("state file write failed for %s: %s", path, exc)
        return False


class StatefulModule:
    """Base class providing JSON file state persistence.

    Subclasses set:
        state_file: str          -- path to JSON state file
        _default_state_factory   -- callable returning initial state object/dict

    Override _serialize_state(obj) and _deserialize_state(data) for custom types.
    """

    state_file: str = ""
    _default_state_factory: Optional[Callable[[], Any]] = None

    def _default_state(self) -> Any:
        if self._default_state_factory is not None:
            return self._default_state_factory()
        return {}

    def _serialize_state(self, obj: Any) -> Any:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return obj

    def _deserialize_state(self, data: Any) -> Any:
        return data

    def _load_state(self) -> Any:
        """Load persisted state from JSON file."""
        try:
            path = Path(self.state_file)
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                return self._deserialize_state(raw)
        except Exception as exc:
            logger.warning("state load failed for %s: %s", self.state_file, exc)
        return self._default_state()

    def _save_state(self, state: Any = None) -> None:
        """Persist state to JSON file atomically."""
        try:
            obj = state if state is not None else getattr(self, "state", None)
            if obj is None:
                return
            data = self._serialize_state(obj)
            path = Path(self.state_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, default=str, indent=2)
            os.replace(tmp, str(path))
        except Exception as exc:
            logger.debug("Failed to save state to %s: %s", self.state_file, exc)


class DictStateModule(StatefulModule):
    """StatefulModule for raw dict state with optional history trimming."""

    def __init__(self, state_file: str, default_state: Optional[Dict[str, Any]] = None,
                 max_history: int = 200, history_key: str = "history"):
        self.state_file = state_file
        self._default_state_factory = lambda: default_state.copy() if default_state else {}
        self.max_history = max_history
        self.history_key = history_key

    def _trim_history(self, state: Dict[str, Any]) -> Dict[str, Any]:
        history = state.get(self.history_key, [])
        if isinstance(history, list) and len(history) > self.max_history:
            state[self.history_key] = history[-self.max_history:]
        return state


class DataclassStateModule(StatefulModule):
    """StatefulModule for dataclass state objects with .to_dict()/.from_dict()."""

    def __init__(self, state_file: str, state_class: type, **default_kwargs: Any):
        self.state_file = state_file
        self._state_class = state_class
        self._default_kwargs = default_kwargs

    def _default_state(self) -> Any:
        return self._state_class(**self._default_kwargs)

    def _serialize_state(self, obj: Any) -> Any:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        return obj

    def _deserialize_state(self, data: Any) -> Any:
        if hasattr(self._state_class, "from_dict"):
            return self._state_class.from_dict(data)
        if hasattr(self._state_class, "__init__"):
            try:
                return self._state_class(**data)
            except TypeError:
                pass
        return data


class StoreBackedState(StatefulModule):
    """StatefulModule whose single source of truth is a TradingStore.

    Subclasses set ``store_key`` (a ``STATE_REGISTRY`` key) and keep their
    existing ``state_file`` for the transitional file mirror.  Domain
    (de)serialization stays in the subclass; only transport lives here.

    Typical subclass migration::

        class MyEngine(StoreBackedState):
            store_key = "my_engine_state"

            def __init__(self, state_file="my_engine_state.json", store=None):
                self.state_file = state_file
                self.store = store
                self.state = self._load_state()
                if store is not None:
                    self.attach_store(store)

            def _load_state(self):
                return MyState.from_dict(self._load_state_dict() or {})

            def _save_state(self):
                self._save_state_dict(self.state.to_dict())
    """

    store_key: str = ""
    mirror_file: bool = True

    @property
    def _store(self) -> Any:
        return getattr(self, "store", None)

    def attach_store(self, store: Any) -> Dict[str, Any]:
        """Attach the authoritative store, migrating file state once.

        Never overwrites populated store state.  Returns an evidence dict.
        """
        self.store = store
        report: Dict[str, Any] = {"store_key": self.store_key, "migrated": False}
        if store is None or not self.store_key:
            report["reason"] = "no store or store key"
            return report
        try:
            existing = store.get_state(self.store_key, None)
        except Exception as exc:
            report["reason"] = f"store read failed: {exc}"
            return report
        if isinstance(existing, dict) and existing:
            report["reason"] = "store already populated"
            return report
        file_data = read_json_dict(getattr(self, "state_file", ""))
        if isinstance(file_data, dict) and file_data:
            try:
                store.set_state(self.store_key, file_data)
            except Exception as exc:
                report["reason"] = f"store write failed: {exc}"
                return report
            report["migrated"] = True
            report["keys"] = sorted(file_data.keys())
        else:
            report["reason"] = "no file state to migrate"
        return report

    def _load_state_dict(self, default: Optional[Dict[str, Any]] = None, *,
                           key: Optional[str] = None, path: Any = None) -> Dict[str, Any]:
        """Store -> file (with one-time migration) -> default.  Never raises.

        ``key``/``path`` override the class ``store_key``/``state_file`` for
        owners of a second state file (e.g. trade conditions).
        """
        store_key = key or self.store_key
        state_file = path if path is not None else getattr(self, "state_file", "")
        fallback = dict(default or {})
        store = self._store
        if store is not None and store_key:
            try:
                stored = store.get_state(store_key, None)
            except Exception as exc:
                logger.debug("store read failed for %s: %s", store_key, exc)
                stored = None
            if isinstance(stored, dict) and stored:
                return stored
            file_data = read_json_dict(state_file)
            if isinstance(file_data, dict) and file_data:
                try:
                    store.set_state(store_key, file_data)
                except Exception as exc:
                    logger.debug("store migration write failed for %s: %s", store_key, exc)
                return file_data
            return fallback
        file_data = read_json_dict(state_file)
        return file_data if isinstance(file_data, dict) else fallback

    def _save_state_dict(self, data: Dict[str, Any], *,
                         key: Optional[str] = None, path: Any = None,
                         mirror: Optional[bool] = None) -> None:
        """Write store (authoritative) + atomic file mirror.  Never raises."""
        if not isinstance(data, dict):
            return
        store_key = key or self.store_key
        state_file = path if path is not None else getattr(self, "state_file", "")
        mirror_file = self.mirror_file if mirror is None else mirror
        store = self._store
        if store is not None and store_key:
            try:
                store.set_state(store_key, data)
            except Exception as exc:
                logger.debug("store write failed for %s: %s", store_key, exc)
        if mirror_file and state_file:
            atomic_write_json(state_file, data)


def _resolve_store(store: Any) -> Any:
    """Accept a TradingStore instance or a DB path; construct lazily to avoid cycles."""
    if isinstance(store, str):
        from trading_safety import TradingStore

        return TradingStore(store)
    return store


def migrate_json_states_to_store(store: Any, state_dir: Any = ".",
                                 registry: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Idempotent one-pass backfill of every registered JSON file into the store.

    Source files are never modified.  Keys already populated in the store are
    skipped.  Returns per-key evidence plus totals.
    """
    resolved = _resolve_store(store)
    mapping = registry or STATE_REGISTRY
    base = Path(str(state_dir or "."))
    result: Dict[str, Any] = {"database": getattr(resolved, "path", str(store)),
                              "state_dir": str(base), "imported": 0, "skipped": 0,
                              "keys": {}, "errors": []}
    for key, filename in mapping.items():
        entry: Dict[str, Any] = {"file": filename, "action": "skipped"}
        try:
            if resolved is not None:
                existing = resolved.get_state(key, None)
                if isinstance(existing, dict) and existing:
                    entry["action"] = "skipped_store_present"
                    result["keys"][key] = entry
                    result["skipped"] += 1
                    continue
            data = read_json_dict(base / filename)
            if not isinstance(data, dict) or not data:
                entry["action"] = "skipped_no_file_data"
                result["keys"][key] = entry
                result["skipped"] += 1
                continue
            if resolved is None:
                entry["action"] = "skipped_no_store"
                result["keys"][key] = entry
                result["skipped"] += 1
                continue
            resolved.set_state(key, data)
            entry["action"] = "imported"
            entry["keys"] = sorted(data.keys())
            result["keys"][key] = entry
            result["imported"] += 1
        except Exception as exc:
            entry["action"] = "error"
            entry["error"] = str(exc)[:200]
            result["keys"][key] = entry
            result["errors"].append(f"{key}: {exc}")
    result["passed"] = not result["errors"]
    result["execution_authority"] = False
    return result


def state_migration_status(store: Any, state_dir: Any = ".",
                           registry: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Report per-key file/store presence and sync state (evidence only)."""
    try:
        resolved = _resolve_store(store)
    except Exception as exc:
        return {"error": f"store unavailable: {exc}", "execution_authority": False}
    mapping = registry or STATE_REGISTRY
    base = Path(str(state_dir or "."))
    keys: Dict[str, Any] = {}
    for key, filename in mapping.items():
        path = base / filename
        file_present = path.exists()
        file_bytes: Optional[int] = None
        file_data: Optional[Dict[str, Any]] = None
        if file_present:
            try:
                file_bytes = path.stat().st_size
            except OSError:
                file_bytes = None
            file_data = read_json_dict(path)
        store_data: Optional[Dict[str, Any]] = None
        store_present = False
        try:
            raw = resolved.get_state(key, None) if resolved is not None else None
            if isinstance(raw, dict) and raw:
                store_data = raw
                store_present = True
        except Exception:
            store_present = False
        in_sync: Optional[bool] = None
        if file_data is not None and store_data is not None:
            try:
                in_sync = json.dumps(file_data, sort_keys=True, default=str) == json.dumps(store_data, sort_keys=True, default=str)
            except (TypeError, ValueError):
                in_sync = None
        keys[key] = {"file": filename, "file_present": file_present, "file_bytes": file_bytes,
                     "store_present": store_present, "in_sync": in_sync}
    migrated = sum(1 for item in keys.values() if item["store_present"])
    return {"database": getattr(resolved, "path", str(store)), "state_dir": str(base),
            "total_keys": len(keys), "migrated_keys": migrated,
            "fully_migrated": migrated == len(keys), "keys": keys,
            "execution_authority": False}
