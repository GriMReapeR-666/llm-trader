"""CycleBrief schema + persist (advisory observability only)."""
from intelligence.cycle_brief import SCHEMA_VERSION, build, persist, load_latest


class _Store:
    def __init__(self):
        self.states = {}

    def set_state(self, key, value):
        self.states[key] = value

    def get_state(self, key, default=None):
        return self.states.get(key, default)


def test_cycle_brief_schema_stable():
    toon = {
        "intermarket_regime": {"regime_id": "RISK_ON", "confidence": 0.7, "status": "OK"},
        "macro_causal_context_regime": [
            {"edge_id": 1, "relation": "STRENGTHENS", "sign": "POSITIVE", "confidence": 0.8,
             "source_name": "CPI", "target_name": "USD", "status": "APPROVED"},
        ],
        "feature_snapshot": {"ready": True, "completed_bar_time": "2026-08-12T10:00:00+00:00"},
        "technical_analysis": {"primary_trend": "BULLISH", "confluence": 3},
        "event_state": {"blackout": False, "reasons": []},
        "opportunity_bus": {"status": "EMPTY", "candidate_ids": []},
        "macro_opportunity_bus": {"status": "OK", "candidate_ids": ["e:1"]},
        "account": {"equity": 10000, "balance": 10000, "margin_free": 9000},
    }
    brief = build(
        toon,
        symbol="EURUSD",
        timeframe="H1",
        strategy="swing",
        cycle_key="swing:EURUSD:H1:bar",
        kill_switch_active=False,
        open_reviews=[{"intent_key": "x", "status": "OPEN"}],
        fusion={"status": "PREVIEW", "score": 0.4},
        propagation={
            "path_id": "cpath:abc", "bias": "BUY", "score": 0.5,
            "horizon_seconds": 3600, "hops": [],
        },
    )
    assert brief["schema_version"] == SCHEMA_VERSION
    assert brief["execution_authority"] is False
    assert brief["regime"]["regime_id"] == "RISK_ON"
    assert brief["drivers"][0]["edge_id"] == 1
    assert brief["working_edge"]["propagation"]["path_id"] == "cpath:abc"
    assert brief["open_review_tasks"][0]["intent_key"] == "x"


def test_cycle_brief_persist_and_load():
    store = _Store()
    brief = build({}, symbol="XAUUSD", timeframe="M15", strategy="scalp", cycle_key="k1")
    result = persist(store, brief)
    assert result["persisted"] is True
    loaded = load_latest(store, symbol="XAUUSD", strategy="scalp")
    assert loaded["status"] == "OK"
    assert loaded["symbol"] == "XAUUSD"
    assert loaded["execution_authority"] is False
