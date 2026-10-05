"""Safe, broker-free operational fault-injection drills.

These drills exercise the deterministic safety boundaries without connecting to
MT5, placing orders, or modifying the production journal.  They are evidence
for local recovery behavior; authenticated broker outage and fill replay still
require a demo environment.
"""

from __future__ import annotations

import gc
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict

from operations.agent_operations import AgentOperations
from data.data_quality import DataQualityGate
from operations.event_policy import evaluate as evaluate_event_policy
from intelligence.market_filters import evaluate
from data.provider_circuit import ProviderCircuitBreaker
from operations.restart_recovery import smoke as restart_smoke
from core.trading_safety import TradingStore


def _fresh_path() -> str:
    fd, path = tempfile.mkstemp(prefix="incident_drill_", suffix=".db")
    os.close(fd)
    os.remove(path)
    return path


def run(path: str = "") -> Dict[str, Any]:
    """Run deterministic safety drills against a temporary or fresh database.

    A supplied path must not exist; this prevents accidental mutation of a
    production journal.  The returned report contains only booleans and safe
    reason summaries, suitable for an audit artifact.
    """
    owned = not bool(path)
    path = path or _fresh_path()
    if os.path.exists(path) or os.path.exists(path + "-wal") or os.path.exists(path + "-shm"):
        raise FileExistsError(f"refusing to overwrite existing drill database: {path}")
    checks: Dict[str, Any] = {}
    store = None
    operations = None
    circuit = None
    try:
        store = TradingStore(path)
        store.set_kill_switch(True, "incident drill")
        checks["kill_switch"] = {
            "passed": store.is_kill_switch_active(),
            "execution_authority": False,
        }

        operations = AgentOperations(path)
        first_created, _ = operations.create_order_intent(
            "incident-drill-intent", {"symbol": "EURUSD", "side": "BUY"},
            actor="RiskController", run_id="incident-drill",
        )
        second_created, _ = operations.create_order_intent(
            "incident-drill-intent", {"symbol": "EURUSD", "side": "BUY"},
            actor="RiskController", run_id="incident-drill-retry",
        )
        checks["duplicate_intent"] = {
            "passed": bool(first_created and not second_created),
            "first_created": bool(first_created),
            "duplicate_blocked": not second_created,
        }

        circuit = ProviderCircuitBreaker(store, key="incident_drill_provider_circuit")
        for _ in range(9):
            circuit.record(False, "synthetic provider outage")
        allowed, reason = circuit.can_call()
        checks["provider_outage"] = {
            "passed": not allowed,
            "deterministic_only": circuit.status().get("mode") == "MANUAL_RESET_REQUIRED",
            "reason": reason,
        }
        circuit.reset("incident-drill", "restore after synthetic outage")

        malformed = DataQualityGate().validate(
            {"context": {"market_data_source": "paper"}, "ohlc": [], "dom": {}},
            require_live_quote=True,
        )
        checks["bad_market_data"] = {
            "passed": not malformed.passed,
            "reasons": malformed.reasons[:10],
        }

        weekend = evaluate(now=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc))
        checks["market_closed_filter"] = {
            "passed": not weekend.allowed and "weekend market closure" in weekend.reasons,
            "reasons": weekend.reasons,
        }

        event_outage = evaluate_event_policy(
            "EURUSD", [{"currency": "USD", "importance": 3}],
            now=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        )
        checks["calendar_news_outage"] = {
            "passed": bool(event_outage["blackout"])
            and "relevant macro event has malformed time" in event_outage["reasons"],
            "reasons": event_outage["reasons"],
        }

        checks["restart_persistence"] = restart_smoke()
        checks["restart_persistence"]["passed"] = bool(checks["restart_persistence"].get("passed"))
        passed = all(bool(item.get("passed")) for item in checks.values())
        result = {
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "execution_authority": False,
            "checks": checks,
            "scope": "local deterministic drills only; no MT5 connection or order submission",
        }
        # Release DB handles BEFORE the finally cleanup: on Windows an open
        # sqlite handle keeps the file locked, and gc.collect() cannot free
        # objects still reachable from this frame.
        circuit = None
        operations = None
        if store is not None:
            store.close()
            store = None
        gc.collect()
        return result
    finally:
        if owned:
            # Defensive: close any handle that survived an exception path.
            for handle in (circuit, operations, store):
                close = getattr(handle, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            circuit = None
            operations = None
            store = None
            gc.collect()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
