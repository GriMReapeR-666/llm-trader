"""Execute the SESSION_NOTES live-session follow-up list end to end.

Waits for the current trader session to end (optional; the shutdown hook
archives the tuning report automatically), then runs the follow-up list:

1. tuning report                      -> artifacts/scheduler_tuning_report.json
2. applier --verify                   -> verdict cross-check + journal provenance
3. applier dry-run, then --apply      -> ONLY if the report has RAISE verdicts
4. duty comparison (--min-span-hours 0.25 early read)
5. dashboard regeneration + consistency check
                                         compliance_dashboard.html is
                                         regenerated, then cross-checked
                                         against the journal and the report
6. scheduler test battery             -> the 8 committed suites

Every step is fail-soft: a failure is recorded and the run continues; the
exit code is 0 unless a CRITICAL step (report, verify) failed. A JSON
summary lands in artifacts/session_followup_<UTC>.json. Nothing is
committed automatically - the summary states what is commit-worthy.

Usage:
    python -X utf8 scripts/run_session_followups.py
    python -X utf8 scripts/run_session_followups.py --no-wait
    python -X utf8 scripts/run_session_followups.py --wait-deadline-min 180
    python -X utf8 scripts/run_session_followups.py --skip-tests
"""
from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "trading_state.db"
REPORT = ROOT / "scripts" / "scheduler_tuning_report.py"
APPLIER = ROOT / "scripts" / "apply_schedule_budget_raises.py"
DASHBOARD = ROOT / "compliance_dashboard.html"
SUITES = [
    "tests/test_agent_operations.py", "tests/test_scheduler_pulse.py",
    "tests/test_compliance_wiring.py", "tests/test_session_report_hook.py",
    "tests/test_scheduler_tuning_report.py", "tests/test_dispatch_blocker_profile.py",
    "tests/test_apply_budget_raises.py", "tests/test_crash_detector.py",
]
CRITICAL = {"report", "verify"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session(con: sqlite3.Connection) -> dict:
    start = con.execute(
        "SELECT MAX(created_at) FROM operational_events WHERE event_type='run_started'"
    ).fetchone()[0]
    finish = con.execute(
        "SELECT MAX(created_at) FROM operational_events WHERE event_type='run_finished'"
    ).fetchone()[0]
    return {"start": start, "finish": finish,
            "open": bool(start) and (not finish or finish < start)}


# A hard crash never emits run_finished, so an "open" run alone cannot tell
# a live session from a dead one: also require a heartbeat newer than the
# run's start. A live session re-stamps heartbeat_at every few seconds; a
# crash freezes it at its last loop turn. Anything older is treated as ended.
HEARTBEAT_STALE_S = 900.0


def _is_live(con: sqlite3.Connection, now_utc: datetime | None = None) -> dict:
    state = _session(con)
    if not state["open"]:
        state["live"] = False
        return state
    beat = con.execute(
        "SELECT heartbeat_at FROM agent_runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    try:
        age_s = (now_utc or datetime.now(timezone.utc)) - datetime.fromisoformat(beat)
        state["live"] = bool(beat) and age_s.total_seconds() < HEARTBEAT_STALE_S
    except (TypeError, ValueError):
        state["live"] = False
    if not state["live"]:
        state["stale_heartbeat"] = beat
    return state


def _run(cmd: list, timeout: int = 600) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          cwd=str(ROOT))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def wait_for_session_end(deadline_min: float) -> dict:
    """Block until the session ends (or the deadline).

    'Ended' means run_finished >= last run_started OR the latest run's
    heartbeat is stale (HEARTBEAT_STALE_S) — a hard crash leaves the run
    row open forever, and without the heartbeat check this wait would idle
    to the deadline instead of proceeding to the follow-up steps.
    """
    print(f"[{_now()}] waiting for the live session to end "
          f"(deadline {deadline_min:.0f} min)...", flush=True)
    deadline = time.monotonic() + deadline_min * 60
    last_beat = 0.0
    while time.monotonic() < deadline:
        con = sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True, timeout=30)
        try:
            state = _is_live(con)
        finally:
            con.close()
        if not state["open"]:
            print(f"[{_now()}] session ended (run_finished {state['finish']}).", flush=True)
            return state
        if not state["live"]:
            print(f"[{_now()}] run is open but its heartbeat is stale "
                  f"(last beat {state.get('stale_heartbeat')}); treating a "
                  "hard-crashed session as ended.", flush=True)
            return state
        if time.monotonic() - last_beat >= 300:
            print(f"[{_now()}] session still open (started {str(state['start'])[:19]})...",
                  flush=True)
            last_beat = time.monotonic()
        time.sleep(60)
    print(f"[{_now()}] deadline reached with the session still open; "
          "running follow-ups anyway (steps will re-check).", flush=True)
    return _session(sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True, timeout=30))


def step_report() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-X", "utf8", str(REPORT)])
    tail = "\n".join(out.strip().splitlines()[-6:])
    return code == 0, f"exit {code}\n{tail}"


