"""Unit tests for Module 06: Autonomous Case Study Research & Learning Engine."""
import json
import os
import pytest

from intelligence.case_study_learner import (
    CaseStudyLearner,
    CaseStudyLearningReport,
    get_case_study_learner,
)
from intelligence.case_study_matcher import get_case_study_matcher


def test_case_study_learner_flow(tmp_path):
    test_store = str(tmp_path / "test_learned_cases.json")
    learner = CaseStudyLearner(persistence_file=test_store)

    # 1. Learn new case study
    report = learner.learn_case_study(
        case_id="case_panama_canal_gatun_drought",
        label="Panama Canal Gatun Lake Extreme Drought Transit Restriction",
        primary_domain="MARITIME_CHOKEPOINT",
        primary_entities=["SHIPPING_FLOW", "FREIGHT_VOLUME", "INFLATION"],
        chokepoints_affected=["PANAMA_CANAL", "GATUN_LAKE"],
        transmission_channels=["DRAFT_RESTRICTION", "TRANSIT_AUCTION_SPIKE", "SUEZ_CAPE_REROUTE"],
        lead_time_days=14,
        duration_days=180,
        tau_substitution_horizon="SEASON",
        core_spread_distortions=["PANAMA_AUCTION_SLOT_FEE", "LNG_TRANSIT_BASIS"],
        historical_outcomes={
            "transits": "Daily transits slashed from 36 to 18 vessels",
            "auction": "Slot auction fees reached record $4M per crossing",
        },
        falsification_tripwires=[
            "Artificial cloud seeding and reservoir transfer restores 36 daily transits",
        ],
    )

    assert report.status == "LEARNED_SUCCESSFULLY"
    assert report.persisted_to_disk is True
    assert os.path.exists(test_store)

    # 2. Check in-memory registration in CaseStudyMatcher
    matcher = get_case_study_matcher()
    arch = matcher.get_case("case_panama_canal_gatun_drought")
    assert arch is not None
    assert arch.label == "Panama Canal Gatun Lake Extreme Drought Transit Restriction"
    assert "PANAMA_CANAL" in arch.chokepoints_affected

    # 3. Match against the newly learned case
    matches = matcher.match_event(
        event_domain="MARITIME_CHOKEPOINT",
        affected_entities=["SHIPPING_FLOW", "FREIGHT_VOLUME"],
        affected_chokepoints=["PANAMA_CANAL"],
        top_k=3,
    )
    assert any(m.matched_case_id == "case_panama_canal_gatun_drought" for m in matches)
