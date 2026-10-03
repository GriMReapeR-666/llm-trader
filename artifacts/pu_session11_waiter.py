"""Session-11 waiter with ACTIVE RAM GUARD (PU-0159).

Monitors Session-11 trader execution, applies active working-set memory
pressure relief when available RAM drops dangerously low, generates the
PU-0150 comparison report upon completion or 5h run cap, and — since the
2026-10-03 hardening — detects runaway trader RSS spikes and winds the
trader down gracefully (CTRL_BREAK -> grace -> hard terminate) so the box
is not thrashed, leaving a durable operational_events audit row.

Marker: END-MARKER-PU-0159
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(ROOT, "artifacts", "pu_session11_meta.json")
WAITER_OUT = os.path.join(ROOT, "artifacts", "pu_session11_waiter.out")
REPORT = os.path.join(ROOT, "artifacts", "pu_session5_report.py")
REPORT_OUT = os.path.join(ROOT, "artifacts", "pu_session11_report.txt")
BOOT_WINDOW_S = 16 * 3600
RUN_CAP_S = 5 * 3600
POLL_S = 60

# RAM Guard Thresholds
RAM_WARN_GB = 0.40       # Log warning when free RAM < 400 MB
RAM_CRITICAL_GB = 0.25   # Trigger working-set trim when free RAM < 250 MB
RAM_EMERGENCY_GB = 0.20  # Emergency threshold for sustained pressure

# RSS Spike Guard (runaway trader process)
RSS_SPIKE_GB = float(os.getenv("WATCH_S11_RSS_SPIKE_GB", "3.5"))
RSS_SPIKE_STRIKES = int(os.getenv("WATCH_S11_RSS_SPIKE_STRIKES", "3"))  # consecutive polls
WIND_DOWN_GRACE_S = int(os.getenv("WATCH_S11_WIND_DOWN_GRACE_S", "120"))

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


def _log(msg: str) -> None:
    line = "{} {}".format(datetime.now(timezone.utc).isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    with open(WAITER_OUT, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _free_gb() -> float:
    import psutil
    return psutil.virtual_memory().available / 2**30


def _trim_process(pid: int) -> bool:
    """Trigger working set trim on Windows to release unreferenced pages to the OS."""
    try:
        import ctypes
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid
        )
        if handle:
            res = ctypes.windll.psapi.EmptyWorkingSet(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(res)
    except Exception:
        pass
    return False


def _emit_guard_event(event_type: str, payload: dict) -> None:
    """Durable operational_events audit row so the report engine and any
    later forensics see the guard's intervention (best-effort, never raises)."""
    try:
        encoded = json.dumps(payload, sort_keys=True)
        con = sqlite3.connect(os.path.join(ROOT, "trading_state.db"), timeout=10)
        try:
            with con:
                con.execute(
                    "INSERT INTO operational_events(created_at,run_id,actor,event_type,"
                    "correlation_id,severity,payload,payload_hash) VALUES(?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), "session11-waiter",
                     "waiter", event_type, "", "WARNING", encoded,
                     hashlib.sha256(encoded.encode("utf-8")).hexdigest()))
        finally:
            con.close()
    except Exception as exc:
        _log("[RSS_GUARD] durable event write failed (non-fatal): %s" % exc)


def _graceful_wind_down(pid: int, cause: str) -> None:
    """Wind a runaway trader down. Empirical Windows reality (tested
    2026-10-03, see pu_rss_guard_selftest.py): a boot-spawned trader sits in
    a CREATE_NEW_PROCESS_GROUP group with CTRL_C disabled, Python maps only
    CTRL_C to KeyboardInterrupt, and CTRL_BREAK terminates via the default
    console handler (0xC000013A) WITHOUT running the trader's
    KeyboardInterrupt cleanup. So the honest ladder is: CTRL_C (graceful IF
    a future trader re-enables it) -> CTRL_BREAK (hard console terminate) ->
    psutil terminate (last resort). Hard kills are acceptable here: the
    trader journals CrashRecovery checkpoints every cycle and its recovery
    machinery replays from the last checkpoint."""
    _log("[RSS_GUARD] ESCALATION (%s): winding down trader pid %s" % (cause, pid))
    _emit_guard_event("trader_rss_guard_escalation", {
        "cause": cause, "pid": pid,
        "rss_spike_gb": RSS_SPIKE_GB, "grace_s": WIND_DOWN_GRACE_S,
    })

    def _alive() -> bool:
        try:
            import psutil
            psutil.Process(pid)
            return True
        except Exception:
            return False

    # Step 1: CTRL_C_EVENT - graceful ONLY if the target re-enabled CTRL_C
    # for its process group (current trader does not; harmless if ignored).
    try:
        import signal
        os.kill(pid, signal.CTRL_C_EVENT)
        _log("[RSS_GUARD] CTRL_C_EVENT sent to pid %s (grace %ss)" % (pid, WIND_DOWN_GRACE_S))
        deadline = time.monotonic() + WIND_DOWN_GRACE_S
        while time.monotonic() < deadline:
            if not _alive():
                _log("[RSS_GUARD] trader pid %s exited gracefully (CTRL_C)" % pid)
                return
            time.sleep(5)
    except Exception as exc:
        _log("[RSS_GUARD] CTRL_C_EVENT unavailable for pid %s: %s" % (pid, exc))

    # Step 2: CTRL_BREAK - hard console terminate (no KeyboardInterrupt in
    # the target; CrashRecovery replays from the last journal checkpoint).
    if _alive():
        try:
            import signal
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            _log("[RSS_GUARD] CTRL_BREAK sent to pid %s (hard console terminate)" % pid)
        except Exception as exc:
            _log("[RSS_GUARD] CTRL_BREAK failed on pid %s: %s" % (pid, exc))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if not _alive():
                _log("[RSS_GUARD] trader pid %s terminated (CTRL_BREAK)" % pid)
                return
            time.sleep(2)

    # Step 3: last resort.
    if _alive():
        try:
            import psutil
            psutil.Process(pid).terminate()
            _log("[RSS_GUARD] psutil terminate sent to trader pid %s" % pid)
        except Exception as exc:
            _log("[RSS_GUARD] terminate failed on pid %s: %s" % (pid, exc))


