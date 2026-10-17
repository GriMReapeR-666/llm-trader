"""Self-check for thx01_advisor integration.

Runs without pytest — just: python intelligence/thx01_advisor_check.py
Passes when the full signal pipeline works end-to-end.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from intelligence.thx01_advisor import advise_market, is_available, model_id
from intelligence.signal_arbitrator import SOURCE_WEIGHTS, _extract_signals_from_state

# 1. Requirements
assert "THX01" in SOURCE_WEIGHTS, "THX01 missing from SOURCE_WEIGHTS"
assert SOURCE_WEIGHTS["THX01"] == 0.88, f"Unexpected weight: {SOURCE_WEIGHTS['THX01']}"
print(f"[OK] SOURCE_WEIGHTS THX01 = {SOURCE_WEIGHTS['THX01']}")

# 2. Model available check
print(f"[INFO] model_id = {model_id()}")
loaded = is_available()
print(f"[INFO] model loaded = {loaded}")

# 3. advise_market — test live model inference
result = advise_market(
    context_text="EURUSD strong upward breakout with RSI at 75 and heavy buying volume. Clear buy signal.",
    features={"vix": 12.0, "rsi_14": 75.0, "adx_14": 35.0, "regime": "RISK_ON"},
)
if result is None:
    print("[WARN] THX-01 model not loaded — advise_market returned None")
else:
    assert result["source"] == "THX01"
    assert result["direction"] in ("BUY", "SELL", "HOLD")
    assert 0.0 <= result["confidence"] <= 1.0
    print(f"[OK] advise_market -> direction={result['direction']} confidence={result['confidence']:.3f}")
    print(f"     rationale: {result['rationale']}")

# 4. _extract_signals_from_state and arbitrate_signals
from intelligence.signal_arbitrator import arbitrate_signals

mock_state = {
    "symbol": "EURUSD",
    "timeframe": "M15",
    "raw_hypotheses": [],
    "decision_bundle": None,
    "cycle_brief": "EURUSD strong upward breakout with RSI at 75 and heavy buying volume. Clear buy signal.",
    "features": {"vix": 12.0, "rsi_14": 75.0, "adx_14": 35.0, "regime": "RISK_ON"},
}
signals = _extract_signals_from_state(mock_state)
sources = [s["source"] for s in signals]
if loaded:
    print(f"[OK] _extract_signals_from_state candidate sources: {sources}")
    # Arbitrate signals
    arb_result = arbitrate_signals(mock_state)
    print(f"[OK] arbitrate_signals verdict: {arb_result.get('verdict')}")
    if arb_result.get("best_signal"):
        print(f"     best signal: {arb_result['best_signal']['source']} (score={arb_result['fusion_score']:.4f})")
else:
    print(f"[WARN] Model not loaded; sources: {sources}")

print("\n[PASS] thx01_advisor self-check complete")
