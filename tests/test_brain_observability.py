"""Tests for Brain observability and MacroStateVector."""
from intelligence.macro_state_vector import build_macro_state_vector
from intelligence.brain_observability import (
    memory_health,
    economic_dynamics_health,
    full_observability,
)
from intelligence.trading_memory import TradingMemory


def test_macro_state_vector_from_observations_and_regime():
    vector = build_macro_state_vector(
        macro_observations=[
            {"entity": "POLICY_RATE", "value": 5.25, "surprise": 0.1, "provider": "FRED"},
            {"entity": "INFLATION", "value": 3.1, "surprise": -0.2, "provider": "FRED"},
        ],
        intermarket_regime={
            "regime_id": "CRISIS_PANIC",
            "confidence": 0.8,
            "advisory_bias": "RISK_OFF",
            "status": "OK",
        },
        opportunity_fusion={"status": "PASS", "score": 0.7},
        causal_propagation={"status": "OK", "path_id": "cpath:x", "bias": "BUY", "score": 0.4},
        legacy_news_score=1,
    )
    assert vector["execution_authority"] is False
    assert vector["macro_bias"] in {"HAWKISH", "DOVISH", "NEUTRAL"}
    assert -1.0 <= vector["composite_score"] <= 1.0
    assert vector["regime"]["regime_id"] == "CRISIS_PANIC"
    assert vector["fusion_status"] == "PASS"
    assert vector["causal_path_id"] == "cpath:x"


def test_memory_health_includes_review_queue(tmp_path):
    path = str(tmp_path / "brain.db")
    mem = TradingMemory(path)
    mem.save_narrative({"k": 1}, "review me", status="CANDIDATE")
    payload = memory_health(journal_path=path, limit=5)
    assert payload["execution_authority"] is False
    assert payload["review_queue"]["status"] == "REVIEW_REQUIRED"
    assert payload["review_queue"]["counts"]["narrative"] >= 1


def test_economic_dynamics_health_lists_central_banks():
    payload = economic_dynamics_health()
    assert payload["execution_authority"] is False
    assert payload["status"] == "OK"
    aliases = payload["central_bank_aliases"]
    assert "FED" in aliases
    assert "ECB" in aliases
    assert "RBA" in aliases


def test_full_observability_shape(tmp_path):
    path = str(tmp_path / "full.db")
    TradingMemory(path)
    payload = full_observability(journal_path=path)
    assert payload["execution_authority"] is False
    assert "memory" in payload
    assert "regime" in payload
    assert "causal_edges" in payload
    assert "macro_observations" in payload
    assert "economic_dynamics" in payload
    assert "realtime_news" in payload
    assert payload["realtime_news"]["execution_authority"] is False


def test_realtime_news_summary_reads_store(tmp_path):
    from core.trading_safety import TradingStore
    from intelligence.brain_observability import realtime_news_summary

    path = str(tmp_path / "news_state.db")
    store = TradingStore(path)
    store.set_state("last_realtime_news_ingest", {
        "status": "PROCESSED",
        "captured_at": "2026-08-14T00:00:00+00:00",
        "article_count": 3,
        "ingest": {"ingested": 2, "skipped": 1, "classified": [{"event_class": "TRADE_WAR"}]},
        "extraction": {"status": "SKIPPED_NO_LLM", "edge_ids": []},
        "macro_entities_extra": ["TRADE_WAR", "EM_FX"],
        "execution_authority": False,
    })
    payload = realtime_news_summary(journal_path=path)
    assert payload["status"] == "PROCESSED"
    assert payload["ingested"] == 2
    assert "TRADE_WAR" in payload["macro_entities_extra"]
    assert payload["execution_authority"] is False
