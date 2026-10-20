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
    - A stale lease is treated as a hang, not merely a death. Before a
      respawn it terminates the logged PID - but only after the command line
      identifies it as ``mt5_trader.py llm-trade`` - because the hung process
      still owns ``engine.lock`` and would make every fresh engine exit rc 3.
      A fresh lease is never touched.
    - It never spawns below the measured free-RAM bar (default 1.5 GB), and a
      stop flag (including the engine's ``MT5_TRADER_STOP_FILE`` override and
      ``trader_stop.flag`` fallback) disarms it. The engine retires
      ``stop.flag`` to ``.consumed``, so a veto-only tick would restart the
      engine the operator just stopped. Killing a stale holder is still allowed
      while RAM is low because that frees the lock; the boot waits.
    - It never writes ``engine.pid``. The lease belongs to the engine; a
      watchdog-written lease would fake exactly the liveness this exists to
      prove, and would keep a dead engine looking alive until the stamp
      expired.
    - It refuses to spawn while a live engine holds the lease - the same
      refusal the boot path makes - so the watchdog can never create the
      twin-engine the single-instance guard exists to prevent.
    - The respawn argv is the operator's AUTO multi-asset boot shape, so a
      restart cannot silently fall back to a single default symbol.

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

import psutil

from operations.live_state_probe import (
    PID_FILE,
    PID_LEASE_MAX_AGE_S,
    SUPERVISOR_STATE_FILE,
    engine_dead,
    read_pid_lease,
    sweep_live_state,
)

logger = logging.getLogger("KeepAliveWatchdog")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Restart budget per crash episode, and the backoff ladder between attempts.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_BACKOFF_S = 30.0
DEFAULT_BACKOFF_CAP_S = 600.0
WATCHDOG_STATE_FILE = os.path.join(_REPO_ROOT, "engine_watchdog.json")

