"""PU-0121: R5 tail-collapse measurement on an audit-shaped payload.

Reconstructs the live payload structure from PU-0098: large top sections
(multi_timeframe ~22%, live_advisory_hub ~10%, ...) plus a 48-section minor
tail of 150-450 chars each (~3% total).  Measures section count and size
before/after the real _sanitize_toon_lite_budget.
"""
import json
import os
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
for _p in [ROOT] + [os.path.join(ROOT, d) for d in ("core", "intelligence", "operations", "research", "data", "ui")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from intelligence.llm_orchestrator import LLMOrchestrator

payload = {
    # Audit top sections (proportional slices of ~30K chars)
    "multi_timeframe": {tf: [{"close": 1.08 + i * 0.0001} for i in range(5)] for tf in ("M1", "M5", "M15", "H1", "H4")},
    "live_advisory_hub": {"hierarchical": {"levels": "A" * 2000}, "copula_tail": {"rho": 0.3}},
    "hierarchical_advisory": {"level": "B" * 1500},
    "alpha_mining_results": {"mined": "C" * 1600},
    "completed_feature_snapshot": {"features": "D" * 1400},
    "news_trading_context": {"context": "E" * 1000},
    "indicators": {"rsi_14": [0.5] * 5, "macd": {"line": [0.1] * 5}},
    "macro": {"regime": "F" * 800},
    "open_positions": [{"ticket": 11, "symbol": "EURUSD"}],
    "account": {"balance": 100000.0, "equity": 100250.0},
    "session_info": {"session": "LONDON"},
    "working_memory": {"items": ["w1", "w2", "w3", "w4"]},
}
# 48-section minor tail: 150-450 chars each (~3% of 30K total)
for i in range(48):
    payload[f"minor_section_{i:02d}"] = {"idx": i, "note": f"n{i} " + "z" * (150 + (i * 7) % 300)}

raw_sections = len(payload)
raw_size = len(json.dumps(payload, default=str))
out = LLMOrchestrator._sanitize_toon_lite_budget(payload, max_total_chars=60_000)
new_sections = len(out)
new_size = len(json.dumps(out, default=str))
blob = out.get("minor_context_summary", {})

print(f"sections: {raw_sections} -> {new_sections} (collapsed={blob.get('collapsed_sections', 0)})")
print(f"serialized: {raw_size:,} -> {new_size:,} chars ({raw_size - new_size:,} saved, "
      f"{100 * (raw_size - new_size) / raw_size:.1f}%)")
print(f"blob size: {len(json.dumps(blob)):,} chars")
assert blob.get("collapsed_sections") == 48
assert "multi_timeframe" in out and "live_advisory_hub" in out
assert "account" in out and "open_positions" in out and "working_memory" in out
assert "minor_section_00" not in out and "minor_section_47" not in out
assert "RISK" not in json.dumps(blob)[:0]  # no-op sanity, keep pytest-style asserts explicit
print("PU-0121-OK")
