"""Unit tests for Module 05: Case Study Matching, Graph Cognition & Analogical Pattern Recognition."""
import pytest

from intelligence.case_study_matcher import (
    calculate_case_based_jaccard_similarity,
    calculate_multi_dimensional_composite_case_similarity,
    traverse_causal_graph_isomorphic_path,
    detect_candidate_dark_nodes,
    CaseStudyMatcher,
    get_case_study_matcher,
    CASE_STUDY_REGISTRY,
)


def test_calculate_case_based_jaccard_similarity():
    # A = {WTI_OIL, INFLATION}, B = {WTI_OIL, INFLATION, CROP_HEALTH} -> intersection 2, union 3 -> 2/3 ~ 0.6667
    j = calculate_case_based_jaccard_similarity(
        ["WTI_OIL", "INFLATION"],
        ["WTI_OIL", "INFLATION", "CROP_HEALTH"],
    )
    assert pytest.approx(j, 1e-4) == 2.0 / 3.0

    # Disjoint sets -> 0.0
    j_zero = calculate_case_based_jaccard_similarity(["AAPL"], ["TSLA"])
    assert j_zero == 0.0

    # Empty sets -> 0.0
    j_empty = calculate_case_based_jaccard_similarity([], [])
    assert j_empty == 0.0


def test_calculate_multi_dimensional_composite_case_similarity():
    score = calculate_multi_dimensional_composite_case_similarity(
        entities_event=["SHIPPING_FLOW", "FREIGHT_VOLUME"],
        entities_case=["SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION"],
        chokepoints_event=["BAB_EL_MANDEB"],
        chokepoints_case=["BAB_EL_MANDEB", "SUEZ_CANAL"],
        spreads_event=["FREIGHTOS_FBX_CONTAINER"],
        spreads_case=["FREIGHTOS_FBX_CONTAINER"],
        domain_event="MARITIME_CHOKEPOINT",
        domain_case="MARITIME_CHOKEPOINT",
    )
    assert 0.60 <= score <= 1.0


def test_traverse_causal_graph_isomorphic_path():
    causal_dag = {
        "BAB_EL_MANDEB": ["CAPE_REROUTE"],
        "CAPE_REROUTE": ["FREIGHTOS_FBX_SPIKE", "BUNKER_FUEL_SPIKE"],
        "FREIGHTOS_FBX_SPIKE": ["IMPORT_INFLATION"],
        "BUNKER_FUEL_SPIKE": ["REFINERY_MARGIN_COMPRESSION"],
    }
    paths = traverse_causal_graph_isomorphic_path("BAB_EL_MANDEB", causal_dag, max_depth=4)
    assert len(paths) >= 2
    # One path should be BAB_EL_MANDEB -> CAPE_REROUTE -> FREIGHTOS_FBX_SPIKE -> IMPORT_INFLATION
    expected_path = ["BAB_EL_MANDEB", "CAPE_REROUTE", "FREIGHTOS_FBX_SPIKE", "IMPORT_INFLATION"]
    assert expected_path in paths


def test_detect_candidate_dark_nodes():
    observed = ["BAB_EL_MANDEB", "U_CHOKEPOINT_SURCHARGE", "NEW_WAR_RISK_POOL"]
    existing = ["BAB_EL_MANDEB", "SUEZ_CANAL", "INFLATION"]
    dark_nodes = detect_candidate_dark_nodes(observed, existing)
    assert "U_CHOKEPOINT_SURCHARGE" in dark_nodes
    assert "NEW_WAR_RISK_POOL" in dark_nodes
    assert "BAB_EL_MANDEB" not in dark_nodes


def test_case_study_matcher_engine():
    matcher = get_case_study_matcher()
    assert len(matcher.registry) >= 16

    # Match Red Sea crisis
    matches = matcher.match_event(
        event_domain="MARITIME_CHOKEPOINT",
        affected_entities=["SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION"],
        affected_chokepoints=["BAB_EL_MANDEB", "RED_SEA"],
        observed_spreads=["FREIGHTOS_FBX_CONTAINER"],
        top_k=3,
    )
    assert len(matches) == 3
    top_match = matches[0]
    assert top_match.matched_case_id in ("case_b_red_sea_reroute", "case_f_ever_given_suez", "case_a_hormuz_blockade")
    assert top_match.similarity_score >= 0.50
    assert len(top_match.falsification_tripwires) > 0
