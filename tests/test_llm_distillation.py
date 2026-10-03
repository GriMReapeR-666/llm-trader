import json
from intelligence.llm_orchestrator import LLMOrchestrator


def test_round_floats_compaction():
    sample_data = {
        "price": 1.0845392817491823,
        "rsi": 54.12849582910385,
        "nested": {
            "atr": 0.0015948392019,
            "probabilities": [0.3333333333333333, 0.6666666666666666],
        },
        "name": "EURUSD",
        "valid": True,
    }

    compacted = LLMOrchestrator._round_floats(sample_data, precision=5)
    assert compacted["price"] == 1.08454
    assert compacted["rsi"] == 54.1285
    assert compacted["nested"]["atr"] == 0.00159
    assert compacted["nested"]["probabilities"] == [0.33333, 0.66667]
    assert compacted["name"] == "EURUSD"
    assert compacted["valid"] is True

    # Check that compact serialization has no formatting spaces
    raw_json = json.dumps(compacted, separators=(",", ":"))
    assert " " not in raw_json
    # Length is significantly reduced compared to standard unrounded dumps
    standard_json = json.dumps(sample_data)
    assert len(raw_json) < len(standard_json)
