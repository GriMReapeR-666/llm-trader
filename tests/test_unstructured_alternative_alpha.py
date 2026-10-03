"""Tests for Unstructured Alternative Data & NLP Alpha Pipeline.

Verifies:
1. Form 10-K Modification Index (Cohen, Malloy, Nguyen 2020)
2. Earnings Call Linguistic Hedging (Unscripted Q&A vs Prepared Remarks)
3. Alternative Activity Proxy Scorer (Job postings, developer velocity)
4. SEC EDGAR Provider Integration
5. Live Advisory Hub Integration
"""
from __future__ import annotations

import pytest

from data.sec_edgar_provider import SECEDGARProvider, SECFilingsRow
from intelligence.unstructured_alternative_alpha import (
    AlternativeActivityProxyScorer,
    EarningsCallHedgingAnalyzer,
    Form10KModificationAnalyzer,
    compute_bow_tf_cosine_similarity,
    tokenize_text,
)
from core.live_advisory_hub import alternative_nlp_advisory, build_live_advisory_snapshot


# -----------------------------------------------------------------------------
# 1. Form 10-K Modification Index Tests
# -----------------------------------------------------------------------------

def test_10k_section_extraction():
    sample_filing = """
    PART I
    ITEM 1. BUSINESS
    We are a leading cloud computing and enterprise software corporation.
    ITEM 1A. RISK FACTORS
    Our operations face significant cybersecurity threats, foreign currency volatility,
    and potential disruptions in our global semiconductor hardware supply chain.
    ITEM 1B. UNRESOLVED STAFF COMMENTS
    None.
    ITEM 2. PROPERTIES
    """
    extracted = Form10KModificationAnalyzer.extract_risk_factors(sample_filing)
    assert "cybersecurity threats" in extracted
    assert "foreign currency volatility" in extracted
    assert "ITEM 1. BUSINESS" not in extracted
    assert "ITEM 2. PROPERTIES" not in extracted


def test_10k_identical_filings_modification_zero():
    text = (
        "We are subject to market volatility, interest rate risk, commodity price fluctuations, "
        "and supply chain bottlenecks that could adversely affect our quarterly operating margins."
    )
    res = Form10KModificationAnalyzer.compute_modification_index(text, text, is_already_extracted=True)
    assert res["cosine_similarity"] >= 0.99
    assert res["modification_index"] <= 0.02
    assert res["revision_severity"] == "UNCHANGED"
    assert res["underperformance_warning"] is False
    assert res["alpha_penalty"] == 0.0
    assert res["execution_authority"] is False


def test_10k_severe_rewrite_triggers_underperformance_warning():
    text_2025 = (
        "Our operations are steady. We rely on standard suppliers and maintain robust liquidity. "
        "Domestic market demand remains predictable with minimal regulatory overhang."
    )
    text_2026 = (
        "We face acute insolvency risks, severe material weaknesses in internal financial controls, "
        "catastrophic antitrust investigations across major jurisdictions, and debt covenants breaches."
    )
    res = Form10KModificationAnalyzer.compute_modification_index(text_2025, text_2026, is_already_extracted=True)
    assert res["cosine_similarity"] < 0.35
    assert res["modification_index"] > 0.65
    assert res["revision_severity"] == "SEVERE_REWRITE"
    assert res["underperformance_warning"] is True
    assert res["alpha_penalty"] < 0.0
    assert res["execution_authority"] is False


def test_10k_empty_input_graceful_handling():
    res = Form10KModificationAnalyzer.compute_modification_index("", "Valid text", is_already_extracted=True)
    assert res["revision_severity"] == "INSUFFICIENT_DATA"
    assert res["underperformance_warning"] is False
    assert res["execution_authority"] is False


# -----------------------------------------------------------------------------
# 2. Earnings Call Linguistic Hedging Tests
# -----------------------------------------------------------------------------

def test_earnings_call_section_splitting():
    transcript = """
    Good morning, everyone. Welcome to our quarterly earnings conference call.
    We are pleased to report strong revenue growth and record cash flow.
    We will now begin the question-and-answer session.
    Operator: Our first question comes from Jane Doe at Morgan Stanley.
    Executive: Thank you Jane. It is somewhat challenging to forecast the second half.
    """
    pres, qa = EarningsCallHedgingAnalyzer.split_call_sections(transcript)
    assert "strong revenue growth" in pres
    assert "question-and-answer" in qa
    assert "somewhat challenging" in qa


def test_earnings_call_confident_conviction():
    confident_text = (
        "We are definitely confident in our accelerating growth trajectory. "
        "Our competitive moat is unmatched, execution has been exceptional and robust, "
        "and our expanding operating margin proves our disciplined capital allocation."
    )
    score = EarningsCallHedgingAnalyzer.score_hedging_ratio(confident_text)
    assert score["affirmative_count"] >= 5
    assert score["hedging_ratio"] < 0.25
    assert score["tone_polarity"] > 0.60
    assert score["conviction_state"] == "HIGH_CONVICTION"


