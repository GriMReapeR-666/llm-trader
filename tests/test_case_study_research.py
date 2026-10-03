"""Unit tests for CaseStudyResearchEngine and Dynamic Supply Shock Analyzer."""
import os
import tempfile
import pytest

from intelligence.case_study_research import (
    CaseStudyResearchEngine,
    CaseStudyResearchReport,
    SupplyShockDecomposition,
    get_case_study_research_engine,
)
from intelligence.case_study_matcher import get_case_study_matcher


def test_research_engine_singleton():
    engine = get_case_study_research_engine()
    assert isinstance(engine, CaseStudyResearchEngine)


def test_research_and_synthesize_maritime_shock():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        engine = CaseStudyResearchEngine(persistence_file=tmp_path)
        headline = "Heavy drone and anti-ship missile strikes in the Bab el-Mandeb strait force major container fleets to reroute around Africa."
        
        report = engine.research_and_synthesize(
            headline_or_text=headline,
            source_reference="https://antigravity.internal/test_maritime",
            auto_assimilate=True,
        )
        
        assert isinstance(report, CaseStudyResearchReport)
        assert report.primary_domain == "MARITIME_CHOKEPOINT"
        assert report.execution_authority is False
        assert len(report.decomposition.chokepoints) > 0
        assert len(report.decomposition.first_order_direct) > 0
        assert len(report.decomposition.second_order_transmission) > 0
        assert len(report.decomposition.third_order_systemic) > 0
        assert len(report.decomposition.falsification_tripwires) > 0
        assert "strategy_type" in report.decomposition.tradeable_playbook
        
        # Check that top historical analogues were matched
        assert len(report.top_historical_analogues) > 0
        top_analog = report.top_historical_analogues[0]
        assert top_analog.similarity_score > 0.0
        assert top_analog.matched_case_id in (
            "case_b_red_sea_reroute", "case_a_hormuz_blockade", "case_f_ever_given_suez",
            "case_m_panama_canal_drought_2023", "case_panama_canal_gatun_drought",
            "case_n_malacca_strait_congestion", "case_o_baltimore_bridge_collapse", report.case_id
        )
        
        # Check persistence
        assert report.persisted_to_disk is True
        assert os.path.exists(tmp_path)
        
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_research_and_synthesize_semiconductor_fab_shock():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        engine = CaseStudyResearchEngine(persistence_file=tmp_path)
        headline = "Major 7.2 magnitude earthquake in Hsinchu Science Park triggers automatic safety shutdowns across advanced TSMC semiconductor fabs."
        
        report = engine.research_and_synthesize(
            headline_or_text=headline,
            auto_assimilate=True,
        )
        
        assert report.primary_domain == "TECH_HARDWARE"
        assert "CORPORATE_HEALTH" in report.decomposition.primary_entities or "EQUITIES" in report.decomposition.primary_entities
        assert report.decomposition.tau_substitution in ("YEARS", "MONTHS", "DECADES")
        assert len(report.decomposition.falsification_tripwires) >= 2
        
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_research_and_synthesize_banking_liquidity_shock():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        engine = CaseStudyResearchEngine(persistence_file=tmp_path)
        headline = "Unprecedented digital bank run as depositors withdraw $30B from regional banks amid unrealized HTM bond losses."
        
        report = engine.research_and_synthesize(
            headline_or_text=headline,
            auto_assimilate=True,
        )
        
        assert report.primary_domain == "SOVEREIGN_LIQUIDITY"
        assert "LIQUIDITY_STRESS" in report.decomposition.primary_entities or "CREDIT_SPREAD" in report.decomposition.primary_entities
        assert report.decomposition.prompt_curve_regime == "INVERTED"
        
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
