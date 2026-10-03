"""PU-0153: whole-system autonomy & performance audit (read-only).

Reads trading_state.db read-only and prints a structured audit:
scheduler cadence adherence, lifecycle gates, agent runs, trader LLM health,
autonomy self-recovery evidence, candle-cycle health, research funnel,
memory pipeline, Kronos footprint. END-MARKER-PU-0153 footed.
"""
from __future__ import annotations

import json
import sqlite3
import sys

DB = "trading_state.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row


def q(sql, *args):
    return con.execute(sql, args).fetchall()


def section(title):
    print("\n=== %s ===" % title)


section("1. Scheduler cadence (last 24h): due vs overruns vs skips")
for r in q(
    "SELECT json_extract(payload,'$.schedule') s, "
    "SUM(CASE WHEN event_type='schedule_due' THEN 1 ELSE 0 END) due, "
    "SUM(CASE WHEN event_type='schedule_handler_overrun' THEN 1 ELSE 0 END) ov, "
    "SUM(CASE WHEN event_type='schedule_skipped' THEN 1 ELSE 0 END) sk, "
    "SUM(CASE WHEN event_type='schedule_session_skip' THEN 1 ELSE 0 END) ssk "
    "FROM operational_events WHERE event_type LIKE 'schedule%' "
    "AND created_at > datetime('now','-1 day') GROUP BY s ORDER BY due DESC LIMIT 25"):
    print("  %-28s due=%-6d overrun=%-4d skip=%-4d sess_skip=%s"
          % (r["s"] or "?", r["due"], r["ov"], r["sk"], r["ssk"]))

section("2. Lifecycle gates (7d): evaluations, transitions, overfit gate")
for r in q(
    "SELECT event_type, COUNT(*) n FROM operational_events "
    "WHERE created_at > datetime('now','-7 day') AND event_type IN ("
    "'strategy_lifecycle_evaluated','lifecycle_evaluation_failed',"
    "'proposal_received','proposal_validated',"
    "'proposal_rejected_overfit_not_validated','proposal_rejected_overfit_reject') "
    "GROUP BY event_type ORDER BY n DESC"):
    print("  %-44s %d" % (r["event_type"], r["n"]))
# journaling-contract failures vs real evaluation failures (contract fix 2026-09-28)
for r in q(
    "SELECT CASE WHEN json_extract(payload,'$.error') LIKE '%not permitted%' "
    "THEN 'contract_gate' ELSE 'real_failure' END k, COUNT(*) n, "
    "SUM(CASE WHEN created_at > '2026-09-28T12:00' THEN 1 ELSE 0 END) post_fix "
    "FROM operational_events WHERE event_type='lifecycle_evaluation_failed' "
    "AND created_at > datetime('now','-7 day') GROUP BY k"):
    print("  lifecycle_failed[%-13s] total=%-4d post_contract_fix=%s" % (r["k"], r["n"], r["post_fix"]))
for r in q(
    "SELECT json_extract(payload,'$.strategy') st, "
    "json_extract(payload,'$.to_state') ts, MAX(created_at) "
    "FROM operational_events WHERE event_type='strategy_lifecycle_evaluated' "
    "AND json_extract(payload,'$.to_state') IS NOT NULL "
    "GROUP BY st, ts ORDER BY MAX(created_at) DESC LIMIT 12"):
    print("  transition: %-24s -> %-18s (last %s)" % (r["st"], r["ts"], r[2]))

section("3. Agent runs & heartbeat")
for r in q("SELECT agent, status, COUNT(*) n FROM agent_runs "
           "GROUP BY agent, status ORDER BY agent, status"):
    print("  %-16s %-12s %d" % (r["agent"], r["status"], r["n"]))
for r in q("SELECT agent, status, heartbeat_at FROM agent_runs "
           "WHERE status='RUNNING' ORDER BY heartbeat_at DESC LIMIT 5"):
    print("  RUNNING: %s hb=%s" % (r["agent"], r["heartbeat_at"]))

section("4. Trader LLM health (7d) vs baseline")
for r in q("SELECT status, COUNT(*) n FROM llm_requests "
           "WHERE strategy IN ('swing','scalp','trader') "
           "AND created_at > datetime('now','-7 day') GROUP BY status"):
    print("  %-10s %d" % (r["status"], r["n"]))
rows4 = q("SELECT COUNT(*) n, SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) e "
          "FROM llm_requests WHERE strategy IN ('swing','scalp','trader') "
          "AND created_at > datetime('now','-7 day')")
r = rows4[0] if rows4 else None
if r and r["n"]:
    print("  error_rate=%.1f%% (baseline 57.9%%)  n=%d"
          % (100.0 * (r["e"] or 0) / r["n"], r["n"]))

section("5. Autonomy: self-recovery & protective events (7d)")
for r in q(
    "SELECT event_type, COUNT(*) n, MAX(created_at) last FROM operational_events "
    "WHERE created_at > datetime('now','-7 day') AND event_type IN ("
    "'run_stale_recovered','circuit_health','circuit_opened','circuit_reset',"
    "'provider_scorecard','boot_datasheet','boot_fetch_stage_timeout',"
    "'quota_pause','alert_emitted','incident_detected','distillation_fallback_used',"
    "'distilled_locally','schedule_skips_cleared') "
    "GROUP BY event_type ORDER BY n DESC"):
    print("  %-30s n=%-5d last=%s" % (r["event_type"], r["n"], r["last"]))

section("6. Candle-cycle health: strategy cycle briefs (24h)")
for r in q(
    "SELECT json_extract(payload,'$.strategy') st, COUNT(*) n, MAX(created_at) last "
    "FROM operational_events WHERE event_type='cycle_brief_recorded' "
    "AND created_at > datetime('now','-1 day') GROUP BY st"):
    print("  %-10s briefs=%-5d last=%s" % (r["st"] or "?", r["n"], r["last"]))

section("7. Decision-gate texture (7d): HOLD reasons sample")
for r in q(
    "SELECT substr(json_extract(payload,'$.reason'),1,60) reason, COUNT(*) n "
    "FROM operational_events WHERE event_type IN ('cycle_held','decision_hold') "
    "AND created_at > datetime('now','-7 day') GROUP BY reason ORDER BY n DESC LIMIT 8"):
    print("  n=%-4d %s" % (r["n"], r["reason"] or "?"))

section("8. Memory pipeline")
for r in q("SELECT status, COUNT(*) n FROM memory_outbox GROUP BY status"):
    print("  outbox: %-10s %d" % (r["status"], r["n"]))

section("9. Kronos footprint in journal (90d)")
r = q("SELECT COUNT(*) n FROM operational_events "
      "WHERE lower(payload) LIKE '%kronos%' "
      "AND created_at > datetime('now','-90 day')").fetchone()
print("  events mentioning kronos: %d" % (r["n"] if r else 0))

con.close()
print("\nPU-0153-OK")
print("END-MARKER-PU-0153")
