"""Autonomy doctrine: levels, circuits and the operator control plane.

The system may operate autonomously up to L2 (micro-size live). Escalation to
L3 (full live size) always requires an operator-signed rollout manifest id.
Every escalation, demotion or circuit trip is persisted and journalled so the
command deck can render a complete governance trail.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.file_utils import safe_atomic_write, safe_atomic_write_json, safe_atomic_write_lines
from core.state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "autonomy_state.json")
CONTROL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "control_plane.jsonl")

CONTROL_ACTIONS = {"pause", "resume", "flatten", "killswitch"}


@dataclass(frozen=True)
class Level:
    key: str
    rank: int
    label: str
    max_risk_pct_per_trade: float
    allows_live: bool


LEVELS: Dict[str, Level] = {
    "L0_SHADOW": Level("L0_SHADOW", 0, "SHADOW", 0.0, False),
    "L1_PAPER": Level("L1_PAPER", 1, "PAPER", 1.0, False),
    "L2_MICRO": Level("L2_MICRO", 2, "MICRO LIVE", 0.25, True),
    "L3_FULL": Level("L3_FULL", 3, "FULL LIVE", 1.0, True),
}


@dataclass
class AutonomySnapshot:
    level: str
    halted: bool
    halt_reason: str
    paused: bool
    pause_reason: str
    history: List[Dict[str, Any]] = field(default_factory=list)


class AutonomyManager(StoreBackedState):
    """Owns the autonomy level, halt/pause flags, circuits and control plane."""

    store_key = "autonomy_state"

    def __init__(self, state_file: str = STATE_FILE, control_file: str = CONTROL_FILE,
                  signature_verifier: Optional[Callable[[str], bool]] = None,
                  store: Optional[Any] = None):
        self._lock = threading.RLock()
        self.state_file = state_file
        self.control_file = control_file
        self.store = store
        self._signature_verifier = signature_verifier
        self._state: Dict[str, Any] = {
            "level": "L1_PAPER",
            "halted": False,
            "halt_reason": "",
            "paused": False,
            "pause_reason": "",
            "history": [],
        }
        self._load()
        if store is not None:
            self.attach_store(store)

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            loaded = self._load_state_dict()
            if isinstance(loaded, dict) and loaded.get("level") in LEVELS:
                self._state = {
                    "level": loaded.get("level", "L1_PAPER"),
                    "halted": bool(loaded.get("halted", False)),
                    "halt_reason": loaded.get("halt_reason", ""),
                    "paused": bool(loaded.get("paused", False)),
                    "pause_reason": loaded.get("pause_reason", ""),
                    "history": list(loaded.get("history", []))[-100:],
                }
        except Exception as exc:
            logger.warning("autonomy state load failed (%s); starting defaults", exc)

    def _persist_locked(self) -> None:
        self._save_state_dict(self._state)

    def _record_locked(self, kind: str, detail: Dict[str, Any]) -> None:
        self._state.setdefault("history", []).append({
            "ts": time.time(), "kind": kind, **detail,
        })
        self._state["history"] = self._state["history"][-100:]

    # ---------- levels ----------
    def snapshot(self) -> AutonomySnapshot:
        with self._lock:
            return AutonomySnapshot(
                level=self._state["level"],
                halted=self._state["halted"],
                halt_reason=self._state["halt_reason"],
                paused=self._state["paused"],
                pause_reason=self._state["pause_reason"],
                history=list(self._state["history"]),
            )

    def level(self) -> Level:
        with self._lock:
            return LEVELS[self._state["level"]]

    def escalate(self, target_key: str, reason: str, manifest_signature: str = "") -> Dict[str, Any]:
        target = LEVELS.get(target_key)
        if target is None:
            return {"granted": False, "reason": f"unknown level {target_key}"}
        with self._lock:
            current = LEVELS[self._state["level"]]
            if target.rank <= current.rank:
                return {"granted": False, "reason": "target not above current level"}
            if target.key == "L3_FULL":
                verifier = self._signature_verifier or (lambda sig: bool(sig))
                if not verifier(manifest_signature):
                    self._record_locked("ESCALATION_DENIED", {
                        "target": target_key, "reason": "missing/invalid operator signature",
                    })
                    self._persist_locked()
                    return {"granted": False, "reason": "L3 requires an operator-signed rollout manifest"}
            self._state["level"] = target.key
            self._record_locked("ESCALATED", {"from": current.key, "to": target.key, "why": reason})
            self._persist_locked()
            return {"granted": True, "level": target.key}

    def demote(self, reason: str) -> str:
        with self._lock:
            current = LEVELS[self._state["level"]]
            lowered = max(0, current.rank - 1)
            target = next(lv for lv in LEVELS.values() if lv.rank == lowered)
            self._state["level"] = target.key
            self._record_locked("DEMOTED", {"from": current.key, "to": target.key, "why": reason})
            self._persist_locked()
            return target.key

    def force_level(self, target_key: str, actor: str) -> Dict[str, Any]:
        if target_key not in LEVELS:
            return {"ok": False, "reason": f"unknown level {target_key}"}
        with self._lock:
            previous = self._state["level"]
            self._state["level"] = target_key
            self._record_locked("FORCE_LEVEL", {"from": previous, "to": target_key, "actor": actor})
            self._persist_locked()
            return {"ok": True, "level": target_key}

    # ---------- halts ----------
    def halt(self, reason: str) -> None:
        with self._lock:
            self._state["halted"] = True
            self._state["halt_reason"] = reason
            self._record_locked("HALT", {"reason": reason})
            self._persist_locked()

    def resume(self, actor: str) -> None:
        with self._lock:
            self._state["halted"] = False
            self._state["halt_reason"] = ""
            self._state["paused"] = False
            self._state["pause_reason"] = ""
            self._record_locked("RESUME", {"actor": actor})
            self._persist_locked()

    def trading_allowed(self) -> tuple[bool, str]:
        with self._lock:
            if self._state["halted"]:
                return False, self._state["halt_reason"] or "kill switch engaged"
            if self._state["paused"]:
                return False, self._state["pause_reason"] or "operator pause active"
            return True, ""

    # ---------- circuits ----------
    CIRCUITS: List[Dict[str, Any]] = [
        {"key": "drawdown", "metric": "drawdown_pct", "op": ">=", "threshold": 6.0,
         "severity": "CRITICAL", "action": "HALT"},
        {"key": "recon_mismatch", "metric": "recon_mismatch_24h", "op": ">=", "threshold": 2,
         "severity": "CRITICAL", "action": "HALT"},
        {"key": "slippage_streak", "metric": "slippage_breach_streak", "op": ">=", "threshold": 3,
         "severity": "WARN", "action": "DEMOTE"},
        {"key": "llm_outage_min", "metric": "llm_outage_minutes", "op": ">=", "threshold": 10,
         "severity": "WARN", "action": "DEGRADE_HOLD_ONLY"},
        {"key": "stale_data_s", "metric": "data_stale_seconds", "op": ">=", "threshold": 120,
         "severity": "WARN", "action": "PAUSE_NEW_CYCLES"},
    ]

    def evaluate_circuits(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        trips: List[Dict[str, Any]] = []
        for circuit in self.CIRCUITS:
            value = metrics.get(circuit["metric"])
            if value is None:
                continue
            fired = value >= circuit["threshold"] if circuit["op"] == ">=" else value <= circuit["threshold"]
            if not fired:
                continue
            trip = {**circuit, "observed": value}
            trips.append(trip)
            with self._lock:
                self._record_locked("CIRCUIT_TRIP", {k: trip[k] for k in ("key", "severity", "action", "observed")})
                if circuit["action"] == "HALT":
                    self._state["halted"] = True
                    self._state["halt_reason"] = f"circuit:{circuit['key']}"
                elif circuit["action"] == "PAUSE_NEW_CYCLES":
                    self._state["paused"] = True
                    self._state["pause_reason"] = f"circuit:{circuit['key']}"
                elif circuit["action"] == "DEMOTE" and LEVELS[self._state["level"]].rank > 0:
                    lowered = LEVELS[self._state["level"]].rank - 1
                    self._state["level"] = next(lv.key for lv in LEVELS.values() if lv.rank == lowered)
                else:
                    self._state.setdefault("flags", {})
                    self._state["flags"]["hold_only"] = True
                self._persist_locked()
        return trips

    # ---------- operator control plane ----------
    def issue_control(self, action: str, actor: str = "operator", source: str = "api") -> Dict[str, Any]:
        action = action.lower().strip()
        if action not in CONTROL_ACTIONS:
            return {"ok": False, "reason": f"unsupported action {action}"}
        record = {"ts": time.time(), "action": action, "actor": actor, "source": source, "consumed": False}
        with self._lock:
            with open(self.control_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            self._record_locked("CONTROL_ISSUED", {"action": action, "actor": actor, "source": source})
            if action == "pause":
                self._state["paused"] = True
                self._state["pause_reason"] = f"operator:{actor}"
                self._persist_locked()
            elif action in ("flatten", "killswitch"):
                self._record_locked("CONTROL_ACK_QUEUED", {"action": action})
        return {"ok": True, "action": action}

    def drain_controls(self) -> List[Dict[str, Any]]:
        pending: List[Dict[str, Any]] = []
        if not os.path.exists(self.control_file):
            return pending
        kept: List[Dict[str, Any]] = []
        with self._lock:
            try:
                with open(self.control_file, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if rec.get("consumed"):
                            kept.append(rec)
                            continue
                        rec["consumed"] = True
                        kept.append(rec)
                        pending.append(rec)
                safe_atomic_write_lines(self.control_file, [json.dumps(rec) for rec in kept[-500:]])
            except Exception as exc:
                logger.error("drain_controls failed: %s", exc)
        for rec in pending:
            if rec.get("action") == "resume":
                self.resume(str(rec.get("actor", "operator")))
        return pending


_default_manager: Optional[AutonomyManager] = None
_default_manager_lock = threading.Lock()


def get_autonomy() -> AutonomyManager:
    global _default_manager
    if _default_manager is None:
        with _default_manager_lock:
            if _default_manager is None:
                _default_manager = AutonomyManager()
    return _default_manager