#: Same shape the operator starts by hand (START_TRADING.bat): paper,
#: continuous, AUTO multi-asset. There is no ``--no-tui`` flag in
#: ``llm-trade``; passing one made every watchdog respawn die on argparse.
ENGINE_ARGV = (
    "llm-trade", "--paper",
    "--symbol", "AUTO",
    "--timeframe", "AUTO",
    "--model", "meta/llama-3.2-11b-vision-instruct",
    "--risk-per-trade", "1.0",
    "--continuous",
)
#: Boot environment parity with START_TRADING.bat. ``setdefault`` keeps an
#: explicit operator override (e.g. TRADE_PLANNER_MODE=observe).
ENGINE_ENV_DEFAULTS = {
    "DELIBERATIVE_GRAPH_SHADOW": "1",
    "TRADE_PLANNER_MODE": "enforce",
    "HEADLESS": "1",
}
#: The measured boot bar. Never lower it to force a boot.
DEFAULT_MIN_FREE_RAM_GB = 1.5


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
                 sleep: Callable[[float], None] = time.sleep,
                 min_free_ram_gb: float = 0.0,
                 kill_stale: bool = False,
                 stop_flag_path: Optional[str] = None,
                 ram_reader: Optional[Callable[[], float]] = None,
                 kill_grace_s: float = 15.0,
                 kill_timeout_s: float = 10.0):
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
        self.min_free_ram_gb = max(0.0, float(min_free_ram_gb))
        self.kill_stale = bool(kill_stale)
        self.stop_flag_path = stop_flag_path or os.path.join(_REPO_ROOT, "stop.flag")
        self.stop_flag_paths = self._resolve_stop_flag_paths()
        self._ram_reader = ram_reader
        self.kill_grace_s = max(1.0, float(kill_grace_s))
        self.kill_timeout_s = max(1.0, float(kill_timeout_s))

    def _resolve_stop_flag_paths(self) -> List[str]:
        """Mirror ``mt5_trader._stop_file_path`` exactly.

        The engine honours ``MT5_TRADER_STOP_FILE`` first and otherwise falls
        back to ``trader_stop.flag`` when ``stop.flag`` is absent. A watchdog
        that watched only ``stop.flag`` could fight a stop issued through the
        override or the fallback.
        """
        override = os.environ.get("MT5_TRADER_STOP_FILE", "").strip()
        if override:
            path = override if os.path.isabs(override) else os.path.join(_REPO_ROOT, override)
            return [path]
        paths = [self.stop_flag_path]
        fallback = os.path.join(os.path.dirname(self.stop_flag_path), "trader_stop.flag")
        if fallback != self.stop_flag_path:
            paths.append(fallback)
        return paths

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
        env = os.environ.copy()
        for key, value in ENGINE_ENV_DEFAULTS.items():
            env.setdefault(key, value)
        try:
            subprocess.Popen(
                [sys.executable, os.path.join(_REPO_ROOT, "mt5_trader.py")] + self.argv,
                cwd=_REPO_ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception as exc:
            logger.error("watchdog could not spawn the engine: %s", exc)
            return False

    # ------------------------------------------------------------------
    # stale-hang recovery
    # ------------------------------------------------------------------
    def free_ram_gb(self) -> float:
        """Available RAM in GB. Fail closed: an unreadable value is 0.0."""
        if self._ram_reader is not None:
            try:
                return float(self._ram_reader())
            except Exception:
                return 0.0
        try:
            return float(psutil.virtual_memory().available) / (1024 ** 3)
        except Exception:
            return 0.0

    @staticmethod
    def _engine_identity(pid: int, lease: Dict[str, Any]) -> bool:
        """True only for *our* engine, so a recycled PID is never killed.

        ``engine_dead`` trusts staleness before it checks identity; a stale
        lease can therefore point at a PID Windows reused. The command line is
        the primary guard; the create-time cross-check catches a same-command
        PID reuse (e.g. an operator-started twin).
        """
        try:
            proc = psutil.Process(int(pid))
            cmdline = " ".join(proc.cmdline()).lower()
            if "mt5_trader.py" not in cmdline or "llm-trade" not in cmdline:
                return False
            recorded = float(lease.get("created_at") or 0.0)
            if recorded and abs(proc.create_time() - recorded) > 2.0:
                return False
            return True
        except (psutil.Error, ValueError, OSError):
            return False

    def _kill_stale_engine(self, verdict: Dict[str, Any]) -> Dict[str, Any]:
        """Terminate a hung engine that still holds ``engine.lock``.

        Without this, a stale lease makes the watchdog spawn an engine that
        immediately loses the OS lock race and exits rc 3: the watchdog counts
        a restart, the hung process keeps the lock, and the outage never ends.
        Killing is gated by the stale-lease verdict plus command-line identity;
        a fresh lease is never touched.
        """
        pid = verdict.get("pid")
        if pid is None:
            return {"killed": False, "reason": "no_pid"}
        pid = int(pid)
        if not psutil.pid_exists(pid):
            return {"killed": True, "already_dead": True, "pid": pid}
        lease = read_pid_lease(self.pid_path)
        if not self._engine_identity(pid, lease):
            return {"killed": False, "reason": "identity_mismatch", "pid": pid}
        try:
            proc = psutil.Process(pid)
            proc.terminate()
        except psutil.NoSuchProcess:
            return {"killed": True, "already_dead": True, "pid": pid}
        except psutil.Error as exc:
            return {"killed": False, "reason": "terminate_failed",
                    "pid": pid, "error": str(exc)}

        escalated = False
        try:
            proc.wait(timeout=self.kill_grace_s)
        except psutil.TimeoutExpired:
            escalated = True
            try:
                proc.kill()
                proc.wait(timeout=self.kill_timeout_s)
            except psutil.Error as exc:
                return {"killed": False, "reason": "kill_failed",
                        "pid": pid, "error": str(exc)}
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as exc:
            return {"killed": False, "reason": "wait_failed",
                    "pid": pid, "error": str(exc)}

        logger.warning(
            "killed stale engine pid %s after lease exceeded %ss%s; "
            "the OS lock is now free for the respawn",
            pid, PID_LEASE_MAX_AGE_S, " (SIGKILL escalation)" if escalated else "")
        return {"killed": True, "pid": pid, "escalated": escalated,
                "reason": "pid_lease_stale"}

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

        stop_hit = next((p for p in self.stop_flag_paths if os.path.exists(p)), None)
        if stop_hit:
            # A stop request is operator intent. Disarm durably: the engine
            # retires stop.flag to .consumed, so a veto-only tick would let the
            # very next tick restart the engine the operator just stopped.
            state["desired"] = False
            result = {"action": "stop_flag_disarmed", "engine": None,
                      "stop_flag": stop_hit,
                      "attempts": state.get("attempts", 0)}
            state["last_action"] = result["action"]
            self._save(state)
            logger.warning("stop flag %s present; watchdog disarmed "
                           "(re-arm explicitly to resume)", stop_hit)
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
            free_gb = self.free_ram_gb()
            ram_ok = (self.min_free_ram_gb <= 0
                      or free_gb >= self.min_free_ram_gb)
            return {"action": "would_restart", "engine": verdict, "attempts": attempts,
                    "would_kill": bool(self.kill_stale
                                       and verdict.get("reason") == "pid_lease_stale"),
                    "free_gb": round(free_gb, 2),
                    "min_free_gb": self.min_free_ram_gb,
                    "ram_ok": bool(ram_ok)}

        kill_info = None
        if self.kill_stale and verdict.get("reason") == "pid_lease_stale":
            kill_info = self._kill_stale_engine(verdict)
            if not kill_info.get("killed"):
                result = {"action": "kill_failed", "engine": verdict,
                          "kill": kill_info, "attempts": attempts}
                state["last_action"] = result["action"]
                self._save(state)
                logger.error("could not kill stale engine pid %s (%s); not spawning",
                             verdict.get("pid"), kill_info.get("reason"))
                return result

        free_gb = self.free_ram_gb()
        if self.min_free_ram_gb > 0 and free_gb < self.min_free_ram_gb:
            result = {"action": "ram_blocked", "engine": verdict,
                      "free_gb": round(free_gb, 2),
                      "min_free_gb": self.min_free_ram_gb,
                      "attempts": attempts}
            if kill_info:
                result["kill"] = kill_info
            state["last_action"] = result["action"]
            self._save(state)
            logger.warning("engine dead (%s) but boot RAM bar not met: %.2fGB < %.2fGB",
                           verdict.get("reason"), free_gb, self.min_free_ram_gb)
            return result

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
                         "spawned": bool(spawned),
                         "killed_stale_pid": (kill_info or {}).get("pid")
                         if kill_info and kill_info.get("killed") else None})
        state["restarts"] = restarts
        result = {"action": "restarted" if spawned else "spawn_failed",
                  "engine": verdict, "attempts": attempts,
                  "next_allowed_at": state["next_allowed_at"], "sweep": sweep}
        if kill_info:
            result["kill"] = kill_info
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
    ap.add_argument("--min-free-ram-gb", type=float, default=DEFAULT_MIN_FREE_RAM_GB,
                    help="refuse to spawn below this much free RAM (default 1.5GB)")
    ap.add_argument("--no-kill-stale", action="store_true",
                    help="do not terminate a hung engine that holds a stale lease")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    watchdog = KeepAliveWatchdog(pid_path=args.pid_file, state_path=args.state,
                                 db_path=args.db, max_attempts=args.max_attempts,
                                 min_free_ram_gb=args.min_free_ram_gb,
                                 kill_stale=not args.no_kill_stale)

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

    print("[watchdog] pid lease=%s budget=%d interval=%ss ram_bar=%.2fGB "
          "kill_stale=%s (armed=%s)"
          % (args.pid_file, args.max_attempts, args.interval,
             watchdog.min_free_ram_gb, watchdog.kill_stale,
             watchdog._load().get("desired")))
    watchdog.run(interval_s=args.interval, iterations=args.iterations, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())