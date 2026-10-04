"""PU-0141: row-level ground truth for the session-4 window (read-only)."""
import json
import sqlite3

DB = "trading_state.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

out = {}

rows = con.execute(
    "SELECT created_at, strategy, provider, model, status, parse_status, error, "
    "estimated_tokens, response_chars "
    "FROM llm_requests WHERE created_at > '2026-09-29T22:30:00' ORDER BY created_at"
).fetchall()
out["llm_requests_since_2230z"] = [dict(r) for r in rows]

rows = con.execute(
    "SELECT created_at, LENGTH(payload) AS chars FROM raw_traces "
    "WHERE trace_type='llm_prompt' AND created_at > '2026-09-29T22:30:00' ORDER BY created_at"
).fetchall()
out["llm_prompt_traces_since_2230z"] = [dict(r) for r in rows]

rows = con.execute(
    "SELECT status, COUNT(*) AS n FROM llm_requests WHERE created_at > '2026-09-29T22:30:00' GROUP BY status"
).fetchall()
out["status_split_since_2230z"] = {r["status"]: r["n"] for r in rows}

con.close()
print(json.dumps(out, indent=2))
print("PU-0141-OK")
