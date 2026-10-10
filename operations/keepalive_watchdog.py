"""Keep-alive watchdog: the engine may not die silently again.

Audit remediation item 2. The audited loop died on Oct 3 and nothing noticed
for six days because every executor is try/except-guarded and the only
liveness artifact was a plain-int pid file nobody re-checked. This module is
the outer ring that makes that failure impossible to repeat silently:

    1. it asks ``live_state_probe.engine_dead`` whether the engine is alive
       (ONE liveness notion - never a second, divergent one),
    2. it restarts the engine through the normal ``llm-trade`` boot path, so
       the restarted engine runs the same boot sweep as a human-started one,
    3. it bounds the restart budget with exponential backoff, so a crash-loop
       costs a bounded number of attempts instead of thrashing the machine.

Safety properties (each one is a rule, not an accident):
    - Arming is explicit. ``desired=False`` (the default) never spawns, so a
      desktop operator who stopped the engine on purpose is not fought.
    - It never writes ``engine.pid``. The lease belongs to the engine; a
      watchdog-written lease would fake exactly the liveness this exists to
      prove, and would keep a dead engine looking alive until the stamp
      expired.
    - It refuses to spawn while a live engine holds the lease - the same
      refusal the boot path makes - so the watchdog can never create the
      twin-engine the single-instance guard exists to prevent.

Usage:
    python -m operations.keepalive_watchdog --arm          # operator intent: keep it up
    python -m operations.keepalive_watchdog --once          # one check (Task Scheduler)
    python -m operations.keepalive_watchdog --interval 30   # daemon loop
    python -m operations.keepalive_watchdog --disarm       # stop watching
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional

from operations.live_state_probe import (
    PID_FILE,
    SUPERVISOR_STATE_FILE,
    engine_dead,
    sweep_live_state,
)

logger = logging.getLogger("KeepAliveWatchdog")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Restart budget per crash episode, and the backoff ladder between attempts.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF_S = 30.0
DEFAULT_BACKOFF_CAP_S = 600.0
WATCHDOG_STATE_FILE = os.path.join(_REPO_ROOT, "engine_watchdog.json")

#: Same shape the operator starts by hand: paper, continuous, no TUI.
ENGINE_ARGV = ("llm-trade", "--paper", "--continuous", "--no-tui")


class KeepAliveWatchdog:
    """Bounded keep-alive supervisor for the trading engine.

    ``check()`` is a pure-ish single tick (no sleeping, no unbounded loops) so
    tests drive it with an injected clock and a fake spawner; ``run()`` is the
    thin polling shell on top of it.
    """

    def __init__(self,
                 pid_path: str = PID_FILE,
                 state_path: str = WATCHDOG_STATE_FILE,
                 db_path: str = "trading_state.db",
                 supervisor_path: Optional[str] = SUPERVISOR_STATE_FILE,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 base_backoff_s: float = DEFAULT_BASE_BACKOFF_S,
                 backoff_cap_s: float = DEFAULT_BACKOFF_CAP_S,
                 spawn: Optional[Callable[[], bool]] = None,
                 argv: Optional[List[str]] = None,
                 now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.pid_path = pid_path
        self.state_path = state_path
        self.db_path = db_path
        self.supervisor_path = supervisor_path
        self.max_attempts = int(max_attempts)
        self.base_backoff_s = float(base_backoff_s)
        self.backoff_cap_s = float(backoff_cap_s)
        self._spawn = spawn
        self.argv = list(argv) if argv else list(ENGINE_ARGV)
        self._now = now
        self._sleep = sleep

    # ------------------------------------------------------------------
    # durable restart budget
    # ------------------------------------------------------------------
    def _load(self) -> Dict[str, Any]:
        state = {"desired": False, "attempts": 0, "next_allowed_at": 0.0,
                 "restarts": [], "last_action": None}
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                state.update(stored)
        except (OSError, ValueError):
            pass                      # absent/corrupt budget == disarmed
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        state["restarts"] = list(state.get("restarts") or [])[-20:]
        try:
            from core.file_utils import safe_atomic_write_json
            safe_atomic_write_json(self.state_path, state)
        except Exception as exc:
            logger.warning("watchdog state persist failed: %s", exc)

    def arm(self, desired: bool = True) -> Dict[str, Any]:
        """Record operator intent. Arming also clears a spent budget."""
        state = self._load()
        state["desired"] = bool(desired)
        if desired:
            state["attempts"] = 0
            state["next_allowed_at"] = 0.0
        self._save(state)
        return state

    def backoff_s(self, attempts: int) -> float:
        return min(self.backoff_cap_s,
                   self.base_backoff_s * (2 ** max(0, int(attempts) - 1)))

    # ------------------------------------------------------------------
    # spawn
    # ------------------------------------------------------------------
    def spawn_engine(self) -> bool:
        """Start the engine the way an operator would. Never writes the lease."""
        if self._spawn is not None:
            return bool(self._spawn())
        try:
            subprocess.Popen(
                [sys.executable, os.path.join(_REPO_ROOT, "mt5_trader.py")] + self.argv,
                cwd=_REPO_ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception as exc:
            logger.error("watchdog could not spawn the engine: %s", exc)
            return False

    # ------------------------------------------------------------------
    # one tick
    # ------------------------------------------------------------------
    def check(self, dry_run: bool = False) -> Dict[str, Any]:
        state = self._load()
        now = self._now()

        if not state.get("desired"):
            result = {"action": "disarmed", "engine": None, "attempts": state.get("attempts", 0)}
            state["last_action"] = result["action"]
            self._save(state)
            return result

        verdict = engine_dead(self.pid_path, now)
        if not verdict.get("dead"):
            # Alive: the crash episode is over, so the budget is restored.
            result = {"action": "alive", "engine": verdict, "attempts": 0}
            if state.get("attempts"):
                logger.warning("engine recovered (pid %s); restart budget reset", verdict.get("pid"))
            state["attempts"] = 0
            state["next_allowed_at"] = 0.0
            state["last_action"] = result["action"]
            self._save(state)
            return result

        attempts = int(state.get("attempts") or 0)
        if attempts >= self.max_attempts:
            # Bounded: a crash-loop must not become an infinite spawn loop.
            result = {"action": "exhausted", "engine": verdict, "attempts": attempts,
                      "note": "restart budget spent; operator action required"}
            state["last_action"] = result["action"]
            self._save(state)
            logger.error("engine still dead (%s) after %d restart attempts - giving up "
                         "until re-armed", verdict.get("reason"), attempts)
            return result

        retry_in = float(state.get("next_allowed_at") or 0.0) - now
        if retry_in > 0:
            result = {"action": "backoff", "engine": verdict, "attempts": attempts,
                      "retry_in_s": round(retry_in, 1)}
            state["last_action"] = result["action"]
            self._save(state)
            return result

        if dry_run:
            return {"action": "would_restart", "engine": verdict, "attempts": attempts}

        # The respawn IS a boot path: run the same sweep a human boot runs so
        # the fresh engine never inherits ghost runs / expired claims / a
        # supervisor phase that lies about being up.
        sweep = {}
        try:
            sweep = sweep_live_state(self.db_path, self.pid_path, self.supervisor_path)
        except Exception as exc:
            logger.warning("pre-restart sweep failed (spawning anyway): %s", exc)

        spawned = self.spawn_engine()
        attempts += 1
        state["attempts"] = attempts
        state["next_allowed_at"] = now + self.backoff_s(attempts)
        restarts = list(state.get("restarts") or [])
        restarts.append({"ts": now, "attempts": attempts, "reason": verdict.get("reason"),
                         "spawned": bool(spawned)})
        state["restarts"] = restarts
        result = {"action": "restarted" if spawned else "spawn_failed",
                  "engine": verdict, "attempts": attempts,
                  "next_allowed_at": state["next_allowed_at"], "sweep": sweep}
        state["last_action"] = result["action"]
        self._save(state)
        logger.warning("engine dead (%s): restart attempt %d/%d %s",
                       verdict.get("reason"), attempts, self.max_attempts,
                       "spawned" if spawned else "FAILED")
        return result

    # ------------------------------------------------------------------
    # polling shell
    # ------------------------------------------------------------------
    def run(self, interval_s: float = 30.0, iterations: Optional[int] = None,
            dry_run: bool = False) -> None:
        count = 0
        while iterations is None or count < iterations:
            count += 1
            try:
                result = self.check(dry_run=dry_run)
                if result["action"] in ("restarted", "exhausted", "would_restart"):
                    print("[watchdog] %s" % json.dumps(result, default=str))
            except KeyboardInterrupt:
                print("\n[watchdog] stopped")
                return
            except Exception as exc:            # a watchdog must not die on a hiccup
                logger.error("watchdog check failed: %s", exc)
            if iterations is not None and count >= iterations:
                return
            self._sleep(max(1.0, float(interval_s)))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="engine keep-alive watchdog")
    ap.add_argument("--state", default=WATCHDOG_STATE_FILE, help="restart-budget state file")
    ap.add_argument("--pid-file", default=PID_FILE, help="engine pid lease")
    ap.add_argument("--db", default="trading_state.db", help="journal used by the boot sweep")
    ap.add_argument("--interval", type=float, default=30.0, help="poll interval (daemon mode)")
    ap.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS,
                    help="restart budget per crash episode")
    ap.add_argument("--once", action="store_true", help="run a single check and exit")
    ap.add_argument("--iterations", type=int, default=None, help="stop after N checks")
    ap.add_argument("--dry-run", action="store_true", help="report without spawning")
    ap.add_argument("--arm", action="store_true", help="record intent: keep the engine up")
    ap.add_argument("--disarm", action="store_true", help="record intent: do not restart")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    watchdog = KeepAliveWatchdog(pid_path=args.pid_file, state_path=args.state,
                                 db_path=args.db, max_attempts=args.max_attempts)

    if args.disarm:
        print("[watchdog] disarmed:", json.dumps(watchdog.arm(False), default=str))
        return 0
    if args.arm:
        print("[watchdog] armed:", json.dumps(watchdog.arm(True), default=str))
        return 0

    if args.once:
        result = watchdog.check(dry_run=args.dry_run)
        print("[watchdog] %s" % json.dumps(result, default=str))
        return 1 if result["action"] == "restarted" else 0

    print("[watchdog] pid lease=%s budget=%d interval=%ss (armed=%s)"
          % (args.pid_file, args.max_attempts, args.interval, watchdog._load().get("desired")))
    watchdog.run(interval_s=args.interval, iterations=args.iterations, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())