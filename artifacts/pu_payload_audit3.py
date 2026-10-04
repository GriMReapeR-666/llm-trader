"""PU-0095: decompose the newest llm_prompt raw trace per section (read-only)."""
import json
import sqlite3

con = sqlite3.connect("file:trading_state.db?mode=ro", uri=True, timeout=30)
con.row_factory = sqlite3.Row

row = con.execute(
    "SELECT payload FROM raw_traces WHERE trace_type='llm_prompt' "
    "ORDER BY created_at DESC LIMIT 1"
).fetchone()
raw = row["payload"]
print(f"newest llm_prompt: {len(raw)} chars")

# Show the envelope: first 400 chars.
print("HEAD:", raw[:400].replace("\n", "\\n"))

# Try to find the embedded JSON payload (toon_lite) inside the prompt text.
data = None
try:
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        data = parsed
        print("envelope: top-level JSON dict")
except Exception:
    # Look for the largest {...} block: scan candidate starts of '{' and try parse.
    best = None
    for i, ch in enumerate(raw):
        if ch == "{":
            for end in (len(raw), len(raw) - 1):
                try:
                    cand = json.loads(raw[i:end])
                    if isinstance(cand, dict) and (best is None or len(json.dumps(cand)) > len(json.dumps(best))):
                        best = cand
                        break
                except Exception:
                    continue
        if best:
            break
    if best:
        data = best
        print(f"envelope: JSON dict found embedded (len={len(json.dumps(best))})")

if data:
    sizes = sorted(((len(json.dumps(v, default=str)), k) for k, v in data.items()), reverse=True)
    total = sum(s for s, _ in sizes)
    print(f"top-level sections: {len(sizes)}, total {total} chars")
    for s, k in sizes[:25]:
        print(f"  {s:>8}  {100*s/total:5.1f}%  {k}")
    consumed = sum(s for s, _ in sizes[:25])
    print(f"(top 25 cover {100*consumed/total:.1f}% of the JSON)")
else:
    print("no embedded JSON dict found; showing section-marker histogram")
    import re
    for m in re.finditer(r"^(#{1,3} .{0,80}|\[[A-Z_]{3,40}\])", raw, re.M):
        print("  marker:", m.group(0))

con.close()
print("PROBE-DONE")
