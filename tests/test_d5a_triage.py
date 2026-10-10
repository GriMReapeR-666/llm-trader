"""D5a escalation-ladder triage: lexical bands, abstention, monotone overrides, summary."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from intelligence.macro_source_policy import assess_source
from data.news_triage import (
    D5A_RANK,
    TriageVerdict,
    _deterministic_d5a,
    summarize_cycle_evidence,
    triage_article,
    triage_articles,
)
from data.realtime_news_ingest import normalize_article


def _art(title, summary="", key=None, url="https://www.reuters.com/geo"):
    row = normalize_article(
        title=title, url=url, publisher="Reuters", provider="reuters",
        text_or_summary=summary,
    ) or {}
    row["dedupe_key"] = key or title[:20]
    return row


class _FakeLayaResult:
    def __init__(self, answers, probabilities=None):
        self.answers = answers
        self.probabilities = probabilities or {}
        self.confidences = {q: 0.9 for q in answers}
        self.latency_ms = 1.0
        self.input_tokens = 10
        self.output_tokens = 0


class _FakeLaya:
    """Answers D0_* correctly (deterministic parity) and D5a with a given label."""

    def __init__(self, d5a_label):
        self._d5a = d5a_label

    def decide(self, state, questions):
        answers = {}
        probs = {}
        det_d01 = "SPOOF_REJECTED" if state.get("headline", "").startswith(("IGNORE", "Jailbreak")) else "VERIFIED_OUTLET"
        answers["D0_1_source_credibility"] = det_d01
        probs["D0_1_source_credibility"] = {det_d01.lower(): 0.9}
        answers["D0_2_duplicate_detection"] = "FIRST_REPORT"
        probs["D0_2_duplicate_detection"] = {"first_report": 0.9}
        answers["D0_3_fact_vs_opinion"] = "VERIFIED_FACT"
        probs["D0_3_fact_vs_opinion"] = {"verified_fact": 0.9}
        answers["D5a_escalation_ladder"] = self._d5a
        probs["D5a_escalation_ladder"] = {str(self._d5a).lower(): 0.9}
        return _FakeLayaResult(answers, probs)


# --- Deterministic ladder bands -------------------------------------------------

def test_blockade_is_strongest_band():
    assert _deterministic_d5a(_art("Navy enforces full blockade, all ports closed")) == "FULL_BLOCKADE"


def test_chokepoint_band_matches_hormuz_and_tanker():
    assert _deterministic_d5a(_art("Tanker seized near Strait of Hormuz")) == "CHOKEPOINT_DISRUPTION"
    assert _deterministic_d5a(_art("Pipeline attack halts flows")) == "CHOKEPOINT_DISRUPTION"


def test_direct_strike_band():
    assert _deterministic_d5a(_art("Missile strikes hit military airbase")) == "DIRECT_STRIKE"


def test_proxy_clash_band():
    assert _deterministic_d5a(_art("Border skirmish erupts between militias")) == "PROXY_CLASH"


def test_rhetoric_band_when_geo_but_no_physical_action():
    assert _deterministic_d5a(_art("Officials warn of retaliation amid rising tensions")) == "RHETORIC_ONLY"


def test_non_geo_headline_abstains():
    assert _deterministic_d5a(_art("CPI rose 0.3 percent in August")) == "INSUFFICIENT_DATA"
    assert _deterministic_d5a(_art("Fed holds rates steady")) == "INSUFFICIENT_DATA"
    assert _deterministic_d5a(_art("")) == "INSUFFICIENT_DATA"


def test_strongest_band_wins_on_mixed_headline():
    # Both 'threatens' (rhetoric) and 'missile strike' (direct) — strike wins.
    v = _deterministic_d5a(_art("After threats, missile strikes hit port"))
    assert v == "DIRECT_STRIKE"


# --- triage_article paths -------------------------------------------------------

def test_verdict_carries_d5a_deterministic(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "off")
    v = triage_article(_art("CPI rose 0.3 percent"), seen_keys=set(), laya=None)
    assert isinstance(v, TriageVerdict)
    assert v.d5a == "INSUFFICIENT_DATA"
    assert v.to_dict()["d5a"] == "INSUFFICIENT_DATA"

    v2 = triage_article(_art("Tanker seized near Strait of Hormuz"), seen_keys=set(), laya=None)
    assert v2.d5a == "CHOKEPOINT_DISRUPTION"


def test_laya_may_not_escalate_but_may_not_deescalate():
    """CONTRACT CHANGE (2026-10-01): the model holds no escalation authority on
    D5a. Measured over 2,269 real production articles it emitted 303
    FULL_BLOCKADE labels (13.4%) against 0/428 for the deterministic ladder, all
    on non-geopolitical headlines ("VNET Group: A Play On China's Data Center
    Boom", "May Mobility's SPAC Merger"). D5a feeds D7/D8 escalation state, so a
    fabricated band corrupts the arbitration chain.

    Both directions are now closed: the model can neither raise nor lower
    severity. It still disagrees loudly in the active-learning signal."""
    art = _art("Border skirmish erupts between militias")  # det: PROXY_CLASH

    # Escalation is refused; the raw answer is still recorded for audit.
    v_up = triage_article(art, seen_keys=set(), laya=_FakeLaya("DIRECT_STRIKE"))
    assert v_up.d5a == "PROXY_CLASH"
    assert "D5a_escalation_ladder" in v_up.disagreement_ids
    assert v_up.laya_answers["D5a_escalation_ladder"] == "DIRECT_STRIKE"

    # De-escalation floor still holds.
    v_down = triage_article(art, seen_keys=set(), laya=_FakeLaya("RHETORIC_ONLY"))
    assert v_down.d5a == "PROXY_CLASH"
    assert "D5a_escalation_ladder" in v_down.disagreement_ids  # raw differed → loggable

    # Model abstains → deterministic label wins.
    v_abstain = triage_article(art, seen_keys=set(), laya=_FakeLaya("INSUFFICIENT_DATA"))
    assert v_abstain.d5a == "PROXY_CLASH"

    # Model returns garbage → deterministic label wins.
    v_junk = triage_article(art, seen_keys=set(), laya=_FakeLaya("NUKE_EVERYTHING"))
    assert v_junk.d5a == "PROXY_CLASH"


def test_non_geo_headline_stays_insufficient_with_laya():
    art = _art("CPI rose 0.3 percent in August")
    v = triage_article(art, seen_keys=set(), laya=_FakeLaya("RHETORIC_ONLY"))
    # det INSUFFICIENT_DATA (rank 0); the model would previously have escalated
    # to RHETORIC_ONLY. With authority withheld the abstention now stands.
    assert v.d5a == "INSUFFICIENT_DATA"
    v2 = triage_article(art, seen_keys=set(), laya=_FakeLaya("INSUFFICIENT_DATA"))
    assert v2.d5a == "INSUFFICIENT_DATA"
    assert "D5a_escalation_ladder" not in v2.disagreement_ids  # agree → no signal


def test_rank_ordering():
    order = ["INSUFFICIENT_DATA", "RHETORIC_ONLY", "PROXY_CLASH", "DIRECT_STRIKE", "CHOKEPOINT_DISRUPTION", "FULL_BLOCKADE"]
    ranks = [D5A_RANK[x] for x in order]
    assert ranks == sorted(ranks)


# --- summary ---------------------------------------------------------------------

def test_summary_counts_geo_and_max_escalation(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "off")
    arts = [
        _art("CPI print", key="n1"),                                    # INSUFFICIENT_DATA
        _art("Border skirmish erupts", key="n2"),                        # PROXY_CLASH
        _art("Navy enforces full blockade", key="n3"),                   # FULL_BLOCKADE
    ]
    triage_articles(arts)   # batch attaches verdict dicts
    s = summarize_cycle_evidence(arts)
    assert s["geo_total"] == 2
    assert s["max_escalation"] == "FULL_BLOCKADE"

    arts_non_geo = [_art("CPI print", key="n4")]
    triage_articles(arts_non_geo)
    s2 = summarize_cycle_evidence(arts_non_geo)
    assert s2["geo_total"] == 0
    assert s2["max_escalation"] == "INSUFFICIENT_DATA"


def test_summary_handles_legacy_verdicts_without_d5a():
    arts = [{"title": "legacy", "triage": {"d0_1": "VERIFIED_OUTLET", "d0_2": "FIRST_REPORT",
                                           "d0_3": "VERIFIED_FACT", "source": "deterministic",
                                           "action": "INGEST", "confidence": 0.9}}]
    s = summarize_cycle_evidence(arts)
    assert s["geo_total"] == 0
    assert s["max_escalation"] == "INSUFFICIENT_DATA"


def test_batch_attaches_d5a_to_verdicts(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "off")
    arts = [_art("Tanker seized near Hormuz", key="b1"), _art("CPI print", key="b2")]
    report = triage_articles(arts)
    assert report["status"] == "TRIAGED"
    assert arts[0]["triage"]["d5a"] == "CHOKEPOINT_DISRUPTION"
    assert arts[1]["triage"]["d5a"] == "INSUFFICIENT_DATA"
