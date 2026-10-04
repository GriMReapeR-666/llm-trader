"""PU-0094: raw_traces by type + llm_requests payload samples (read-only)."""
import json
import sqlite3

con = sqlite3.connect("file:trading_state.db?mode=ro", uri=True, timeout=30)
con.row_factory = sqlite3.Row

print("raw_traces by trace_type (7d):")
for r in con.execute(
    "SELECT trace_type, COUNT(*) AS n, AVG(LENGTH(payload)) AS avg_chars, "
    "MAX(LENGTH(payload)) AS max_chars FROM raw_traces "
    "WHERE created_at >= datetime('now', '-7 days') GROUP BY trace_type ORDER BY avg_chars DESC"
):
    print(f"  {r['trace_type']:<28} n={r['n']:<4} avg={int(r['avg_chars']):>7} max={r['max_chars']:>7}")

print("\nllm_requests newest 3 payload samples:")
for r in con.execute(
    "SELECT created_at, payload FROM llm_requests ORDER BY created_at DESC LIMIT 3"
):
    print(f"  {r['created_at']}: {r['payload'][:200]}")

print("\nraw_traces newest trace_type:")
for r in con.execute(
    "SELECT trace_type, created_at, LENGTH(payload) AS n FROM raw_traces "
    "ORDER BY created_at DESC LIMIT 5"
):
    print(f"  {r['created_at']}: {r['trace_type']} ({r['n']} chars)")

con.close()
print("PROBE-DONE")