def test_earnings_call_evasive_qa_divergence():
    transcript = """
    Prepared Presentation:
    We achieved record revenue, robust customer adoption, and strong momentum across all product suites.
    We are definitely confident in our market leadership.

    Question-and-Answer Session:
    Analyst: Can you clarify why free cash flow dropped 40% this quarter?
    Executive: Well, it is somewhat uncertain, and we face turbulent headwinds.
    It is roughly possible that things remain challenging, delayed, and hesitant.
    There are multiple vulnerable risks and softening pressures.
    """
    analysis = EarningsCallHedgingAnalyzer.analyze_transcript(transcript)
    assert analysis["qa_available"] is True
    assert analysis["presentation_score"]["conviction_state"] == "HIGH_CONVICTION"
    assert analysis["qa_score"]["hedging_ratio"] >= 0.60
    assert analysis["qa_divergence_delta"] >= 0.25
    assert analysis["overall_verdict"] == "EVASIVE_DIVERGENCE_WARNING"
    assert analysis["credibility_score"] < 0.40
    assert analysis["execution_authority"] is False


# -----------------------------------------------------------------------------
# 3. Alternative Activity Proxy Scorer Tests
# -----------------------------------------------------------------------------

def test_job_postings_hiring_freeze():
    # Job openings drop from 500 to 250 (-50%)
    res = AlternativeActivityProxyScorer.score_job_postings_momentum(current_openings=250, prior_openings=500)
    assert res["operational_status"] == "HIRING_FREEZE"
    assert res["signal_bias"] < 0.0
    assert res["postings_pct_change"] == -0.50
    assert res["execution_authority"] is False


def test_job_postings_expansion_surge():
    # Job openings surge from 100 to 180 (+80%)
    res = AlternativeActivityProxyScorer.score_job_postings_momentum(current_openings=180, prior_openings=100)
    assert res["operational_status"] == "AGGRESSIVE_EXPANSION"
    assert res["signal_bias"] > 0.0
    assert res["postings_pct_change"] == 0.80


def test_developer_velocity_acceleration_and_desertion():
    accel = AlternativeActivityProxyScorer.score_developer_velocity(
        current_commits=150, prior_commits=80, active_contributors=12
    )
    assert accel["status"] == "DEVELOPER_ACCELERATION"
    assert accel["signal_bias"] > 0.0

    desertion = AlternativeActivityProxyScorer.score_developer_velocity(
        current_commits=10, prior_commits=100, active_contributors=0
    )
    assert desertion["status"] == "DEVELOPER_DESERTION"
    assert desertion["signal_bias"] < 0.0


def test_synthesize_composite_alternative_alpha():
    mod_res = {"alpha_penalty": -0.25}
    hedging_res = {"qa_score": {"tone_polarity": 0.40}}
    jobs_res = {"signal_bias": 0.20}
    dev_res = {"signal_bias": 0.40}

    synth = AlternativeActivityProxyScorer.synthesize_alternative_alpha_signal(
        modification_index_result=mod_res,
        transcript_hedging_result=hedging_res,
        job_postings_result=jobs_res,
        developer_velocity_result=dev_res,
    )
    assert -1.0 <= synth["composite_alternative_alpha"] <= 1.0
    assert synth["signals_evaluated"] == 4
    assert synth["confidence"] == 1.0
    assert synth["execution_authority"] is False
    assert synth["advisory_only"] is True


# -----------------------------------------------------------------------------
# 4. SEC EDGAR Provider Integration Tests
# -----------------------------------------------------------------------------

def test_sec_edgar_provider_modification_index():
    provider = SECEDGARProvider()
    filing1 = SECFilingsRow(
        ticker="TEST",
        form_type="10-K",
        filing_date="2025-02-15",
        title="2024 Form 10-K Annual Report",
        source_url="https://sec.gov",
        content_hash="h1",
        key_items=["Item 1A"],
        material_risk_factors=["Supply chain disruptions", "Foreign exchange volatility"],
        sentiment_bias="NEUTRAL",
    )
    filing2 = SECFilingsRow(
        ticker="TEST",
        form_type="10-K",
        filing_date="2026-02-15",
        title="2025 Form 10-K Annual Report",
        source_url="https://sec.gov",
        content_hash="h2",
        key_items=["Item 1A"],
        material_risk_factors=["Severe bankruptcy risk", "Material internal accounting weakness"],
        sentiment_bias="NEGATIVE",
    )

    divergence = provider.extract_risk_divergences(filing=filing2, prior_filing=filing1)
    assert divergence["modification_analysis"] is not None
    assert divergence["modification_analysis"]["modification_index"] > 0.30
    assert divergence["modification_analysis"]["underperformance_warning"] is True


# -----------------------------------------------------------------------------
# 5. Live Advisory Hub Integration Tests
# -----------------------------------------------------------------------------

def test_live_advisory_hub_alternative_nlp():
    adv = alternative_nlp_advisory(
        transcript_text="We are definitely confident and robust. Q&A: Things are very strong and accelerated.",
        current_job_openings=200,
        prior_job_openings=150,
    )
    assert adv["enabled"] is True
    assert adv["execution_authority"] is False
    assert adv["advisory_only"] is True
    assert adv["composite_alternative_alpha"] > 0.0

    snapshot = build_live_advisory_snapshot(
        prices=[100.0, 101.0, 102.0, 101.5, 103.0] * 10,
        transcript_text="We definitely see strong momentum.",
    )
    assert "alternative_nlp" in snapshot
    assert snapshot["alternative_nlp"]["execution_authority"] is False
    assert snapshot["alternative_nlp"]["advisory_only"] is True
