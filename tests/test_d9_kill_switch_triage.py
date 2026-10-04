"""D9 emergency kill-switch: live noul tripwire over the streaming news feed."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))

import pytest

from data.news_triage import (
    D9_KILL_SWITCH_ACTIVE,
    D9_KILL_SWITCH_NORMAL,
    D9_KILL_SWITCH_NO_DATA,
    TRIAGE_QUESTION_IDS,
    _deterministic_d9,
    summarize_cycle_evidence,
    triage_article,
)
from research.decision_models.extended_taxonomy import (
    D9_EmergencyKillSwitch,
    create_decision_bundle,
)
from research.decision_models.feature_block import DecisionFeatureBlock


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


# ---------------------------------------------------------------------------
# Deterministic tripwire bands.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,summary,expected", [
    ("NYSE halts trading", "Exchange closed for the session.", D9_KILL_SWITCH_ACTIVE),
    ("Circuit breaker triggered on open", "Markets in freefall.", D9_KILL_SWITCH_ACTIVE),
    ("Swiss franc de-pegged from euro", "SNB abandons the peg.", D9_KILL_SWITCH_ACTIVE),
    ("Bank runs spread across region", "Depositors queue at branches.", D9_KILL_SWITCH_ACTIVE),
    ("Bank freezes redemptions on funds", "Investors cannot withdraw.", D9_KILL_SWITCH_ACTIVE),
    ("Emergency rate cut announced", "Central bank acts overnight.", D9_KILL_SWITCH_ACTIVE),
    ("Fed minutes signal steady rates", "FOMC held policy.", D9_KILL_SWITCH_NORMAL),
    ("Quarterly outlook piece", "Analysts mull earnings.", D9_KILL_SWITCH_NORMAL),
])
def test_d9_tripwire_bands(title, summary, expected):
    assert _deterministic_d9({"title": title, "text_or_summary": summary}) == expected


def test_d9_empty_text_abstains():
    assert _deterministic_d9({}) == D9_KILL_SWITCH_NO_DATA


def test_d9_in_triage_question_ids():
    assert "D9_emergency_kill_switch" in TRIAGE_QUESTION_IDS


# ---------------------------------------------------------------------------
# Near-zero latency: the tripwire short-circuits before any model work.
# ---------------------------------------------------------------------------

def test_d9_short_circuit_is_near_zero_latency():
    art = _reuters_article("k9", "NYSE halts trading as circuit breaker triggered")
    t0 = time.perf_counter()
    v = triage_article(art, laya=_FakeLaya({}))  # model present but never called
    dt_ms = (time.perf_counter() - t0) * 1000
    assert v.d9 == D9_KILL_SWITCH_ACTIVE
    assert v.source == "deterministic"
    assert dt_ms < 250.0  # vs ~19,000 ms for a real forward pass
    assert "d9_kill_switch_tripwire" in v.reasons
    assert "short_circuit_before_model" in v.reasons


def test_d9_short_circuit_preserves_d0_gate_action():
    # The tripwire is a cycle-state signal, not an article-quality gate: the
    # verdict keeps the normal D0 action (here TAG — the generic fixture text
    # reads as commentary), while the kill-switch state rides alongside.
    art = _reuters_article("k10", "Exchange halts trading after flash crash")
    v = triage_article(art, laya=_FakeLaya({}))
    assert v.d9 == D9_KILL_SWITCH_ACTIVE
    assert v.action in ("INGEST", "TAG")
    assert v.to_dict()["d9_kill_switch"] == D9_KILL_SWITCH_ACTIVE


def test_d9_normal_headline_with_model_reports_normal():
    art = _reuters_article("k11", "Fed minutes signal steady rates")
    v = triage_article(art, laya=_FakeLaya({
        "D9_emergency_kill_switch": "FALSE",
        "D0_1_source_credibility": "VERIFIED_OUTLET",
        "D0_2_duplicate_detection": "FIRST_REPORT",
        "D0_3_fact_vs_opinion": "OFFICIAL_STATEMENT",
        "D5a_escalation_ladder": "INSUFFICIENT_DATA",
        "R1_headline_event_routing": "EVENT_CB_MEETING",
    }))
    assert v.d9 == D9_KILL_SWITCH_NORMAL
    assert v.source == "laya"


# ---------------------------------------------------------------------------
# Floor / escalation semantics.
# ---------------------------------------------------------------------------

def test_d9_model_cannot_escalate_normal_to_kill():
    # CONTRACT CHANGE (2026-10-01): the model no longer holds D9 escalation
    # authority. Measured over 2269 real production articles it tripped the
    # kill switch on 214 (9.4%) versus 0/428 for the deterministic tripwire,
    # and every trip was a false positive ("Why AutoZone Stock Rallied Today",
    # "Federal Reserve Board announces approval of application by BancFirst
    # Corporation"). D9 is a veto trigger, so a fabricated trip zeroes
    # risk_multiplier and freezes the cycle. The lexical tripwire is now the
    # sole escalation path. See news_triage.MODEL_ESCALATION_AUTHORITY.
    art = _reuters_article("k12", "Unusual market conditions reported")
    v = triage_article(art, laya=_FakeLaya({"D9_emergency_kill_switch": "TRUE"}))
    assert v.d9 == D9_KILL_SWITCH_NORMAL
    # The raw model answer still differs from det, so it stays in the
    # active-learning signal rather than vanishing.
    assert "D9_emergency_kill_switch" in v.disagreement_ids


def test_d9_model_cannot_unkill_fired_tripwire():
    # The short-circuit returns BEFORE the model runs, so a "FALSE" answer is
    # never even consulted — the model structurally cannot un-kill a fired
    # tripwire. (The ledger un-kill predicate guards other verdict sources,
    # covered by test_ledger_marks_unkill_attempt_as_safety_pair.)
    art = _reuters_article("k13", "Trading halted indefinitely")
    v = triage_article(art, laya=_FakeLaya({"D9_emergency_kill_switch": "FALSE"}))
    assert v.d9 == D9_KILL_SWITCH_ACTIVE
    assert v.source == "deterministic"
    assert "D9_emergency_kill_switch" not in v.disagreement_ids
    assert "short_circuit_before_model" in v.reasons


def test_d9_model_garbage_defers_to_det():
    art = _reuters_article("k14", "Steady session for equities")
    v = triage_article(art, laya=_FakeLaya({"D9_emergency_kill_switch": "MAYBE"}))
    assert v.d9 == D9_KILL_SWITCH_NORMAL


# ---------------------------------------------------------------------------
# Cycle summary + run() result shape.
# ---------------------------------------------------------------------------

def test_summary_flags_kill_switch():
    arts = [
        {"title": "a", "triage": {"action": "INGEST", "confidence": 0.9, "d9_kill_switch": D9_KILL_SWITCH_ACTIVE}},
        {"title": "b", "triage": {"action": "INGEST", "confidence": 0.9, "d9_kill_switch": D9_KILL_SWITCH_NORMAL}},
        {"title": "legacy", "triage": {"action": "INGEST", "confidence": 0.9}},  # no d9 key
    ]
    s = summarize_cycle_evidence(arts)
    assert s["d9_kill_switch_count"] == 1
    assert s["d9_kill_switch_active"] is True


def test_summary_no_kill_switch():
    arts = [{"title": "a", "triage": {"action": "INGEST", "confidence": 0.9, "d9_kill_switch": D9_KILL_SWITCH_NORMAL}}]
    s = summarize_cycle_evidence(arts)
    assert s["d9_kill_switch_count"] == 0
    assert s["d9_kill_switch_active"] is False


def test_run_result_reports_kill_switch_block(tmp_path, monkeypatch):
    import data.realtime_news_ingest as rni
    from intelligence.macro_graph import MacroGraph

    # Latch semantics: N independent batches confirm; a single batch is only
    # a pending confirmation, so assert on the latch block, not active.
    monkeypatch.setenv("D9_LATCH_PATH", str(tmp_path / "latch.json"))
    monkeypatch.setattr("data.news_triage._get_laya", lambda: None)
    monkeypatch.setattr(rni, "collect_rss_articles", lambda: [])
    monkeypatch.setattr(rni, "collect_newsapi_articles", lambda *a, **k: [])
    monkeypatch.setattr(rni, "collect_seeking_alpha_articles", lambda *a, **k: [])
    monkeypatch.setattr(
        rni, "normalize_article",
        lambda **kw: {
            "title": kw.get("title"), "url": kw.get("url"),
            "publisher": kw.get("publisher"), "provider": kw.get("provider"),
            "text_or_summary": kw.get("text_or_summary"), "published_at": "",
            "dedupe_key": kw.get("title", "")[:20],
        },
    )

    class _StubGraph:
        def ingest_source(self, **kw):
            return 1

    articles = [
        _reuters_article("x1", "Exchange halts trading as circuit breaker hits"),
        _reuters_article("x2", "Fed minutes signal steady rates"),
    ]
    result = None
    ingest = rni.ingest_articles(_StubGraph(), articles)
    result = {"ingest": ingest}
    assert result["ingest"]["execution_authority"] is False
    # ingest_articles carries per-article triage verdicts; the latch lives in
    # run()'s kill_switch block, so verify the tripwire verdicts here.
    verdicts = [
        (a.get("triage") or {}).get("d9_kill_switch")
        for a in articles if isinstance(a, dict)
    ]
    assert "EMERGENCY_HALT_ACTIVE" in verdicts


# ---------------------------------------------------------------------------
# MacroAnalyst: kill-switch flips is_exchange_halt -> veto-class D9.
# ---------------------------------------------------------------------------

def test_kill_switch_flips_feature_block_and_bundle_veto():
    fb = DecisionFeatureBlock(event_name="TEST", is_exchange_halt=True)
    assert fb.to_prompt_state()["integrity"]["is_kill_switch_active"] is True
    bundle = create_decision_bundle(features=fb)
    rec = bundle.get("D9_emergency_kill_switch")
    assert rec is not None
    assert rec.rule_label == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE.value
    assert rec.is_veto is True
    assert bundle.veto_enforced is True
    assert bundle.risk_multiplier == 0.0
    assert rec.execution_authority is False


def test_no_kill_switch_keeps_normal_operation():
    fb = DecisionFeatureBlock(event_name="TEST")
    bundle = create_decision_bundle(features=fb)
    rec = bundle.get("D9_emergency_kill_switch")
    assert rec.rule_label == D9_EmergencyKillSwitch.NORMAL_OPERATION.value
    assert rec.is_veto is False
    assert bundle.veto_enforced is False


def test_macro_analyst_reads_cycle_kill_switch(monkeypatch, tmp_path):
    import intelligence.graph_nodes as gn
    from intelligence.graph_nodes import MacroAnalystNode
    from data.kill_switch_latch import KillSwitchLatch

    monkeypatch.setattr(gn, "get_kill_switch_latch", lambda: KillSwitchLatch(path=str(tmp_path / "latch.json")))
    node = MacroAnalystNode.__new__(MacroAnalystNode)
    monkeypatch.setattr(gn, "require_agent_action", lambda *a, **k: None, raising=False)

    captured = {}

    def fake_build(symbol, features, snapshot, **kwargs):
        captured["ks"] = kwargs.get("kill_switch")
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
    node.narrative_sm = None
    node.causal_extractor = None

    monkeypatch.setattr(gn, "ui_state", type("UI", (), {
        "macro_regime": "", "net_liquidity_stance": "", "last_decision_bundle": None,
        "active_narrative_summary": "", "active_tripwires_status": [],
        "last_causal_attribution": None, "news_evidence_summary": None,
        "news_kill_switch_active": False,
    })())

    # Cycle path: triage summary flags the tripwire (no orchestrator signal).
    state = {
        "symbol": "EURUSD",
        "feature_vector": {},
        "market_snapshot": {},
        "news_articles": [
            {"title": "halt", "triage": {"action": "INGEST", "confidence": 0.9, "d9_kill_switch": D9_KILL_SWITCH_ACTIVE}},
        ],
    }
    try:
        MacroAnalystNode.__call__(node, state)
    except Exception:
        pass

    ks = captured.get("ks")
    assert isinstance(ks, dict) and ks["active"] is True
    assert gn.ui_state.news_kill_switch_active is True


def test_macro_analyst_no_tripwire_stays_inactive(monkeypatch, tmp_path):
    import intelligence.graph_nodes as gn
    from intelligence.graph_nodes import MacroAnalystNode
    from data.kill_switch_latch import KillSwitchLatch

    monkeypatch.setattr(gn, "get_kill_switch_latch", lambda: KillSwitchLatch(path=str(tmp_path / "latch.json")))
    node = MacroAnalystNode.__new__(MacroAnalystNode)
    monkeypatch.setattr(gn, "require_agent_action", lambda *a, **k: None, raising=False)
    captured = {}

    def fake_build(symbol, features, snapshot, **kwargs):
        captured["ks"] = kwargs.get("kill_switch")
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
    node.narrative_sm = None
    node.causal_extractor = None

    monkeypatch.setattr(gn, "ui_state", type("UI", (), {
        "macro_regime": "", "net_liquidity_stance": "", "last_decision_bundle": None,
        "active_narrative_summary": "", "active_tripwires_status": [],
        "last_causal_attribution": None, "news_evidence_summary": None,
        "news_kill_switch_active": False,
    })())

    state = {"symbol": "EURUSD", "feature_vector": {}, "market_snapshot": {}}
    try:
        MacroAnalystNode.__call__(node, state)
    except Exception:
        pass

    # With the latch wired, kill_switch always carries the latch snapshot;
    # "inactive" now means not latched and no news tripwire this cycle.
    ks = captured.get("ks")
    assert isinstance(ks, dict) and ks["active"] is False
    assert ks["latch"]["latched"] is False
    assert gn.ui_state.news_kill_switch_active is False


# ---------------------------------------------------------------------------
# Ledger safety pair: model attempting to un-kill is the hazard signal.
# ---------------------------------------------------------------------------

def test_ledger_marks_unkill_attempt_as_safety_pair(tmp_path):
    from data.news_triage import _D9_TRUTHY  # shared predicate
    from research.learning.contrastive_pair_miner import ContrastivePairMiner
    from research.learning.triage_ledger import TriageDisagreementLogger

    class _Verdict:
        source = "laya"
        d0_1 = "VERIFIED_OUTLET"
        d0_2 = "FIRST_REPORT"
        d0_3 = "VERIFIED_FACT"
        d5a = "INSUFFICIENT_DATA"
        d9 = D9_KILL_SWITCH_ACTIVE
        r1 = "NOT_EVENT_NOISE"
        disagreement_ids = ["D9_emergency_kill_switch"]
        laya_answers = {"D9_emergency_kill_switch": "FALSE"}
        deterministic_labels = {"D9_emergency_kill_switch": D9_KILL_SWITCH_ACTIVE}
        prompt_state = {}

    lg = TriageDisagreementLogger(str(tmp_path / "ledger.db"), miner=ContrastivePairMiner(str(tmp_path / "pool.db")))
    counts = lg.log_verdict(_Verdict(), {"title": "halt headline", "dedupe_key": "d9x", "url": "", "publisher": "", "published_at": "", "text_or_summary": ""})
    assert counts["ledger_rows"] == 1
    assert counts["pairs_persisted"] == 1
