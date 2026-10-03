"""PU-0123: journal delta since the fresh watermark (read-only).

WM updated 2026-09-30 pre-launch (PU-0122): 2026-09-29T19:32:32.596902+00:00.
"""
import json
import sqlite3

DB = "trading_state.db"
WM = "2026-09-29T19:32:32.596902+00:00"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

out = {}
row = con.execute(
    "SELECT strategy, model, status, COUNT(*) AS n FROM llm_requests "
    "WHERE created_at > ? GROUP BY strategy, model, status ORDER BY strategy, status",
    (WM,),
).fetchall()
out["llm_requests_since_wm"] = [dict(r) for r in row]

row = con.execute(
    "SELECT COUNT(*) AS n, AVG(LENGTH(payload)) AS avg_chars, MAX(LENGTH(payload)) AS max_chars "
    "FROM raw_traces WHERE trace_type='llm_prompt' AND created_at > ?",
    (WM,),
).fetchone()
out["prompts_since_wm"] = {
    "count": row["n"], "avg_chars": round(row["avg_chars"] or 0), "max_chars": row["max_chars"] or 0,
}

rows = con.execute(
    "SELECT created_at, LENGTH(payload) AS chars FROM raw_traces "
    "WHERE trace_type='llm_prompt' AND created_at > ? ORDER BY created_at",
    (WM,),
).fetchall()
out["prompt_rows"] = [{"at": r["created_at"], "chars": r["chars"]} for r in rows]

new_wm = con.execute("SELECT MAX(created_at) AS wm FROM llm_requests").fetchone()["wm"]
out["new_watermark_llm_requests"] = new_wm

con.close()
print(json.dumps(out, indent=2))
print("PU-0123-OK")
