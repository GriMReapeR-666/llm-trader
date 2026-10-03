"""Session-11 guarded boot: PAIRED BASELINE MEASUREMENT RUN.

Session-10 was the no_llm ablation measurement run.
Session-11 is the paired same-condition baseline:
  - Exact same budget: 24 loops, EURUSD, M15/AUTO, paper mode.
  - WITH LLM enabled (meta/llama-3.2-11b-vision-instruct, NO PU_ABLATION).
  - Slot gated on Session-10 completion: checks session-10 report exists and
    both session-10 processes are gone.
  - Preflight checks: fresh M15 completed candle and free RAM >= BAR_GB.

Marker: END-MARKER-PU-0159
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
OUT = os.path.join(ROOT, "artifacts", "pu_session11_boot.out")
SESSION_LOG = os.path.join(ROOT, "artifacts", "pu_session11_boot.log")
META = os.path.join(ROOT, "artifacts", "pu_session11_meta.json")
BAR_GB = float(os.getenv("WATCH_S11_BAR_GB", "1.2"))
WINDOW_H = float(os.getenv("WATCH_S11_WINDOW_H", "16"))
POLL_S = float(os.getenv("WATCH_S11_POLL_S", "30"))
# PU-0161: daily alternating ablation/baseline schedule. This chassis is the
# BASELINE arm (with LLM), so it only boots on days the schedule assigns
# with_llm. See artifacts/pu_ablation_schedule.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pu_ablation_schedule import ARM_BASELINE, resolve as _resolve_schedule

S10_WAITER_TOKEN = "pu_session10_waiter.py"
S10_BOOT_TOKEN = "pu_session10_boot.py"
S10_REPORT = os.path.join(ROOT, "artifacts", "pu_session10_report.txt")
S10_META = os.path.join(ROOT, "artifacts", "pu_session10_meta.json")

CHASSIS_TOKENS = (
    "pu_session11_boot", "pu_session11_waiter",
    "pu_session10_boot", "pu_session10_waiter",
    "pu_session9_boot", "pu_session9_waiter",
    "pu_session8_boot", "pu_session8_waiter",
    "pu_session7_boot", "pu_session7_waiter",
    "pu_session6_boot", "pu_session6_waiter",
    "pu_session5_boot", "pu_session5_waiter",
    "pu_session5_report.py", "pu_candle_state.py",
)
_PY_PROBE_RE = re.compile(r"process_iter|END-MARKER-PU-")

CMD = [
    sys.executable, "-X", "utf8", "-u", "mt5_trader.py", "llm-trade",
    "--paper", "--symbol", "EURUSD", "--timeframe", "AUTO",
    "--model", "meta/llama-3.2-11b-vision-instruct",
    "--risk-per-trade", "1.0", "--loops", "24", "--no-dashboard",
]


def _log(msg: str) -> None:
    line = "{} {}".format(datetime.now(timezone.utc).isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _free_gb() -> float:
    import psutil
    return psutil.virtual_memory().available / 2**30


def _s10_waiter_running() -> bool:
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if ((p.info["name"] or "").lower().startswith("python")
                    and S10_WAITER_TOKEN in cmd
                    and not _PY_PROBE_RE.search(cmd)):
                return True
        except Exception:
            continue
    return False


def _s10_boot_watcher_running() -> bool:
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if ((p.info["name"] or "").lower().startswith("python")
                    and S10_BOOT_TOKEN in cmd
                    and not _PY_PROBE_RE.search(cmd)):
                return True
        except Exception:
            continue
    return False


def _session10_done() -> bool:
    """Session-10 slot is free when its waiter and boot watcher exited AND its report landed.

    PU-0161: on a baseline day the schedule makes session-10 SKIP (it only
    runs on ablation days), so no report will ever be written. In that case
    "session-10 chose not to run today" satisfies the slot just as well as a
    report — but only once its processes are actually gone, so we never race
    a session-10 that is still deciding.
    """
    if _s10_waiter_running() or _s10_boot_watcher_running():
        return False
    if os.path.exists(S10_REPORT):
        return True
    sched = _resolve_schedule()
    if sched.enabled and sched.arm_today != ARM_ABLATION:
        _log("slot free by schedule: session-10 is the ablation arm and today "
             "is arm '{}' ({}), so no s10 report is expected".format(
                 sched.arm_today, sched.today.isoformat()))
        return True
    return False


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
    _sched = _resolve_schedule(arm_env=os.getenv("PU_ABLATION_ARM", ARM_BASELINE))
    _log("armed: bar {:.2f}GB, window {:.1f}h, poll {:.0f}s, free {:.2f}GB, "
         "baseline mode (loops=24, with-LLM, waits for session-10 slot) | {}"
         .format(BAR_GB, WINDOW_H, POLL_S, _free_gb(), _sched.reason))
    if _sched.skip_reason:
        # Schedule gate FIRST: exit before waiting on the session-10 slot so
        # an ablation day is never blocked behind another chassis's gate.
        _log("SKIP - {}".format(_sched.skip_reason))
        return 0
    deadline = time.monotonic() + WINDOW_H * 3600.0
    while time.monotonic() < deadline:
        if _session10_done():
            break
        time.sleep(POLL_S)
    else:
        _log("window expired with session-10 still holding the slot - NOT booted")
        return 0
    _log("session-10 slot free (report present) - proceeding to preflight loop")

    trader_strikes = 0
    while time.monotonic() < deadline:
        try:
            if _trader_running():
                trader_strikes += 1
                if trader_strikes >= 2:
                    _log("trader confirmed on two consecutive polls - watcher exits")
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
                 "launching BASELINE boot (WITH LLM enabled)".format(free, BAR_GB))
            env = dict(os.environ)
            env["LLM_TRADER_COMPACT_CONTEXT"] = "1"
            env["NEWS_TRIAGE_MODE"] = "off"
            env["HEADLESS"] = "1"
            env["CI"] = "1"
            # Ensure NO PU_ABLATION is active so LLM executes as baseline
            env.pop("PU_ABLATION", None)
            session_log = open(SESSION_LOG, "ab")
            proc = subprocess.Popen(
                CMD, cwd=ROOT, env=env, stdout=session_log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            now = datetime.now(timezone.utc).isoformat()
            with open(META, "w", encoding="utf-8") as fh:
                json.dump({"booted_at_utc": now, "pid": proc.pid,
                           "free_gb_at_boot": round(free, 2),
                           "mode": "baseline_with_llm", "cmd": CMD}, fh, indent=2)
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
              "s10_done={} trader_running={} script_ok={}".format(
                  BAR_GB, WINDOW_H, POLL_S, _free_gb(),
                  _session10_done(), _trader_running(),
                  os.path.exists(os.path.join(ROOT, "mt5_trader.py"))))
        sys.exit(0)
    sys.exit(main())