def step_verify() -> tuple[bool, str]:
    data = json.loads((ROOT / "artifacts" / "scheduler_tuning_report.json")
                      .read_text(encoding="utf-8"))
    block = data.get("budget_raise_candidates") or {}
    n_rows = len(block.get("raise") or []) + len(block.get("watch") or [])
    if not n_rows:
        return True, ("no raise/watch verdict rows (no overruns or <20 dispatch "
                      "baseline) - graduated verdicts deferred to the next session")
    code, out = _run([sys.executable, "-X", "utf8", str(APPLIER), "--verify"])
    tail = "\n".join(out.strip().splitlines()[-4:])
    return code == 0, f"rows={n_rows} exit {code}\n{tail}"


def step_apply() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-X", "utf8", str(APPLIER)])
    if code != 0:
        return False, f"dry-run exit {code}\n{out.strip()[-400:]}"
    if "no RAISE verdicts" in out:
        return True, "no RAISE verdicts - nothing to apply (budgets untouched)"
    code, out = _run([sys.executable, "-X", "utf8", str(APPLIER), "--apply"])
    return code == 0, f"apply exit {code}\n{out.strip()[-400:]}"


def step_duty() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-X", "utf8", str(REPORT),
                      "--min-span-hours", "0.25"])
    lines = out.splitlines()
    verdicts = [l for l in lines
                if l.startswith("  ") and ("->" in l or "%" in l)
                and ("FIXED" in l or "IMPROVED" in l or "UNCHANGED" in l
                     or "WORSE" in l or "NO_DATA" in l)]
    overall = next((l.strip() for l in lines if l.strip().startswith("OVERALL:")), None)
    return code == 0, "\n".join(verdicts + ([overall] if overall else [])) or \
        (f"exit {code} (no comparison verdicts yet)" if code == 0 else f"exit {code}")


def step_dashboard() -> tuple[bool, str]:
    sys.path.insert(0, str(ROOT / "ui"))
    try:
        path = runpy.run_module("compliance_dashboard", run_name="not_main")[
            "generate_compliance_dashboard"](hours=24)
        return (bool(path), f"dashboard written: {path}" if path else "renderer returned empty")
    except Exception as exc:  # fail-soft by contract
        return False, f"dashboard regeneration failed: {exc}"


