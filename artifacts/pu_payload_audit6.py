"""PU-0098: current-payload section ranking + history-vs-current share (read-only)."""
import json
import sqlite3

con = sqlite3.connect("file:trading_state.db?mode=ro", uri=True, timeout=30)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT payload FROM raw_traces WHERE trace_type='llm_prompt' "
    "ORDER BY created_at DESC LIMIT 5"
).fetchall()

total_chars = 0
history_chars = 0
current_chars = 0
last_sections = None

for r in rows:
    env = json.loads(r["payload"])
    msgs = env.get("messages") or []
    user_msgs = [m for m in msgs if m.get("role") == "user"]
    if not user_msgs:
        continue
    newest_user = user_msgs[-1]
    for m in msgs:
        c = m.get("content") or ""
        c = c if isinstance(c, str) else ""
        total_chars += len(c)
        if m is not newest_user:
            history_chars += len(c)
    current_chars += len(newest_user.get("content") or "")
    # Decompose the newest user message JSON (after "Market Data JSON:\n").
    c = newest_user.get("content") or ""
    idx = c.find("{")
    try:
        payload, _ = json.JSONDecoder().raw_decode(c[idx:])
        if isinstance(payload, dict):
            sizes = sorted(((len(json.dumps(v, default=str)), k) for k, v in payload.items()), reverse=True)
            last_sections = (newest_user["content"][:0], sizes, len(c))
    except Exception as exc:
        print(f"newest-user parse failed: {exc}")

print(f"across {len(rows)} recent prompts:")
print(f"  total content chars:      {total_chars}")
print(f"  replayed-history chars:   {history_chars} ({100*history_chars/total_chars:.1f}%)")
print(f"  current-payload chars:    {current_chars} ({100*current_chars/total_chars:.1f}%)")
print(f"  avg per-prompt: {total_chars/len(rows):.0f} chars, of which current ~{current_chars/len(rows):.0f}")

if last_sections:
    _, sizes, msg_len = last_sections
    total = sum(s for s, _ in sizes)
    print(f"\ncurrent user message: {msg_len} chars, JSON {total} chars, {len(sizes)} sections:")
    for s, k in sizes[:25]:
        print(f"  {s:>7}  {100*s/total:5.1f}%  {k}")
    rest = sum(s for s, _ in sizes[25:])
    if rest:
        print(f"  {rest:>7}  (remaining {len(sizes)-25} sections)")

con.close()
print("PROBE-DONE")
