"""One-shot watcher (throwaway, artifacts/): wait for a LIVE paper session to
cross the 2.0h span floor, then run the early duty-cycle comparison at that
floor plus the follow-up executor's dashboard cross-check -- without waiting
for the session to end.

Liveness reuses run_session_followups._is_live (open run + fresh heartbeat).
Gives up after 6h of no live session. Outputs land in artifacts/.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "run_session_followups", ROOT / "scripts" / "run_session_followups.py")
rsf = importlib.util.module_from_spec(spec)
sys.modules.setdefault("run_session_followups", rsf)
spec.loader.exec_module(rsf)

MAX_WAIT_H = 6.0
FLOOR_H = 2.0
POLL_S = 120


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}",
          flush=True)


def main() -> int:
    started = datetime.now(timezone.utc)
    log(f"watcher armed; waiting up to {MAX_WAIT_H:.0f}h for a live session "
        f"to cross the {FLOOR_H:.1f}h span floor")
    while True:
        if datetime.now(timezone.utc) - started > timedelta(hours=MAX_WAIT_H):
            log("gave up: no live session crossed the floor within the wait "
                "window (see artifacts/watch_2h_*.out if it fired)")
            return 1
        con = sqlite3.connect(f"file:{ROOT / 'trading_state.db'}?mode=ro",
                              uri=True, timeout=30)
        try:
            state = rsf._is_live(con)
        finally:
            con.close()
        if not state.get("live"):
            log("no live session (open run + fresh heartbeat); retrying")
            time.sleep(POLL_S)
            continue
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(state["start"])).total_seconds() / 3600
        if age_h < FLOOR_H:
            log(f"live session at {age_h:.2f}h (< {FLOOR_H:.1f}h); retrying")
            time.sleep(POLL_S)
            continue
        log(f"session crossed the floor ({age_h:.2f}h); running early duty "
            "comparison at --min-span-hours 2.0")
        r1 = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(ROOT / "scripts" / "scheduler_tuning_report.py"),
             "--min-span-hours", str(FLOOR_H)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(ROOT), timeout=300)
        (ROOT / "artifacts" / "watch_2h_duty.out").write_text(
            (r1.stdout or "") + (r1.stderr or ""), encoding="utf-8")
        log(f"duty report exit {r1.returncode} -> artifacts/watch_2h_duty.out")
        log("running follow-up executor (dashboard regen + cross-check, no tests)")
        r2 = subprocess.run(
            [sys.executable, "-X", "utf8",
             str(ROOT / "scripts" / "run_session_followups.py"),
             "--no-wait", "--skip-tests"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(ROOT), timeout=900)
        (ROOT / "artifacts" / "watch_2h_followup.out").write_text(
            (r2.stdout or "") + (r2.stderr or ""), encoding="utf-8")
        log(f"executor exit {r2.returncode} -> artifacts/watch_2h_followup.out")
        log("WATCHER DONE")
        return 0


if __name__ == "__main__":
    sys.exit(main())