def _get_trader_proc() -> tuple[bool, int, float]:
    """Returns (is_running, pid, rss_gb)."""
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            if (name.startswith("python") and "mt5_trader.py" in cmd
                    and "llm-trade" in cmd
                    and not _PY_PROBE_RE.search(cmd)
                    and not any(tok in cmd for tok in CHASSIS_TOKENS)):
                rss = (p.info["memory_info"].rss if p.info.get("memory_info") else 0) / 2**30
                return True, p.pid, rss
        except Exception:
            continue
    return False, 0, 0.0


def main() -> int:
    _log("waiter armed (boot window {:.0f}h, run cap {:.0f}h, RAM guard + RSS spike guard armed: spike bar {:.2f}GB, {} strikes, wind-down grace {}s)".format(
        BOOT_WINDOW_S / 3600, RUN_CAP_S / 3600, RSS_SPIKE_GB, RSS_SPIKE_STRIKES, WIND_DOWN_GRACE_S))
    booted_at = None
    trader_pid = 0
    deadline = time.monotonic() + BOOT_WINDOW_S
    while time.monotonic() < deadline:
        if os.path.exists(META):
            try:
                with open(META, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    booted_at = data.get("booted_at_utc")
                    trader_pid = data.get("pid", 0)
            except Exception as exc:
                _log("meta read error: %s" % exc)
            break
        time.sleep(POLL_S)
    if not booted_at:
        _log("boot window expired with no meta file - session NOT booted; exit")
        print("END-MARKER-PU-0159")
        return 1

    _log("boot detected at %s (pid %s) - waiting out grace period" % (booted_at, trader_pid))
    time.sleep(300)

    gone_strikes = 0
    low_ram_strikes = 0
    rss_strikes = 0
    wind_down_started = False
    emergency_logged = False
    run_deadline = time.monotonic() + RUN_CAP_S

    while time.monotonic() < run_deadline:
        running, cur_pid, rss = _get_trader_proc()
        free_ram = _free_gb()

        if running:
            gone_strikes = 0
            if cur_pid:
                trader_pid = cur_pid

            # --- RSS SPIKE GUARD: runaway trader RSS => flag, then wind down ---
            if rss > RSS_SPIKE_GB:
                rss_strikes += 1
                _log("[RSS_GUARD] trader RSS {:.2f}GB > {:.2f}GB spike bar (strike {}/{}), free RAM {:.2f}GB".format(
                    rss, RSS_SPIKE_GB, rss_strikes, RSS_SPIKE_STRIKES, free_ram))
                if rss_strikes >= RSS_SPIKE_STRIKES and not wind_down_started:
                    wind_down_started = True
                    _graceful_wind_down(trader_pid, cause="rss_spike")
            else:
                rss_strikes = 0

            # --- RAM GUARD TELEMETRY & DEFENSIVE RELIEF ---
            if free_ram < RAM_WARN_GB:
                _log("[RAM_GUARD] WARNING: Low free RAM: {:.2f}GB (trader RSS: {:.2f}GB)".format(
                    free_ram, rss))

            if free_ram < RAM_CRITICAL_GB:
                low_ram_strikes += 1
                _log("[RAM_GUARD] CAUTION: System free RAM critically low ({:.2f}GB, strike {}/3). Attempting working-set trim on trader pid {}...".format(
                    free_ram, low_ram_strikes, trader_pid))
                trimmed = _trim_process(trader_pid)
                new_free = _free_gb()
                _log("[RAM_GUARD] Working-set trim result: {} -> new free RAM: {:.2f}GB".format(
                    trimmed, new_free))

                if low_ram_strikes >= 3 and new_free < RAM_EMERGENCY_GB:
                    if not emergency_logged:
                        emergency_logged = True
                        _log("[RAM_GUARD] EMERGENCY: Sustained severe RAM depletion (<{:.2f}GB) across 3 polls. Logging resource alert.".format(
                            RAM_EMERGENCY_GB))
                        _emit_guard_event("trader_ram_emergency", {
                            "free_gb": round(new_free, 3), "trader_rss_gb": round(rss, 2),
                            "pid": trader_pid,
                        })
                    if not wind_down_started:
                        wind_down_started = True
                        _graceful_wind_down(trader_pid, cause="system_ram_emergency")
            else:
                low_ram_strikes = 0
        else:
            gone_strikes += 1
            if gone_strikes >= 2:
                _log("trader process gone on two consecutive polls - run complete")
                break

        time.sleep(POLL_S)
    else:
        _log("run cap {:.0f}h hit - proceeding with report on bounded window".format(
            RUN_CAP_S / 3600))
        gone_strikes = 99

    if gone_strikes < 2:
        _log("run cap hit with process still alive - reporting anyway")

    time.sleep(20)  # let final journal writes commit
    _log("running PU-0150 report (window start %s)" % booted_at)
    with open(REPORT_OUT, "a", encoding="utf-8") as report_fh:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", REPORT, booted_at],
            cwd=ROOT, stdout=report_fh, stderr=subprocess.STDOUT, timeout=600)
    _log("report exit=%s -> artifacts/pu_session11_report.txt" % proc.returncode)
    print("END-MARKER-PU-0159")
    return 0


if __name__ == "__main__":
    sys.exit(main())
