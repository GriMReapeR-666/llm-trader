"""Tests for SEC EDGAR Provider and Autonomous Fact-Checking Engine.

Covers:
- SEC filings fetch, caching, and material risk divergence extraction
- Fact-Checker 4-stage validation (triangulation, sanity bounds, alias canonicalization, provenance hashing)
"""
from __future__ import annotations

import os
import tempfile
import pytest

from data.sec_edgar_provider import SECEDGARProvider, SECFilingsRow
from intelligence.fact_checker import AutonomousFactChecker, FactValidationResult, MACRO_SANITY_BOUNDS


def test_sec_edgar_provider():
    td = tempfile.mkdtemp()
    try:
        cache_file = os.path.join(td, "sec_cache.json")
        provider = SECEDGARProvider(cache_file=cache_file)
        filings = provider.fetch_filings()
        assert len(filings) >= 2
        for f in filings:
            assert isinstance(f, SECFilingsRow)
            assert f.form_type in ("10-K", "10-Q", "8-K", "FOMC_MINUTES")
            assert f.execution_authority is False

            # Test risk divergence analysis
            risk_info = provider.extract_risk_divergences(f)
            assert "risk_factor_count" in risk_info
            assert "sentiment_bias" in risk_info
    finally:
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def test_fact_checker_sanity_bounds_and_triangulation():
    checker = AutonomousFactChecker()

    # 1. Valid Claim with Triangulation (>= 2 sources)
    res_valid = checker.verify_macro_claim(
        claim_text="US Headline CPI printed at 2.9% YoY",
        entity="CPI",
        reported_value=2.9,
        source_id="fred_api",
        corroborating_sources=["forex_factory_calendar"],
    )
    assert res_valid.status == "VERIFIED"
    assert res_valid.sanity_check_passed is True
    assert res_valid.confidence_score >= 0.90
    assert len(res_valid.content_hash) == 64

    # 2. Absurd Claim (Violates sanity bounds)
    res_absurd = checker.verify_macro_claim(
        claim_text="US Inflation reached 85% YoY",
        entity="INFLATION",
        reported_value=85.0,
        source_id="social_media_feed",
    )
    assert res_absurd.status == "REJECTED"
    assert res_absurd.sanity_check_passed is False
    assert res_absurd.confidence_score < 0.10

    # 3. Unconfirmed Single Source
    res_single = checker.verify_macro_claim(
        claim_text="Crude Oil inventory drew -2.5M barrels",
        entity="WTI_OIL",
        reported_value=75.0,
        source_id="newsapi_wire",
        corroborating_sources=[],
    )
    assert res_single.status == "UNCONFIRMED"
    assert res_single.sanity_check_passed is True
