"""Process-wide StateManager: transport-only binding for TUI/tools to read/patch state keys.

Zero authority — never grants execution permissions.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_instance: Optional["StateManager"] = None
_lock = threading.Lock()


class StateManager:
    """Thin transport layer over a TradingStore for diagnostics/TUI/tools."""

    def __init__(self, store_or_path: Any = "trading_state.db"):
        if isinstance(store_or_path, str):
            try:
                from core.trading_safety import TradingStore
                self._store = TradingStore(store_or_path)
            except Exception:
                self._store = None
        else:
            self._store = store_or_path
        self._path = store_or_path if isinstance(store_or_path, str) else getattr(store_or_path, "path", "trading_state.db")

    def get(self, key: str, default: Any = None) -> Any:
        """Read a state key from the store."""
        if self._store is None:
            return default
        try:
            return self._store.get_state(key, default)
        except Exception as exc:
            logger.debug("StateManager.get(%s) failed: %s", key, exc)
            return default

    def set(self, key: str, value: Any) -> bool:
        """Write a state key to the store (transport only, zero authority)."""
        if self._store is None:
            return False
        try:
            self._store.set_state(key, value)
            return True
        except Exception as exc:
            logger.debug("StateManager.set(%s) failed: %s", key, exc)
            return False

    def keys(self) -> list:
        """List all registered state keys."""
        if self._store is None:
            return []
        try:
            if hasattr(self._store, "list_state_keys"):
                return self._store.list_state_keys()
        except Exception:
            pass
        return []


def get_state_manager(store: Any = None) -> StateManager:
    """Get or create the process-wide StateManager singleton."""
    global _instance
    if store is not None:
        with _lock:
            _instance = StateManager(store)
            return _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = StateManager()
    return _instance
