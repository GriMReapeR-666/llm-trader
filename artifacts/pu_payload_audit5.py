"""PU-0097: message-level breakdown of llm_prompt traces + the malformed-JSON
region near the toon payload (read-only)."""
import json
import sqlite3

con = sqlite3.connect("file:trading_state.db?mode=ro", uri=True, timeout=30)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT payload FROM raw_traces WHERE trace_type='llm_prompt' "
    "ORDER BY created_at DESC LIMIT 2"
).fetchall()

for i, r in enumerate(rows):
    env = json.loads(r["payload"])
    msgs = env.get("messages") or []
    print(f"\nprompt#{i}: {len(msgs)} messages")
    for j, m in enumerate(msgs):
        c = m.get("content") or ""
        c = c if isinstance(c, str) else json.dumps(c, default=str)
        head = c[:110].replace("\n", "\\n")
        print(f"  [{j}] {m.get('role','?'):<9} {len(c):>7} chars | {head}")

# Zoom into the region around '{"account"' in the largest message.
raw = rows[0]["payload"]
env = json.loads(raw)
biggest = max(env["messages"], key=lambda m: len(m.get("content") or ""))
content = biggest["content"]
idx = content.find('{"account"')
print(f"\nlargest message: {len(content)} chars; '{{\"account\"' at {idx}")
print("context around the match:")
print(content[max(0, idx-200):idx+400].replace("\n", "\\n"))

con.close()
print("PROBE-DONE")
