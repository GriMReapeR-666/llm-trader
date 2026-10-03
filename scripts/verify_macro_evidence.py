"""Post-session verifier: did the macro evidence stage accumulate causal evidence?

Read-only. Opens the trade journal with a ``mode=ro`` URI so it is safe to run
against a live trader. Never boots a process and never writes.

Usage:
    python -X utf8 scripts/verify_macro_evidence.py            # default trading_state.db
    python -X utf8 scripts/verify_macro_evidence.py --db path/to/journal.db

Exit codes:
    0  evidence gained (tables populated AND macro_event_evidence_updated seen)
    1  schedule registered but no evidence yet (stage never ran or produced nothing)
    2  blocked: no live session detected (journal stale) or schedule not registered
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    return con.execute(sql, args).fetchall()


def _one(con: sqlite3.Connection, sql: str, args: tuple = ()):
    return con.execute(sql, args).fetchone()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="trading_state.db")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    print(f"journal: {args.db} (read-only)")
    print(f"now:     {datetime.now(timezone.utc).isoformat()}")

    # ---- 0. live-session detection -------------------------------------
    last_event = _one(con, "SELECT MAX(created_at) FROM operational_events")
    print(f"\n== session ==")
    print(f"last journal event: {last_event}")
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    fresh = _one(
        con,
        "SELECT COUNT(*) FROM operational_events WHERE event_type='schedule_due' AND created_at > ?",
        (cutoff,),
    )
    live = bool(last_event) and last_event > cutoff and fresh > 0
    print(f"schedule_due in last 6h: {fresh} -> live session {'DETECTED' if live else 'NOT detected'}")

    # ---- 1. schedule registration --------------------------------------
    print(f"\n== schedule registration ==")
    sched = con.execute(
        "SELECT agent, interval_seconds, enabled, next_run_at, last_run_at "
        "FROM agent_schedules WHERE schedule_name='macro_event_evidence'"
    ).fetchone()
    if sched is None:
        # Informational, not a gate: the schedule registers on next boot, and
        # evidence may already exist via the historical backfill.
        print("macro_event_evidence: NOT REGISTERED (registers on next trader boot)")
        sched = None
    else:
        print(f"macro_event_evidence: agent={sched['agent']} interval={sched['interval_seconds']}s "
              f"enabled={sched['enabled']} last_run={sched['last_run_at']} next_run={sched['next_run_at']}")

    # ---- 2. the three evidence tables ----------------------------------
    print(f"\n== evidence tables ==")
    macro_events = _one(con, "SELECT COUNT(*) FROM macro_events")
    trackers = _one(con, "SELECT COUNT(*) FROM event_reaction_trackers")
    reactions = _one(con, "SELECT COUNT(*) FROM event_reactions")
    me_last = _one(con, "SELECT MAX(captured_at) FROM macro_events")
    er_last = _one(con, "SELECT MAX(observed_at) FROM event_reactions")
    print(f"macro_events:            {macro_events} (max captured_at {me_last})")
    print(f"event_reaction_trackers: {trackers}")
    for row in _rows(con, "SELECT status, COUNT(*) c FROM event_reaction_trackers GROUP BY status ORDER BY c DESC"):
        print(f"  status {row['status']}: {row['c']}")
    print(f"event_reactions:         {reactions} (max observed_at {er_last})")
    if reactions:
        for row in _rows(con, "SELECT symbol, COUNT(*) c FROM event_reactions GROUP BY symbol ORDER BY c DESC LIMIT 5"):
            print(f"  {row['symbol']}: {row['c']} reactions")
    # source-quality breakdown mirrors the causal engine's confidence weighting
    for row in _rows(con, "SELECT source_quality, COUNT(*) c FROM event_reactions GROUP BY source_quality ORDER BY c DESC LIMIT 5"):
        print(f"  quality {row['source_quality']}: {row['c']}")

    # ---- 2b. backfilled evidence --------------------------------------
    print(f"\n== backfilled evidence ==")
    try:
        bf_reactions = _one(con, "SELECT COUNT(*) FROM event_reactions WHERE source_quality='BACKFILL_FRED_LEVEL_RELEASE'")
        # Trackers carry the generic anchor-quality label (set by
        # schedule_event_reaction), so match them via their backfill source.
        bf_trackers = _one(
            con,
            "SELECT COUNT(*) FROM event_reaction_trackers WHERE event_source_id IN "
            "(SELECT source_id FROM macro_sources WHERE payload LIKE '%macro_evidence_backfill%')",
        )
        bf_sources = _one(con, "SELECT COUNT(*) FROM macro_sources WHERE payload LIKE '%macro_evidence_backfill%'")
        print(f"backfilled sources:   {bf_sources}")
        print(f"backfilled trackers:  {bf_trackers}")
        print(f"backfilled reactions: {bf_reactions} (of {reactions} total)")
    except sqlite3.OperationalError as exc:
        print(f"backfill tables unavailable: {exc}")

    # ---- 3. stage telemetry --------------------------------------------
    print(f"\n== stage telemetry ==")
    for et in ("macro_event_evidence_updated", "macro_event_evidence_failed", "macro_event_record_failed"):
        n = _one(con, "SELECT COUNT(*) FROM operational_events WHERE event_type=?", (et,))
        print(f"{et}: {n}")
    updated = _rows(
        con,
        "SELECT created_at, payload FROM operational_events WHERE event_type='macro_event_evidence_updated' ORDER BY created_at DESC LIMIT 1",
    )
    if updated:
        payload = json.loads(updated[0]["payload"] or "{}")
        print(f"last updated event: {updated[0]['created_at']}")
        print(f"  calendar_events={payload.get('calendar_events')} sources_ingested={payload.get('sources_ingested')} "
              f"ingest_errors={payload.get('ingest_errors')}")
        print(f"  macro_events_recorded={payload.get('macro_events_recorded')} "
              f"trackers_scheduled={payload.get('trackers_scheduled')} anchor={payload.get('anchor')}")
        cap = payload.get("reaction_capture") or {}
        print(f"  reaction_capture: scheduled={cap.get('scheduled')} measured={cap.get('measured')} completed={cap.get('completed')}")

    # aggregates across all updated events
    agg = _one(
        con,
        "SELECT COUNT(*) FROM operational_events WHERE event_type='macro_event_evidence_updated' "
        "AND json_extract(payload,'$.macro_events_recorded') > 0",
    )
    tracked_any = _one(
        con,
        "SELECT COUNT(*) FROM operational_events WHERE event_type='macro_event_evidence_updated' "
        "AND json_extract(payload,'$.trackers_scheduled') > 0",
    )
    print(f"pulses that recorded >=1 macro_event: {agg}")
    print(f"pulses that scheduled >=1 tracker:    {tracked_any}")

    # ---- 4. supporting chain -------------------------------------------
    print(f"\n== supporting chain ==")
    cal_sources = _one(
        con, "SELECT COUNT(*) FROM macro_sources WHERE payload LIKE '%scheduled_calendar_observation%'"
    )
    cal_last = _one(
        con, "SELECT MAX(captured_at) FROM macro_sources WHERE payload LIKE '%scheduled_calendar_observation%'"
    )
    print(f"macro_sources from evidence stage: {cal_sources} (max captured_at {cal_last})")
    try:
        fact_keys = _one(con, "SELECT COUNT(*) FROM atomic_facts WHERE fact_key LIKE 'macro_event:%'")
        print(f"atomic_facts macro_event keys (exactly-once gate): {fact_keys}")
    except sqlite3.OperationalError:
        print("atomic_facts: table unavailable")

    # skip telemetry that named this schedule
    skipped = _one(
        con,
        "SELECT COUNT(*) FROM operational_events WHERE event_type='schedule_skipped' "
        "AND payload LIKE '%macro_event_evidence%'",
    )
    print(f"schedule_skipped events naming macro_event_evidence: {skipped}")

    # ---- 5. evidence gained since the last session start ----------------
    print(f"\n== gained since last run_started ==")
    session_start = _one(con, "SELECT MAX(created_at) FROM operational_events WHERE event_type='run_started'")
    if session_start:
        gained_me = _one(con, "SELECT COUNT(*) FROM macro_events WHERE captured_at > ?", (session_start,))
        # anchor_observed_at doubles as the scheduling stamp for trackers.
        gained_tr = _one(con, "SELECT COUNT(*) FROM event_reaction_trackers WHERE anchor_observed_at > ?", (session_start,))
        gained_er = _one(con, "SELECT COUNT(*) FROM event_reactions WHERE observed_at > ?", (session_start,))
        print(f"session start: {session_start}")
        print(f"  macro_events +{gained_me}, trackers +{gained_tr}, reactions +{gained_er}")
    else:
        print("no run_started events found")

    con.close()

    has_evidence = bool(macro_events or trackers or reactions)
    if not live:
        if has_evidence:
            print("\nRESULT: EVIDENCE PRESENT (backfill era) - live-session accumulation "
                  "still pending; start the trader and re-run after a session.")
        else:
            print("\nRESULT: BLOCKED - no live session detected; start the trader and re-run.")
        return 2
    if has_evidence:
        print("\nRESULT: EVIDENCE GAINED - macro evidence chain is accumulating.")
        return 0
    print("\nRESULT: WIRED BUT EMPTY - schedule registered, no evidence rows yet "
          "(stage may need a completed calendar release or a live tick anchor).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
