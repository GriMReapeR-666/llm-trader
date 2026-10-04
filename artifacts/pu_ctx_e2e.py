"""PU-0102: R1 effect probe (sandbox, no network/journal/MT5).

Feeds six realistic ~30K-char decision cycles into ContextBuffer in legacy
and compact mode and reports the assembled prompt size, mirroring the
orchestrator call pattern (set_system_prompt / add_message / get_messages).
"""
import os
import sys

sys.path.insert(0, ".")

from brain.memory import ContextBuffer

SYSTEM = "You are a trading assistant. " * 40  # ~1K chars


def payload(turn: int) -> str:
    return f'{{"cycle": {turn}, "multi_timeframe": "{{...30K of market data...}}"}}'.ljust(
        30_000, "x"
    )


def reply(action: str) -> str:
    return f'{{"action": "{action}", "confidence": 0.42}}'


def run(compact: bool):
    os.environ["LLM_TRADER_COMPACT_CONTEXT"] = "1" if compact else "0"
    buf = ContextBuffer(max_messages=10)
    for turn in range(6):
        buf.set_system_prompt(SYSTEM)
        buf.add_message("user", payload(turn))
        buf.add_message("assistant", reply("HOLD" if turn % 2 else "BUY"))
    messages = buf.get_messages()
    joined = "\n".join(m["content"] for m in messages)
    n_user = sum(1 for m in messages if m["role"] == "user")
    n_summ = buf.summary_count
    print(
        f"compact={compact}: prompt_chars={len(joined):,} "
        f"(~{len(joined)//4:,} tok) user_turns={n_user} summaries={n_summ}"
    )
    return joined, buf


print("== 6 decision cycles x 30K-char payloads ==")
legacy, _buf_l = run(compact=False)
compact, buf_c = run(compact=True)
# Latest cycle is live in both modes.
assert '"cycle": 5' in compact and '"cycle": 5' in legacy
assert len(compact) < len(legacy)
# Every compact summary line respects the 300-char cap.
assert all(len(s) <= 300 for s in buf_c._summaries)
ratio = len(legacy) / len(compact)
print("ratio: %.2fx smaller" % ratio)
assert ratio >= 3.0
print("PU-0102-OK")
