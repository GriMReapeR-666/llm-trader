"""D9 kill-switch latch: confirmation, persistence, and market-data clearing.

The Layer-0 news tripwire (data/news_triage.py) fires per headline. This module
owns the *cycle-crossing* kill-switch state so that:

1. Confirmation — a single batch of tripwire headlines cannot kill the cycle;
   ``confirmation_threshold`` independent batches within ``confirm_window_s``
   are required before the latch engages (news-only path).
2. Persistence — once latched, the state survives across ingest cycles AND
   process restarts (atomic JSON checkpoint). News silence never clears it.
3. Market-data authority — only an explicit calm market signal clears the
   latch (e.g. VIX below the clear threshold); an explicit market halt flag
   engages it immediately, bypassing the confirmation ladder.
4. Re-arm cooldown — after a clear, new tripwires are recorded but cannot
   re-trip the latch for ``rearm_cooldown_s``, preventing flapping.

Advisory only: this state gates cycle risk posture, never order authority.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

# Confirmation ladder (news-only path): N batches within the window.
CONFIRMATION_THRESHOLD = 2
CONFIRM_WINDOW_S = 300.0
# After an explicit market-data clear, tripwires cannot re-trip for this long.
REARM_COOLDOWN_S = 900.0
# Market VIX at or above this *blocks* a news-side clear (calm market required).
VIX_CLEAR_THRESHOLD = 35.0

DEFAULT_RELPATH = os.path.join("artifacts", "d9_kill_switch_latch.json")


def _default_state_path() -> str:
    # Tests pin FAST_TEST=1 (tests/conftest.py); keep the shared artifacts
    # checkpoint out of test runs unless D9_LATCH_PATH pins one explicitly.
    override = os.environ.get("D9_LATCH_PATH")
    if override:
        return override
    if os.environ.get("FAST_TEST") == "1":
        return os.path.join(tempfile.gettempdir(), "d9_kill_switch_latch_fasttest.json")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), DEFAULT_RELPATH
    )


class KillSwitchLatch:
    """Single owner of cross-cycle D9 kill-switch state."""

    def __init__(
        self,
        path: Optional[str] = None,
        now_fn: Optional[Any] = None,
        confirmation_threshold: int = CONFIRMATION_THRESHOLD,
        confirm_window_s: float = CONFIRM_WINDOW_S,
        rearm_cooldown_s: float = REARM_COOLDOWN_S,
        vix_clear_threshold: float = VIX_CLEAR_THRESHOLD,
    ) -> None:
        self._path = path or _default_state_path()
        self._now = now_fn or time.time
        self._threshold = int(confirmation_threshold)
        self._window_s = float(confirm_window_s)
        self._cooldown_s = float(rearm_cooldown_s)
        self._vix_clear = float(vix_clear_threshold)
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = self._blank()
        self._load()

    # ------------------------------------------------------------------ core
    def _blank(self) -> Dict[str, Any]:
        return {
            "latched": False,
            "pending_confirmations": 0,
            "recent_batches": [],
            "triggered_at": None,
            "triggered_headlines": [],
            "cleared_at": None,
            "clear_reason": None,
            "cooldown_until": None,
            "suppressed_during_cooldown": 0,
            "trigger_source": None,
        }

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and "latched" in loaded:
                fresh = self._blank()
                fresh.update({k: v for k, v in loaded.items() if k in fresh})
                self._state = fresh
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f)
            os.replace(tmp, self._path)
        except OSError:
            pass  # checkpoint is best-effort; in-memory state stays authoritative

    # ------------------------------------------------------- news-side input
    def observe(
        self,
        tripwire_count: int,
        triggered_headlines: Optional[List[str]] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record one ingest batch's tripwire result (the only news-side writer)."""
        now = float(now if now is not None else self._now())
        headlines = [str(h)[:120] for h in (triggered_headlines or [])][:5]
        with self._lock:
            st = self._state
            if st["latched"]:
                # Latched stays latched: news can sustain, never clear.
                if headlines:
                    st["triggered_headlines"] = headlines
                self._save()
                return self.snapshot_locked()

            cooldown_until = st.get("cooldown_until")
            if cooldown_until is not None and now < float(cooldown_until):
                if headlines:
                    st["suppressed_during_cooldown"] = int(
                        st.get("suppressed_during_cooldown") or 0
                    ) + len(headlines)
                self._save()
                return self.snapshot_locked()

            if tripwire_count <= 0 and not headlines:
                # Expire stale confirmation batches even on quiet cycles.
                st["recent_batches"] = [
                    float(t) for t in st.get("recent_batches", [])
                    if now - float(t) <= self._window_s
                ]
                st["pending_confirmations"] = len(st["recent_batches"])
                self._save()
                return self.snapshot_locked()

            batches = [
                float(t) for t in st.get("recent_batches", [])
                if now - float(t) <= self._window_s
            ]
            batches.append(now)
            st["recent_batches"] = batches
            st["pending_confirmations"] = len(batches)
            if headlines:
                st["triggered_headlines"] = headlines
            if len(batches) >= self._threshold:
                st["latched"] = True
                st["triggered_at"] = now
                st["trigger_source"] = "news_tripwire_confirmed"
                st["recent_batches"] = []
                st["pending_confirmations"] = 0
                st["cooldown_until"] = None
                st["suppressed_during_cooldown"] = 0
            self._save()
            return self.snapshot_locked()

    # --------------------------------------------------- market-data authority
    def apply_market_data(
        self,
        vix: Optional[float] = None,
        halt_flag: Optional[bool] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Explicit market data may engage (halt) or clear (calm) the latch.

        Absence of market data (both None) is a no-op: news silence never
        clears a latched kill-switch.
        """
        now = float(now if now is not None else self._now())
        with self._lock:
            st = self._state
            if halt_flag is True:
                if not st["latched"]:
                    st["latched"] = True
                    st["triggered_at"] = now
                    st["trigger_source"] = "market_data_halt"
                    st["recent_batches"] = []
                    st["pending_confirmations"] = 0
                    st["cooldown_until"] = None
                    st["suppressed_during_cooldown"] = 0
                    st["clear_reason"] = None
                self._save()
                return self.snapshot_locked()

            if not st["latched"]:
                return self.snapshot_locked()

            # Explicit calm signal required to clear: VIX below threshold and
            # no market halt asserted. A missing VIX value does NOT clear.
            calm = vix is not None and float(vix) < self._vix_clear and halt_flag is not True
            if calm:
                st["latched"] = False
                st["cleared_at"] = now
                st["clear_reason"] = f"market_data_calm_vix_below_{self._vix_clear:g}"
                st["cooldown_until"] = now + self._cooldown_s
                st["recent_batches"] = []
                st["pending_confirmations"] = 0
                st["triggered_headlines"] = []
                st["suppressed_during_cooldown"] = 0
                self._save()
            return self.snapshot_locked()

    # ---------------------------------------------------------------- views
    def snapshot_locked(self) -> Dict[str, Any]:
        st = self._state
        now = float(self._now())
        return {
            "latched": bool(st["latched"]),
            "active": bool(st["latched"]),  # legacy alias for existing consumers
            "pending_confirmations": int(st["pending_confirmations"]),
            "confirmation_threshold": self._threshold,
            "confirm_window_s": self._window_s,
            "triggered_at": st["triggered_at"],
            "triggered_headlines": list(st["triggered_headlines"]),
            "cleared_at": st["cleared_at"],
            "clear_reason": st["clear_reason"],
            "cooldown_active": bool(
                st.get("cooldown_until") is not None and now < float(st["cooldown_until"])
            ),
            "cooldown_until": st["cooldown_until"],
            "suppressed_during_cooldown": int(st.get("suppressed_during_cooldown") or 0),
            "trigger_source": st["trigger_source"],
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self.snapshot_locked()

    def reset(self) -> None:
        """Test/diagnostics helper: clear all state (including checkpoint)."""
        with self._lock:
            self._state = self._blank()
            self._save()


# --------------------------------------------------------------- singleton
_LATCH: Optional[KillSwitchLatch] = None
_LATCH_LOCK = threading.Lock()


def _singleton_token() -> str:
    # Distinguishes test singletons from the production one so a pytest run
    # can never inherit a latched state from (or leak one into) production.
    return os.environ.get("D9_LATCH_SINGLETON") or "production"


def get_kill_switch_latch() -> KillSwitchLatch:
    global _LATCH
    with _LATCH_LOCK:
        if _LATCH is None or getattr(_LATCH, "_singleton_token", "production") != _singleton_token():
            _LATCH = KillSwitchLatch()
            _LATCH._singleton_token = _singleton_token()
        return _LATCH


# Canonical singleton registration: alias both bare and package module keys in sys.modules
import sys as _sys
_curr_mod = _sys.modules.get(__name__)
if _curr_mod:
    _sys.modules.setdefault("kill_switch_latch", _curr_mod)
    _sys.modules.setdefault("data.kill_switch_latch", _curr_mod)
    # If one was already set differently, unify them to this module
    _sys.modules["kill_switch_latch"] = _curr_mod
    _sys.modules["data.kill_switch_latch"] = _curr_mod
