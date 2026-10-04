"""PU-0105: pre-session baseline (read-only) for live verification.

Captures llm_requests status split + raw_traces llm_prompt size stats over
the last 7d, plus MAX(created_at) watermarks so the verification session is
measured as everything strictly after this snapshot.
"""
import json
import sqlite3

DB = "trading_state.db"

out = {}
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

row = con.execute(
    "SELECT COUNT(*) AS n, "
    "SUM(CASE WHEN status='ERROR' THEN 1 ELSE 0 END) AS errors, "
    "SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) AS success "
    "FROM llm_requests WHERE created_at >= datetime('now','-7 days')"
).fetchone()
out["llm_requests_7d"] = {"total": row["n"], "error": row["errors"] or 0, "success": row["success"] or 0}

row = con.execute(
    "SELECT COUNT(*) AS n, AVG(LENGTH(payload)) AS avg_chars, MAX(LENGTH(payload)) AS max_chars "
    "FROM raw_traces WHERE trace_type='llm_prompt' AND created_at >= datetime('now','-7 days')"
).fetchone()
out["prompts_7d"] = {"count": row["n"], "avg_chars": round(row["avg_chars"] or 0), "max_chars": row["max_chars"] or 0}

for tbl in ("llm_requests", "raw_traces", "operational_events"):
    wm = con.execute(f"SELECT MAX(created_at) AS wm FROM {tbl}").fetchone()["wm"]
    out[f"watermark_{tbl}"] = wm

con.close()
print(json.dumps(out, indent=2))
print("PU-0105-OK")
