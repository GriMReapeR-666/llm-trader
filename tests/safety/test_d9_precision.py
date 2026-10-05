"""Corpus regression test for D9 news kill-switch tripwire precision (EV, PI-P9).

Verifies against tests/fixtures/d9_corpus.json:
- 13 real flash-crisis trips must trip.
- Of 33 adversarial benign headlines, 31 must be suppressed (only the 2 measured residuals trip).
- 14 benign passes keep passing.
- The 2 recall holes (passive peg abandoned, instrument-intervening halts) now trip.
"""
import json
from pathlib import Path

import pytest

from data.news_triage import _deterministic_d9, D9_KILL_SWITCH_ACTIVE, D9_KILL_SWITCH_NORMAL

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "d9_corpus.json"


@pytest.fixture(scope="module")
def corpus():
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_harvested_real_trips_all_trip(corpus):
    real_trips = corpus["harvested_trips"]
    assert len(real_trips) == 13
    failed = []
    for item in real_trips:
        text = item["text"]
        verdict = _deterministic_d9({"title": text, "text_or_summary": ""})
        if verdict != D9_KILL_SWITCH_ACTIVE:
            failed.append(text)
    assert not failed, f"Lost {len(failed)} real trips: {failed}"


def test_benign_false_positives_suppress_31_of_33(corpus):
    benign_fps = corpus["benign_false_positives"]
    assert len(benign_fps) == 33
    tripped = []
    for item in benign_fps:
        text = item["text"]
        verdict = _deterministic_d9({"title": text, "text_or_summary": ""})
        if verdict == D9_KILL_SWITCH_ACTIVE:
            tripped.append(text)

    # Exactly the 2 measured residuals may trip
    assert len(tripped) == 2, f"Expected 2 residual benign trips, got {len(tripped)}: {tripped}"
    for text in tripped:
        assert any(cue in text for cue in ("three lithium miners", "microcap stock")), (
            f"Unexpected residual benign trip: {text}"
        )


def test_benign_passes_all_pass(corpus):
    benign_passes = corpus["benign_passes"]
    assert len(benign_passes) == 14
    tripped = []
    for text in benign_passes:
        verdict = _deterministic_d9({"title": text, "text_or_summary": ""})
        if verdict == D9_KILL_SWITCH_ACTIVE:
            tripped.append(text)
    assert not tripped, f"Benign passes unexpectedly tripped: {tripped}"


def test_recall_holes_now_trip():    # Passive voice: "Swiss franc peg abandoned"
    v1 = _deterministic_d9({"title": "Swiss franc peg abandoned", "text_or_summary": ""})
    assert v1 == D9_KILL_SWITCH_ACTIVE, "Recall hole 1 (passive peg abandoned) failed to trip"

    # Instrument intervening: "LME halts nickel trading"
    v2 = _deterministic_d9({"title": "LME halts nickel trading", "text_or_summary": ""})
    assert v2 == D9_KILL_SWITCH_ACTIVE, "Recall hole 2 (instrument-intervening halt) failed to trip"


def test_realistic_crisis_headlines_must_trip():
    """H3 recall floor: plausible crisis phrasing the old guard suppressed.

    Each of these was verified to MISS under the previous guard (speculative
    modal / conditional / bare-no / single-venue suppression). They must all
    trip; if the guard is ever re-widened, this pins the recall cost.
    """
    must_trip = [
        "Trading halted on NYSE after technical glitch; exchange says it may reopen later",
        "Stock exchange closed after earthquake as officials review damage",
        "Exchange halts trading in all stocks after crash",
        "Credit Suisse bank run: clients pull $10 billion, no end in sight",
        "Treasury flash crash: Fed may intervene",
    ]
    missed = [
        text for text in must_trip
        if _deterministic_d9({"title": text, "text_or_summary": ""}) != D9_KILL_SWITCH_ACTIVE
    ]
    assert not missed, f"Recall loss on realistic crises: {missed}"


def test_wire_domains_count_toward_latch():
    """H3: latch contribution is PRIMARY + wire hosts only (domain-keyed)."""
    from intelligence.macro_source_policy import counts_toward_latch

    assert counts_toward_latch({"allowed": True, "tier": "PRIMARY", "domain": "federalreserve.gov"}) is True
    assert counts_toward_latch({"allowed": True, "tier": "RUNTIME_FEED", "domain": "reuters.com"}) is True
    assert counts_toward_latch({"allowed": True, "tier": "RUNTIME_FEED", "domain": "www.bloomberg.com"}) is True
    # Aggregators never count, even when allowed for ingestion.
    assert counts_toward_latch({"allowed": True, "tier": "RUNTIME_FEED", "domain": "finance.yahoo.com"}) is False
    assert counts_toward_latch({"allowed": True, "tier": "RUNTIME_FEED", "domain": "seekingalpha.com"}) is False
    assert counts_toward_latch({"allowed": True, "tier": "RUNTIME_FEED", "domain": "newsapi.org"}) is False
    # Publisher-name match on a foreign host never counts.
    assert counts_toward_latch({"allowed": True, "tier": "RUNTIME_FEED", "domain": "attacker.example"}) is False
    assert counts_toward_latch({"allowed": False, "tier": "UNVERIFIED", "domain": "x.com"}) is False
