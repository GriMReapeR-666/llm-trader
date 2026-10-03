"""PU-0150: session-5 decisive measurement (read-only).

Usage: python -X utf8 artifacts/pu_session5_report.py <booted_at_utc ISO>

Measures, since the boot timestamp:
  * trader-family llm_requests rows by status (ERROR rate)
  * per-strategy split, errors, parse_status
  * prompt sizes joined from raw_traces llm_prompt (nearest within ±90s)
Compared against the 7-day baseline (PU-0105/PU-0143: 38 trader requests,
22 ERROR/16 SUCCESS, prompts avg 129,820 / max 287,319 chars) and against the
structural predictions: requests ≤ ~96K chars, no context-length failures.
"""
import json
import sqlite3
import sys
from datetime import datetime

DB = "trading_state.db"
WINDOW_START = sys.argv[1] if len(sys.argv) > 1 else "1970-01-01"

out = {"report": "PU-0150", "window_start": WINDOW_START}

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT created_at, strategy, provider, model, status, parse_status, error, "
    "estimated_tokens, response_chars "
    "FROM llm_requests WHERE created_at > ? ORDER BY created_at", (WINDOW_START,)
).fetchall()
out["llm_requests"] = [dict(r) for r in rows]

trader_rows = [r for r in rows
               if r["strategy"] in ("swing", "scalp", "trader", "scanner")]
n = len(trader_rows)
errors = sum(1 for r in trader_rows if r["status"] == "ERROR")
successes = sum(1 for r in trader_rows if r["status"] == "SUCCESS")
out["trader_summary"] = {
    "n": n, "errors": errors, "successes": successes,
    "error_rate": round(errors / n, 3) if n else None,
    "baseline_error_rate": round(22 / 38, 3),
}

by_strategy = {}
for r in trader_rows:
    s = by_strategy.setdefault(r["strategy"], {"n": 0, "errors": 0, "successes": 0})
    s["n"] += 1
    s["errors" if r["status"] == "ERROR" else "successes"] += 1
out["by_strategy"] = by_strategy

errs = [r for r in trader_rows if r["status"] == "ERROR"]
out["errors_detail"] = [
    {"at": r["created_at"], "strategy": r["strategy"],
     "parse_status": r["parse_status"],
     "error": (r["error"] or "")[:160]}
    for r in errs
]

prompt_rows = con.execute(
    "SELECT created_at, LENGTH(payload) AS chars FROM raw_traces "
    "WHERE trace_type='llm_prompt' AND created_at > ? ORDER BY created_at",
    (WINDOW_START,)).fetchall()


def ts(s):
    return datetime.fromisoformat(s)


prompts = [(ts(r["created_at"]), r["chars"]) for r in prompt_rows]
sizes = []
for r in trader_rows:
    t = ts(r["created_at"])
    best, best_dt = None, None
    for pt, size in prompts:
        dt = abs((pt - t).total_seconds())
        if best_dt is None or dt < best_dt:
            best_dt, best = dt, size
    if best_dt is not None and best_dt <= 90:
        sizes.append((r["created_at"], r["strategy"], r["status"], best))

out["prompt_join"] = [
    {"at": a, "strategy": s, "status": st, "prompt_chars": c}
    for (a, s, st, c) in sizes
]
if sizes:
    vals = [c for (_, _, _, c) in sizes]
    over = [c for c in vals if c > 96000]
    out["prompt_stats"] = {
        "n": len(vals), "min": min(vals), "max": max(vals),
        "avg": round(sum(vals) / len(vals)),
        "over_96k": len(over),
        "baseline_avg": 129820, "baseline_max": 287319,
    }
else:
    out["prompt_stats"] = {"n": 0}

memory_rows = [r for r in rows if r["strategy"] == "memory"]
out["memory_summary"] = {
    "n": len(memory_rows),
    "errors": sum(1 for r in memory_rows if r["status"] == "ERROR"),
    "parse_status_counts": {},
    "error_strings": sorted({(r["error"] or "")[:80] for r in memory_rows
                             if r["status"] == "ERROR"}),
}
for r in memory_rows:
    ps = r["parse_status"]
    out["memory_summary"]["parse_status_counts"][ps] = \
        out["memory_summary"]["parse_status_counts"].get(ps, 0) + 1

events = con.execute(
    "SELECT event_type, COUNT(*) AS n FROM operational_events "
    "WHERE created_at > ? AND event_type IN ('distillation_failed',"
    "'distillation_fallback_used','distilled_locally','schedule_handler_overrun') "
    "GROUP BY event_type", (WINDOW_START,)).fetchall()
out["operational_event_counts"] = {r["event_type"]: r["n"] for r in events}

outbox = con.execute(
    "SELECT status, COUNT(*) AS n FROM memory_outbox GROUP BY status").fetchall()
out["memory_outbox_status"] = {r["status"]: r["n"] for r in outbox}

# --- Ablation harness section (PU-0156 §2, backward-compatible) ---
# Discovers an ablation measurement run by its durable boot marker; when
# present, quantifies per-component gate activity + decision distribution in
# the window. Absent for normal runs (key set to None) — old reports unchanged.
try:
    abl_boot = con.execute(
        "SELECT created_at, payload FROM operational_events "
        "WHERE event_type='ablation_session_modes' AND created_at > ? "
        "ORDER BY created_at LIMIT 1", (WINDOW_START,)).fetchone()
except Exception:
    abl_boot = None
if abl_boot is not None:
    try:
        modes = json.loads(abl_boot["payload"] or "{}").get("modes", [])
    except Exception:
        modes = []
    abl = {"modes": modes, "boot_marker_at": abl_boot["created_at"],
           "events": {}}
    for et in ("ablation_news_gate_bypassed", "ablation_macro_context_stripped",
               "task_released"):
        row = con.execute(
            "SELECT COUNT(*) AS n FROM operational_events "
            "WHERE event_type=? AND created_at > ?",
            (et, WINDOW_START)).fetchone()
        abl["events"][et] = row["n"]
    holds = {}
    ablated_llm_holds = 0
    for t in con.execute(
            "SELECT result FROM agent_tasks WHERE task_type='analyse_completed_candle' "
            "AND updated_at > ?", (WINDOW_START,)).fetchall():
        try:
            res = json.loads(t["result"] or "{}")
        except Exception:
            continue
        reason = str(res.get("reason", ""))
        holds[reason[:60]] = holds.get(reason[:60], 1) + 1
        if reason.startswith("ablation no_llm"):
            ablated_llm_holds += 1
    abl["decision_reason_counts"] = holds
    abl["ablated_no_llm_holds"] = ablated_llm_holds
    out["ablation"] = abl

con.close()
print(json.dumps(out, indent=2))
print("PU-0150-OK")
