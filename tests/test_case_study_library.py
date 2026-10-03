"""Unit tests for CaseStudyLibrary Master Repository and Universal Domain Categorization."""
import pytest
from intelligence.case_study_library import (
    CaseStudyLibrary,
    get_case_study_library,
    MASTER_CASE_STUDY_REGISTRY,
    MARITIME_CHOKEPOINT_CASES,
    TECH_HARDWARE_CASES,
    ENERGY_PETROCHEM_CASES,
    CRITICAL_MINERALS_CASES,
    AGRICULTURAL_FERTILIZER_CASES,
    SOVEREIGN_LIQUIDITY_CASES,
    MONETARY_POLICY_CASES,
    CURRENCY_PEG_CASES,
    SANCTIONS_FINANCE_CASES,
    SYSTEMIC_LIQUIDITY_CASES,
)
from intelligence.case_study_matcher import CaseStudyArchetype


def test_master_case_study_library_completeness():
    library = get_case_study_library()
    assert isinstance(library, CaseStudyLibrary)
    
    # Verify overall volume of case studies
    all_cases = library.list_all_cases()
    assert len(all_cases) >= 30
    assert len(MASTER_CASE_STUDY_REGISTRY) >= 30


def test_domain_distribution_and_coverage():
    library = get_case_study_library()
    domains = library.get_domains_summary()
    
    expected_domains = [
        "MARITIME_CHOKEPOINT",
        "TECH_HARDWARE",
        "ENERGY_PETROCHEM",
        "CRITICAL_MINERALS",
        "AGRICULTURAL_FERTILIZER",
        "SOVEREIGN_LIQUIDITY",
        "MONETARY_POLICY",
        "CURRENCY_PEG",
        "SANCTIONS_FINANCE",
        "SYSTEMIC_LIQUIDITY",
    ]
    
    for dom in expected_domains:
        assert dom in domains, f"Missing domain coverage for {dom}"
        assert domains[dom] >= 2, f"Domain {dom} should have multiple archetypes"


def test_archetype_structural_integrity():
    library = get_case_study_library()
    for case in library.list_all_cases():
        assert isinstance(case, CaseStudyArchetype)
        assert case.case_id.startswith("case_")
        assert len(case.label) > 5
        assert len(case.primary_domain) > 0
        assert len(case.chokepoints_affected) > 0
        assert len(case.primary_entities) > 0
        assert len(case.transmission_channels) > 0
        assert case.tau_substitution_horizon in ("DAYS", "MONTHS", "SEASON", "YEARS", "DECADES")
        assert len(case.core_spread_distortions) > 0
        assert case.prompt_curve_regime in ("BACKWARDATION", "CONTANGO", "INVERTED")
        assert len(case.historical_outcomes) > 0
        assert len(case.falsification_tripwires) > 0
        assert case.reference_url.startswith("http")


def test_library_domain_filtering_and_search():
    library = get_case_study_library()
    
    # Domain filter test
    maritime_cases = library.filter_by_domain("MARITIME_CHOKEPOINT")
    assert len(maritime_cases) >= 6
    assert all(c.primary_domain == "MARITIME_CHOKEPOINT" for c in maritime_cases)
    
    # Keyword search test
    results = library.search_cases("semiconductor")
    assert len(results) >= 2
    case_ids = [r.case_id for r in results]
    assert "case_c_ukraine_neon_semis" in case_ids or "case_r_us_china_gpu_export_controls" in case_ids


def test_to_dict_list_serialization():
    library = get_case_study_library()
    dict_list = library.to_dict_list()
    assert isinstance(dict_list, list)
    assert len(dict_list) >= 30
    for row in dict_list:
        assert "case_id" in row
        assert "execution_authority" in row
        assert row["execution_authority"] is False
