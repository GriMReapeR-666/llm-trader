"""Session-8 waiter: poll for the guarded boot, then for run completion, then
emit the PU-0150 measurement report against the session-8 window.

Session-6/7 chassis with path/name-only changes (clean session-8 paths; marker
END-MARKER-PU-0154). Completion signal: the mt5_trader.py llm-trade process
gone for two consecutive polls after a 5-minute grace period. Bounded: 10h
boot window + 3h run cap; the report always runs with whatever window exists
so numbers are never lost.
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
META = os.path.join(ROOT, "artifacts", "pu_session8_meta.json")
WAITER_OUT = os.path.join(ROOT, "artifacts", "pu_session8_waiter.out")
REPORT = os.path.join(ROOT, "artifacts", "pu_session5_report.py")
REPORT_OUT = os.path.join(ROOT, "artifacts", "pu_session8_report.txt")
BOOT_WINDOW_S = 10 * 3600
RUN_CAP_S = 3 * 3600
POLL_S = 60

# Skip chassis/tooling cmdlines that only mention 'mt5_trader.py llm-trade' as
# scan text (session probes, psutil checks) - they are not a live trader.
CHASSIS_TOKENS = ("pu_session8_boot", "pu_session8_waiter", "pu_session7_boot",
                  "pu_session7_waiter", "pu_session6_boot", "pu_session6_waiter",
                  "pu_session5_boot", "pu_session5_waiter",
                  "pu_session5_report.py", "pu_candle_state.py")
_PY_PROBE_RE = re.compile(r"process_iter|END-MARKER-PU-")


def _log(msg: str) -> None:
    line = "{} {}".format(datetime.now(timezone.utc).isoformat(timespec="seconds"), msg)
    print(line, flush=True)
    with open(WAITER_OUT, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _trader_running() -> bool:
    import psutil
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            if (name.startswith("python") and "mt5_trader.py" in cmd
                    and "llm-trade" in cmd
                    and not _PY_PROBE_RE.search(cmd)
                    and not any(tok in cmd for tok in CHASSIS_TOKENS)):
                return True
        except Exception:
            continue
    return False


def main() -> int:
    _log("waiter armed (boot window {:.0f}h, run cap {:.0f}h)".format(
        BOOT_WINDOW_S / 3600, RUN_CAP_S / 3600))
    booted_at = None
    deadline = time.monotonic() + BOOT_WINDOW_S
    while time.monotonic() < deadline:
        if os.path.exists(META):
            try:
                with open(META, "r", encoding="utf-8") as fh:
                    booted_at = json.load(fh).get("booted_at_utc")
            except Exception as exc:
                _log("meta read error: %s" % exc)
            break
        time.sleep(POLL_S)
    if not booted_at:
        _log("boot window expired with no meta file - session NOT booted; exit")
        print("END-MARKER-PU-0154")
        return 1
    _log("boot detected at %s - waiting out grace period" % booted_at)
    time.sleep(300)
    gone_strikes = 0
    run_deadline = time.monotonic() + RUN_CAP_S
    while time.monotonic() < run_deadline:
        if _trader_running():
            gone_strikes = 0
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
    time.sleep(20)  # let the final journal writes commit
    _log("running PU-0150 report (window start %s)" % booted_at)
    with open(REPORT_OUT, "a", encoding="utf-8") as report_fh:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", REPORT, booted_at],
            cwd=ROOT, stdout=report_fh, stderr=subprocess.STDOUT, timeout=600)
    _log("report exit=%s -> artifacts/pu_session8_report.txt" % proc.returncode)
    print("END-MARKER-PU-0154")
    return 0


if __name__ == "__main__":
    sys.exit(main())
