import json

import pytest

from macro_extraction import extract_candidates, persist_candidates
from macro_graph import MacroGraph
from macro_source_policy import assess_source
from trading_memory import TradingMemory


class _LLM:
    def generate(self, messages, model=None):
        return '{"principles":[{"text":"Rates matter","confidence":0.6,"source_span":"Rates matter"}],"causal_relationships":[],"historical_precedents":[],"uncertainties":[]}'


def test_source_policy_allows_primary_and_runtime_feeds_only():
    assert assess_source("https://www.federalreserve.gov/release", "")['allowed'] is True
    assert assess_source("https://www.reuters.com/world/", "Reuters")['allowed'] is True
    assert assess_source("https://seekingalpha.com/news/1", "Seeking Alpha")['allowed'] is True
    assert assess_source("", "runtime calendar")['tier'] == "RUNTIME_FEED"
    assert assess_source("https://truthsocial.com/@x", "Truth Social")['allowed'] is False
    assert assess_source("https://example.invalid/news", "unknown")['allowed'] is False


def test_source_policy_malformed_identity_fails_closed_without_exception():
    result = assess_source(None, publisher=None, rights_note=None)
    assert result["allowed"] is False
    assert result["tier"] == "UNVERIFIED"


def test_source_policy_malformed_url_cannot_become_primary():
    result = assess_source("http://[malformed", publisher="Federal Reserve")
    assert result["allowed"] is False


def test_macro_extraction_rejects_unverified_sources_before_llm_call():
    with pytest.raises(ValueError, match="source policy"):
        extract_candidates(
            _LLM(), source_id="source-1", source_title="Unknown", source_text="text",
            source_url="https://example.invalid/article", publisher="unknown",
        )


def test_candidate_persistence_requires_source_policy_metadata(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    memory = TradingMemory(str(tmp_path / "journal.db"))
    with pytest.raises(ValueError, match="source policy"):
        persist_candidates(graph, memory, {"source_id": "source-1", "principles": []})


def test_candidate_facts_retain_source_span_and_rights_provenance(tmp_path):
    graph = MacroGraph(str(tmp_path / "macro.db"))
    memory = TradingMemory(str(tmp_path / "journal.db"))
    source_id = graph.ingest_source(
        title="Official release", payload={"value": 1},
        source_url="https://www.federalreserve.gov/release", publisher="Federal Reserve",
    )
    policy = assess_source("https://www.federalreserve.gov/release", "Federal Reserve")
    result = persist_candidates(graph, memory, {
        "source_id": source_id,
        "source_title": "Official release",
        "source_url": "https://www.federalreserve.gov/release",
        "publisher": "Federal Reserve",
        "rights_note": policy["retention"],
        "source_policy": policy,
        "principles": [{"text": "Rates matter", "source_span": "Rates matter", "confidence": 0.5}],
        "causal_relationships": [], "historical_precedents": [], "uncertainties": [],
    })
    assert result["fact_ids"]
    with memory._con() as con:
        row = con.execute("SELECT fact_value FROM atomic_facts WHERE id=?", (result["fact_ids"][0],)).fetchone()
    value = json.loads(row[0])
    assert value["source_span"] == "Rates matter"
    assert value["source_policy"]["tier"] == "PRIMARY"
    assert value["extractor"] == "macro-extraction-v1"
