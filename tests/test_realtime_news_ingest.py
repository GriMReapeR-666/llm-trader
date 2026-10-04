"""Realtime news ingest: policy, classification, graph ingest (no order authority)."""
import json

import pytest

from intelligence.macro_graph import MacroGraph
from intelligence.macro_source_policy import assess_source
from data.realtime_news_ingest import (
    ingest_articles,
    normalize_article,
    watch_entities_for_event_class,
)
from intelligence.macro_extraction import extract_candidates, persist_candidates
from intelligence.trading_memory import TradingMemory


class _LLM:
    def generate(self, messages, model=None):
        return json.dumps({
            "principles": [{"text": "Tariffs pressure EM FX", "confidence": 0.5, "source_span": "Tariffs pressure EM FX"}],
            "causal_relationships": [{
                "source_entity": "TRADE_WAR",
                "source_type": "EVENT_CLASS",
                "target_entity": "EM_FX",
                "target_type": "CURRENCY",
                "relation": "PRESSURES",
                "sign": "NEGATIVE",
                "confidence": 0.4,
                "source_span": "Tariffs pressure EM FX",
                "uncertainty": "hypothesis",
            }],
            "historical_precedents": [],
            "uncertainties": [],
        })


def test_source_policy_allows_reuters_rejects_social():
    assert assess_source("https://www.reuters.com/world/us/", "Reuters")["allowed"] is True
    assert assess_source("https://seekingalpha.com/news/123", "Seeking Alpha")["allowed"] is True
    assert assess_source("https://www.federalreserve.gov/newsevents/press.htm", "Federal Reserve")["allowed"] is True
    assert assess_source("https://truthsocial.com/@user/post", "Truth Social")["allowed"] is False
    assert assess_source("https://x.com/user/status/1", "Twitter")["allowed"] is False
    assert assess_source("https://random-blog.example/article", "unknown")["allowed"] is False


def test_normalize_classifies_tariff_as_trade_war():
    row = normalize_article(
        title="US announces new tariff package on China imports",
        url="https://www.reuters.com/world/trade/tariffs",
        publisher="Reuters",
        provider="reuters",
    )
    assert row is not None
    assert row["event_class"] == "TRADE_WAR"
    assert row["execution_authority"] is False


def test_ingest_articles_creates_realtime_sources(tmp_path):
    graph = MacroGraph(str(tmp_path / "news.db"))
    articles = [
        normalize_article(
            title="Fed holds rates steady",
            url="https://www.federalreserve.gov/newsevents/press.htm",
            publisher="Federal Reserve",
            text_or_summary="The Federal Reserve held rates steady at today's meeting.",
            provider="federalreserve",
        ),
        normalize_article(
            title="Random blog post",
            url="https://evil-blog.example/x",
            publisher="unknown",
            provider="blog",
        ),
    ]
    result = ingest_articles(graph, [a for a in articles if a])
    assert result["ingested"] == 1
    assert result["skipped"] == 1
    assert result["execution_authority"] is False
    with graph._con() as con:
        rows = con.execute("SELECT payload FROM macro_sources").fetchall()
    assert any("realtime" in str(r[0]) for r in rows)


def test_watch_entities_for_event_class_expands_watchlist():
    ents = watch_entities_for_event_class("WAR_SHOCK")
    assert "WAR_SHOCK" in ents
    assert "WTI_OIL" in ents or "GOLD" in ents


def test_extraction_blocked_for_unverified_before_llm():
    with pytest.raises(ValueError, match="source policy"):
        extract_candidates(
            _LLM(),
            source_id="s1",
            source_title="Bad",
            source_text="Some long enough text for extraction to attempt parsing here.",
            source_url="https://evil-blog.example/x",
            publisher="unknown",
        )


def test_extraction_persists_candidate_edges(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro2.db"))
    memory = TradingMemory(str(tmp_path / "journal.db"))
    source_id = graph.ingest_source(
        title="Reuters tariff headline",
        payload={"kind": "realtime_news"},
        source_url="https://www.reuters.com/world/trade/tariffs",
        publisher="Reuters",
    )
    policy = assess_source("https://www.reuters.com/world/trade/tariffs", "Reuters")
    candidates = extract_candidates(
        _LLM(),
        source_id=source_id,
        source_title="Tariff headline",
        source_text="Tariffs pressure EM FX according to analysts covering the trade war.",
        source_url="https://www.reuters.com/world/trade/tariffs",
        publisher="Reuters",
    )
    persisted = persist_candidates(graph, memory, candidates)
    assert persisted["edge_ids"]
    assert persisted["execution_authority"] is False
