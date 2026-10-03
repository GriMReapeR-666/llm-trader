"""EventAgent persona: deterministic calendar/news blackout triage.

State lives in event_windows.json so the fast lane can consult blackouts
without touching the journal, and restarts inherit the current windows.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.file_utils import safe_atomic_write_json

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "event_windows.json")

BLACKOUT_BEFORE_MIN = 90
BLACKOUT_AFTER_MIN = 30


class EventWindowManager:
    def __init__(self, state_file: str = STATE_FILE):
        self._lock = threading.RLock()
        self.state_file = state_file
        self._load()

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            with open(self.state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._windows: List[Dict[str, Any]] = list(data.get("windows", []))
        except Exception:
            self._windows = []

    def _persist_locked(self) -> None:
        safe_atomic_write_json(self.state_file, {"windows": self._windows[-200:]})

    # ---------- triage ----------
    @staticmethod
    def _parse_ts(value: Any) -> Optional[float]:
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def triage(self, events: List[Dict[str, Any]], now: Optional[float] = None) -> Dict[str, Any]:
        """Classify events into blackout windows.

        Deterministic rule: impact in {high, 3} opens a window
        [start - 90m, start + 30m]. Anything else is recorded but never gates.
        """
        now = now or time.time()
        added = 0
        with self._lock:
            known_keys = {w.get("key") for w in self._windows}
            for ev in events or []:
                impact = str(ev.get("impact", "")).lower()
                start = self._parse_ts(ev.get("time") or ev.get("ts"))
                if impact not in ("high", "3") or start is None:
                    continue
                key = f"{ev.get('symbol', '*')}:{ev.get('title', '')[:40]}:{int(start)}"
                if key in known_keys:
                    continue
                self._windows.append({
                    "key": key,
                    "title": str(ev.get("title", ""))[:80],
                    "symbol": "*",  # high-impact events gate the whole book
                    "start": start,
                    "blackout_from": start - BLACKOUT_BEFORE_MIN * 60,
                    "blackout_to": start + BLACKOUT_AFTER_MIN * 60,
                    "created_ts": now,
                })
                known_keys.add(key)
                added += 1
            self._expire_locked(now)
            self._persist_locked()
        return {"added": added, "active_windows": len(self.active(now))}

    def _expire_locked(self, now: float) -> None:
        cutoff = now - 24 * 3600
        self._windows = [w for w in self._windows if w.get("blackout_to", 0) > cutoff]

    # ---------- gate ----------
    def active(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = now or time.time()
        with self._lock:
            return [w for w in self._windows
                    if w["blackout_from"] <= now <= w["blackout_to"]]

    def is_blackout(self, symbol: str = "*", now: Optional[float] = None) -> tuple[bool, str]:
        hits = [w for w in self.active(now)
                if w.get("symbol") in ("*", symbol)]
        if not hits:
            return False, ""
        nearest = min(hits, key=lambda w: w["start"])
        mins = int((nearest["start"] - (now or time.time())) / 60)
        when = f"in {mins}m" if mins >= 0 else f"{-mins}m ago"
        return True, f"high-impact '{nearest['title']}' {when}"


_manager: Optional[EventWindowManager] = None
_manager_lock = threading.Lock()


def get_manager() -> EventWindowManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = EventWindowManager()
        return _manager


def handle_triage(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Executor entry for schedule 'event_triage'."""
    from operations.event_bridge import emit

    manager = get_manager()
    result = manager.triage(payload.get("events") or [])
    blocked_symbol = payload.get("symbol", "*")
    gated, why = manager.is_blackout(blocked_symbol)
    emit("EVENT_TRIAGE", {**result, "blackout": gated, "why": why},
         actor="EventAgent")
    return {**result, "blackout": gated, "why": why}
