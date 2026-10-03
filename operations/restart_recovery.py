"""Restart/recovery smoke checks for durable state and idempotency."""
from __future__ import annotations

import os
import gc
import tempfile
from typing import Any, Dict

from agent_operations import AgentOperations
from trading_safety import TradingStore


def smoke(path: str = "") -> Dict[str, Any]:
    """Simulate process loss by closing/reopening every store boundary."""
    owned_path = not path
    if owned_path:
        fd, path = tempfile.mkstemp(prefix="restart_recovery_", suffix=".db")
        os.close(fd)
        os.remove(path)
    # This is a diagnostic helper, not a database reset command.  Refuse an
    # existing path so an operator cannot accidentally destroy a real journal.
    # Callers should supply a fresh temporary path for each smoke run.
    if not path:
        raise ValueError("a fresh smoke database path is required")
    if any(os.path.exists(path + suffix) for suffix in ("", "-wal", "-shm")):
        raise FileExistsError(f"refusing to overwrite existing smoke path: {path}")
    second_store = None
    second_ops = None
    try:
        first_store = TradingStore(path)
        first_store.set_kill_switch(True, "restart smoke")
        first_ops = AgentOperations(path)
        first_claim = first_ops.claim_task("restart-smoke-task", "TechnicalNet", "calculate_features", {"source": "smoke"}, "run-1")
        first_ops.create_order_intent("restart-smoke-intent", {"symbol": "EURUSD", "side": "BUY"}, actor="RiskController")
        del first_ops, first_store

        second_store = TradingStore(path)
        second_ops = AgentOperations(path)
        persisted_kill = second_store.is_kill_switch_active()
        duplicate_claim = second_ops.claim_task("restart-smoke-task", "TechnicalNet", "calculate_features", {"source": "after-restart"}, "run-2")
        intents = second_ops.order_intents(("CREATED",))
        intent = intents[0] if intents else {}
        result = {"passed": bool(first_claim and persisted_kill and not duplicate_claim and intent.get("intent_key") == "restart-smoke-intent"),
                "first_claim": first_claim, "persisted_kill_switch": persisted_kill,
                "duplicate_claim_blocked": not duplicate_claim, "intent_persisted": intent.get("intent_key") == "restart-smoke-intent",
                "scope": "state/task/order-intent persistence only; MT5 network fault injection remains required"}
        # Release the DB handles BEFORE the finally cleanup: on Windows an
        # open sqlite handle keeps the file locked, and gc.collect() cannot
        # free objects that are still reachable from this frame.
        if second_ops is not None:
            second_ops = None
        if second_store is not None:
            second_store.close()
            second_store = None
        gc.collect()
        return result
    finally:
        # SQLite connections are created by legacy helpers via context
        # managers that commit but do not explicitly close.  Collect them
        # before removing an internally-created smoke file on Windows.
        if owned_path:
            # Defensive: close any handle that survived an exception path.
            for handle in (second_ops, second_store):
                close = getattr(handle, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            second_ops = None
            second_store = None
            gc.collect()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass
