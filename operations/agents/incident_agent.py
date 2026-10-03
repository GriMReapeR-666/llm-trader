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
