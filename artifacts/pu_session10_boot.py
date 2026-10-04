"""Session-10 guarded boot: FIRST ABLATION MEASUREMENT RUN (PU-0156 §2).

Session-6..9 chassis with two additions:
  1. ABLATION GATE: waits until the session-9 slot is free — its waiter and
     boot watcher exited AND either the session-9 report exists, or session-9
     provably wound down without ever booting (no meta file, no 'boot
     spawned' line in its stdout, both session-9 processes gone). Then falls
     through to the normal preflight loop.
  2. ABLATION ENV: boots the trader with PU_ABLATION (default no_llm —
     "does the LLM add value over pure quant?"). Set WATCH_S10_ABLATION to
     "no_news" or "no_macro" to reuse this chassis for the other ablations.

The orchestrator emits an ablation_session_modes boot marker on the first
cycle; the PU-0150 report engine (ablation section) quantifies the window.
Baseline for comparison: session-9 (same 24-loop budget WITH all components).
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
OUT = os.path.join(ROOT, "artifacts", "pu_session10_boot.out")
SESSION_LOG = os.path.join(ROOT, "artifacts", "pu_session10_boot.log")
META = os.path.join(ROOT, "artifacts", "pu_session10_meta.json")
BAR_GB = float(os.getenv("WATCH_S10_BAR_GB", "1.5"))
WINDOW_H = float(os.getenv("WATCH_S10_WINDOW_H", "16"))
POLL_S = float(os.getenv("WATCH_S10_POLL_S", "30"))
ABLATION = os.getenv("WATCH_S10_ABLATION", "no_llm")
# PU-0161: daily alternating ablation/baseline schedule. This chassis is the
# ABLATION arm (no_llm), so it only boots on days the schedule assigns no_llm.
# On baseline days it yields immediately (before any slot wait) so the paired
# session-11 chassis can take the day. Disabled unless
# PU_ABLATION_SCHEDULE=alternate — see artifacts/pu_ablation_schedule.py.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pu_ablation_schedule import ARM_ABLATION, resolve as _resolve_schedule
S9_WAITER_TOKEN = "pu_session9_waiter.py"
S9_BOOT_TOKEN = "pu_session9_boot.py"
S9_REPORT = os.path.join(ROOT, "artifacts", "pu_session9_report.txt")
S9_META = os.path.join(ROOT, "artifacts", "pu_session9_meta.json")
S9_BOOT_STDOUT = os.path.join(ROOT, "artifacts", "pu_session9_boot_stdout.txt")
_BOOT_SPAWNED_RE = re.compile(r"boot spawned \(pid")

CHASSIS_TOKENS = ("pu_session10_boot", "pu_session10_waiter", "pu_session9_boot",
                  "pu_session9_waiter", "pu_session8_boot", "pu_session8_waiter",
                  "pu_session7_boot", "pu_session7_waiter",
                  "pu_session6_boot", "pu_session6_waiter",
                  "pu_session5_boot", "pu_session5_waiter",
                  "pu_session5_report.py", "pu_candle_state.py")
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


def _s9_waiter_running() -> bool:
    # NOTE: no CHASSIS_TOKENS exclusion here — that list contains
    # "pu_session9_waiter" itself, which would make this scan always False.
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if ((p.info["name"] or "").lower().startswith("python")
                    and S9_WAITER_TOKEN in cmd
                    and not _PY_PROBE_RE.search(cmd)):
                return True
        except Exception:
            continue
    return False


def _s9_boot_watcher_running() -> bool:
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if ((p.info["name"] or "").lower().startswith("python")
                    and S9_BOOT_TOKEN in cmd
                    and not _PY_PROBE_RE.search(cmd)):
                return True
        except Exception:
            continue
    return False


def _s9_failed_safe() -> bool:
    """True when session-9 provably never took the trader slot: its watcher
    and waiter are both gone, its meta file never landed (meta is written at
    boot), and its boot stdout shows no 'boot spawned' line."""
    if _s9_boot_watcher_running():
        return False
    if os.path.exists(S9_META):
        return False  # meta exists => boot watcher did boot the trader
    if not os.path.exists(S9_BOOT_STDOUT):
        return True
    try:
        with open(S9_BOOT_STDOUT, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return False
    return not _BOOT_SPAWNED_RE.search(text)


def _session9_done() -> bool:
    """Session-9 slot is free when its waiter exited and its report landed,
    or when session-9 provably wound down without ever booting the trader."""
    if _s9_waiter_running() or _s9_boot_watcher_running():
        return False
    return os.path.exists(S9_REPORT) or _s9_failed_safe()


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
    _sched = _resolve_schedule(arm_env=os.getenv("PU_ABLATION_ARM", ARM_ABLATION))
    _log("armed: bar {:.2f}GB, window {:.1f}h, poll {:.0f}s, free {:.2f}GB, "
         "ablation={} (loops=24, waits for session-9 slot: report or proven "
         "wind-down) | {}".format(BAR_GB, WINDOW_H, POLL_S, _free_gb(),
                                  ABLATION, _sched.reason))
    if _sched.skip_reason:
        # Schedule gate FIRST: exit before waiting on the session-9 slot so a
        # baseline day is never blocked behind another chassis's gate.
        _log("SKIP - {}".format(_sched.skip_reason))
        return 0
    deadline = time.monotonic() + WINDOW_H * 3600.0
    while time.monotonic() < deadline:
        if _session9_done():
            break
        time.sleep(POLL_S)
    else:
        _log("window expired with session-9 still holding the slot - NOT booted")
        return 0
    _log("session-9 slot free (waiter exited; report{} present) - preflight loop"
         .format("" if os.path.exists(S9_REPORT) else " NOT"))

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
                 "launching ABLATION boot (PU_ABLATION={})".format(free, BAR_GB, ABLATION))
            env = dict(os.environ)
            env["LLM_TRADER_COMPACT_CONTEXT"] = "1"
            env["NEWS_TRIAGE_MODE"] = "off"
            env["HEADLESS"] = "1"
            env["CI"] = "1"
            env["PU_ABLATION"] = ABLATION
            session_log = open(SESSION_LOG, "ab")
            proc = subprocess.Popen(
                CMD, cwd=ROOT, env=env, stdout=session_log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            now = datetime.now(timezone.utc).isoformat()
            with open(META, "w", encoding="utf-8") as fh:
                json.dump({"booted_at_utc": now, "pid": proc.pid,
                           "free_gb_at_boot": round(free, 2),
                           "ablation": ABLATION, "cmd": CMD}, fh, indent=2)
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
              "ablation={} s9_done={} trader_running={} script_ok={}".format(
                  BAR_GB, WINDOW_H, POLL_S, _free_gb(), ABLATION,
                  _session9_done(), _trader_running(),
                  os.path.exists(os.path.join(ROOT, "mt5_trader.py"))))
        sys.exit(0)
    sys.exit(main())
