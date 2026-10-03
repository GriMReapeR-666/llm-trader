"""Scheduler budget tuning report (read-only).

Aggregates the scheduler telemetry introduced 2026-09-26/27 into one tuning
report:

- ``schedule_handler_overrun``  {schedule, budget_s, elapsed_s, over_by_s}
- ``schedule_session_skip``     {schedule, silence_s, threshold_s}
- ``schedule_skips_cleared``    {schedule}
- ``schedule_skipped``          {schedules: [...], claimed, dispatched}

and, from ``schedule_due`` history (available on any mature journal), the
per-schedule duty cycle: actual claim cadence versus the registry interval.

Budget recommendations are heuristics for an operator, not automatic changes:
for overrunning handlers, ``max(current_budget, p95_elapsed * 1.5)`` capped at
half the schedule interval; the duty cycle flags starvation regardless of
cause (budget, upstream blocking, or claim-order starvation).

Usage:
    python -X utf8 scripts/scheduler_tuning_report.py
    python -X utf8 scripts/scheduler_tuning_report.py --db path/to/journal.db
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    return con.execute(sql, args).fetchall()


def _one(con: sqlite3.Connection, sql: str, args: tuple = ()):
    return con.execute(sql, args).fetchone()[0]


def _load_registry_intervals() -> dict:
    """schedule_name -> interval_seconds from the agent scheduler registry."""
    try:
        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        for sub in ("operations", ""):
            candidate = root / sub / "agent_scheduler.py"
            if candidate.exists():
                parent = str(candidate.parent)
                if parent not in sys.path:
                    sys.path.insert(0, parent)
                from agent_scheduler import SCHEDULES  # noqa: E402
                return {s["name"]: int(s["interval"]) for s in SCHEDULES}
    except Exception:
        pass
    return {}


def _load_current_budgets(root: Path) -> dict:
    """Parse _SCHEDULE_HANDLER_BUDGETS from the orchestrator source (no import)."""
    try:
        text = (root / "intelligence" / "llm_orchestrator.py").read_text(encoding="utf-8")
        match = re.search(r"_SCHEDULE_HANDLER_BUDGETS[^=]*=\s*(\{.*?\n    \})", text, re.S)
        if match:
            return {k: float(v) for k, v in ast.literal_eval(match.group(1)).items()}
    except Exception:
        pass
    return {}


def _percentile(values: list, q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


# Pre-fix duty-cycle baseline, measured on the last full active session BEFORE
# the claim-time cadence restore (2026-09-26 00:00:31..13:31:23 UTC, 13.51h,
# schedule_due claims vs registry intervals). Starvation signature: deficits
# roughly uniform across handlers with very different budgets. Cited so the
# comparison below is reproducible rather than folklore.
PRE_RESTORE_BASELINE = {
    "market_health": 0.22,       # 351/1621 claims at 30s interval
    "event_monitor": 0.36,       # 295/811 at 60s
    "incident_sweep": 0.25,      # 199/811 at 60s
    "event_triage": 0.25,        # 199/811 at 60s
    "position_reconcile": 0.06,  # 48/811 at 60s (worst; claim-order starvation)
    "alloc_rebalance": 0.28,     # 45/162 at 300s
    "causal_edge_validate": 0.30,# 16/54 at 900s
    "alert_dispatch": 0.22,      # 182/811 at 60s
}


def _compare_to_baseline(duty_session: dict) -> None:
    """Per-schedule duty comparison against the pre-restore baseline."""
    print("\n== claim-time-restore comparison (last session vs pre-fix baseline) ==")
    rows = []
    for name, baseline in PRE_RESTORE_BASELINE.items():
        entry = duty_session.get(name)
        if not entry or entry.get("duty_ratio") is None:
            rows.append({"schedule": name, "baseline": baseline,
                         "current": None, "verdict": "NO_DATA"})
            continue
        current = entry["duty_ratio"]
        if current >= 0.90:
            verdict = "FIXED"
        elif current >= baseline + 0.10 or current >= baseline * 2:
            verdict = "IMPROVED"
        elif current >= baseline - 0.05:
            verdict = "UNCHANGED"
        else:
            verdict = "WORSE"
        rows.append({"schedule": name, "baseline": baseline,
                     "current": round(current, 3), "verdict": verdict})
    measured = [r for r in rows if r["current"] is not None]
    if not measured:
        print("  no post-fix session ratios yet (session shorter than the span "
              "floor, or no schedule_due since the restore). Re-run after a "
              "live session, or pass --min-span-hours 0.25 for early checks.")
        return
    for row in rows:
        if row["current"] is None:
            print(f"  {row['schedule']}: baseline {row['baseline'] * 100:.0f}% -> "
                  "no data this session")
            continue
        delta = (row["current"] - row["baseline"]) * 100
        sign = "+" if delta >= 0 else ""
        print(f"  {row['schedule']}: {row['baseline'] * 100:.0f}% -> "
              f"{row['current'] * 100:.0f}% ({sign}{delta:.0f} pts) {row['verdict']}")
    verdicts = {r["verdict"] for r in measured}
    if verdicts <= {"FIXED"}:
        overall = "FIXED — every measured schedule is at/above 90% duty"
    elif "WORSE" in verdicts or "UNCHANGED" in verdicts:
        overall = "NOT FIXED — at least one schedule shows no improvement or regressed"
    else:
        overall = "PARTIALLY FIXED — improved but at least one schedule still below 90%"
    print(f"  OVERALL: {overall}")


def _classify_overruns(overruns: dict, due_denominators: dict,
                       intervals: dict, budgets: dict,
                       recommendations: dict | None = None) -> tuple[list, list]:
    """Classify overrun evidence into (raise_candidates, watch_candidates).

    Pure function so the thresholds are unit-testable (see
    tests/test_scheduler_tuning_report.py): RAISE needs BOTH frequency
    (>=2% of dispatches trip) and severity (p95 over-by >=5s or median
    >=2s) over a >=20-dispatch baseline; AT_CAP when the measured
    suggestion cannot exceed the current budget (interval/2 heuristic
    cap - raise the schedule interval instead); everything else WATCH.
    Boundaries are inclusive. Never lowers a budget.
    """
    recommendations = recommendations or {}
    raise_candidates, watch_candidates = [], []
    for name, item in overruns.items():
        count = item["count"]
        elapsed = item["elapsed"]
        over_by = item["over_by"]
        if not elapsed or not over_by:
            continue
        rec = recommendations.get(name) or {}
        # The events are ground truth of the budget that was active; the
        # source table is only a fallback for malformed payloads.
        current = round(max(item["budgets"]), 1) if item["budgets"] else (
            rec.get("current_budget_s") or (budgets.get(name) or 45.0))
        due = due_denominators.get(name, 0)
        rate = (count / due) if due else None
        p95_over_by = _percentile(over_by, 0.95)
        median_over_by = _percentile(over_by, 0.5)
        p95_elapsed = _percentile(elapsed, 0.95)
        interval = intervals.get(name, 0)
        cap = (interval / 2) if interval else current * 2
        suggested = round(min(max(current, p95_elapsed * 1.5), max(cap, current)), 1)
        base = {
            "schedule": name, "overruns": count, "due_dispatches": due,
            "trip_rate": round(rate, 4) if rate is not None else None,
            "current_budget_s": current,
            "median_over_by_s": round(median_over_by, 2),
            "p95_over_by_s": round(p95_over_by, 2),
            "p95_elapsed_s": round(p95_elapsed, 2),
            "suggested_budget_s": suggested,
        }
        if due >= 20 and rate is not None and rate >= 0.02 and (
                p95_over_by >= 5.0 or median_over_by >= 2.0):
            if suggested <= current:
                row = {**base, "verdict": "AT_CAP",
                       "rationale": (f"{rate * 100:.1f}% trip rate but budget "
                                     f"{current}s already at/above the interval/2 "
                                     f"heuristic cap — raise the schedule interval "
                                     f"instead")}
            else:
                row = {**base, "verdict": "RAISE",
                       "rationale": f"{rate * 100:.1f}% trip rate, p95 over by {p95_over_by:.1f}s"}
            raise_candidates.append(row)
        else:
            why = ("insufficient dispatch baseline" if due < 20 else
                   f"only {rate * 100:.2f}% trip rate" if rate is not None else "no baseline")
            if p95_over_by < 5.0 and median_over_by < 2.0 and due >= 20:
                why += "; over-by seconds are trivial"
            watch_candidates.append({**base, "verdict": "WATCH", "rationale": why})
    return raise_candidates, watch_candidates


def _aggregate_boot_stage_timeouts(rows: list) -> dict:
    """Fold boot_fetch_stage_timeout events into per-stage stats.

    The boot-fetch watchdog emits ``{stage, budget_s, elapsed_s}`` when a
    pre-pulse ingest stage (calendar_collect, data_source_pipeline, ...) is
    abandoned at its deadline — the hung-provider signature. Aggregating
    here puts those trips in the same post-session duty report as the
    scheduler overruns, so a session's provider stalls are visible in the
    duty comparison without querying the journal separately. Pure function
    over (payload, created_at) rows so it is unit-testable.
    """
    stages: dict = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        stage = str(payload.get("stage") or "?")
        item = stages.setdefault(
            stage, {"trips": 0, "budgets": [], "elapsed": [],
                    "first_at": None, "last_at": None})
        item["trips"] += 1
        created = row["created_at"]
        if created:
            if item["first_at"] is None or created < item["first_at"]:
                item["first_at"] = created
            if item["last_at"] is None or created > item["last_at"]:
                item["last_at"] = created
        for key in ("budget_s", "elapsed_s"):
            try:
                item["budgets" if key == "budget_s" else "elapsed"].append(
                    float(payload.get(key)))
            except (TypeError, ValueError):
                pass
    for stage, item in stages.items():
        item["budget_s"] = (max(item["budgets"]) if item["budgets"] else None)
        item["elapsed_p95_s"] = (round(_percentile(item["elapsed"], 0.95), 2)
                                  if item["elapsed"] else None)
        # Suggested budget: p95 observed overrun * 1.5 above the configured
        # budget — the same raise heuristic as the handler overruns, minus
        # the interval/2 cap (boot stages have no schedule interval).
        if item["budget_s"] is not None and item["elapsed_p95_s"] is not None:
            item["suggested_budget_s"] = round(
                max(item["budget_s"], item["elapsed_p95_s"] * 1.5), 1)
        else:
            item["suggested_budget_s"] = None
        for scratch in ("budgets", "elapsed"):
            item.pop(scratch, None)
    return stages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="trading_state.db")
    parser.add_argument("--artifact", default=None,
                        help="override the output JSON path (tests; default "
                             "artifacts/scheduler_tuning_report.json)")
    parser.add_argument("--min-span-hours", type=float, default=2.0,
                        help="minimum span before duty-cycle ratios are computed")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    intervals = _load_registry_intervals()
    budgets = _load_current_budgets(root)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row

    print(f"journal: {args.db} (read-only)")
    print(f"now:     {datetime.now(timezone.utc).isoformat()}")
    span_row = con.execute(
        "SELECT MIN(created_at), MAX(created_at) FROM operational_events").fetchone()
    first_event, last_event = span_row[0], span_row[1]
    print(f"journal span: {str(first_event)[:19]} .. {str(last_event)[:19]}")
    # "Live" means an OPEN run with fresh activity - recency alone is not
    # enough. A finished 5-minute maintenance pass must read "not detected"
    # even seconds after it ends (the old 6h recency window kept it flagged
    # as live for hours). An open run with no run_finished yet goes quiet
    # after 15 minutes without events (crash edge). String comparison is
    # safe here: all stamps are T-separated UTC ISO of the same shape.
    session_start = _one(
        con, "SELECT MAX(created_at) FROM operational_events WHERE event_type='run_started'")
    session_finish = _one(
        con, "SELECT MAX(created_at) FROM operational_events WHERE event_type='run_finished'")
    activity_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    open_run = bool(session_start) and (not session_finish or session_finish < session_start)
    recent_activity = bool(last_event) and last_event > activity_cutoff
    live = open_run and recent_activity
    print(f"live session: {'DETECTED' if live else 'not detected'} "
          f"(open run since {str(session_start)[:19]}, last event {str(last_event)[:19]}; "
          f"run_finished {str(session_finish)[:19] if session_finish else 'none'})")

    def _duty_window(where_sql: str, label: str) -> dict:
        """Duty cycle for one window; ratios exclude windows shorter than --min-span-hours."""
        print(f"\n== duty cycle: {label} ==")
        window = {}
        for row in _rows(
            con,
            f"""SELECT json_extract(payload,'$.schedule') s, COUNT(*) n,
                       MIN(created_at) first_at, MAX(created_at) last_at
                FROM operational_events WHERE event_type='schedule_due' {where_sql}
                GROUP BY s ORDER BY n DESC""",
        ):
            name = row["s"]
            if not name:
                continue
            try:
                span_hours = (datetime.fromisoformat(row["last_at"])
                              - datetime.fromisoformat(row["first_at"])).total_seconds() / 3600
            except (TypeError, ValueError):
                span_hours = 0.0
            interval = intervals.get(name, 0)
            expected = (span_hours * 3600 / interval) if interval and span_hours >= args.min_span_hours else None
            ratio = (row["n"] / expected) if expected else None
            window[name] = {"claims": row["n"], "span_hours": round(span_hours, 1),
                            "interval_s": interval or None,
                            "expected_claims": round(expected, 1) if expected else None,
                            "duty_ratio": round(ratio, 3) if ratio is not None else None,
                            "budget_s": budgets.get(name)}
            flag = ""
            if ratio is not None:
                flag = "  <-- STARVED" if ratio < 0.8 else ("  <-- low" if ratio < 0.95 else "")
            interval_note = f"interval {interval}s" if interval else "interval unknown"
            ratio_note = f"duty {ratio * 100:.0f}%" if ratio is not None else "span too short for ratio"
            print(f"  {name}: {row['n']} claims over {span_hours:.1f}h, {interval_note}, {ratio_note}{flag}")
        return window

    # All-time ratios INCLUDE trader-down hours, so they lower-bound duty;
    # the per-session window (last run_started -> last event) is actionable.
    duty = _duty_window("", "all-time (includes trader-down hours; lower bound)")
    duty_session = _duty_window(
        f"AND created_at >= '{session_start}'" if session_start else "AND 0",
        f"last session (since run_started {str(session_start)[:19]})" if session_start else "last session (none recorded)")
    _compare_to_baseline(duty_session)

    # ---- overrun telemetry ----------------------------------------------
    print(f"\n== handler overrun telemetry ==")
    overrun_rows = _rows(
        con,
        """SELECT json_extract(payload,'$.schedule') s,
                  json_extract(payload,'$.budget_s') budget_s,
                  json_extract(payload,'$.elapsed_s') elapsed_s,
                  json_extract(payload,'$.over_by_s') over_by_s
           FROM operational_events WHERE event_type='schedule_handler_overrun'""",
    )
    overruns = {}
    for row in overrun_rows:
        item = overruns.setdefault(row["s"], {"count": 0, "budgets": [], "elapsed": [], "over_by": []})
        item["count"] += 1
        for key, column in (("budgets", "budget_s"), ("elapsed", "elapsed_s"), ("over_by", "over_by_s")):
            try:
                item[key].append(float(row[column]))
            except (TypeError, ValueError):
                pass
    recommendations = {}
    if not overruns:
        print("  no schedule_handler_overrun events yet (instrumentation added 2026-09-27; "
              "needs a live session)")
    for name, item in sorted(overruns.items(), key=lambda kv: -kv[1]["count"]):
        p50 = _percentile(item["elapsed"], 0.50)
        p95 = _percentile(item["elapsed"], 0.95)
        worst = max(item["over_by"], default=0.0)
        current = budgets.get(name) or max(item["budgets"], default=45.0)
        interval = intervals.get(name, 0)
        cap = (interval / 2) if interval else current * 2
        # A raise verdict never LOWERS: budgets already above the interval/2
        # heuristic cap (e.g. event_monitor 60s/60s) keep their current value
        # as the floor.
        suggested = round(min(max(current, p95 * 1.5), max(cap, current)), 1) if item["elapsed"] else current
        recommendations[name] = {
            "overruns": item["count"], "current_budget_s": current,
            "elapsed_p50_s": round(p50, 2), "elapsed_p95_s": round(p95, 2),
            "worst_over_by_s": round(worst, 2), "suggested_budget_s": suggested,
            "cap_reason": "interval/2" if interval and suggested == round(cap, 1) else "p95*1.5",
        }
        print(f"  {name}: {item['count']} overruns, budget {current}s, "
              f"elapsed p50 {p50:.1f}s / p95 {p95:.1f}s, worst over by {worst:.1f}s "
              f"-> suggested {suggested}s")

    # ---- budget-raise verdict (the actionable summary) -------------------
    # Overrun samples are selection-biased (every recorded elapsed tripped),
    # so classification uses TRIP RATE (overruns / schedule_due dispatches)
    # plus over-by SEVERITY — never the median of tripped elapsed values.
    due_denominators = {row["s"]: row["n"] for row in _rows(
        con,
        """SELECT json_extract(payload,'$.schedule') s, COUNT(*) n
           FROM operational_events WHERE event_type='schedule_due' GROUP BY s""")}
    raise_candidates, watch_candidates = _classify_overruns(
        overruns, due_denominators, intervals, budgets, recommendations)
    if overruns:
        print(f"\n== budget-raise verdict ==")
        if raise_candidates:
            print("  RAISE (persistent, measured against total dispatches):")
            for row in sorted(raise_candidates, key=lambda r: -r["overruns"]):
                print(f"    {row['schedule']}: {row['current_budget_s']}s -> "
                      f"{row['suggested_budget_s']}s ({row['verdict']}; {row['rationale']})")
        else:
            print("  RAISE: none — no schedule trips often and severely enough")
        if watch_candidates:
            print("  WATCH (keep budget):")
            for row in sorted(watch_candidates, key=lambda r: -r["overruns"]):
                print(f"    {row['schedule']}: {row['overruns']} overruns — {row['rationale']}")
        else:
            print("  WATCH: none")
        print("  (apply edits to _SCHEDULE_HANDLER_BUDGETS in intelligence/llm_orchestrator.py)")

    # ---- session-skip telemetry ------------------------------------------
    print(f"\n== session-skip watchdog telemetry ==")
    skip_rows = _rows(
        con,
        """SELECT json_extract(payload,'$.schedule') s,
                  json_extract(payload,'$.silence_s') silence_s,
                  json_extract(payload,'$.threshold_s') threshold_s,
                  COUNT(*) n, MAX(created_at) last_at
           FROM operational_events WHERE event_type='schedule_session_skip'
           GROUP BY s ORDER BY n DESC""",
    )
    if not skip_rows:
        print("  no schedule_session_skip events yet")
    skips = {}
    for row in skip_rows:
        skips[row["s"]] = {"events": row["n"], "last_silence_s": row["silence_s"],
                           "threshold_s": row["threshold_s"], "last_at": row["last_at"]}
        print(f"  {row['s']}: {row['n']} alerts, silence {row['silence_s']}s vs "
              f"threshold {row['threshold_s']}s, last {str(row['last_at'])[:19]}")
    cleared = {row["s"]: row["n"] for row in _rows(
        con,
        """SELECT json_extract(payload,'$.schedule') s, COUNT(*) n
           FROM operational_events WHERE event_type='schedule_skips_cleared' GROUP BY s""")}
    if cleared:
        print(f"  recovered episodes (skips_cleared): {cleared}")

    # ---- boot-fetch watchdog trips (per-stage) ----------------------------
    # The boot watchdog (intelligence/boot_fetch_watchdog.py) emits
    # boot_fetch_stage_timeout when a pre-pulse ingest stage is abandoned at
    # its deadline — the hung-provider signature. Same duty-report treatment
    # as handler overruns so provider stalls surface in the post-session
    # duty comparison without a separate journal query.
    print(f"\n== boot-fetch watchdog trips (per stage) ==")
    timeout_rows = _rows(
        con,
        """SELECT payload, created_at FROM operational_events
           WHERE event_type='boot_fetch_stage_timeout' ORDER BY created_at""",
    )
    boot_timeouts = _aggregate_boot_stage_timeouts(timeout_rows)
    if not boot_timeouts:
        print("  no boot_fetch_stage_timeout events yet (watchdog added 2026-09-30; "
              "fires only when a stage misses its deadline)")
    for stage, item in sorted(boot_timeouts.items(), key=lambda kv: -kv[1]["trips"]):
        budget = item["budget_s"]
        p95 = item["elapsed_p95_s"]
        suggested = item["suggested_budget_s"]
        print(f"  {stage}: {item['trips']} trips, budget "
              f"{budget if budget is not None else '?'}s, elapsed p95 "
              f"{p95 if p95 is not None else '?'}s"
              + (f" -> suggested {suggested}s" if suggested is not None else "")
              + f" (first {str(item['first_at'])[:19]}, last {str(item['last_at'])[:19]})")
    if boot_timeouts:
        print("  (apply edits to LLM_TRADER_BOOT_STAGE_BUDGETS or _DEFAULT_STAGE_BUDGETS "
              "in intelligence/boot_fetch_watchdog.py)")

    # ---- aggregate claimed-but-skipped ------------------------------------
    print(f"\n== claimed-but-abandoned batches (schedule_skipped) ==")
    agg_rows = _rows(
        con,
        """SELECT payload FROM operational_events
           WHERE event_type='schedule_skipped' ORDER BY created_at DESC LIMIT 200""",
    )
    name_counts = {}
    name_reasons = {}
    reason_totals = {}
    batch_examples = 0
    for row in agg_rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        batch_examples += 1
        reason = str(payload.get("reason") or "unattributed")
        reason_totals[reason] = reason_totals.get(reason, 0) + 1
        for name in payload.get("schedules") or []:
            name_counts[name] = name_counts.get(name, 0) + 1
            name_reasons.setdefault(name, {})
            name_reasons[name][reason] = name_reasons[name].get(reason, 0) + 1
    if not name_counts:
        print("  no schedule_skipped events yet")
    for name, count in sorted(name_counts.items(), key=lambda kv: -kv[1]):
        reasons = name_reasons.get(name) or {}
        reason_note = ", ".join(f"{r} x{n}" for r, n in
                                sorted(reasons.items(), key=lambda kv: -kv[1]))
        print(f"  {name}: abandoned in {count} batches ({reason_note})")
    if reason_totals:
        totals_note = ", ".join(f"{r}: {n}" for r, n in
                                sorted(reason_totals.items(), key=lambda kv: -kv[1]))
        print(f"  deferral reasons across {batch_examples} batches: {totals_note}")
    print(f"  ({batch_examples} batches scanned)")

    con.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal": args.db, "live_session": live,
        "live_session_evidence": {"open_run": open_run,
                                  "recent_activity": recent_activity,
                                  "last_run_started": session_start,
                                  "last_run_finished": session_finish},
        "duty_cycle_all_time": duty,
        "duty_cycle_last_session": duty_session,
        "pre_restore_baseline": PRE_RESTORE_BASELINE,
        "overruns": recommendations,
        "budget_raise_candidates": {
            "raise": raise_candidates, "watch": watch_candidates,
        },
        "session_skips": skips, "skips_cleared": cleared,
        "boot_stage_timeouts": boot_timeouts,
        "claimed_but_skipped": name_counts,
        "claimed_but_skipped_by_reason": name_reasons,
        "skipped_reason_totals": reason_totals,
        "current_budgets": budgets,
        "note": "recommendations are operator heuristics, not automatic changes",
    }
    artifact = (Path(args.artifact) if args.artifact else
                root / "artifacts" / "scheduler_tuning_report.json")
    try:
        artifact.write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            print(f"\nsaved: {artifact.relative_to(root)}")
        except ValueError:
            print(f"\nsaved: {artifact}")
    except OSError:
        pass
    has_signal = bool(overruns or skips or name_counts or boot_timeouts)
    if not has_signal:
        print("\nNOTE: no new-telemetry events found; duty-cycle section still yields "
              "tuning signal from schedule_due history. Re-run after a live session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