def _element_percentile(values: list, q: float) -> float:
    """Element-pick percentile, same semantics as the report's _percentile.

    (Pinned by tests/test_scheduler_tuning_report.py.) A local copy keeps
    this step import-free. The dashboard now computes p50/p95 with the
    report's _percentile too, so report, dashboard and this check share
    one percentile definition.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[idx])


def _journal_facts(hours: int = 24) -> dict:
    """Overrun/deferral/duty facts straight from the journal (ro).

    The same window the dashboard renders (default 24h), so the check
    compares like with like.
    """
    con = sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True, timeout=30)
    try:
        cutoff = (datetime.now(timezone.utc).isoformat()
                  if hours is None else
                  (datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=hours)
                   ).isoformat())
        overruns: dict = {}
        for name, elapsed, over_by in con.execute(
                """SELECT json_extract(payload,'$.schedule'),
                          json_extract(payload,'$.elapsed_s'),
                          json_extract(payload,'$.over_by_s')
                   FROM operational_events
                   WHERE event_type='schedule_handler_overrun' AND created_at >= ?""",
                (cutoff,)):
            if not name:
                continue
            item = overruns.setdefault(name, {"count": 0, "elapsed": [], "over_by": []})
            item["count"] += 1
            for key, value in (("elapsed", elapsed), ("over_by", over_by)):
                try:
                    item[key].append(float(value))
                except (TypeError, ValueError):
                    pass
        reason_totals: dict = {}
        for (payload,) in con.execute(
                """SELECT payload FROM operational_events
                   WHERE event_type='schedule_skipped' AND created_at >= ?""",
                (cutoff,)):
            try:
                parsed = json.loads(payload or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            reason = str(parsed.get("reason") or "unattributed")
            reason_totals[reason] = reason_totals.get(reason, 0) + 1
        duty_names = [row[0] for row in con.execute(
            """SELECT DISTINCT json_extract(payload,'$.schedule')
               FROM operational_events
               WHERE event_type='schedule_due' AND created_at >= ?""", (cutoff,))
            if row[0]]
        return {"overruns": overruns, "reason_totals": reason_totals,
                "duty_names": duty_names}
    finally:
        con.close()


def _check_dashboard_consistency(html_text: str, report: dict, facts: dict) -> tuple[bool, list]:
    """Cross-check the rendered dashboard against journal + report facts.

    Strict everywhere: the dashboard computes p50/p95 with the report's
    _percentile, so ANY mismatch against the journal's report-style
    percentiles fails the check (the former lenient even-count-p50 and
    n=2-p95 notes are gone along with the SQL percentile they excused).
    """
    ok = True
    notes: list = []
    overruns = facts.get("overruns") or {}
    if overruns and "Handler overruns" not in html_text:
        return False, ["journal has overruns but the dashboard has no overrun table"]
    for name, item in sorted(overruns.items()):
        row = re.search(
            rf"<tr><td>{re.escape(name)}</td><td>(\d+)</td>"
            rf"<td>([0-9.]+)s</td><td>([0-9.]+)s</td>"
            rf"<td>([0-9.]+)s</td><td>([0-9.]+)s</td>",
            html_text)
        if not row:
            ok = False
            notes.append(f"{name}: overrun row missing from dashboard")
            continue
        n = int(row.group(1))
        if n != item["count"]:
            ok = False
            notes.append(f"{name}: dashboard shows {n} overruns, journal has {item['count']}")
        worst_elapsed = max(item["elapsed"], default=0.0)
        worst_over = max(item["over_by"], default=0.0)
        for label, shown, expected in (
                ("worst elapsed", float(row.group(2)), worst_elapsed),
                ("worst over-by", float(row.group(3)), worst_over)):
            if abs(shown - expected) > 0.05:
                ok = False
                notes.append(f"{name}: dashboard {label} {shown}s != journal {expected:.2f}s")
        p50 = _element_percentile(item["elapsed"], 0.50)
        p95 = _element_percentile(item["elapsed"], 0.95)
        shown_p50, shown_p95 = float(row.group(4)), float(row.group(5))
        if abs(shown_p50 - p50) > 0.05:
            ok = False
            notes.append(f"{name}: dashboard p50 {shown_p50}s != report-style {p50:.2f}s")
        if abs(shown_p95 - p95) > 0.05:
            ok = False
            notes.append(f"{name}: dashboard p95 {shown_p95}s != report-style {p95:.2f}s")
    reason_totals = facts.get("reason_totals") or {}
    if reason_totals:
        missing = [r for r in reason_totals if r not in html_text]
        if missing:
            ok = False
            notes.append(f"deferral reasons {missing} not reflected in the dashboard")
    for name in facts.get("duty_names") or []:
        if name not in html_text:
            notes.append(f"{name}: has schedule_due in window but no dashboard row (note)")
    if not notes and ok:
        notes.append("consistent: dashboard matches journal and report facts")
    return ok, notes


def step_dashboard_check() -> tuple[bool, str]:
    if not DASHBOARD.exists():
        return False, "compliance_dashboard.html not found (regeneration step failed?)"
    report_path = ROOT / "artifacts" / "scheduler_tuning_report.json"
    if not report_path.exists():
        return False, "scheduler_tuning_report.json not found (report step failed?)"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    html_text = DASHBOARD.read_text(encoding="utf-8", errors="replace")
    ok, notes = _check_dashboard_consistency(html_text, report, _journal_facts(24))
    return ok, "\n".join(notes)


def step_tests() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-X", "utf8", "-m", "pytest", *SUITES,
                      "-p", "no:warnings", "-n0", "-q", "--tb=short"], timeout=1800)
    tail = "\n".join(out.strip().splitlines()[-3:])
    return code == 0, f"exit {code}\n{tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-deadline-min", type=float, default=240.0)
    parser.add_argument("--no-wait", action="store_true",
                        help="skip the wait (session already ended)")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    # The post-session hook must never fire from THIS process's subprocesses.
    os.environ["LLM_TRADER_POST_SESSION_REPORT"] = "0"

    con = sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True, timeout=30)
    state = _session(con)
    con.close()
    if state["open"] and not args.no_wait:
        wait_for_session_end(args.wait_deadline_min)

    steps = [("report", step_report), ("verify", step_verify),
             ("apply", step_apply), ("duty", step_duty),
             ("dashboard", step_dashboard), ("dashboard_check", step_dashboard_check)]
    if not args.skip_tests:
        steps.append(("tests", step_tests))

    results = {}
    for name, fn in steps:
        print(f"\n=== step: {name} ===", flush=True)
        try:
            ok, note = fn()
        except Exception as exc:
            ok, note = False, f"step crashed: {exc}"
        results[name] = {"ok": ok, "note": note}
        print(f"[{_now()}] {name}: {'OK' if ok else 'FAIL'}\n{note}", flush=True)

    critical_failed = [n for n in CRITICAL if n in results and not results[n]["ok"]]
    code = 1 if critical_failed else 0
    summary = {
        "ran_at": _now(), "session": state, "results": results,
        "critical_failed": critical_failed,
        "commit_worthies": [n for n, r in results.items() if r["ok"] and n in
                            ("apply",)],
        "note": "no auto-commit; review applied budgets and the refreshed "
                "report artifact before committing",
    }
    out_path = ROOT / "artifacts" / (
        f"session_followup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    try:
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nsummary artifact: {out_path}")
    except OSError:
        pass
    print(f"EXIT {code}" + (f" (critical: {critical_failed})" if critical_failed else ""))
    return code


if __name__ == "__main__":
    sys.exit(main())
