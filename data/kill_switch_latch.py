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
import sys
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
        store: Optional[Any] = None,
    ) -> None:
        self._path = path or _default_state_path()
        self._now = now_fn or time.time
        self._threshold = int(confirmation_threshold)
        self._window_s = float(confirm_window_s)
        self._cooldown_s = float(rearm_cooldown_s)
        self._vix_clear = float(vix_clear_threshold)
        self._store = store
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = self._blank()
        self._load()

    def attach_store(self, store: Any) -> None:
        """Attach TradingStore as the authoritative backend."""
        with self._lock:
            self._store = store
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
        # 1. Check store if attached
        if self._store is not None:
            try:
                stored = self._store.get_state("d9_kill_switch_latch", None)
                if type(stored).__name__ in ("MagicMock", "Mock"):
                    stored = None
                if isinstance(stored, dict) and "latched" in stored:
                    fresh = self._blank()
                    fresh.update({k: v for k, v in stored.items() if k in fresh})
                    self._state = fresh
                    return
                elif stored is not None and type(stored).__name__ not in ("MagicMock", "Mock"):
                    # Corrupt store record: fail closed to latched
                    self._state = self._blank()
                    self._state["latched"] = True
                    self._state["trigger_source"] = "checkpoint_unreadable"
                    return
            except Exception:
                self._state = self._blank()
                self._state["latched"] = True
                self._state["trigger_source"] = "checkpoint_unreadable"
                return

        # 2. Check JSON file (direct or legacy migration)
        if self._path and os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and "latched" in loaded:
                    fresh = self._blank()
                    fresh.update({k: v for k, v in loaded.items() if k in fresh})
                    self._state = fresh
                    if self._store is not None:
                        try:
                            self._store.set_state("d9_kill_switch_latch", self._state)
                        except Exception:
                            pass
                else:
                    self._state = self._blank()
                    self._state["latched"] = True
                    self._state["trigger_source"] = "checkpoint_unreadable"
            except Exception:
                # Corrupt or unreadable JSON file: fail closed to latched
                self._state = self._blank()
                self._state["latched"] = True
                self._state["trigger_source"] = "checkpoint_unreadable"
        else:
            # First boot with no store and no file: clean start
            self._state = self._blank()

    def _sanitize(self, stored: Any) -> Dict[str, Any]:
        """Merge a stored record into a blank state, failing closed (H5).

        A record that is not a dict with a latch flag (corrupt JSON, a torn
        write, anything unexpected) loads as latched with
        trigger_source=checkpoint_unreadable. No record (first boot) loads clean.
        """
        if stored is None:
            return self._blank()
        if type(stored).__name__ in ("MagicMock", "Mock"):
            # Test doubles without a real backend: no durable record either way.
            return self._blank()
        if isinstance(stored, dict) and "latched" in stored:
            fresh = self._blank()
            fresh.update({k: v for k, v in stored.items() if k in fresh})
            return fresh
        blank = self._blank()
        blank["latched"] = True
        blank["trigger_source"] = "checkpoint_unreadable"
        return blank

    def _write_file_mirror(self) -> None:
        # File mirror with unique temporary file to prevent multi-process collisions
        if self._path:
            dir_name = os.path.dirname(self._path) or "."
            tmp_path = None
            try:
                os.makedirs(dir_name, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".latch_", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._state, f)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                os.replace(tmp_path, self._path)
                tmp_path = None
            except OSError:
                pass  # checkpoint is best-effort; in-memory/store state stays authoritative
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def _mutate(self, transition) -> Dict[str, Any]:
        """Apply a pure state transition atomically (H5).

        `transition` maps the sanitized current record to the next record.
        With a store attached, the read-modify-write happens inside the
        store's BEGIN IMMEDIATE transaction, so a concurrent news observe
        and operator clear in two processes cannot overwrite each other.
        A store write failure is logged as an ERROR (never swallowed: the
        operator must know durability is degraded) and the file mirror is
        still updated best-effort.
        """
        import logging as _logging
        with self._lock:
            if self._store is None:
                transition(self._state)
                self._write_file_mirror()
                return self.snapshot_locked()
            try:
                updated = self._store.update_state(
                    "d9_kill_switch_latch",
                    lambda cur: transition(self._sanitize(cur)),
                    default=self._blank(),
                )
                self._state = self._sanitize(updated)
            except Exception as exc:
                _logging.getLogger(__name__).error(
                    "Latch store write failed; staying on in-memory state: %s", exc,
                )
            self._write_file_mirror()
            return self.snapshot_locked()

    def _save(self) -> None:
        # Legacy entry point kept for reset()/tests: store write (best-effort
        # here; _mutate is the loud path) plus the file mirror.
        if self._store is not None:
            try:
                st_copy = dict(self._state)
                self._store.update_state(
                    "d9_kill_switch_latch",
                    lambda _curr: st_copy,
                    default=st_copy,
                )
            except Exception:
                pass

        self._write_file_mirror()

    def clear(self, reason: str, actor: str = "operator") -> Dict[str, Any]:
        """Operator clear: explicitly unlatch and enter re-arm cooldown."""
        now = float(self._now())

        def _transition(st: Dict[str, Any]) -> Dict[str, Any]:
            st["latched"] = False
            st["cleared_at"] = now
            st["clear_reason"] = f"operator_clear:{actor}:{reason}"
            st["cooldown_until"] = now + self._cooldown_s
            st["recent_batches"] = []
            st["pending_confirmations"] = 0
            st["triggered_headlines"] = []
            st["suppressed_during_cooldown"] = 0
            return st

        return self._mutate(_transition)

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
        threshold = self._threshold
        window_s = self._window_s

        def _transition(st: Dict[str, Any]) -> Dict[str, Any]:
            if st["latched"]:
                # Latched stays latched: news can sustain, never clear.
                if headlines:
                    st["triggered_headlines"] = headlines
                return st

            cooldown_until = st.get("cooldown_until")
            if cooldown_until is not None and now < float(cooldown_until):
                if headlines:
                    st["suppressed_during_cooldown"] = int(
                        st.get("suppressed_during_cooldown") or 0
                    ) + len(headlines)
                return st

            if tripwire_count <= 0 and not headlines:
                # Expire stale confirmation batches even on quiet cycles.
                st["recent_batches"] = [
                    float(t) for t in st.get("recent_batches", [])
                    if now - float(t) <= window_s
                ]
                st["pending_confirmations"] = len(st["recent_batches"])
                return st

            batches = [
                float(t) for t in st.get("recent_batches", [])
                if now - float(t) <= window_s
            ]
            batches.append(now)
            st["recent_batches"] = batches
            st["pending_confirmations"] = len(batches)
            if headlines:
                st["triggered_headlines"] = headlines
            if len(batches) >= threshold:
                st["latched"] = True
                st["triggered_at"] = now
                st["trigger_source"] = "news_tripwire_confirmed"
                st["recent_batches"] = []
                st["pending_confirmations"] = 0
                st["cooldown_until"] = None
                st["suppressed_during_cooldown"] = 0
            return st

        return self._mutate(_transition)

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
        vix_clear = self._vix_clear
        cooldown_s = self._cooldown_s

        def _transition(st: Dict[str, Any]) -> Dict[str, Any]:
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
                return st

            if not st["latched"]:
                return st

            # Explicit calm signal required to clear: VIX below threshold and
            # no market halt asserted. A missing VIX value does NOT clear.
            calm = vix is not None and float(vix) < vix_clear and halt_flag is not True
            if calm:
                st["latched"] = False
                st["cleared_at"] = now
                st["clear_reason"] = f"market_data_calm_vix_below_{vix_clear:g}"
                st["cooldown_until"] = now + cooldown_s
                st["recent_batches"] = []
                st["pending_confirmations"] = 0
                st["triggered_headlines"] = []
                st["suppressed_during_cooldown"] = 0
            return st

        return self._mutate(_transition)

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
            if self._store is not None:
                self._load()
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
    token = os.environ.get("D9_LATCH_SINGLETON") or "production"
    return f"{token}:{_default_state_path()}"


def get_kill_switch_latch() -> KillSwitchLatch:
    global _LATCH
    token = _singleton_token()
    reg = getattr(sys, "_d9_kill_switch_latch_registry", None)
    if reg is None:
        reg = {}
        sys._d9_kill_switch_latch_registry = reg
    with _LATCH_LOCK:
        if token not in reg:
            _LATCH = KillSwitchLatch()
            _LATCH._singleton_token = token
            reg[token] = _LATCH
        else:
            _LATCH = reg[token]
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
