"""One-command scheduler self-verification (marker-footed smoke).

Backports the quant_knowledge_framework smoke pattern to the live stack:
a single command that proves the operations/scheduler stack is healthy and
leaves a marker-footed report as evidence.

    python -X utf8 scripts/scheduler_smoke.py
    python -X utf8 scripts/scheduler_smoke.py --skip-tests
    python -X utf8 scripts/scheduler_smoke.py --json   # also print the JSON

Steps (all fail-soft except where noted; exit 0 only when every gating step
is OK — the report records warnings without failing the run):

1. journal    read-only integrity of trading_state.db (quick_check; file-level
              integrity_check is attempted but soft — a locked live journal is
              expected, not a failure)
2. schedules  agent_schedules table vs operations/agent_scheduler.SCHEDULES:
              unregistered drift and interval/payload mismatches (this table is
              rebuilt by bootstrap() on every trader start, so a mismatch means
              the running code and the durable state disagree)
3. events     operational_events / agent_runs freshness over the last 24h:
              severity counts plus stale-RUNNING detection (a run whose
              heartbeat_at is older than --stale-minutes is a WARNING, not a
              gate — freshness is informational in a smoke)
4. autonomy   autonomy_state.json parses, level is a known LEVELS key, and
              halted/paused flags are booleans (absent file = OK-with-note:
              a fresh deployment has no state yet)
5. tests      the committed scheduler battery (same suites as
              run_session_followups.py); skippable with --skip-tests

Nothing here writes to the journal, the autonomy state, or any control file:
every database access is opened ``mode=ro`` and the report is the only output.
The report carries an ``END-MARKER-SMOKE-<UTC>`` footer (channel-doctrine:
trust marker-footed files read back from disk, not scrollback) and stdout
ends with ``SCHEDULER-SMOKE-VERDICT: GREEN|RED`` for automation.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
JOURNAL = Path(os.getenv("LLM_TRADER_JOURNAL_PATH", str(ROOT / "trading_state.db")))
AUTONOMY_STATE = ROOT / "autonomy_state.json"

# Same battery run_session_followups.py trusts post-session.
SUITES = [
    "tests/test_agent_operations.py", "tests/test_scheduler_pulse.py",
    "tests/test_compliance_wiring.py", "tests/test_session_report_hook.py",
    "tests/test_scheduler_tuning_report.py", "tests/test_dispatch_blocker_profile.py",
    "tests/test_apply_budget_raises.py", "tests/test_crash_detector.py",
]
STALE_RUNNING_MINUTES_DEFAULT = 15.0
RECENT_WINDOW_HOURS = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_minutes(ts_value: Any, now: datetime | None = None) -> float | None:
    ts = _parse_ts(ts_value)
    if ts is None:
        return None
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 60.0)


def _ensure_repo_import_paths() -> None:
    """Make repo-style imports work outside pytest/the trader.

    The repo convention is that ``operations/`` modules import their siblings
    bare (``from agent_contracts import ...``); the trader and the pytest
    conftest both put ``operations/`` on sys.path. A standalone script must do
    the same before importing anything under operations/.
    """
    for path in (str(ROOT), str(ROOT / "operations")):
        if path not in sys.path:
            sys.path.insert(0, path)


def _load_schedule_registry() -> List[Dict[str, Any]]:
    _ensure_repo_import_paths()
    from operations.agent_scheduler import SCHEDULES  # noqa: PLC0415 (runtime path setup)

    return list(SCHEDULES)


# ---------------------------------------------------------------------------
# Step 1: journal integrity (read-only)
# ---------------------------------------------------------------------------
def step_journal(journal: Path = JOURNAL) -> Tuple[bool, str]:
    if not journal.exists():
        return False, f"journal database missing: {journal}"
    try:
        con = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as exc:
        return False, f"cannot open journal read-only: {exc}"
    try:
        rows = con.execute("PRAGMA quick_check").fetchall()
        quick = " ".join(str(r[0]) for r in rows)
        notes = [f"quick_check: {quick}"]
        ok = quick.startswith("ok")
        # A busy live journal can refuse integrity_check; that is not a failure.
        try:
            depth_rows = con.execute("PRAGMA integrity_check(20)").fetchall()
            depth = " ".join(str(r[0]) for r in depth_rows)
            notes.append(f"integrity_check: {depth}")
            if not depth.startswith("ok"):
                ok = False
        except sqlite3.Error as exc:
            notes.append(f"integrity_check skipped (live journal): {exc}")
        tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [t for t in ("agent_runs", "operational_events", "agent_schedules") if t not in tables]
        if missing:
            ok = False
            notes.append(f"missing core tables: {missing}")
        return ok, "; ".join(notes)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Step 2: schedule registry drift (read-only vs bootstrap truth)
# ---------------------------------------------------------------------------
def _schedule_drift(rows: List[sqlite3.Row], registry: List[Dict[str, Any]]) -> Tuple[bool, str]:
    registered = {r["schedule_name"]: r for r in rows}
    expected = {item["name"]: item for item in registry}
    notes: List[str] = [f"{len(registered)} schedules registered, {len(expected)} in code registry"]

    missing = sorted(set(expected) - set(registered))
    extra = sorted(set(registered) - set(expected))
    if missing:
        return False, "; ".join(notes + [f"NOT registered: {missing}"])
    if extra:
        return False, "; ".join(notes + [f"unregistered rows in table: {extra}"])

    drifted = []
    for name, item in expected.items():
        row = registered[name]
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
        except json.JSONDecodeError:
            payload = {}
        if int(row["interval_seconds"]) != int(item["interval"]):
            drifted.append(f"{name}: interval {row['interval_seconds']} != {item['interval']}")
        if payload.get("kind") != (item.get("payload") or {}).get("kind"):
            drifted.append(f"{name}: payload kind {payload.get('kind')!r} != {(item.get('payload') or {}).get('kind')!r}")
    if drifted:
        return False, "; ".join(notes + ["drift: " + ", ".join(drifted)])
    notes.append("registry matches code (names, intervals, payload kinds)")
    return True, "; ".join(notes)


def step_schedules(journal: Path = JOURNAL, registry: List[Dict[str, Any]] | None = None) -> Tuple[bool, str]:
    if not journal.exists():
        return False, f"journal database missing: {journal}"
    try:
        con = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return False, f"cannot open journal read-only: {exc}"
    try:
        rows = con.execute(
            "SELECT schedule_name, agent, interval_seconds, next_run_at, last_run_at,"
            " enabled, payload FROM agent_schedules ORDER BY schedule_name"
        ).fetchall()
    except sqlite3.Error as exc:
        return False, f"agent_schedules not readable: {exc}"
    finally:
        con.close()
    registry = registry if registry is not None else _load_schedule_registry()
    return _schedule_drift([dict(r) for r in rows], registry)


# ---------------------------------------------------------------------------
# Step 3: event freshness (read-only; stale-RUNNING is a warning)
# ---------------------------------------------------------------------------
def _event_facts(rows_by_query: Dict[str, List[sqlite3.Row]],
                 stale_running: int, stale_minutes: float) -> Tuple[bool, str]:
    notes: List[str] = []
    ok = True

    severity = {r["severity"]: r["n"] for r in rows_by_query["severity"]}
    notes.append("24h severities: " + (json.dumps(severity, sort_keys=True) if severity else "{}"))
    critical = sum(n for s, n in severity.items() if str(s).lower() in ("critical", "fatal"))
    if critical:
        ok = False
        notes.append(f"{critical} critical/fatal events in the last 24h (gate)")

    runs = {r["status"]: r["n"] for r in rows_by_query["run_status"]}
    notes.append("agent_runs by status (all time): " + json.dumps(runs, sort_keys=True))
    if stale_running:
        notes.append(
            f"WARNING: {stale_running} RUNNING run(s) with heartbeat older than "
            f"{stale_minutes:g}m (informational, not a gate)"
        )
    return ok, "; ".join(notes)


def step_events(journal: Path = JOURNAL,
                stale_minutes: float = STALE_RUNNING_MINUTES_DEFAULT,
                now: datetime | None = None) -> Tuple[bool, str]:
    if not journal.exists():
        return False, f"journal database missing: {journal}"
    try:
        con = sqlite3.connect(f"file:{journal}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return False, f"cannot open journal read-only: {exc}"
    try:
        severity = con.execute(
            "SELECT severity, COUNT(*) AS n FROM operational_events "
            "WHERE created_at >= datetime('now', ?) GROUP BY severity",
            (f"-{RECENT_WINDOW_HOURS} hours",),
        ).fetchall()
        run_status = con.execute(
            "SELECT status, COUNT(*) AS n FROM agent_runs GROUP BY status"
        ).fetchall()
        stale = 0
        for row in con.execute(
            "SELECT heartbeat_at FROM agent_runs WHERE status = 'RUNNING'"
        ).fetchall():
            age = _age_minutes(row["heartbeat_at"], now)
            if age is None or age > stale_minutes:
                stale += 1
    except sqlite3.Error as exc:
        return False, f"event tables not readable: {exc}"
    finally:
        con.close()
    return _event_facts(
        {"severity": [dict(r) for r in severity], "run_status": [dict(r) for r in run_status]},
        stale, stale_minutes,
    )


# ---------------------------------------------------------------------------
# Step 4: autonomy state file (parse + vocabulary check)
# ---------------------------------------------------------------------------
def _autonomy_check(state: Dict[str, Any], known_levels: Any) -> Tuple[bool, str]:
    notes: List[str] = []
    level = state.get("level")
    if not isinstance(level, str) or not level:
        return False, f"autonomy level malformed: {level!r}"
    if isinstance(known_levels, dict):
        if level not in known_levels:
            return False, f"autonomy level {level!r} not in known levels {sorted(known_levels)}"
        notes.append(f"level {level}")
    else:
        notes.append(f"level {level} (vocabulary not importable; syntax-only check)")

    for flag in ("halted", "paused"):
        value = state.get(flag)
        if not isinstance(value, bool):
            return False, f"{flag} flag not a boolean: {value!r}"
        notes.append(f"{flag}={str(value).lower()}")
    history = state.get("history")
    if history is not None and not isinstance(history, list):
        return False, f"history not a list: {type(history).__name__}"
    return True, "; ".join(notes)


def step_autonomy(state_path: Path = AUTONOMY_STATE) -> Tuple[bool, str]:
    if not state_path.exists():
        return True, "autonomy_state.json absent (fresh deployment; nothing to verify)"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"autonomy_state.json unreadable/malformed: {exc}"
    if not isinstance(state, dict):
        return False, f"autonomy_state.json is not an object: {type(state).__name__}"
    known_levels: Any = None
    try:
        _ensure_repo_import_paths()
        from operations.autonomy import LEVELS  # noqa: PLC0415 (runtime path setup)

        known_levels = dict(LEVELS)
    except Exception as exc:  # pragma: no cover - import environment dependent
        known_levels = None
        del exc
    return _autonomy_check(state, known_levels)


# ---------------------------------------------------------------------------
# Step 5: committed scheduler test battery
# ---------------------------------------------------------------------------
def step_tests(root: Path = ROOT, suites: List[str] = SUITES, timeout: int = 1800) -> Tuple[bool, str]:
    env = dict(os.environ)
    # Never let test subprocesses fire the post-session report hook.
    env["LLM_TRADER_POST_SESSION_REPORT"] = "0"
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "pytest", *suites,
             "-p", "no:warnings", "-n0", "-q", "--tb=short"],
            cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"test battery timed out after {timeout}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-3:])
    return proc.returncode == 0, f"exit {proc.returncode}\n{tail}"


# ---------------------------------------------------------------------------
# Orchestration + marker-footed report
# ---------------------------------------------------------------------------
def evaluate(steps: List[Tuple[str, bool, str]]) -> Tuple[str, List[str]]:
    verdict = "GREEN" if all(ok for _, ok, _ in steps) else "RED"
    failures = [name for name, ok, _ in steps if not ok]
    return verdict, failures


def build_report(steps: List[Tuple[str, bool, str]]) -> Dict[str, Any]:
    verdict, failures = evaluate(steps)
    return {
        "generated_at": _now(),
        "verdict": verdict,
        "failures": failures,
        "steps": [
            {"name": name, "ok": ok, "note": note} for name, ok, note in steps
        ],
        "marker": f"END-MARKER-SMOKE-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true",
                        help="skip the pytest battery (journal/state checks only)")
    parser.add_argument("--stale-minutes", type=float, default=STALE_RUNNING_MINUTES_DEFAULT,
                        help="heartbeat age that flags a RUNNING run as stale (warning)")
    parser.add_argument("--json", action="store_true", help="also print the JSON report")
    args = parser.parse_args()

    ARTIFACTS.mkdir(exist_ok=True)
    steps: List[Tuple[str, bool, str]] = []

    # Paths are looked up through module globals at call time so tests can
    # point the whole smoke at a sandbox journal/state via monkeypatch.
    for name, fn in (
        ("journal", lambda: step_journal(JOURNAL)),
        ("schedules", lambda: step_schedules(JOURNAL)),
        ("events", lambda: step_events(JOURNAL, stale_minutes=args.stale_minutes)),
        ("autonomy", lambda: step_autonomy(AUTONOMY_STATE)),
    ):
        try:
            ok, note = fn()
        except Exception as exc:  # a step crashing must not kill the smoke
            ok, note = False, f"step crashed: {exc}"
        steps.append((name, bool(ok), note))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {note}", flush=True)

    if args.skip_tests:
        steps.append(("tests", True, "SKIPPED (--skip-tests)"))
        print("[PASS] tests: SKIPPED (--skip-tests)", flush=True)
    else:
        print("running scheduler test battery (this can take a while)...", flush=True)
        ok, note = step_tests()
        steps.append(("tests", bool(ok), note))
        print(f"[{'PASS' if ok else 'FAIL'}] tests: {note}", flush=True)

    report = build_report(steps)
    verdict = report["verdict"]

    text_lines = ["# Scheduler Smoke Report", "", f"generated_at: {report['generated_at']}",
                  f"verdict: {verdict}", ""]
    for name, ok, note in steps:
        text_lines.append(f"[{'PASS' if ok else 'FAIL'}] {name}: {note.replace(chr(10), ' | ')}")
    text_lines.append("")
    text_lines.append(f"SCHEDULER-SMOKE-VERDICT: {verdict}")
    text_lines.append(report["marker"])

    report_path = ARTIFACTS / "scheduler_smoke_report.txt"
    report_path.write_text("\n".join(text_lines) + "\n", encoding="utf-8")
    (ARTIFACTS / "scheduler_smoke_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )

    print()
    print(f"SCHEDULER-SMOKE-VERDICT: {verdict}")
    print(f"report: {report_path}")
    if args.json:
        print(json.dumps(report, indent=2))
    return 0 if verdict == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
