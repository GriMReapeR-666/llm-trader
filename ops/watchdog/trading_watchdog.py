"""Engine watchdog: restarts the trading loop when the supervisor stalls.

Reads the supervisor heartbeat from ``supervisor_state.json``.  If the last
engine run was supposed to be up (phase RUNNING/DEGRADED/PAUSED) but has not
heartbeated for ``--stale-seconds`` (default 90), the engine likely crashed —
spawn it again with the same arguments the NSSM service uses.

Usage:
    python ops/watchdog/trading_watchdog.py --once          # single check (cron/Task Scheduler)
    python ops/watchdog/trading_watchdog.py --interval 30   # daemon loop

Safety:
    - Never restarts an engine that was intentionally STOPPED/OFFLINE.
    - Refuses to start a second engine while one is alive (lockfile + WMI check).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = ROOT / "supervisor_state.json"
LOG_DIR = ROOT / "artifacts" / "logs"
LOCK_FILE = LOG_DIR / "engine.pid"

ENGINE_ARGS = [
    sys.executable, str(ROOT / "mt5_trader.py"), "llm-trade",
    "--paper", "--continuous", "--tui",
    "--api-sync", "http://127.0.0.1:8000/api/internal/update",
]

STALE_SECONDS = 90


def _now() -> float:
    return time.time()


def load_supervisor():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _wmi_engine_alive() -> bool:
    """Best-effort: is any python process running mt5_trader.py llm-trade?"""
    if sys.platform != "win32":
        try:
            out = subprocess.run(["pgrep", "-f", r"mt5_trader\.py"], capture_output=True, text=True, timeout=15)
        except Exception:
            return False
        return bool(set(out.stdout.split()) - {str(os.getpid())})
    try:
        cmd = ("powershell", "-NoProfile", "-Command",
               "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
               "Where-Object { $_.CommandLine -match 'mt5_trader\\.py' }")
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    text = (out.stdout or "").strip()
    return bool(text and "mt5_trader.py" in text)


def engine_alive() -> bool:
    pid = None
    try:
        pid = int(LOCK_FILE.read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            LOCK_FILE.unlink(missing_ok=True)
    return _wmi_engine_alive()


def spawn_engine() -> bool:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out = open(LOG_DIR / "engine-out.log", "a", encoding="utf-8")
    err = open(LOG_DIR / "engine-err.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        ENGINE_ARGS, cwd=str(ROOT), stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        LOCK_FILE.write_text(str(proc.pid))
    except OSError:
        pass
    return True


def should_restart(sup, stale_seconds: float) -> bool:
    if not sup:
        return False
    phase = sup.get("phase")
    if phase not in ("RUNNING", "DEGRADED", "PAUSED"):
        return False
    last_beat = float(sup.get("last_heartbeat") or 0.0)
    if not last_beat:
        return False
    return (_now() - last_beat) > stale_seconds


def run_once(stale_seconds: float = STALE_SECONDS, dry_run: bool = False) -> int:
    sup = load_supervisor()
    if not should_restart(sup, stale_seconds):
        return 0
    if engine_alive():
        print("[watchdog] engine process alive; skipping restart")
        return 0
    if dry_run:
        print("[watchdog] engine would be restarted (heartbeat stale)")
        return 0
    print("[watchdog] supervisor heartbeat stale; restarting engine")
    spawn_engine()
    return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=30.0,
                    help="poll interval in seconds (daemon mode)")
    ap.add_argument("--stale-seconds", type=float, default=STALE_SECONDS,
                    help="heartbeat staleness that triggers restart")
    ap.add_argument("--once", action="store_true",
                    help="run a single check and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would happen without spawning")
    args = ap.parse_args()

    if args.once:
        raise SystemExit(run_once(stale_seconds=args.stale_seconds,
                                  dry_run=args.dry_run))

    print(f"[watchdog] monitoring {STATE_FILE} (stale={args.stale_seconds}s, "
          f"interval={args.interval}s)")
    while True:
        try:
            restarted = run_once(stale_seconds=args.stale_seconds,
                                 dry_run=args.dry_run)
            if restarted:
                print("[watchdog] engine respawned")
        except KeyboardInterrupt:
            print("\n[watchdog] stopped")
            return
        except Exception as exc:  # never let the watchdog die on a parse hiccup
            print(f"[watchdog] check error: {exc}")
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
