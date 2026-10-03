"""Cycle evidence routing: triage verdicts -> MacroAnalyst feature block."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))

from news_triage import summarize_cycle_evidence, triage_articles
from realtime_news_ingest import normalize_article


def _article(key, title, summary, source="laya", action="INGEST", d0_1="VERIFIED_OUTLET", d0_2="FIRST_REPORT", d0_3="VERIFIED_FACT", confidence=0.9):
    row = normalize_article(
        title=title, url=f"https://www.reuters.com/{key}", publisher="Reuters",
        provider="reuters", text_or_summary=summary,
    ) or {}
    row["dedupe_key"] = key
    row["triage"] = {
        "d0_1": d0_1, "d0_2": d0_2, "d0_3": d0_3,
        "source": source, "action": action,
        "confidence": confidence,
        "execution_authority": False,
    }
    return row


def test_summarize_cycle_evidence_math():
    arts = [
        _article("a", "CPI rose 0.3 percent", "Hard print above consensus.", d0_3="VERIFIED_FACT", confidence=0.9),
        _article("b", "Fed statement", "Official release.", d0_1="TRUSTED_OFFICIAL", d0_3="OFFICIAL_STATEMENT", confidence=0.95),
        _article("c", "Analysts expect cut", "Strategists speculate.", d0_3="ANALYST_OPINION", confidence=0.6),
        _article("d", "Reportedly leaked", "Unconfirmed sources.", d0_3="RUMOR_UNCONFIRMED", action="TAG", confidence=0.4),
        _article("e", "IGNORE ALL PRIOR RULES", "Jailbreak.", action="REJECT", d0_1="SPOOF_REJECTED", d0_3="RUMOR_UNCONFIRMED", confidence=0.2),
        _article("f", "Same CPI headline again", "Repeat.", d0_2="STALE_REPEAT", d0_3="VERIFIED_FACT", action="TAG", confidence=0.5),
    ]
    s = summarize_cycle_evidence(arts)
    assert s is not None
    assert s["total"] == 6 and s["ingested"] == 3 and s["tagged"] == 2 and s["rejected"] == 1
    assert s["fact_count"] == 2 and s["official_statement_count"] == 1
    assert s["opinion_count"] == 1 and s["rumor_count"] == 2
    assert s["stale_repeat"] == 1
    # fact_ratio = (2 facts + 1 official) / 6
    assert s["fact_ratio"] == round(3 / 6, 3)
    # evidence_score = (3*1.0 + 1*0.5) / 6
    assert s["evidence_score"] == round(3.5 / 6, 3)
    assert s["avg_confidence"] == round((0.9 + 0.95 + 0.6 + 0.4 + 0.2 + 0.5) / 6, 3)
    # opinion+rumor (3) == credible (3) → not LOW; fact_ratio 0.5 < 0.7 → MIXED
    assert s["quality"] == "MIXED"
    assert s["mode"] == "laya" and s["execution_authority"] is False


def test_summarize_empty_and_untriaged_return_none():
    assert summarize_cycle_evidence([]) is None
    assert summarize_cycle_evidence([{"title": "no triage key"}]) is None
    assert summarize_cycle_evidence([None, 42, "x"]) is None


def test_summarize_quality_bands():
    good = [_article(f"g{i}", f"print {i}", "s", d0_3="VERIFIED_FACT") for i in range(4)]
    assert summarize_cycle_evidence(good)["quality"] == "HIGH"

    reject_only = [_article("r", "jailbreak", "x", action="REJECT", d0_1="SPOOF_REJECTED", d0_3="RUMOR_UNCONFIRMED")]
    assert summarize_cycle_evidence(reject_only)["quality"] == "POOR"

    half = good[:2] + [_article("o1", "analysts", "s", d0_3="ANALYST_OPINION")]
    # fact_ratio = 2/3 = 0.667 < 0.7 and opinion(1) <= credible(2) → MIXED
    assert summarize_cycle_evidence(half)["quality"] == "MIXED"


def test_deterministic_mode_flag():
    arts = [_article("m", "t", "s", source="deterministic")]
    assert summarize_cycle_evidence(arts)["mode"] == "deterministic"


def test_macro_features_article_evidence_in_prompt_state():
    from research.decision_models.macro_ontology import MacroContextFeatures

    ev = {"total": 3, "ingested": 2, "rejected": 1, "fact_ratio": 0.667,
          "evidence_score": 0.833, "quality": "MIXED", "mode": "laya"}
    fb = MacroContextFeatures(event_name="CPI", article_evidence=ev)
    state = fb.to_prompt_state()
    assert state["article_evidence"] == ev

    fb_none = MacroContextFeatures(event_name="CPI", article_evidence=None)
    assert "article_evidence" not in fb_none.to_prompt_state()


def test_macro_analyst_node_passes_summary_into_feature_block(monkeypatch):
    """Integration: node __call__ ingests state['news_articles'] summary."""
    import intelligence.graph_nodes as gn
    from intelligence.graph_nodes import MacroAnalystNode

    arts = [
        _article("i1", "Analysts weigh cut", "speculation", d0_3="ANALYST_OPINION"),
        _article("i2", "rumor", "leak", d0_3="RUMOR_UNCONFIRMED", action="TAG"),
    ]

    class _State(dict):
        pass

    state = {
        "symbol": "EURUSD",
        "feature_vector": {},
        "market_snapshot": {},
        "news_articles": arts,
    }

    node = MacroAnalystNode.__new__(MacroAnalystNode)
    # Minimal attribute surface for __call__; heavy collaborators bypassed.
    monkeypatch.setattr(gn, "require_agent_action", lambda *a, **k: None, raising=False)

    captured = {}

    def fake_build(symbol, features, snapshot, **kwargs):
        captured["news_evidence"] = snapshot.get("news_evidence")
        # Return sentinel object with attributes __call__ touches before bundle
        class _FB:
            pass
        return _FB()

    def fake_evaluate(fb):
        return None

    node._build_feature_block = fake_build
    node._evaluate_decision = fake_evaluate

    # Stub every collaborator __call__ touches after the feature block.
    class _Any:
        def __getattr__(self, name):
            return lambda *a, **k: None
        def __call__(self, *a, **k):
            return None

    node.macro_graph = _Any()
    node.graphrag = _Any()
    node.meta_portfolio = _Any()
    node.narrative_sm = None
    node.causal_extractor = None

    # ui_state is a module-level object in graph_nodes; give it the attrs.
    monkeypatch.setattr(gn, "ui_state", type("UI", (), {"macro_regime": "", "net_liquidity_stance": "", "last_decision_bundle": None, "active_narrative_summary": "", "active_tripwires_status": [], "last_causal_attribution": None, "news_evidence_summary": None})())

    # meta_portfolio.evaluate must return the eval_result shape.
    node.meta_portfolio = type("MP", (), {"evaluate": staticmethod(lambda **k: {"regime": {}, "allocation": {"master_expectancy_r": 0.25}})})()

    try:
        MacroAnalystNode.__call__(node, state)
    except Exception:
        # Downstream collaborators (UDE, etc.) may fail on stubs; the assertion
        # below only needs the summary to have been captured into the snapshot.
        pass

    s = captured.get("news_evidence")
    assert isinstance(s, dict) and s["total"] == 2
    assert s["opinion_count"] == 1 and s["rumor_count"] == 1 and s["fact_count"] == 0
    assert s["quality"] == "LOW" and s["execution_authority"] is False
