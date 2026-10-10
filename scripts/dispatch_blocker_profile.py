"""Dispatch blocker profile: why is a schedule still starved? (read-only)

Answers the post-wave-dispatcher follow-up: when the tuning report still
shows UNCHANGED/WORSE duty ratios, attribute each starved schedule to a
blocker class and propose the matching fix.

Two modes, chosen automatically per window:

- telemetry mode (any ``schedule_handler_overrun`` / ``schedule_skipped``
  in the window): direct attribution from the wave dispatcher's own
  events - overrun percentiles name the expensive handlers; the
  ``reason``-tagged ``schedule_skipped`` batches name exactly which
  schedules were deferred before dispatch.
- structural mode (pre-telemetry journals, e.g. the 2026-09-26 baseline
  session): reconstructs waves by clustering ``schedule_due`` claims with
  sub-2s gaps, then infers - CLAIM_ORDER_TAIL for schedules consistently
  claimed at the tail of long waves (claimed but the wave ended before
  their slot), HANDLER_COST_UPSTREAM for long waves that still dispatched
  most of their batch (cost eaten inside the wave).

Verdicts and the fix each one proposes:

- HANDLER_COST (own overruns exceed budget; p95 > budget): raise the
  per-handler budget per the tuning report's RAISE rule, or split the
  handler into sub-cadence chunks when already at the interval/2 cap.
- HANDLER_COST_UPSTREAM (structural mode: long waves, batch mostly
  dispatched): identify the wave's cost center by dispatch-position
  correlation; same fixes as HANDLER_COST.
- CLAIM_ORDER_TAIL (deferred batches / tail-position concentration):
  the wave deadline deferred the tail. Next fix: dispatch-order change -
  shortest-interval-first (or fast/slow lanes) so cheap high-cadence
  schedules never sit behind expensive ones at the deadline.
- DEADLINE_CAPACITY (large claimed-vs-dispatched gaps on every wave):
  total handler demand exceeds the wave deadline; raise _SCHEDULE_TIMEOUT
  or spread waves across pulses (N handlers per pulse).

Usage:
    python -X utf8 scripts/dispatch_blocker_profile.py
    python -X utf8 scripts/dispatch_blocker_profile.py --db trading_state.db \
        --session-start 2026-09-26T00:00:31 --session-end 2026-09-26T13:31:23
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    return con.execute(sql, args).fetchall()


def _load_registry_intervals() -> dict:
    try:
        root = Path(__file__).resolve().parent.parent
        for sub in ("operations", ""):
            candidate = root / sub / "agent_scheduler.py"
            if candidate.exists():
                parent = str(candidate.parent)
                if parent not in sys.path:
                    sys.path.insert(0, parent)
                from operations.agent_scheduler import SCHEDULES  # noqa: E402
                return {s["name"]: int(s["interval"]) for s in SCHEDULES}
    except Exception:
        pass
    return {}


def _load_current_budgets(root: Path) -> dict:
    try:
        text = (root / "intelligence" / "llm_orchestrator.py").read_text(encoding="utf-8")
        match = re.search(r"_SCHEDULE_HANDLER_BUDGETS[^=]*=\s*(\{.*?\n    \})", text, re.S)
        if match:
            import ast
            return {k: float(v) for k, v in ast.literal_eval(match.group(1)).items()}
    except Exception:
        pass
    return {}


def _percentile(values: list, q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[idx])


def _duty(con, start: str, end: str, intervals: dict, min_span: float) -> dict:
    out = {}
    for row in _rows(
        con,
        """SELECT json_extract(payload,'$.schedule') s, COUNT(*) n,
                  MIN(created_at) first_at, MAX(created_at) last_at
           FROM operational_events
           WHERE event_type='schedule_due' AND created_at BETWEEN ? AND ?
           GROUP BY s""",
        (start, end),
    ):
        name = row["s"]
        if not name:
            continue
        interval = intervals.get(name, 0)
        try:
            span_h = (datetime.fromisoformat(row["last_at"])
                      - datetime.fromisoformat(row["first_at"])).total_seconds() / 3600
        except (TypeError, ValueError):
            span_h = 0.0
        expected = (span_h * 3600 / interval) if interval and span_h >= min_span else None
        out[name] = {"claims": row["n"], "span_h": round(span_h, 2),
                     "interval": interval, "duty": round(row["n"] / expected, 3) if expected else None}
    return out


def _reconstruct_waves(stamps: list, names: dict) -> list:
    """Cluster ordered claim stamps into waves: gaps <= 2s join the wave."""
    waves = []
    current, prev_t = [], None
    for ts in stamps:
        t = datetime.fromisoformat(ts)
        if prev_t is not None and (t - prev_t).total_seconds() <= 2.0:
            current.append(names.get(ts, "?"))
        else:
            if current:
                waves.append(current)
            current = [names.get(ts, "?")]
        prev_t = t
    if current:
        waves.append(current)
    return waves


def _classify_structural(name: str, waves: list) -> dict:
    """Attribute one starved schedule from reconstructed waves alone.

    CLAIM_ORDER_TAIL when the median in-wave claim position is >= 0.6 OR
    the tail-position share (position >= 2/3 of the wave) is >= 0.4;
    HANDLER_COST_UPSTREAM otherwise; NEVER_CLAIMED when the schedule
    appears in no wave.
    """
    positions, tail_share, n_seen = [], 0, 0
    for w in waves:
        if name in w:
            pos = w.index(name) / max(1, len(w) - 1)
            positions.append(pos)
            n_seen += 1
            if pos >= 2 / 3:
                tail_share += 1
    if not n_seen:
        return {"verdict": "NEVER_CLAIMED", "n_seen": 0,
                "med_pos": None, "tail_share": None}
    med_pos = _percentile(positions, 0.5)
    verdict = ("CLAIM_ORDER_TAIL" if med_pos >= 0.6 or tail_share / n_seen >= 0.4
               else "HANDLER_COST_UPSTREAM")
    return {"verdict": verdict, "n_seen": n_seen, "med_pos": med_pos,
            "tail_share": tail_share / n_seen}


def _classify_telemetry(name: str, own: dict | None, deferrals: int, budgets: dict) -> dict:
    """Attribute one starved schedule from the dispatcher's own telemetry.

    Own overruns -> HANDLER_COST (evidence: p50/p95 elapsed vs budget);
    deferrals without own overruns -> CLAIM_ORDER_TAIL; neither -> AMBIGUOUS.
    """
    if own:
        p50 = _percentile(own["elapsed"], 0.50)
        p95 = _percentile(own["elapsed"], 0.95)
        budget = budgets.get(name) or max(own["budgets"], default=0.0)
        return {"verdict": "HANDLER_COST", "n_overruns": len(own["elapsed"]),
                "p50": p50, "p95": p95, "budget": budget}
    if deferrals:
        return {"verdict": "CLAIM_ORDER_TAIL", "deferrals": deferrals}
    return {"verdict": "AMBIGUOUS"}


def _telemetry_mode(con, start: str, end: str, budgets: dict, duty: dict) -> None:
    print("\n== mode: dispatcher telemetry (direct attribution) ==")
    overruns = {}
    for row in _rows(
        con,
        """SELECT json_extract(payload,'$.schedule') s,
                  json_extract(payload,'$.budget_s') budget_s,
                  json_extract(payload,'$.elapsed_s') elapsed
           FROM operational_events
           WHERE event_type='schedule_handler_overrun' AND created_at BETWEEN ? AND ?""",
        (start, end),
    ):
        item = overruns.setdefault(row["s"], {"elapsed": [], "budgets": []})
        for key, col in (("elapsed", "elapsed"), ("budgets", "budget_s")):
            try:
                item[key].append(float(row[col]))
            except (TypeError, ValueError):
                pass
    deferred = {}
    reasons = {}
    for row in _rows(
        con,
        """SELECT payload FROM operational_events
           WHERE event_type='schedule_skipped' AND created_at BETWEEN ? AND ?""",
        (start, end),
    ):
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        reason = str(payload.get("reason") or "unattributed")
        reasons[reason] = reasons.get(reason, 0) + 1
        for name in payload.get("schedules") or []:
            deferred[name] = deferred.get(name, 0) + 1

    starved = {n: d for n, d in duty.items() if d["duty"] is not None and d["duty"] < 0.8}
    if not starved:
        print("  no starved schedules in window (all measured duties >= 80%)")
    for name in sorted(starved, key=lambda n: starved[n]["duty"]):
        d = starved[name]
        c = _classify_telemetry(name, overruns.get(name), deferred.get(name, 0), budgets)
        verdict = c["verdict"]
        if verdict == "HANDLER_COST":
            fix = (f"raise budget above p95 ({c['p95']:.0f}s) per tuning-report RAISE rule, "
                   f"or chunk the handler if budget is at interval/2 cap "
                   f"({(d['interval'] or 0) // 2}s for {d['interval']}s cadence)")
            evidence = (f"{c['n_overruns']} own overruns, p50 {c['p50']:.1f}s / "
                        f"p95 {c['p95']:.1f}s vs budget {c['budget']:.0f}s")
        elif verdict == "CLAIM_ORDER_TAIL":
            fix = ("dispatch-order fix: shortest-interval-first ordering (or fast/slow "
                   "lanes) so cheap high-cadence handlers are not queued behind "
                   "expensive ones at the wave deadline")
            evidence = f"deferred before dispatch in {c['deferrals']} wave(s); no own overruns"
        else:
            fix = ("no direct events for this schedule in window - check upstream "
                   "handler costs (long waves) and claim-order position")
            evidence = "no overruns, no deferrals recorded"
        print(f"  {name}: duty {d['duty'] * 100:.0f}% ({d['claims']} claims / "
              f"{d['span_h']}h @ {d['interval']}s) -> {verdict}\n"
              f"      evidence: {evidence}\n      next fix: {fix}")
    if reasons:
        note = ", ".join(f"{r}: {n}" for r, n in sorted(reasons.items(), key=lambda kv: -kv[1]))
        print(f"  deferral reasons in window: {note}")


def _structural_mode(con, start: str, end: str, intervals: dict, duty: dict) -> None:
    print("\n== mode: structural (no dispatcher telemetry in window; "
          "waves reconstructed from claim clustering) ==")
    stamps = [r[0] for r in _rows(
        con,
        """SELECT created_at FROM operational_events
           WHERE event_type='schedule_due' AND created_at BETWEEN ? AND ?
           ORDER BY created_at""",
        (start, end))]
    names = {r[0]: r[1] for r in _rows(
        con,
        """SELECT created_at, json_extract(payload,'$.schedule') s
           FROM operational_events
           WHERE event_type='schedule_due' AND created_at BETWEEN ? AND ?""",
        (start, end))}
    # Waves: consecutive claims with sub-2s gaps.
    waves = _reconstruct_waves(stamps, names)
    if not waves:
        print("  no schedule_due claims in window")
        return
    sizes = [len(w) for w in waves]
    print(f"  {len(waves)} waves reconstructed; size p50 {_percentile(sizes, 0.5):.0f} "
          f"/ p95 {_percentile(sizes, 0.95):.0f} / max {max(sizes)}")
    starved = {n: d for n, d in duty.items() if d["duty"] is not None and d["duty"] < 0.8}
    if not starved:
        print("  no starved schedules in window (all measured duties >= 80%)")
    for name in sorted(starved, key=lambda n: starved[n]["duty"]):
        d = starved[name]
        c = _classify_structural(name, waves)
        n_seen = c["n_seen"]
        if c["verdict"] == "NEVER_CLAIMED":
            print(f"  {name}: duty {d['duty'] * 100:.0f}% -> NEVER_CLAIMED "
                  f"(registry/claim predicate problem, not dispatch)")
            continue
        med_pos = c["med_pos"]
        verdict = c["verdict"]
        if verdict == "CLAIM_ORDER_TAIL":
            fix = ("claims land at the wave tail and the wave ends first: wave dispatcher "
                   "defers them; next fix is dispatch ordering (shortest-interval-first "
                   "or fast lanes) so tail position stops tracking cost")
        else:
            fix = ("waves are long because an upstream handler eats the budget; identify "
                   "the cost center by its dispatch position early in waves and apply the "
                   "HANDLER_COST fixes (budget raise per RAISE rule, or chunk the handler)")
        print(f"  {name}: duty {d['duty'] * 100:.0f}% ({d['claims']} claims / "
              f"{d['span_h']}h @ {d['interval']}s) -> {verdict}\n"
              f"      evidence: claimed in {n_seen} waves, median position "
              f"{med_pos:.2f}, tail-position share {c['tail_share']:.0%}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="trading_state.db")
    parser.add_argument("--session-start", default=None)
    parser.add_argument("--session-end", default=None)
    parser.add_argument("--min-span-hours", type=float, default=2.0)
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    root = Path(__file__).resolve().parent.parent
    intervals = _load_registry_intervals()
    budgets = _load_current_budgets(root)

    start, end = args.session_start, args.session_end
    if not (start and end):
        row = con.execute(
            """SELECT MIN(created_at) a, MAX(created_at) b FROM operational_events
               WHERE event_type IN ('run_started','run_finished')
                 AND created_at >= (SELECT MAX(created_at) FROM operational_events
                                    WHERE event_type='run_started')""").fetchone()
        if row and row["a"] and row["b"]:
            start, end = row["a"], row["b"]
    if not (start and end):
        row = con.execute("SELECT MIN(created_at), MAX(created_at) FROM operational_events").fetchone()
        start, end = row[0], row[1]
    print(f"journal: {args.db} (read-only)")
    print(f"window:  {str(start)[:19]} .. {str(end)[:19]}")

    duty = _duty(con, start, end, intervals, args.min_span_hours)
    has_telemetry = bool(_rows(
        con,
        """SELECT 1 FROM operational_events
           WHERE event_type IN ('schedule_handler_overrun','schedule_skipped')
             AND created_at BETWEEN ? AND ? LIMIT 1""",
        (start, end)))
    if has_telemetry:
        _telemetry_mode(con, start, end, budgets, duty)
    else:
        _structural_mode(con, start, end, intervals, duty)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
