"""Supervisor kernel: owns the engine lifecycle from boot to shutdown.

A tiny, dependency-free finite state machine that every entrypoint can share.
It sequences startup checks, tracks component health, degrades/pauses on
faults and leaves a forensic snapshot on disk for crash recovery and the
command deck.

    OFFLINE -> BOOTING -> PREFLIGHT -> WARMUP -> RUNNING <-> DEGRADED
                                                RUNNING <-> PAUSED
                                                any -> SHUTDOWN_REQUESTED -> STOPPED
"""
from __future__ import annotations

import json
import logging

from core.file_utils import safe_atomic_write_json
from core.state_persistence import StoreBackedState
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "supervisor_state.json")

PHASES = ["OFFLINE", "BOOTING", "PREFLIGHT", "WARMUP", "RUNNING", "DEGRADED", "PAUSED", "SHUTDOWN_REQUESTED", "STOPPED"]

TRANSITIONS: Dict[str, List[str]] = {
    "OFFLINE": ["BOOTING"],
    "BOOTING": ["PREFLIGHT", "SHUTDOWN_REQUESTED"],
    "PREFLIGHT": ["WARMUP", "DEGRADED", "SHUTDOWN_REQUESTED"],
    "WARMUP": ["RUNNING", "DEGRADED", "SHUTDOWN_REQUESTED"],
    "RUNNING": ["DEGRADED", "PAUSED", "SHUTDOWN_REQUESTED"],
    "DEGRADED": ["RUNNING", "PAUSED", "SHUTDOWN_REQUESTED"],
    "PAUSED": ["RUNNING", "DEGRADED", "SHUTDOWN_REQUESTED"],
    "SHUTDOWN_REQUESTED": ["STOPPED"],
    "STOPPED": [],
}


@dataclass
class ComponentHealth:
    name: str
    ok: bool
    detail: str = ""
    ts: float = 0.0


