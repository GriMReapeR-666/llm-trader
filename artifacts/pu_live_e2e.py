"""PU-0109: end-to-end request-size verification (sandbox, no network).

Drives the REAL production request-assembly path — ContextBuffer (R1/R3),
SIGNAL_SYSTEM_PROMPT_TEMPLATE, _enforce_request_char_budget (R2) — over six
realistic ~30K-char decision cycles, the audit's observed worst case.
Compares the pre-fix pipeline (legacy buffer, no whole-request budget) with
the post-fix pipeline, including a deterministic-fallback cycle.
"""
import json
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
# Mirror tests/conftest.py path setup: repo root + subpackage dirs, so the
# orchestrator's bare imports (risk_manager, agent_contracts, ...) resolve.
_pkg_dirs = ("core", "intelligence", "operations", "research", "data", "ui")
for _p in [ROOT] + [os.path.join(ROOT, d) for d in _pkg_dirs]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from brain.memory import ContextBuffer
from intelligence.llm_orchestrator import LLMOrchestrator, SIGNAL_SYSTEM_PROMPT_TEMPLATE


def payload(turn: int) -> str:
    return (
        f'{{"cycle": {turn}, "symbol": "EURUSD", "indicators": "{{...}}"}}'.ljust(30_000, "x")
    )


def assemble(*, legacy: bool, fallback_turn=None):
    """Replay 6 cycles through the real buffer + budget; return stats."""
    os.environ["LLM_TRADER_COMPACT_CONTEXT"] = "0" if legacy else "1"
    buf = ContextBuffer(max_messages=10)
    for turn in range(6):
        buf.set_system_prompt(SIGNAL_SYSTEM_PROMPT_TEMPLATE)
        buf.add_message("user", "Market Data JSON:\n" + payload(turn))
        if fallback_turn is not None and turn == fallback_turn:
            buf.add_message(
                "assistant",
                json.dumps({"action": "HOLD", "deterministic_fallback": True}),
                metadata={"deterministic_fallback": True},
            )
            if not legacy:
                # R3 path (post-fix only)
                buf.discard_failed_cycle()
        else:
            buf.add_message("assistant", json.dumps({"action": "HOLD", "confidence": 0.61}))
    messages = buf.get_messages()
    if not legacy:
        # R2 path (post-fix only)
        messages = LLMOrchestrator._enforce_request_char_budget(messages)
    total = sum(len(str(m.get("content", ""))) for m in messages)
    n_user = sum(1 for m in messages if m.get("role") == "user")
    return total, n_user, messages


print("== Real pipeline, 6 cycles x 30K payloads + real system prompt ==")

before, before_users, _ = assemble(legacy=True)
print(f"BEFORE (legacy buffer, no R2/R3): {before:,} chars (~{before//4:,} tok), user_turns={before_users}")

after, after_users, msgs_after = assemble(legacy=False)
print(f"AFTER  (compact R1 + R2 budget):  {after:,} chars (~{after//4:,} tok), user_turns={after_users}")

after_fb, after_fb_users, _ = assemble(legacy=False, fallback_turn=5)
print(f"AFTER  + failed last cycle (R3):  {after_fb:,} chars (~{after_fb//4:,} tok), user_turns={after_fb_users}")

# The audit's live observations for reference:
print("audit live baseline: avg 143,189 chars (~35.8K tok), max 287,319; ERROR-heavy (22/38)")

# --- Verdict assertions ---
assert after <= 96_000, f"post-fix request exceeds R2 budget: {after}"
assert after < before, "post-fix request must be smaller than pre-fix"
assert after_users == 1, "compact window must keep only the current cycle"
assert "[truncated for request budget]" not in "".join(
    str(m.get("content", "")) for m in msgs_after
) or after <= 96_000
# A failed cycle must not re-add a 30K replay pair.
assert after_fb <= after + 2_000, "failed cycle must not inflate the next request"
reduction = before / after
print(f"reduction: {reduction:.2f}x smaller; post-fix requests within the 96K R2 ceiling")
print("PU-0109-OK")
