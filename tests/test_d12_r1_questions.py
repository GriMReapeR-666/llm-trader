"""D12 narrative decay + R1 headline event routing: typed-question tests."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))

import pytest

from data.news_triage import (
    TRIAGE_QUESTION_IDS,
    _deterministic_r1,
    summarize_cycle_evidence,
    triage_article,
)
from research.decision_models.extended_taxonomy import (
    D12_NarrativeDecay,
    R1_HeadlineEventRouting,
    TAXONOMY_REGISTRY,
    create_decision_bundle,
)
from research.decision_models.feature_block import DecisionFeatureBlock
from research.decision_models.macro_ontology import get_standard_macro_questions
from research.decision_models.deterministic_labeler_expanded import (
    label_d12_narrative_decay,
)


class _FB:
    """Minimal attr-bag feature object for labeler unit tests."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# Registration: the new typed questions exist everywhere they must.
# ---------------------------------------------------------------------------

def test_questions_registered_in_standard_set():
    q = get_standard_macro_questions()
    assert "D12_narrative_decay" in q
    assert "R1_headline_event_routing" in q
    assert q["D12_narrative_decay"]["type"] == "choice"
    assert q["R1_headline_event_routing"]["type"] == "choice"
    assert "TRIPWIRE_INVALIDATED" in q["D12_narrative_decay"]["criteria"]
    assert "NOT_EVENT_NOISE" in q["R1_headline_event_routing"]["criteria"]


def test_questions_registered_in_taxonomy_registry():
    assert TAXONOMY_REGISTRY["D12_narrative_decay"]["enum_cls"] is D12_NarrativeDecay
    assert TAXONOMY_REGISTRY["D12_narrative_decay"]["default_abstain"] == "INSUFFICIENT_DATA"
    assert TAXONOMY_REGISTRY["R1_headline_event_routing"]["enum_cls"] is R1_HeadlineEventRouting
    assert TAXONOMY_REGISTRY["R1_headline_event_routing"]["default_abstain"] == "INSUFFICIENT_DATA"


def test_r1_in_triage_question_ids():
    assert "R1_headline_event_routing" in TRIAGE_QUESTION_IDS


# ---------------------------------------------------------------------------
# D12 deterministic bands.
# ---------------------------------------------------------------------------

def test_d12_bands_follow_decay_factor():
    base = dict(narrative_evidence_for=2, narrative_evidence_against=0)
    assert label_d12_narrative_decay(_FB(narrative_decay_factor=0.9, **base)) == D12_NarrativeDecay.FRESH_CONFIRMED
    assert label_d12_narrative_decay(_FB(narrative_decay_factor=0.5, **base)) == D12_NarrativeDecay.ACTIVE_FRESH
    assert label_d12_narrative_decay(_FB(narrative_decay_factor=0.2, **base)) == D12_NarrativeDecay.STALE_FADING
    assert label_d12_narrative_decay(_FB(narrative_decay_factor=0.05, **base)) == D12_NarrativeDecay.EXHAUSTED_PRICED


def test_d12_invalidated_and_tripwire_precedence():
    assert label_d12_narrative_decay(_FB(
        narrative_decay_factor=0.9, narrative_is_invalidated=True,
    )) == D12_NarrativeDecay.TRIPWIRE_INVALIDATED
    assert label_d12_narrative_decay(_FB(
        narrative_decay_factor=0.9, narrative_tripwire_hit=True,
    )) == D12_NarrativeDecay.TRIPWIRE_INVALIDATED
    assert label_d12_narrative_decay(_FB(
        narrative_decay_factor=0.9, tripwire_hit=True,
    )) == D12_NarrativeDecay.TRIPWIRE_INVALIDATED


def test_d12_negative_evidence_demotes_one_band():
    fb = _FB(narrative_decay_factor=0.9, narrative_evidence_for=1, narrative_evidence_against=3)
    assert label_d12_narrative_decay(fb) == D12_NarrativeDecay.ACTIVE_FRESH
    # Positive balance never demotes.
    fb2 = _FB(narrative_decay_factor=0.9, narrative_evidence_for=3, narrative_evidence_against=1)
    assert label_d12_narrative_decay(fb2) == D12_NarrativeDecay.FRESH_CONFIRMED


def test_d12_abstains_without_decay_metrics():
    assert label_d12_narrative_decay(_FB()) == D12_NarrativeDecay.INSUFFICIENT_DATA
    assert label_d12_narrative_decay(_FB(narrative_decay_factor="garbage")) == D12_NarrativeDecay.INSUFFICIENT_DATA


