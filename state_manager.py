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
        self._apply_pragmas()

    def _apply_pragmas(self) -> None:
        """Harden SQLite concurrency with WAL mode, synchronous=NORMAL, and busy_timeout=5000."""
        if isinstance(self._path, str) and self._path != ":memory:":
            try:
                import sqlite3
                with sqlite3.connect(self._path, timeout=5.0) as con:
                    con.execute("PRAGMA journal_mode=WAL;")
                    con.execute("PRAGMA synchronous=NORMAL;")
                    con.execute("PRAGMA busy_timeout=5000;")
            except Exception as exc:
                logger.debug("StateManager._apply_pragmas failed on %s: %s", self._path, exc)

    def get_connection(self):
        """Return a configured SQLite connection with hardened concurrency pragmas."""
        if hasattr(self._store, "_connect"):
            return self._store._connect()
        import sqlite3
        con = sqlite3.connect(self._path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=5000;")
        return con

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
