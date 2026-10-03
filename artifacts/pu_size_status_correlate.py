"""PU-0143: correlate llm_requests status with prompt size (read-only).

Joins each 7d llm_requests row (trader-family strategies) to the nearest
raw_traces llm_prompt row within ±90s (both are written inside the same
get_decision call) and buckets status by the prompt's serialized size.
Decides whether historical ERRORs rode on oversized (>96K chars) prompts.
"""
import json
import sqlite3
from datetime import datetime

DB = "trading_state.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

def ts(s):
    return datetime.fromisoformat(s)

rows = con.execute(
    "SELECT created_at, strategy, status, parse_status, error FROM llm_requests "
    "WHERE created_at >= datetime('now','-7 days') AND strategy IN "
    "('swing','scalp','trader','scanner') ORDER BY created_at"
).fetchall()

prompts = [
    (ts(r["created_at"]), r["chars"])
    for r in con.execute(
        "SELECT created_at, LENGTH(payload) AS chars FROM raw_traces "
        "WHERE trace_type='llm_prompt' AND created_at >= datetime('now','-7 days')"
    ).fetchall()
]

def nearest_prompt_size(t):
    best = None
    best_dt = None
    for pt, size in prompts:
        dt = abs((pt - t).total_seconds())
        if best_dt is None or dt < best_dt:
            best_dt = dt
            best = size
    return (best, best_dt) if best_dt is not None and best_dt <= 90 else (None, None)

pairs = []
for r in rows:
    size, dt = nearest_prompt_size(ts(r["created_at"]))
    pairs.append({
        "at": r["created_at"], "strategy": r["strategy"], "status": r["status"],
        "parse_status": r["parse_status"],
        "prompt_chars": size, "prompt_dt_s": round(dt) if dt is not None else None,
    })

matched = [p for p in pairs if p["prompt_chars"] is not None]
big_errors = [p for p in matched if p["status"] == "ERROR" and p["prompt_chars"] > 96000]
big_success = [p for p in matched if p["status"] == "SUCCESS" and p["prompt_chars"] > 96000]
small_errors = [p for p in matched if p["status"] == "ERROR" and p["prompt_chars"] <= 96000]

out = {
    "trader_rows_7d": len(rows),
    "matched_with_prompt": len(matched),
    "errors_on_gt96k_prompts": len(big_errors),
    "success_on_gt96k_prompts": len(big_success),
    "errors_on_le96k_prompts": len(small_errors),
    "pairs": pairs[-12:],
}
con.close()
print(json.dumps(out, indent=2))
print("PU-0143-OK")
