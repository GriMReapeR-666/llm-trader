"""PU-0096: per-section decomposition of the embedded toon JSON inside the
newest llm_prompt trace (read-only)."""
import json
import sqlite3

con = sqlite3.connect("file:trading_state.db?mode=ro", uri=True, timeout=30)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT payload FROM raw_traces WHERE trace_type='llm_prompt' "
    "ORDER BY created_at DESC LIMIT 5"
).fetchall()

decoder = json.JSONDecoder()


def analyze(raw: str, label: str) -> None:
    try:
        env = json.loads(raw)
    except Exception as exc:
        print(f"{label}: envelope parse failed: {exc}")
        return
    msgs = env.get("messages") or []
    print(f"\n{label}: model={env.get('model')} messages={len(msgs)}")
    content = ""
    for m in msgs:
        c = m.get("content") or ""
        content += c if isinstance(c, str) else json.dumps(c, default=str)
    print(f"  total content: {len(content)} chars")

    # Locate the embedded toon JSON. The context dict's first key is
    # "account"; find its first occurrence and raw_decode from there.
    idx = content.find('{"account"')
    payload = None
    prose_end = len(content)
    if idx >= 0:
        try:
            payload, end = decoder.raw_decode(content[idx:])
            prose_end = idx
        except Exception as exc:
            print(f"  raw_decode at {{\"account\" failed: {exc}}}")
    prose = content[:prose_end] if payload else content
    print(f"  prose (system prompt + wrapper): {len(prose)} chars")
    if payload and isinstance(payload, dict):
        sizes = sorted(((len(json.dumps(v, default=str)), k) for k, v in payload.items()), reverse=True)
        total = sum(s for s, _ in sizes)
        print(f"  toon JSON: {total} chars, {len(sizes)} sections")
        for s, k in sizes[:20]:
            print(f"    {s:>8}  {100*s/total:5.1f}%  {k}")
        rest = sum(s for s, _ in sizes[20:])
        if rest:
            print(f"    {rest:>8}  (remaining {len(sizes)-20} sections)")


for i, r in enumerate(rows):
    analyze(r["payload"], f"prompt#{i}")

con.close()
print("PROBE-DONE")
