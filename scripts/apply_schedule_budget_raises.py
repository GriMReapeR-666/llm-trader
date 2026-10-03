"""Apply scheduler_tuning_report RAISE verdicts to _SCHEDULE_HANDLER_BUDGETS.

Reads artifacts/scheduler_tuning_report.json (the read-only report's output)
and rewrites the matching entries in intelligence/llm_orchestrator.py,
replacing each budget value and its trailing rationale comment with the
report's measured evidence (trip rate, p95/median over-by, dispatch count).

Safety contract:
- DRY-RUN BY DEFAULT: prints the exact old -> new dict lines and exits 0
  without writing. Pass --apply to actually edit the source file.
- --verify CROSS-CHECK (no writes): recomputes every raise/watch verdict
  from the row's own evidence against the pinned thresholds
  (tests/test_scheduler_tuning_report.py: RAISE = due>=20 AND rate>=2% AND
  (p95 over-by>=5s OR median>=2s); AT_CAP at the interval/2 cap; else
  WATCH) and re-derives counts/percentiles from the journal named in the
  report. Exit 1 on any contradiction.
- --apply runs the same cross-check before writing and refuses on a
  failed verification unless --force is passed.
- Only verdict == "RAISE" rows are applied. AT_CAP rows are skipped (the
  report itself says raising the budget cannot help at the interval/2 cap;
  the schedule interval needs raising instead). WATCH rows are skipped.
- Refuses to run when the report has no telemetry (an empty recommendation
  set means no session with overruns has been captured yet).
- Idempotent: re-running with the same report rewrites the same lines.
- Line-structure aware: matches the budget entry by schedule name inside
  the _SCHEDULE_HANDLER_BUDGETS literal only, so no other dict is touched.

Usage:
    python -X utf8 scripts/apply_schedule_budget_raises.py            # dry run
    python -X utf8 scripts/apply_schedule_budget_raises.py --verify   # cross-check only
    python -X utf8 scripts/apply_schedule_budget_raises.py --apply    # write
    python -X utf8 scripts/apply_schedule_budget_raises.py --report path.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCHESTRATOR = ROOT / "intelligence" / "llm_orchestrator.py"
DEFAULT_REPORT = ROOT / "artifacts" / "scheduler_tuning_report.json"

# Single source of truth for percentile semantics and the registry intervals
# (both pinned by tests/test_scheduler_tuning_report.py).
from scheduler_tuning_report import _load_registry_intervals, _percentile  # noqa: E402


def _rationale(row: dict) -> str:
    """Evidence-based comment replacing the existing per-entry comment."""
    rate = row.get("trip_rate")
    rate_note = f"{rate * 100:.1f}% trip" if isinstance(rate, (int, float)) else "trip rate n/a"
    p95 = row.get("p95_over_by_s")
    med = row.get("median_over_by_s")
    due = row.get("due_dispatches")
    parts = [f"raised from {row.get('current_budget_s')}s: {row['overruns']} overruns "
             f"({rate_note} of {due} dispatches)",
             f"p95 over-by {p95}s / median {med}s"]
    return "; ".join(parts)


def _expected_suggested(row: dict, intervals: dict) -> float | None:
    """Recompute suggested_budget_s from the row's evidence (pinned rule)."""
    current = row.get("current_budget_s")
    p95_elapsed = row.get("p95_elapsed_s")
    if current is None or p95_elapsed is None:
        return None
    interval = intervals.get(row.get("schedule"), 0)
    cap = (interval / 2) if interval else current * 2
    return round(min(max(current, p95_elapsed * 1.5), max(cap, current)), 1)


def _expected_verdict(row: dict, intervals: dict) -> str:
    """Recompute the verdict from the row's evidence (pinned thresholds).

    Mirrors _classify_overruns in scheduler_tuning_report.py: RAISE needs
    BOTH frequency (>=2% trip rate) and severity (p95 over-by >=5s or
    median >=2s) over a >=20-dispatch baseline; AT_CAP when the suggestion
    cannot exceed the current budget; everything else WATCH. Inclusive
    boundaries.
    """
    due = row.get("due_dispatches") or 0
    rate = row.get("trip_rate")
    p95_over_by = row.get("p95_over_by_s") or 0.0
    median_over_by = row.get("median_over_by_s") or 0.0
    current = row.get("current_budget_s") or 0.0
    qualifies = (due >= 20 and rate is not None and rate >= 0.02
                 and (p95_over_by >= 5.0 or median_over_by >= 2.0))
    if not qualifies:
        return "WATCH"
    suggested = _expected_suggested(row, intervals)
    return "AT_CAP" if suggested is not None and suggested <= current else "RAISE"


