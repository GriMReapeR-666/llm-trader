"""Guarded auto-boot: launch the paper trader the moment free RAM clears 1.5GB.

Doctrine (2026-09-28): four consecutive torch 0xc0000005 boot crashes while
free RAM fell 1.19 -> 0.76GB; the boot bar is >=1.5GB free, and the 05:42Z
guarded attempt was REFUSED at 0.76GB. This watcher re-arms that same guard
as an auto-boot so the next live session validates the sub-cadenced
event_monitor + fast lane as soon as the box can hold a boot.

Safety rails:
- Single-shot: one boot spawn, then the watcher exits. A crashed boot is NOT
  re-spawned here (crash recovery belongs elsewhere).
- Preflight re-checked at boot time; REFUSE (no spawn) if below the bar or if
  a real trader process already exists (name-aware python.exe match, the
  watcher's own heredoc/self-match excluded - that artifact produced a false
  GUARD_FAIL earlier today).
- Every decision is logged to artifacts/watch_guarded_boot.out BEFORE acting;
  a REFUSE is terminal for this watcher (no boot loops).
- Bounded window: exits after WATCH_GUARDED_BOOT_WINDOW_H (default 4h) so a
  stale watcher cannot linger.

RAM note: the two stdin-driven python.exe processes (611MB/490MB) and the
repo-wide pytest run pinning the box near 0.5GB free belong to OTHER agent
sessions; they are outside this checkout's ownership rules and must be closed
by their owners, not killed from here.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "watch_guarded_boot.out")
BAR_GB = float(os.getenv("WATCH_GUARDED_BOOT_BAR_GB", "1.5"))
WINDOW_H = float(os.getenv("WATCH_GUARDED_BOOT_WINDOW_H", "4"))
POLL_S = float(os.getenv("WATCH_GUARDED_BOOT_POLL_S", "20"))


def _log(msg: str) -> None:
    line = "{} {}".format(datetime.now(timezone.utc).isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _free_gb() -> float:
    import psutil
    return psutil.virtual_memory().available / 2**30


def _trader_running() -> bool:
    import psutil
    me = os.getpid()
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            if (name.startswith("python") and "mt5_trader.py" in cmd
                    and "llm-trade" in cmd  # real boot shape, not a mention
                    and "watch_guarded_boot" not in cmd and p.pid != me):
                return True
        except Exception:
            continue
    return False


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    _log("armed: bar {:.2f}GB, window {:.1f}h, free {:.2f}GB".format(
        BAR_GB, WINDOW_H, _free_gb()))
    deadline = time.monotonic() + WINDOW_H * 3600.0
    trader_strikes = 0
    while time.monotonic() < deadline:
        try:
            if _trader_running():
                # Two consecutive polls must agree: other agents' ephemeral
                # tooling can mention mt5_trader.py in a cmdline for one poll
                # (that false positive killed the first watcher at 09:46Z).
                trader_strikes += 1
                if trader_strikes >= 2:
                    _log("trader confirmed on two consecutive polls - "
                         "nothing to do, watcher exits")
                    return 0
                time.sleep(POLL_S)
                continue
            trader_strikes = 0
            free = _free_gb()
            if free >= BAR_GB:
                _log("PREFLIGHT PASS: {:.2f}GB >= {:.2f}GB - launching "
                     "guarded paper boot".format(free, BAR_GB))
                env = dict(os.environ)
                env["HEADLESS"] = "1"
                env["CI"] = "1"
                session_log = open(
                    os.path.join(ROOT, "artifacts", "watch_guarded_boot_session.log"),
                    "ab")
                # DETACHED_PROCESS: the boot must survive the death of whatever
                # launched it. Live-proven 2026 session: a harness restart
                # cancelled the parent shell and the engine child died with it
                # (lease went 7.8h stale). CREATE_NEW_PROCESS_GROUP alone is
                # not isolation; a detached session is.
                _detach_flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                proc = subprocess.Popen(
                    ["cmd", "/c", os.path.join(ROOT, "START_TRADING.bat")],
                    cwd=ROOT, env=env, stdout=session_log, stderr=subprocess.STDOUT,
                    creationflags=_detach_flags)
                _log("boot spawned (pid {}); watcher exits - session monitoring "
                     "stays with the 2h duty watcher".format(proc.pid))
                return 0
            time.sleep(POLL_S)
        except KeyboardInterrupt:
            _log("interrupted - exiting without booting")
            return 130
        except Exception as exc:  # never die on a transient probe error
            _log("probe error (continuing): {}".format(exc))
            time.sleep(POLL_S)
    _log("window expired after {:.1f}h without clearing the bar - watcher "
         "exits, boot NOT attempted".format(WINDOW_H))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("bar={:.2f}GB window={:.1f}h poll={:.0f}s free={:.2f}GB "
              "trader_running={}".format(BAR_GB, WINDOW_H, POLL_S, _free_gb(),
                                         _trader_running()))
        sys.exit(0)
    sys.exit(main())
