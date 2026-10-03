"""PU-0149: reproduce the memory-batch distillation failure with the exact
production prompt + router, printing the raw reply to pin the shape mismatch.
No secrets printed; the reply content is a model-generated summary attempt.
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "data"), str(ROOT / "intelligence"),
          str(ROOT / "operations"), str(ROOT / "core"), str(ROOT / "research")):
    if p not in sys.path:
        sys.path.insert(0, p)

from llm_router import LLMRouter  # noqa: E402

# Exact outbox row (read-only)
con = sqlite3.connect(f"file:{ROOT / 'trading_state.db'}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
row = dict(con.execute(
    "SELECT * FROM memory_outbox WHERE source_id='39' AND kind='event_reaction'").fetchone())
con.close()

payload = json.loads(row["payload"])
prompt = (
    "Convert this VERIFIED trading journal record into compact JSON only. "
    "Never invent market facts or claim profitability. Return keys tags (object), "
    "summary (max 450 chars), lesson (max 300 chars). Treat one outcome as "
    "insufficient evidence.\nRECORD:\n" + json.dumps(
        {"kind": row["kind"], "source_id": row["source_id"], "payload": payload}, default=str)
)
print("prompt chars: %d" % len(prompt))

router = LLMRouter()
model = "mistralai/mistral-7b-instruct-v0.2"   # the model recorded in the journal rows
raw = router.generate(
    agent="trader",
    messages=[
        {"role": "system", "content": "You summarize verified trading records. Output strict JSON only."},
        {"role": "user", "content": prompt},
    ],
    model=model,
)
text = raw if isinstance(raw, str) else str(raw)
print("reply type: %s, chars: %d" % (type(raw).__name__, len(text)))
print("--- RAW REPLY ---")
print(text[:2500])
print("--- END RAW REPLY ---")

text = text.strip()
if text.startswith("```"):
    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
try:
    distilled = json.loads(text)
    print("json.loads OK; type=%s" % type(distilled).__name__)
    if isinstance(distilled, dict):
        print("keys: %s" % sorted(distilled.keys()))
        tags_ok = isinstance(distilled.get("tags"), dict)
        summary_ok = bool(distilled.get("summary"))
        print("tags-is-dict=%s summary-truthy=%s" % (tags_ok, summary_ok))
        print("validation error would be: %s" % (
            "none - would PASS" if (tags_ok and summary_ok)
            else "memory response missing required fields"))
    else:
        print("non-dict JSON -> validation fails ('missing required fields' only if dict)")
except Exception as exc:
    print("json.loads FAILED: %s" % exc)

print("PU-0149-OK")
