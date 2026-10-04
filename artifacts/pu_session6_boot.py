"""Session-6 guarded boot: launch the DECISIVE bounded paper measurement run
(loops 8, --no-dashboard, COMPACT_CONTEXT=1, NEWS_TRIAGE_MODE=off) the moment
free RAM clears 1.5GB AND a fresh M15 candle is available.

Rerun of artifacts/pu_session5_boot.py (session-proven 2026-09-30 doctrine):
single-shot spawn, name-aware trader detection, decisions logged BEFORE acting,
bounded window. Session-6 changes are path/name only: session-5 evidence files
stay pristine and the old pu_session5_meta.json cannot short-circuit anything.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "artifacts", "pu_session6_boot.out")
SESSION_LOG = os.path.join(ROOT, "artifacts", "pu_session6_boot.log")
META = os.path.join(ROOT, "artifacts", "pu_session6_meta.json")
BAR_GB = float(os.getenv("WATCH_S6_BAR_GB", "1.5"))
WINDOW_H = float(os.getenv("WATCH_S6_WINDOW_H", "10"))
POLL_S = float(os.getenv("WATCH_S6_POLL_S", "30"))

# Chassis/tooling cmdlines that mention 'mt5_trader.py llm-trade' only as scan
# text must never count as a live trader: a verification scan containing those
# literal strings agreed with itself on two consecutive polls and killed the
# pre-restart watcher via the double-boot refusal branch (see boot.out).
CHASSIS_TOKENS = ("pu_session6_boot", "pu_session6_waiter", "pu_session5_boot",
                  "pu_session5_waiter", "pu_session5_report.py",
                  "pu_candle_state.py")
_PY_PROBE_RE = re.compile(r"process_iter|END-MARKER-PU-")

CMD = [
    sys.executable, "-X", "utf8", "-u", "mt5_trader.py", "llm-trade",
    "--paper", "--symbol", "EURUSD", "--timeframe", "AUTO",
    "--model", "meta/llama-3.2-11b-vision-instruct",
    "--risk-per-trade", "1.0", "--loops", "8", "--no-dashboard",
]


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
                    and "llm-trade" in cmd
                    and not _PY_PROBE_RE.search(cmd)
                    and not any(tok in cmd for tok in CHASSIS_TOKENS)
                    and p.pid != me):
                return True
        except Exception:
            continue
    return False


def _fresh_m15() -> bool:
    """Re-run the PU-0139 probe and read its fresh_m15_available verdict."""
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8",
             os.path.join(ROOT, "artifacts", "pu_candle_state.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=120)
        m = re.search(r'"fresh_m15_available":\s*(true|false)', proc.stdout)
        return bool(m and m.group(1) == "true")
    except Exception as exc:
        _log("candle probe error (treating as NOT fresh this poll): %s" % exc)
        return False


def main() -> int:
    _log("armed: bar {:.2f}GB, window {:.1f}h, poll {:.0f}s, free {:.2f}GB".format(
        BAR_GB, WINDOW_H, POLL_S, _free_gb()))
    deadline = time.monotonic() + WINDOW_H * 3600.0
    trader_strikes = 0
    while time.monotonic() < deadline:
        try:
            if _trader_running():
                trader_strikes += 1
                if trader_strikes >= 2:
                    _log("trader confirmed on two consecutive polls - exiting")
                    return 0
                time.sleep(POLL_S)
                continue
            trader_strikes = 0
            free = _free_gb()
            if free < BAR_GB:
                time.sleep(POLL_S)
                continue
            if not _fresh_m15():
                _log("RAM pass ({:.2f}GB) but NO fresh M15 candle - waiting".format(free))
                time.sleep(POLL_S)
                continue
            _log("PREFLIGHT PASS: {:.2f}GB >= {:.2f}GB and fresh M15 candle - "
                 "launching bounded measurement boot".format(free, BAR_GB))
            env = dict(os.environ)
            env["LLM_TRADER_COMPACT_CONTEXT"] = "1"
            env["NEWS_TRIAGE_MODE"] = "off"
            env["HEADLESS"] = "1"
            env["CI"] = "1"
            session_log = open(SESSION_LOG, "ab")
            proc = subprocess.Popen(
                CMD, cwd=ROOT, env=env, stdout=session_log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            now = datetime.now(timezone.utc).isoformat()
            with open(META, "w", encoding="utf-8") as fh:
                json.dump({"booted_at_utc": now, "pid": proc.pid,
                           "free_gb_at_boot": round(free, 2),
                           "cmd": CMD}, fh, indent=2)
            _log("boot spawned (pid {}); meta written; watcher exits".format(proc.pid))
            return 0
        except KeyboardInterrupt:
            _log("interrupted - exiting without booting")
            return 130
        except Exception as exc:
            _log("probe error (continuing): {}".format(exc))
            time.sleep(POLL_S)
    _log("window expired after {:.1f}h without clearing the bar - NOT booted".format(WINDOW_H))
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("bar={:.2f}GB window={:.1f}h poll={:.0f}s free={:.2f}GB "
              "trader_running={} script_ok={}".format(
                  BAR_GB, WINDOW_H, POLL_S, _free_gb(), _trader_running(),
                  os.path.exists(os.path.join(ROOT, "mt5_trader.py"))))
        sys.exit(0)
    sys.exit(main())