def test_d12_record_in_decision_bundle():
    fb = DecisionFeatureBlock(
        event_name="TEST_EVENT",
        narrative_decay_factor=0.85,
        narrative_age_days=3.0,
        narrative_evidence_for=4,
        narrative_evidence_against=0,
    )
    bundle = create_decision_bundle(features=fb)
    rec = bundle.get("D12_narrative_decay")
    assert rec is not None
    assert rec.rule_label == D12_NarrativeDecay.FRESH_CONFIRMED.value
    assert rec.execution_authority is False


# ---------------------------------------------------------------------------
# Feature block: decay + article_evidence prompt-state plumbing.
# ---------------------------------------------------------------------------

def test_feature_block_decay_in_prompt_state():
    fb = DecisionFeatureBlock(
        event_name="TEST",
        narrative_decay_factor=0.5,
        narrative_age_days=12.0,
        narrative_evidence_for=3,
        narrative_evidence_against=1,
    )
    nd = fb.to_prompt_state()["narrative_decay"]
    assert nd["decay_factor"] == 0.5
    assert nd["age_days"] == 12.0
    assert nd["evidence_for"] == 3 and nd["evidence_against"] == 1
    assert nd["tripwire_hit"] is False

    fb_none = DecisionFeatureBlock(event_name="TEST")
    assert "narrative_decay" not in fb_none.to_prompt_state()


def test_feature_block_accepts_article_evidence():
    """Regression: DecisionFeatureBlock previously lacked this field entirely."""
    fb = DecisionFeatureBlock(event_name="TEST", article_evidence={"quality": "HIGH", "total": 2})
    assert fb.to_prompt_state()["article_evidence"] == {"quality": "HIGH", "total": 2}
    assert "article_evidence" not in DecisionFeatureBlock(event_name="TEST").to_prompt_state()


# ---------------------------------------------------------------------------
# R1 deterministic routing bands.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("CPI rises faster than expected", "EVENT_CPI"),
    ("PCE print cools", "EVENT_CPI"),
    ("Nonfarm payrolls blowout", "EVENT_NFP"),
    ("Jobless claims fall", "EVENT_NFP"),
    ("FOMC holds rates steady", "EVENT_CB_MEETING"),
    ("Powell testimony before senate", "EVENT_CB_MEETING"),
    ("Missile strike hits refinery", "EVENT_GEO_SHOCK"),
    ("Naval blockade announced", "EVENT_GEO_SHOCK"),
    ("SVB collapse sparks deposit flight", "EVENT_BANKING_STRESS"),
    ("Tariffs on steel doubled", "EVENT_TRADE_WAR"),
    ("Impeachment vote scheduled", "EVENT_POLITICAL"),
    ("PBOC gold buying accelerates", "EVENT_CB_GOLD"),
    ("Analysts mull quarterly outlook", "NOT_EVENT_NOISE"),
])
def test_r1_deterministic_bands(title, expected):
    assert _deterministic_r1({"title": title}) == expected


def test_r1_is_case_insensitive_and_falls_back_to_summary():
    assert _deterministic_r1({"title": "cpi surprise"}) == "EVENT_CPI"
    assert _deterministic_r1({"title": "", "text_or_summary": "The ECB raised rates"}) == "EVENT_CB_MEETING"
    assert _deterministic_r1({}) == "NOT_EVENT_NOISE"


# ---------------------------------------------------------------------------
# R1 post-validation floor: model may add a class, never erase one.
# ---------------------------------------------------------------------------

class _FakeLaya:
    def __init__(self, answers, probs=None):
        self._answers = answers
        self._probs = probs or {}

    def decide(self, state, questions):
        class _R:
            pass
        r = _R()
        r.answers = dict(self._answers)
        r.probabilities = {q: dict(self._probs.get(q, {})) for q in questions}
        r.confidences = {q: 0.9 for q in questions}
        return r


def _reuters_article(key, title, summary="context"):
    return {
        "title": title,
        "text_or_summary": summary,
        "url": f"https://www.reuters.com/{key}",
        "publisher": "Reuters",
        "provider": "reuters",
        "dedupe_key": key,
    }


def test_r1_model_cannot_erase_concrete_class():
    art = _reuters_article("k1", "CPI surges 0.4 percent")
    v = triage_article(art, laya=_FakeLaya({"R1_headline_event_routing": "NOT_EVENT_NOISE"}))
    assert v.r1 == "EVENT_CPI"
    assert "R1_headline_event_routing" in v.disagreement_ids  # logged for learning


def test_r1_model_adds_class_lexical_router_missed():
    art = _reuters_article("k2", "Quarterly outlook piece")
    v = triage_article(
        art,
        laya=_FakeLaya(
            {"R1_headline_event_routing": "EVENT_CPI"},
            probs={"R1_headline_event_routing": {"event_cpi": 0.8}},
        ),
    )
    assert v.r1 == "EVENT_CPI"