def _journal_aggregates(journal_path: Path) -> tuple[dict, dict, str]:
    """Per-schedule overrun/due aggregates straight from the journal.

    Returns ({schedule: {count, over_by, elapsed, budgets}},
    {schedule: due_count}, error); error is empty on success.
    """
    if not journal_path.exists():
        return {}, {}, "journal not found"
    try:
        con = sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as exc:
        return {}, {}, f"cannot open journal: {exc}"
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "operational_events" not in tables:
            return {}, {}, "operational_events table not found (wrong journal?)"
        overruns: dict = {}
        for name, over_by, elapsed, budget in con.execute(
                """SELECT json_extract(payload,'$.schedule'),
                          json_extract(payload,'$.over_by_s'),
                          json_extract(payload,'$.elapsed_s'),
                          json_extract(payload,'$.budget_s')
                   FROM operational_events
                   WHERE event_type='schedule_handler_overrun'"""):
            item = overruns.setdefault(name, {"count": 0, "over_by": [],
                                              "elapsed": [], "budgets": []})
            item["count"] += 1
            for key, value in (("over_by", over_by), ("elapsed", elapsed),
                               ("budgets", budget)):
                try:
                    item[key].append(float(value))
                except (TypeError, ValueError):
                    pass
        due = {name: n for name, n in con.execute(
            """SELECT json_extract(payload,'$.schedule'), COUNT(*)
               FROM operational_events WHERE event_type='schedule_due'
               GROUP BY 1""")}
        return overruns, due, ""
    finally:
        con.close()


