"""Multi-hop causal propagation with lag attenuation."""
from intelligence.causal_propagate import propagate


def test_propagate_scores_path_with_attenuation():
    edges = [
        {
            "edge_id": 1, "source_name": "CPI", "target_name": "POLICY_RATE",
            "sign": "POSITIVE", "confidence": 0.9, "decay_half_life_seconds": 10000,
        },
        {
            "edge_id": 2, "source_name": "POLICY_RATE", "target_name": "USD",
            "sign": "POSITIVE", "confidence": 0.8, "decay_half_life_seconds": 10000,
        },
    ]
    result = propagate(edges, seed_entities=["CPI"], symbol="EURUSD", age_seconds=0)
    assert result["status"] == "OK"
    assert result["path_id"]
    assert result["bias"] == "BUY"
    assert result["score"] > 0
    assert len(result["hops"]) >= 1
    assert result["execution_authority"] is False


def test_propagate_no_path_when_empty():
    result = propagate([], seed_entities=["CPI"], symbol="EURUSD")
    assert result["status"] == "NO_PATH"
    assert result["path_id"] is None
    assert result["execution_authority"] is False


def test_propagate_age_reduces_score():
    edges = [{
        "edge_id": 9, "source_name": "CPI", "target_name": "USD",
        "sign": "NEGATIVE", "confidence": 1.0, "decay_half_life_seconds": 100.0,
    }]
    fresh = propagate(edges, seed_entities=["CPI"], age_seconds=0)
    stale = propagate(edges, seed_entities=["CPI"], age_seconds=500)
    assert fresh["score"] > stale["score"]
    assert stale["bias"] == "SELL"