class Supervisor(StoreBackedState):
    store_key = "supervisor_state"

    def __init__(self, state_file: str = STATE_FILE, store: Optional[Any] = None):
        self._lock = threading.RLock()
        self.state_file = state_file
        self.store = store
        self._phase = "OFFLINE"
        self._components: Dict[str, ComponentHealth] = {}
        self._events: List[Dict[str, Any]] = []
        self._principals: Dict[str, Dict[str, Any]] = {}
        self._booted_at = 0.0
        self._last_heartbeat = 0.0
        self._load()
        if store is not None:
            self.attach_store(store)

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            prev = self._load_state_dict()
            if prev:
                self._events = list(prev.get("events", []))[-200:]
                if prev.get("phase") not in (None, "STOPPED", "OFFLINE"):
                    self._record(f"RECOVER_FROM_{prev.get('phase')}", {"previous_phase": prev.get("phase")})
        except Exception as exc:
            logger.warning("supervisor state load failed (%s)", exc)

    def _persist_locked(self) -> None:
        self._save_state_dict({
            "phase": self._phase,
            "booted_at": self._booted_at,
            "last_heartbeat": self._last_heartbeat,
            "components": {k: vars(v) for k, v in self._components.items()},
            "principals": self._principals,
            "events": self._events[-200:],
        })

    def _record(self, kind: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self._events.append({"ts": time.time(), "kind": kind, **(detail or {})})
        self._events = self._events[-200:]

    # ---------- phase machine ----------
    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def transition(self, target: str, reason: str = "") -> Dict[str, Any]:
        with self._lock:
            if target not in PHASES:
                return {"ok": False, "reason": f"unknown phase {target}"}
            if target not in TRANSITIONS[self._phase]:
                return {"ok": False, "reason": f"illegal transition {self._phase} -> {target}"}
            previous = self._phase
            self._phase = target
            if target == "RUNNING" and not self._booted_at:
                self._booted_at = time.time()
            self._record("PHASE", {"from": previous, "to": target, "why": reason})
            self._persist_locked()
            return {"ok": True, "from": previous, "to": target}

    # ---------- boot ----------
    def boot_sequence(self, checks: Dict[str, Callable[[], bool]]) -> Dict[str, Any]:
        """Run ordered startup checks through BOOTING -> PREFLIGHT -> WARMUP."""
        report: Dict[str, Any] = {"checks": {}, "ok": True}
        self.transition("BOOTING", "supervisor.boot_sequence")
        for name, check in checks.items():
            started = time.time()
            try:
                ok = bool(check())
                detail = "" if ok else "check returned false"
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            elapsed = round((time.time() - started) * 1000, 1)
            report["checks"][name] = {"ok": ok, "ms": elapsed, "detail": detail}
            self.note_component(name, ok=ok, detail=detail)
            if not ok:
                report["ok"] = False
                break
        if not report["ok"]:
            self.transition("PREFLIGHT", "boot failed; entering degraded preflight")
            return report
        self.transition("PREFLIGHT", "all boot checks passed")
        self.transition("WARMUP", "data warmup window")
        return report

    def ready(self) -> Dict[str, Any]:
        with self._lock:
            if self._phase != "WARMUP":
                return {"ok": False, "reason": f"phase is {self._phase}"}
            self.transition("RUNNING", "warmup complete")
            return {"ok": True}

    # ---------- health ----------
    def note_component(self, name: str, *, ok: bool, detail: str = "") -> None:
        with self._lock:
            self._components[name] = ComponentHealth(name=name, ok=ok, detail=detail, ts=time.time())
            self._persist_locked()

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = time.time()

    # ---------- per-principal freshness (Phase D) ----------
    def note_principal(self, name: str, *, ok: bool = True, detail: str = "") -> None:
        """Record a liveness beat for an executing principal."""
        with self._lock:
            prev = self._principals.get(name, {})
            streak = 0 if ok else int(prev.get("fail_streak", 0)) + 1
            self._principals[name] = {
                "last_beat": time.time(),
                "ok": ok,
                "detail": detail,
                "fail_streak": streak,
            }
            self._persist_locked()

    def stale_principals(self) -> List[str]:
        """Principals whose last beat exceeds their spec heartbeat age."""
        try:
            from operations.agent_contracts import PRINCIPAL_SPECS
            ages = {n: s.heartbeat_max_age_s for n, s in PRINCIPAL_SPECS.items()}
        except Exception:
            ages = {}
        now = time.time()
        with self._lock:
            return [
                name for name, info in self._principals.items()
                if not info.get("ok")
                or now - float(info.get("last_beat", 0)) > ages.get(name, 300)
            ]

    def principal_freshness(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = {
                name: {
                    "age_s": round(time.time() - float(info.get("last_beat", 0)), 1),
                    "ok": bool(info.get("ok")),
                    "fail_streak": info.get("fail_streak", 0),
                }
                for name, info in self._principals.items()
            }
        return {"principals": snapshot, "stale": self.stale_principals()}

    def degrade(self, reason: str) -> Dict[str, Any]:
        with self._lock:
            if self._phase == "RUNNING":
                return self.transition("DEGRADED", reason)
            self._record("DEGRADE_NOTED", {"reason": reason})
            return {"ok": False, "reason": f"phase {self._phase} cannot degrade"}

    def recover(self) -> Dict[str, Any]:
        with self._lock:
            if self._phase == "DEGRADED":
                return self.transition("RUNNING", "recovered")
            return {"ok": False, "reason": f"phase {self._phase} cannot recover"}

    def pause(self, reason: str) -> Dict[str, Any]:
        with self._lock:
            if self._phase in ("RUNNING", "DEGRADED"):
                return self.transition("PAUSED", reason)
            return {"ok": False, "reason": f"phase {self._phase} cannot pause"}

    def request_shutdown(self, reason: str = "") -> Dict[str, Any]:
        with self._lock:
            return self.transition("SHUTDOWN_REQUESTED", reason or "operator request")

    def complete_shutdown(self) -> Dict[str, Any]:
        with self._lock:
            result = self.transition("STOPPED", "shutdown complete")
            self._persist_locked()
            return result

    # ---------- introspection ----------
    def overall_health(self) -> Dict[str, Any]:
        with self._lock:
            comps = {k: vars(v) for k, v in self._components.items()}
            failing = [k for k, v in self._components.items() if not v.ok]
            stale = [k for k, v in self._components.items() if v.ts and time.time() - v.ts > 300]
            healthy = (not failing) and (not stale)
            freshness = self.principal_freshness()
            return {
                "phase": self._phase,
                "healthy": healthy and not freshness["stale"],
                "failing_components": failing,
                "stale_components": stale,
                "stale_principals": freshness["stale"],
                "principals": freshness["principals"],
                "uptime_s": round(time.time() - self._booted_at, 1) if self._booted_at else 0.0,
                "last_heartbeat_age_s": round(time.time() - self._last_heartbeat, 1) if self._last_heartbeat else None,
                "components": comps,
                "recent_events": self._events[-25:],
            }

    def snapshot(self) -> Dict[str, Any]:
        snap = self.overall_health()
        snap["events"] = self._events
        return snap


_default_supervisor: Optional[Supervisor] = None


def get_supervisor() -> Supervisor:
    global _default_supervisor
    if _default_supervisor is None:
        _default_supervisor = Supervisor()
    return _default_supervisor