def _verify_report(data: dict, tolerance: float = 0.1) -> tuple[bool, list, str]:
    """Cross-check report verdicts against the pinned boundaries + journal.

    Returns (ok, problems, provenance_status). provenance_status is
    "confirmed" (journal re-derived and consistent), "unverified" (no
    journal in the report / file missing - boundaries still checked) or
    "failed" (journal unreadable or structurally wrong).
    """
    problems: list = []
    verdict_block = data.get("budget_raise_candidates") or {}
    rows = list(verdict_block.get("raise") or []) + list(verdict_block.get("watch") or [])
    if not rows:
        return True, [], "no_rows"
    intervals = _load_registry_intervals()
    for row in rows:
        if not isinstance(row, dict):
            problems.append(f"malformed verdict row (not an object): {str(row)[:60]}")
            continue
        name = str(row.get("schedule"))
        expected = _expected_verdict(row, intervals)
        if expected != row.get("verdict"):
            problems.append(
                f"{name}: verdict '{row.get('verdict')}' contradicts the pinned "
                f"thresholds (expected '{expected}'; due={row.get('due_dispatches')} "
                f"rate={row.get('trip_rate')} p95_over_by={row.get('p95_over_by_s')}s "
                f"median={row.get('median_over_by_s')}s)")
            continue
        expected_suggested = _expected_suggested(row, intervals)
        stored_suggested = row.get("suggested_budget_s")
        if (expected == "RAISE" and expected_suggested is not None
                and stored_suggested is not None
                and abs(expected_suggested - stored_suggested) > tolerance):
            problems.append(
                f"{name}: suggested_budget_s {stored_suggested} outside tolerance "
                f"of recomputed {expected_suggested}")

    journal = data.get("journal")
    status = "unverified"
    if not journal:
        return (not problems), problems, status
    journal_path = Path(journal)
    if not journal_path.is_absolute():
        journal_path = ROOT / journal_path
    overruns, due_counts, error = _journal_aggregates(journal_path)
    if error == "journal not found":
        print(f"  note: journal not found ({journal}); provenance unverified")
        return (not problems), problems, status
    if error:
        problems.append(f"journal {journal}: {error}")
        return (not problems), problems, "failed"
    status = "confirmed"
    for row in rows:
        if not isinstance(row, dict):
            continue  # already reported by the boundary pass
        name = str(row.get("schedule"))
        item = overruns.get(name) or {}
        n_over = item.get("count", 0)
        n_due = due_counts.get(name, 0)
        if row.get("overruns") != n_over:
            problems.append(f"{name}: overruns {row.get('overruns')} != "
                            f"journal count {n_over}")
        if row.get("due_dispatches") != n_due:
            problems.append(f"{name}: due_dispatches {row.get('due_dispatches')} != "
                            f"journal schedule_due count {n_due}")
        if n_due and row.get("trip_rate") is not None:
            rate = round(n_over / n_due, 4)
            if abs(rate - row["trip_rate"]) > 0.0002:
                problems.append(f"{name}: trip_rate {row['trip_rate']} != "
                                f"journal-derived {rate}")
        if item.get("over_by"):
            recomputed = {"p95_over_by_s": round(_percentile(item["over_by"], 0.95), 2),
                          "median_over_by_s": round(_percentile(item["over_by"], 0.5), 2)}
            for field, value in recomputed.items():
                stored = row.get(field)
                if stored is not None and abs(value - stored) > 0.02:
                    problems.append(f"{name}: {field} {stored} != "
                                    f"journal-derived {value}")
        if item.get("elapsed") and row.get("p95_elapsed_s") is not None:
            p95_elapsed = _percentile(item["elapsed"], 0.95)
            if abs(p95_elapsed - row["p95_elapsed_s"]) > 0.02:
                problems.append(f"{name}: p95_elapsed_s {row['p95_elapsed_s']} != "
                                f"journal-derived {round(p95_elapsed, 2)}")
        if item.get("budgets") and row.get("current_budget_s") is not None:
            current = round(max(item["budgets"]), 1)
            if abs(current - row["current_budget_s"]) > 0.02:
                problems.append(f"{name}: current_budget_s {row['current_budget_s']} != "
                                f"journal max budget_s {current}")
    return (not problems), problems, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: dry run)")
    parser.add_argument("--verify", action="store_true",
                        help="cross-check the report's verdicts against the pinned "
                             "thresholds and the journal; no files written")
    parser.add_argument("--force", action="store_true",
                        help="apply even when verification fails (not recommended)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="absolute tolerance when re-deriving suggested budgets")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"report not found: {report_path}")
        return 2
    data = json.loads(report_path.read_text(encoding="utf-8"))
    verdict_block = data.get("budget_raise_candidates") or {}
    all_rows = list(verdict_block.get("raise") or []) + list(verdict_block.get("watch") or [])
    candidates = [r for r in verdict_block.get("raise", [])
                  if r.get("verdict") == "RAISE"]

    if args.verify:
        if not all_rows:
            print("no raise/watch verdict rows in the report - nothing to verify "
                  "(a session with overrun telemetry must be captured first)")
            return 0
        ok, problems, status = _verify_report(data, args.tolerance)
        print(f"verifying {len(all_rows)} verdict row(s) against the pinned thresholds:")
        if ok:
            print(f"VERIFIED - all verdicts match the pinned RAISE/AT_CAP/WATCH "
                  f"boundaries (provenance: {status})")
            return 0
        print(f"FAILED - {len(problems)} contradiction(s):")
        for problem in problems:
            print(f"  {problem}")
        return 1

    if not candidates:
        print("no RAISE verdicts in the report - nothing to apply.")
        print("(a session with overrun telemetry must be captured first: "
              "run scripts/scheduler_tuning_report.py after a live session)")
        return 0

    text = ORCHESTRATOR.read_text(encoding="utf-8")
    block = re.search(r"(_SCHEDULE_HANDLER_BUDGETS(?::\s*Dict\[str,\s*float\])?\s*=\s*\{)(.*?)(\n    \})",
                      text, re.S)
    if not block:
        print("could not locate _SCHEDULE_HANDLER_BUDGETS literal")
        return 2
    body = block.group(2)

    applied, skipped = [], []
    for row in candidates:
        name = row["schedule"]
        suggested = row.get("suggested_budget_s")
        if suggested is None:
            skipped.append((name, "no suggested_budget_s"))
            continue
        entry = re.search(
            rf"(?m)^(\s*\"{re.escape(name)}\"\s*:\s*)([0-9.]+)(,)"
            rf"(\s*#[^\n\r]*)?(\r?)$",
            body)
        if not entry:
            skipped.append((name, "entry not found in dict literal"))
            continue
        current = float(entry.group(2))
        if suggested <= current:
            skipped.append((name, f"suggested {suggested}s <= current {current}s"))
            continue
        tail = entry.group(5) or ""
        if entry.group(4):   # keep the existing rationale comment
            replacement = f"{entry.group(1)}{suggested},{entry.group(4)}{tail}"
        else:                # no comment yet: attach the measured evidence
            replacement = f"{entry.group(1)}{suggested},  # {_rationale(row)}{tail}"
        body = body[:entry.start()] + replacement + body[entry.end():]
        applied.append((name, current, suggested))

    print(f"report: {report_path.name} (generated {str(data.get('generated_at'))[:19]})")
    if not applied:
        print("no applicable raises (see skips below)")
    for name, current, suggested in applied:
        print(f"  RAISE {name}: {current}s -> {suggested}s")
    for name, why in skipped:
        print(f"  skip  {name}: {why}")
    if not args.apply:
        print("\nDRY RUN - no files written. Re-run with --apply to write.")
        return 0
    if not args.force:
        ok, problems, status = _verify_report(data, args.tolerance)
        if not ok:
            print("\nverification FAILED - refusing to apply (pass --force to override):")
            for problem in problems:
                print(f"  {problem}")
            return 1
        print(f"\nverification passed (provenance: {status})")
    else:
        print("\nverification skipped (--force)")
    text = text[:block.start(2)] + body + text[block.end(2):]
    ORCHESTRATOR.write_text(text, encoding="utf-8", newline="")
    try:
        target = str(ORCHESTRATOR.relative_to(ROOT))
    except ValueError:
        target = str(ORCHESTRATOR)
    print(f"\napplied {len(applied)} raise(s) to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
