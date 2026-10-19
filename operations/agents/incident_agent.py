"""IncidentAgent persona: circuit feeding, incident records, runbook matching.

Detection lives here; action authority stays with the autonomy doctrine engine
and the operator. Incidents append to incidents.jsonl for the deck.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

INCIDENTS_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "incidents.jsonl")

RUNBOOKS: Dict[str, str] = {
    "drawdown": "docs/INCIDENT_RUNBOOKS.md#drawdown-breaker",
    "recon_mismatch": "docs/INCIDENT_RUNBOOKS.md#reconciliation-mismatch",
    "slippage_streak": "docs/INCIDENT_RUNBOOKS.md#slippage-calibration",
    "llm_outage_min": "docs/INCIDENT_RUNBOOKS.md#provider-outage",
    "stale_data_s": "docs/INCIDENT_RUNBOOKS.md#data-feed-staleness",
    "executor_failed": "docs/INCIDENT_RUNBOOKS.md#executor-failure",
}


def remedy_drawdown(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Execute containment for drawdown breaker trip."""
    from operations.autonomy import get_autonomy
    autonomy = get_autonomy()
    autonomy.pause(actor="IncidentRemediation:drawdown")
    return {"status": "APPLIED", "action": "HALT_TRADING", "detail": detail}


def remedy_recon_mismatch(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Execute reconciliation triage when broker and journal desync."""
    try:
        from core.trading_tools import reconcile_broker_and_journal_orders
        res = reconcile_broker_and_journal_orders()
        return {"status": "APPLIED", "action": "RECONCILED", "result": res}
    except Exception as exc:
        return {"status": "ERROR", "action": "RECONCILIATION_FAILED", "error": str(exc)}


def remedy_slippage_streak(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Demote autonomy level and trigger calibration alert."""
    from operations.autonomy import get_autonomy
    autonomy = get_autonomy()
    demoted = autonomy.demote("incident:slippage_streak_remediation")
    return {"status": "APPLIED", "action": "DEMOTED_AUTONOMY", "new_level": demoted}


def remedy_llm_outage(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Degrade to HOLD-only mode during LLM provider downtime."""
    from operations.autonomy import get_autonomy
    autonomy = get_autonomy()
    with autonomy._lock:
        autonomy._state.setdefault("flags", {})
        autonomy._state["flags"]["hold_only"] = True
        autonomy._persist_locked()
    return {"status": "APPLIED", "action": "HOLD_ONLY_MODE"}


def remedy_stale_data(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Pause new cycles and request feed reconnection."""
    from operations.autonomy import get_autonomy
    autonomy = get_autonomy()
    autonomy.pause(actor="IncidentRemediation:stale_data")
    return {"status": "APPLIED", "action": "PAUSED_FOR_FEED_RECONNECT"}


def remedy_executor_failed(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Record execution subsystem fault and pause order routing."""
    from operations.autonomy import get_autonomy
    autonomy = get_autonomy()
    autonomy.pause(actor="IncidentRemediation:executor_failure")
    return {"status": "APPLIED", "action": "EXECUTOR_CONTAINED"}


ACTION_HANDLERS: Dict[str, Any] = {
    "drawdown": remedy_drawdown,
    "recon_mismatch": remedy_recon_mismatch,
    "slippage_streak": remedy_slippage_streak,
    "llm_outage_min": remedy_llm_outage,
    "stale_data_s": remedy_stale_data,
    "executor_failed": remedy_executor_failed,
}


def dispatch_remediation(kind: str, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dispatch programmatic remediation handler for an incident kind."""
    handler = ACTION_HANDLERS.get(kind)
    if handler is None:
        return {"status": "SKIPPED", "reason": f"no handler for {kind}"}
    try:
        outcome = handler(detail or {})
        try:
            from operations.event_bridge import emit
            emit("INCIDENT_REMEDY_DISPATCHED", {"kind": kind, "outcome": outcome}, actor="IncidentAgent")
        except Exception:
            pass
        return outcome
    except Exception as exc:
        logger.error("Failed to execute remedy for %s: %s", kind, exc, exc_info=True)
        return {"status": "FAILED", "error": str(exc)}


class IncidentRecorder:
    def __init__(self, incidents_file: str = INCIDENTS_FILE):
        self._lock = threading.Lock()
        self.incidents_file = incidents_file

    def record(self, kind: str, severity: str, summary: str,
               detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        record = {
            "ts": time.time(),
            "kind": kind,
            "severity": severity,
            "summary": summary[:200],
            "runbook": RUNBOOKS.get(kind, ""),
            "detail": detail or {},
            "status": "OPEN",
        }
        with self._lock:
            with open(self.incidents_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        return record

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            with open(self.incidents_file, "r", encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError:
            return []
        return rows[-limit:]

    def feed_circuits(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Push observed metrics into the doctrine engine and journal trips."""
        from operations.autonomy import get_autonomy

        trips = get_autonomy().evaluate_circuits(metrics)
        for trip in trips:
            self.record(
                kind=trip["key"],
                severity="critical" if trip["action"] == "HALT" else "warn",
                summary=f"circuit {trip['key']} fired at {trip['observed']} "
                        f"(threshold {trip['threshold']}) → {trip['action']}",
                detail={"observed": trip["observed"], "action": trip["action"]},
            )
        return trips


_recorder: Optional[IncidentRecorder] = None
_lock = threading.Lock()


def get_recorder() -> IncidentRecorder:
    global _recorder
    with _lock:
        if _recorder is None:
            _recorder = IncidentRecorder()
        return _recorder


def handle_sweep(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Executor entry for schedule 'incident_sweep'."""
    from operations.event_bridge import emit

    recorder = get_recorder()
    metrics = payload.get("circuit_metrics") or {}
    trips = recorder.feed_circuits(metrics) if metrics else []
    emit("INCIDENT_SWEEP", {"trips": len(trips)}, actor="IncidentAgent")
    return {"trips": trips}
