"""PU-0161c replay probe: run the real recorded s11 prompts through the new
ceiling guard and report what would have shipped.

Read-only over trading_state.db. Marker: END-MARKER-PU-0161C-REPLAY
"""
import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in [_ROOT] + [
    os.path.join(_ROOT, s)
    for s in ("core", "intelligence", "operations", "research", "data", "ui")
]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from intelligence.llm_orchestrator import LLMOrchestrator  # noqa: E402

DB = "file:trading_state.db?mode=ro"
MARKER = "END-MARKER-PU-0161C-REPLAY"
CAP = 100_000


def main() -> None:
    con = sqlite3.connect(DB, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT created_at, payload
        FROM raw_traces
        WHERE trace_type = 'llm_prompt'
          AND created_at >= '2026-10-02T17:00'
        ORDER BY created_at
        """
    ).fetchall()

    os.environ.pop("LLM_TRADER_PROMPT_CEILING_CHARS", None)
    blocked = 0
    intact = 0
    print(f"{'created_at':<28} {'before':>8} {'after':>8} {'action':<10}")
    for r in rows:
        msgs = json.loads(r["payload"]).get("messages") or []
        before = len(json.dumps(msgs, default=str))
        out = LLMOrchestrator._enforce_request_ceiling(msgs, max_chars=CAP)
        after = len(json.dumps(out, default=str))

        # Guard contract: system prompt + live payload byte-identical.
        sys_ok = out[0]["content"] == msgs[0]["content"]
        live_ok = out[-1]["content"] == msgs[-1]["content"]
        try:
            json.loads(out[-1]["content"].split("\n", 1)[1])
            parse_ok = True
        except Exception:
            parse_ok = False

        if before > CAP:
            blocked += 1
            action = "COMPACTED"
        else:
            action = "pass"
        if not (sys_ok and live_ok and parse_ok):
            action = "BROKEN!"
            intact += 1
        print(
            f"{r['created_at'][:26]:<28} {before:>8} {after:>8} {action:<10}"
        )

    print(f"\nover-cap requests compacted: {blocked}/{len(rows)}")
    print(f"contract violations (system/live/json broken): {intact}")
    print(MARKER)
    con.close()


if __name__ == "__main__":
    main()
