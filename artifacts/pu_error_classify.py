"""PU-0142: classify the 7d llm_requests ERROR rows by error string (read-only).

Decides whether the historical ERROR baseline was context-length-driven
(R2/R3's target) or quota/auth/other-driven.
"""
import json
import re
import sqlite3

DB = "trading_state.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT created_at, strategy, provider, model, status, parse_status, error, "
    "estimated_tokens, response_chars FROM llm_requests "
    "WHERE created_at >= datetime('now','-7 days') AND status='ERROR' ORDER BY created_at"
).fetchall()

buckets = {"quota_429": 0, "payment_402": 0, "auth_403": 0, "context_length": 0, "other": 0}
examples = {}
for r in rows:
    err = (r["error"] or "")
    if "429" in err or "rate limit" in err.lower() or "quota" in err.lower():
        b = "quota_429"
    elif "402" in err or "payment" in err.lower():
        b = "payment_402"
    elif "403" in err or "forbidden" in err.lower() or "authorization" in err.lower():
        b = "auth_403"
    elif re.search(r"context.length|max.{0,12}tokens|too.{0,12}long|8192|32768|context_window", err, re.I):
        b = "context_length"
    else:
        b = "other"
    buckets[b] += 1
    examples.setdefault(b, []).append({
        "at": r["created_at"], "strategy": r["strategy"], "provider": r["provider"],
        "parse_status": r["parse_status"], "err_head": err[:180],
    })

out = {"error_rows_7d": len(rows), "buckets": buckets,
       "non_context_failures": len(rows) - buckets["context_length"]}
for b, exs in examples.items():
    out[f"example_{b}"] = exs[0]

con.close()
print(json.dumps(out, indent=2))
print("PU-0142-OK")
