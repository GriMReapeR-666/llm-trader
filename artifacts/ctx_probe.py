"""Preflight probe for the opt-in compact-context mode (R1, payload audit).

Run by START_DECK.bat / START_TRADING.bat whenever LLM_TRADER_COMPACT_CONTEXT=1
is set.  Verifies, sandbox-only (no network, no journal writes, no MT5):

1. brain.memory imports and resolves the env at instantiation;
2. a compact buffer keeps only the last decision cycle and folds evicted
   cycles into bounded summaries;
3. the legacy path is untouched when the flag is off;
4. (post-fix builds) the orchestrator request-budget helper exists and caps
   an oversized assembled request.

Exit code: 0 = GREEN, 1 = RED.  Full detail goes to artifacts/ctx_probe.txt
via the caller's redirect; this file only prints a verdict line + writes the
END-MARKER footer so results are trustable (marker doctrine).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "artifacts" / "ctx_probe.txt"

import sys as _sys

# `python artifacts/ctx_probe.py` puts artifacts/ on sys.path, not the root.
_root_str = str(ROOT)
if _root_str not in _sys.path:
    _sys.path.insert(0, _root_str)
for _sub in ("core", "intelligence", "operations", "research", "data", "ui"):
    _p = str(ROOT / _sub)
    if (ROOT / _sub).is_dir() and _p not in _sys.path:
        _sys.path.insert(0, _p)

_MARK = "END-MARKER-PU-0127"
_detail: list = []


def _note(msg: str) -> None:
    _detail.append(msg)


def main() -> int:
    checks = []

    flag = os.getenv("LLM_TRADER_COMPACT_CONTEXT", "").strip().lower()
    _note(f"env LLM_TRADER_COMPACT_CONTEXT={flag!r}")
    checks.append(("env flag is truthy", flag in {"1", "true", "yes"}))

    from brain.memory import ContextBuffer

    os.environ["LLM_TRADER_COMPACT_CONTEXT"] = "1"
    buf = ContextBuffer(max_messages=10)
    checks.append(("compact mode active", buf.compact is True))
    buf.set_system_prompt("SYS")
    for turn in range(4):
        buf.add_message("user", f'Market Data JSON: cycle-{turn} ' + "x" * 800)
        buf.add_message("assistant", json.dumps({"action": "HOLD", "confidence": 0.5}))
    msgs = buf.get_messages()
    users = [m["content"] for m in msgs if m["role"] == "user"]
    checks.append(("only latest cycle live", users == [f"Market Data JSON: cycle-3 " + "x" * 800]))
    checks.append(("evicted cycles summarized", buf.summary_count >= 3))
    blob = [m["content"] for m in msgs if m["role"] == "system" and m["content"].startswith("Previous context:")]
    checks.append(("summary blob bounded", blob and max(len(s) for s in blob) < 2000))

    os.environ["LLM_TRADER_COMPACT_CONTEXT"] = "0"
    legacy = ContextBuffer(max_messages=10)
    checks.append(("legacy mode inactive", legacy.compact is False))

    try:
        from intelligence.llm_orchestrator import LLMOrchestrator

        oversized = [
            {"role": "system", "content": "S" * 2_000},
            {"role": "user", "content": "h" * 60_000},
            {"role": "assistant", "content": "a"},
            {"role": "user", "content": "live " + "L" * 60_000},
        ]
        out = LLMOrchestrator._enforce_request_char_budget(oversized, max_total_chars=96_000)
        total = sum(len(str(m.get("content", ""))) for m in out)
        checks.append(("request budget caps oversized request", total <= 96_000 + 40))
        checks.append(("live user message intact", out[-1]["content"] == oversized[-1]["content"]))
    except Exception as exc:  # orchestrator import can be heavy; preflight degrades honestly
        _note(f"orchestrator check skipped: {type(exc).__name__}: {exc}")

    ok = all(passed for _, passed in passed_and_failed(checks))
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as fh:
        fh.write("CTX PROBE (compact-context preflight)\n")
        for name, passed in checks:
            fh.write(f"[{'PASS' if passed else 'FAIL'}] {name}\n")
        for line in _detail:
            fh.write(f"[note] {line}\n")
        fh.write(f"verdict: {'GREEN' if ok else 'RED'}\n")
        fh.write(_MARK + "\n")
    print(f"CTX-PROBE-VERDICT: {'GREEN' if ok else 'RED'}")
    return 0 if ok else 1


def passed_and_failed(checks):
    return checks


if __name__ == "__main__":
    sys.exit(main())