def test_r1_model_noise_on_det_noise_stays_noise():
    art = _reuters_article("k3", "Totally unrelated analysis")
    v = triage_article(art, laya=_FakeLaya({"R1_headline_event_routing": "NOT_EVENT_NOISE"}))
    assert v.r1 == "NOT_EVENT_NOISE"
    assert "R1_headline_event_routing" not in v.disagreement_ids


def test_r1_low_probability_model_route_defers_to_det():
    art = _reuters_article("k4", "Quarterly outlook piece")
    v = triage_article(
        art,
        laya=_FakeLaya(
            {"R1_headline_event_routing": "EVENT_CPI"},
            probs={"R1_headline_event_routing": {"event_cpi": 0.3}},
        ),
    )
    assert v.r1 == "NOT_EVENT_NOISE"


def test_r1_serialized_in_verdict_dict(monkeypatch):
    # No explicit laya: pin mode off so the deterministic path never loads
    # the real 421M checkpoint.
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "off")
    art = _reuters_article("k5", "FOMC minutes released")
    v = triage_article(art)
    assert v.to_dict()["r1"] == "EVENT_CB_MEETING"


# ---------------------------------------------------------------------------
# Cycle summary inherits R1 counts.
# ---------------------------------------------------------------------------

def _triaged(key, title, r1):
    return {"title": title, "triage": {"action": "INGEST", "r1": r1, "confidence": 0.9}}


def test_summary_counts_r1_classes_and_dominant():
    arts = [
        _triaged("a", "CPI 1", "EVENT_CPI"),
        _triaged("b", "CPI 2", "EVENT_CPI"),
        _triaged("c", "NFP", "EVENT_NFP"),
        _triaged("d", "noise", "NOT_EVENT_NOISE"),
    ]
    s = summarize_cycle_evidence(arts)
    assert s["r1_class_counts"] == {"EVENT_CPI": 2, "EVENT_NFP": 1}
    assert s["dominant_event_class"] == "EVENT_CPI"


def test_summary_handles_legacy_verdicts_without_r1():
    arts = [{"title": "old", "triage": {"action": "INGEST", "confidence": 0.5}}]
    s = summarize_cycle_evidence(arts)
    assert s["r1_class_counts"] == {}
    assert s["dominant_event_class"] is None


# ---------------------------------------------------------------------------
# MacroAnalystNode passes narrative decay into the feature block.
# ---------------------------------------------------------------------------

def test_macro_analyst_passes_narrative_decay(monkeypatch):
    import intelligence.graph_nodes as gn
    from intelligence.graph_nodes import MacroAnalystNode

    class _Tripwire:
        is_triggered = False

    class _Narrative:
        decay_factor = 0.62
        narrative_age_days = 9.5
        evidence_for = ["e1", "e2", "e3"]
        evidence_against = ["a1"]
        tripwires = [_Tripwire()]
        is_invalidated = False

    class _SM:
        def decay_step(self):
            pass

        def active_narratives(self):
            return [_Narrative()]

    node = MacroAnalystNode.__new__(MacroAnalystNode)
    monkeypatch.setattr(gn, "require_agent_action", lambda *a, **k: None, raising=False)

    captured = {}

    def fake_build(symbol, features, snapshot, **kwargs):
        captured["decay"] = kwargs.get("narrative_decay")
        class _FB:
            pass
        return _FB()

    node._build_feature_block = fake_build
    node._evaluate_decision = lambda fb: None
    node.macro_graph = type("G", (), {"summary": staticmethod(lambda: {})})()
    node.graphrag = type("R", (), {
        "find_causal_paths": staticmethod(lambda **k: []),
        "format_cited_prompt_context": staticmethod(lambda *a, **k: ""),
    })()
    node.meta_portfolio = type("MP", (), {"evaluate": staticmethod(lambda **k: {"regime": {}, "allocation": {"master_expectancy_r": 0.25}})})()
    node.narrative_sm = _SM()
    node.causal_extractor = None

    monkeypatch.setattr(gn, "ui_state", type("UI", (), {
        "macro_regime": "", "net_liquidity_stance": "", "last_decision_bundle": None,
        "active_narrative_summary": "", "active_tripwires_status": [],
        "last_causal_attribution": None, "news_evidence_summary": None,
    })())

    state = {"symbol": "EURUSD", "feature_vector": {}, "market_snapshot": {}}
    try:
        MacroAnalystNode.__call__(node, state)
    except Exception:
        pass  # downstream stubs may fail; capture point already passed

    nd = captured.get("decay")
    assert isinstance(nd, dict)
    assert nd["decay_factor"] == pytest.approx(0.62)
    assert nd["age_days"] == pytest.approx(9.5)
    assert nd["evidence_for"] == 3 and nd["evidence_against"] == 1
    assert nd["tripwire_hit"] is False and nd["is_invalidated"] is False
