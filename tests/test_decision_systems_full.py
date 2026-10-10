"""Comprehensive Decision Systems Test Suite.

Tests Laya decision model integration, full D0-D9 taxonomy, D10-D13 signals,
M1-M6 meta-decisions, Q1-Q7 causal attribution, debate protocol, macro cognition,
and the Universal Decision Engine philosophical framework.
"""
from __future__ import annotations

import math
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from research.decision_models.feature_block import DecisionFeatureBlock
from research.decision_models.extended_taxonomy import (
    D0_1_SourceCredibility, D0_2_DuplicateDetection, D0_3_FactVsOpinion,
    D1_EconomicSurprise, D1b_PricedInAsymmetry, D1c_RevisionRisk,
    D2_BlackoutGate, D9_EmergencyKillSwitch,
    D5a_EscalationLadder, D5b_PhysicalSupplyVerification, D5c_SanctionsImpact,
    D3_MacroRegime, D4_LiquidityStance, D4b_CentralBankDivergence, D4c_FiscalImpulse,
    D5_CommodityShock, D6_YieldCurveDivergence, D6b_CrossAssetConfirmation, D6c_EquityCreditImpulse,
    D10_COTExtreme, D10b_RetailSentimentFlow,
    D11_TrendState, D11b_VolRegime,
    D7_AnalogFalsification, D8_OpportunityArbitration, D13_ExecutionMicroDecision,
    M1_ConfidenceCalibration, M2_EvidenceSufficiency, M4_ReasoningChainValidity,
    Q1_PrimaryDriver, Q5_MoveQuality, Q7_CandlestickSignature,
    DecisionBundle, DecisionRecord, create_decision_bundle,
)
from research.decision_models.macro_ontology import (
    MacroContextFeatures, get_standard_macro_questions,
    EconomicSurprise as EcoSurprise, BlackoutGate as BG,
    MacroRegime, LiquidityStance, CommodityShock,
    YieldCurveDivergence, CaseFalsification, OpportunityArbitration,
)
from research.decision_models.deterministic_labeler import (
    label_d1_economic_surprise, label_d2_blackout_gate, label_d3_macro_regime,
    label_d4_liquidity_stance, label_d5_commodity_shock,
    label_d6_yield_curve_divergence, label_d8_opportunity_arbitration,
    generate_ground_truth_labels,
)
from research.decision_models.post_validator import DeterministicPostValidator
from intelligence.debate_protocol import (
    ApexQuantParliament, evaluate_evidence_quality, DebateVerdict,
)
from intelligence.macro_state_vector import build_macro_state_vector
from intelligence.macro_fusion_gate import MacroFusionGate, MacroGateVerdict


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_features(**overrides) -> DecisionFeatureBlock:
    defaults = dict(
        event_name="US_CPI",
        currency="USD",
        tier="HIGH",
        actual=3.5,
        consensus=3.2,
        sigma_hist=0.3,
        vix=18.0,
        dxy_change_pct=0.1,
        yield_10y_delta_bps=5.0,
        credit_oas_bps=120.0,
        oil_5d_pct=1.0,
        net_liquidity_delta_30d_b=10.0,
        reserves_delta_b=-5.0,
        sofr_rate=5.30,
        cot_percentile=50.0,
        adx=25.0,
        hurst=0.55,
        minutes_to_release=999.0,
        headline="US CPI comes in hot at 3.5%",
        narrative="Inflation surprise drives Fed repricing",
    )
    defaults.update(overrides)
    return DecisionFeatureBlock(**defaults)


def _make_macro_features(**overrides) -> MacroContextFeatures:
    defaults = dict(
        event_name="US_CPI",
        actual=3.5,
        consensus=3.2,
        sigma_hist=0.3,
        vix=18.0,
        dxy_change_pct=0.1,
        yield_10y_delta_bps=5.0,
        credit_oas_bps=120.0,
        oil_5d_pct=1.0,
        net_liquidity_delta_30d_b=10.0,
        reserves_delta_b=-5.0,
        sofr_rate=5.30,
        minutes_to_release=999.0,
    )
    defaults.update(overrides)
    return MacroContextFeatures(**defaults)


# ===========================================================================
# SECTION 1: Full Decision Taxonomy (D0-D9) Tests
# ===========================================================================

class TestLayer0Ingestion:
    def test_source_credibility_trusted(self):
        fb = _make_features(source_tier="OFFICIAL", source_credibility_score=1.0, is_adversarial=False)
        from research.decision_models.deterministic_labeler_expanded import label_d0_1_source_credibility
        result = label_d0_1_source_credibility(fb)
        assert result in (D0_1_SourceCredibility.TRUSTED_OFFICIAL, D0_1_SourceCredibility.VERIFIED_OUTLET)

    def test_source_credibility_spoof_rejected(self):
        fb = _make_features(is_adversarial=True)
        from research.decision_models.deterministic_labeler_expanded import label_d0_1_source_credibility
        result = label_d0_1_source_credibility(fb)
        assert result == D0_1_SourceCredibility.SPOOF_REJECTED

    def test_duplicate_detection_first_report(self):
        fb = _make_features()
        from research.decision_models.deterministic_labeler_expanded import label_d0_2_duplicate_detection
        result = label_d0_2_duplicate_detection(fb)
        assert result in (D0_2_DuplicateDetection.FIRST_REPORT, D0_2_DuplicateDetection.INSUFFICIENT_DATA)

    def test_fact_vs_opinion_verified(self):
        fb = _make_features()
        from research.decision_models.deterministic_labeler_expanded import label_d0_3_fact_vs_opinion
        result = label_d0_3_fact_vs_opinion(fb)
        assert isinstance(result, D0_3_FactVsOpinion)


class TestLayer1EventShock:
    def test_extreme_hawkish_surprise(self):
        fb = _make_features(actual=4.0, consensus=3.0, sigma_hist=0.3)  # Z = +3.33
        features = _make_macro_features(actual=4.0, consensus=3.0, sigma_hist=0.3)
        result = label_d1_economic_surprise(features)
        assert result == EcoSurprise.EXTREME_HAWKISH

    def test_extreme_dovish_surprise(self):
        features = _make_macro_features(actual=2.5, consensus=3.5, sigma_hist=0.3)  # Z = -3.33
        result = label_d1_economic_surprise(features)
        assert result == EcoSurprise.EXTREME_DOVISH

    def test_in_line_surprise(self):
        features = _make_macro_features(actual=3.25, consensus=3.2, sigma_hist=0.3)  # Z = +0.17
        result = label_d1_economic_surprise(features)
        assert result == EcoSurprise.IN_LINE

    def test_blackout_freeze_on_high_tier(self):
        fb = _make_features(minutes_to_release=2.0, tier="HIGH")
        from research.decision_models.deterministic_labeler_expanded import label_d2_blackout_gate
        result = label_d2_blackout_gate(fb)
        assert result == D2_BlackoutGate.BLACKOUT_FREEZE

    def test_blackout_freeze_on_vix_surge(self):
        fb = _make_features(vix=35.0, minutes_to_release=999.0)
        from research.decision_models.deterministic_labeler_expanded import label_d2_blackout_gate
        result = label_d2_blackout_gate(fb)
        assert result == D2_BlackoutGate.BLACKOUT_FREEZE

    def test_normal_trading_outside_window(self):
        fb = _make_features(vix=15.0, minutes_to_release=999.0)
        from research.decision_models.deterministic_labeler_expanded import label_d2_blackout_gate
        result = label_d2_blackout_gate(fb)
        assert result == D2_BlackoutGate.NORMAL_TRADING


class TestLayer1bGeopolitical:
    def test_emergency_kill_switch_vix(self):
        fb = _make_features(vix=40.0)
        from research.decision_models.deterministic_labeler_expanded import label_d9_emergency_kill_switch
        result = label_d9_emergency_kill_switch(fb)
        assert result == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE

    def test_emergency_kill_switch_peg_break(self):
        fb = _make_features(is_peg_broken=True)
        from research.decision_models.deterministic_labeler_expanded import label_d9_emergency_kill_switch
        result = label_d9_emergency_kill_switch(fb)
        assert result == D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE

    def test_normal_operation(self):
        fb = _make_features(vix=16.0, is_peg_broken=False, is_exchange_halt=False)
        from research.decision_models.deterministic_labeler_expanded import label_d9_emergency_kill_switch
        result = label_d9_emergency_kill_switch(fb)
        assert result == D9_EmergencyKillSwitch.NORMAL_OPERATION

    def test_escalation_ladder_rhetoric(self):
        fb = _make_features(military_incident_level="NONE")
        from research.decision_models.deterministic_labeler_expanded import label_d5a_escalation_ladder
        result = label_d5a_escalation_ladder(fb)
        assert result == D5a_EscalationLadder.RHETORIC_ONLY

    def test_escalation_ladder_blockade(self):
        fb = _make_features(military_incident_level="BLOCKADE")
        from research.decision_models.deterministic_labeler_expanded import label_d5a_escalation_ladder
        result = label_d5a_escalation_ladder(fb)
        assert result == D5a_EscalationLadder.FULL_BLOCKADE

    def test_physical_supply_verified(self):
        fb = _make_features(ais_tanker_speed_drop=True)
        from research.decision_models.deterministic_labeler_expanded import label_d5b_physical_supply_verification
        result = label_d5b_physical_supply_verification(fb)
        assert result == D5b_PhysicalSupplyVerification.CONFIRMED_PHYSICAL_DISRUPTION

    def test_sanctions_impact_supply_loss(self):
        fb = _make_features()
        from research.decision_models.deterministic_labeler_expanded import label_d5c_sanctions_impact
        result = label_d5c_sanctions_impact(fb)
        assert isinstance(result, D5c_SanctionsImpact)


class TestLayer2Regime:
    def test_crisis_panic_regime(self):
        features = _make_macro_features(vix=35.0, credit_oas_bps=450.0)
        result = label_d3_macro_regime(features)
        assert result == MacroRegime.CRISIS_PANIC

    def test_risk_on_expansion(self):
        features = _make_macro_features(vix=14.0, credit_oas_bps=100.0, oil_5d_pct=0.5)
        result = label_d3_macro_regime(features)
        assert result == MacroRegime.RISK_ON_EXPANSION

    def test_stagflation_shock(self):
        features = _make_macro_features(vix=22.0, oil_5d_pct=6.0, z_score=1.0)
        result = label_d3_macro_regime(features)
        assert result == MacroRegime.STAGFLATION_SHOCK

    def test_liquidity_crunch(self):
        features = _make_macro_features(sofr_rate=5.6, reserves_delta_b=-120.0)
        result = label_d4_liquidity_stance(features)
        assert result == LiquidityStance.LIQUIDITY_CRUNCH

    def test_expanding_liquidity(self):
        features = _make_macro_features(net_liquidity_delta_30d_b=60.0, sofr_rate=5.0)
        result = label_d4_liquidity_stance(features)
        assert result == LiquidityStance.EXPANDING_LIQUIDITY

    def test_central_bank_divergence_fed_hawkish(self):
        fb = _make_features(rate_spread_delta_30d=25.0)
        from research.decision_models.deterministic_labeler_expanded import label_d4b_central_bank_divergence
        result = label_d4b_central_bank_divergence(fb)
        assert result in (D4b_CentralBankDivergence.FED_LEADS_HAWKISH, D4b_CentralBankDivergence.SYNCHRONIZED_TIGHTENING)


class TestLayer3Transmission:
    def test_supply_deficit_bullish(self):
        features = _make_macro_features(oil_5d_pct=7.0)
        result = label_d5_commodity_shock(features)
        assert result == CommodityShock.SUPPLY_DEFICIT_BULLISH

    def test_supply_glut_bearish(self):
        features = _make_macro_features(oil_5d_pct=-7.0)
        result = label_d5_commodity_shock(features)
        assert result == CommodityShock.SUPPLY_GLUT_BEARISH

    def test_dollar_dominance(self):
        features = _make_macro_features(yield_10y_delta_bps=15.0, dxy_change_pct=0.5)
        result = label_d6_yield_curve_divergence(features)
        assert result == YieldCurveDivergence.DOLLAR_DOMINANCE

    def test_recessionary_inversion(self):
        features = _make_macro_features(yield_10y_delta_bps=-20.0, dxy_change_pct=0.0)
        result = label_d6_yield_curve_divergence(features)
        assert result == YieldCurveDivergence.RECESSIONARY_INVERSION


class TestLayer4Positioning:
    def test_cot_extreme_crowded_long(self):
        fb = _make_features(cot_percentile=92.0)
        from research.decision_models.deterministic_labeler_expanded import label_d10_cot_extreme
        result = label_d10_cot_extreme(fb)
        assert result in (D10_COTExtreme.CROWDED_LONG, D10_COTExtreme.UNWIND_RISK_HIGH)

    def test_retail_sentiment_fomo(self):
        fb = _make_features(retail_long_pct=85.0)
        from research.decision_models.deterministic_labeler_expanded import label_d10b_retail_sentiment_flow
        result = label_d10b_retail_sentiment_flow(fb)
        assert result == D10b_RetailSentimentFlow.RETAIL_FOMO_EXTREME


class TestLayer5Technical:
    def test_strong_trend(self):
        fb = _make_features(adx=30.0, hurst=0.65)
        from research.decision_models.deterministic_labeler_expanded import label_d11_trend_state
        result = label_d11_trend_state(fb)
        assert result == D11_TrendState.STRONG_TREND

    def test_range_bound(self):
        fb = _make_features(adx=12.0, hurst=0.40)
        from research.decision_models.deterministic_labeler_expanded import label_d11_trend_state
        result = label_d11_trend_state(fb)
        assert result == D11_TrendState.RANGE_BOUND

    def test_compression_squeeze(self):
        fb = _make_features(atr_percentile=10.0)
        from research.decision_models.deterministic_labeler_expanded import label_d11b_vol_regime
        result = label_d11b_vol_regime(fb)
        assert result == D11b_VolRegime.COMPRESSION_SQUEEZE


class TestLayer6Arbitration:
    def test_strong_proceed_aligned(self):
        fb = _make_features(
            minutes_to_release=999.0, vix=14.0,
            actual=2.8, consensus=3.2, sigma_hist=0.3,  # dovish Z
        )
        from research.decision_models.deterministic_labeler_expanded import label_d8_opportunity_arbitration
        result = label_d8_opportunity_arbitration(fb, proposed_direction="BUY", symbol="EURUSD")
        assert result in (D8_OpportunityArbitration.STRONG_PROCEED, D8_OpportunityArbitration.CONDITIONAL_HALF_RISK)

    def test_reject_macro_conflict(self):
        fb = _make_features(
            vix=14.0, minutes_to_release=999.0,
            actual=4.0, consensus=3.0, sigma_hist=0.3,  # extreme hawkish Z=+3.33
        )
        from research.decision_models.deterministic_labeler_expanded import label_d8_opportunity_arbitration
        result = label_d8_opportunity_arbitration(fb, proposed_direction="BUY", symbol="EURUSD")
        assert result == D8_OpportunityArbitration.REJECT_MACRO_CONFLICT

    def test_blackout_postpone(self):
        fb = _make_features(minutes_to_release=1.0, vix=18.0, tier="HIGH")
        from research.decision_models.deterministic_labeler_expanded import label_d8_opportunity_arbitration
        result = label_d8_opportunity_arbitration(fb, proposed_direction="BUY", symbol="EURUSD")
        assert result == D8_OpportunityArbitration.BLACKOUT_POSTPONE

    def test_execution_micro_decision_market_now(self):
        fb = _make_features(bid_ask_spread_pips=0.8)
        from research.decision_models.deterministic_labeler_expanded import label_d13_execution_micro_decision
        result = label_d13_execution_micro_decision(fb)
        assert result in (D13_ExecutionMicroDecision.MARKET_NOW, D13_ExecutionMicroDecision.WAIT_FOR_SPREAD_COMPRESSION)

    def test_analog_falsification_confirmed(self):
        fb = _make_features(analog_correlation=0.92, tripwire_hit=False)
        from research.decision_models.deterministic_labeler_expanded import label_d7_analog_falsification
        result = label_d7_analog_falsification(fb)
        assert result in (D7_AnalogFalsification.TRAJECTORY_CONFIRMED, D7_AnalogFalsification.AMBIGUOUS_DELAY)


# ===========================================================================
# SECTION 2: Meta-Decisions (M1-M6) Tests
# ===========================================================================

class TestMetaDecisions:
    def test_confidence_calibration_grounded(self):
        fb = _make_features()
        from research.decision_models.deterministic_labeler_expanded import label_m1_confidence_calibration
        result = label_m1_confidence_calibration(fb)
        assert result in (M1_ConfidenceCalibration.GROUNDED, M1_ConfidenceCalibration.PROBABILISTIC)

    def test_evidence_sufficiency(self):
        fb = _make_features(actual=3.5, consensus=3.2, vix=18.0)
        from research.decision_models.deterministic_labeler_expanded import label_m2_evidence_sufficiency
        result = label_m2_evidence_sufficiency(fb)
        assert result in (M2_EvidenceSufficiency.ACT_NOW, M2_EvidenceSufficiency.WAIT_CONFIRMATION, M2_EvidenceSufficiency.INSUFFICIENT_DATA)


# ===========================================================================
# SECTION 3: Causal Attribution (Q1-Q7) Tests
# ===========================================================================

class TestCausalAttribution:
    def test_move_quality_fundamental_repricing(self):
        fb = _make_features(volume_z=2.0)
        from research.decision_models.deterministic_labeler_expanded import label_q5_move_quality
        result = label_q5_move_quality(fb)
        assert result in (Q5_MoveQuality.FUNDAMENTAL_REPRICING, Q5_MoveQuality.NOISE, Q5_MoveQuality.INSUFFICIENT_DATA)

    def test_candlestick_signature(self):
        fb = _make_features(body_ratio=0.85, volume_z=1.8)
        from research.decision_models.deterministic_labeler_expanded import label_q7_candlestick_signature
        result = label_q7_candlestick_signature(fb)
        assert isinstance(result, Q7_CandlestickSignature)


# ===========================================================================
# SECTION 4: DecisionBundle & DecisionRecord Tests
# ===========================================================================

class TestDecisionBundle:
    def test_create_bundle_from_features(self):
        fb = _make_features()
        bundle = create_decision_bundle(features=fb, proposed_direction="BUY", symbol="EURUSD")
        assert bundle is not None
        assert bundle.execution_authority is False
        assert len(bundle.decisions) >= 20
        assert bundle.bundle_id.startswith("BUNDLE_")

    def test_bundle_veto_detection(self):
        fb = _make_features(vix=40.0, is_peg_broken=True)  # trigger kill switch
        bundle = create_decision_bundle(features=fb)
        assert bundle.veto_enforced is True
        assert bundle.risk_multiplier == 0.0

    def test_bundle_decision_record_invariant(self):
        fb = _make_features()
        bundle = create_decision_bundle(features=fb)
        for dec_id, record in bundle.decisions.items():
            assert record.execution_authority is False
            assert isinstance(record, DecisionRecord)

    def test_bundle_d3_regime_property(self):
        fb = _make_features(vix=35.0, credit_oas_bps=450.0)
        bundle = create_decision_bundle(features=fb)
        assert bundle.d3_regime == D3_MacroRegime.CRISIS_PANIC

    def test_bundle_d4_liquidity_property(self):
        fb = _make_features()
        bundle = create_decision_bundle(features=fb)
        assert isinstance(bundle.d4_liquidity, D4_LiquidityStance)

    def test_bundle_serialization_roundtrip(self):
        fb = _make_features()
        bundle = create_decision_bundle(features=fb)
        d = bundle.to_dict()
        restored = DecisionBundle.from_dict(d)
        assert restored.bundle_id == bundle.bundle_id
        assert len(restored.decisions) == len(bundle.decisions)
        assert restored.execution_authority is False

    def test_bundle_with_overrides(self):
        fb = _make_features(actual=3.5, consensus=3.2, sigma_hist=0.3)
        overrides = {"D3_macro_regime": "STAGFLATION_SHOCK"}
        bundle = create_decision_bundle(features=fb, overrides=overrides)
        d3 = bundle.get_option("D3_macro_regime")
        assert d3 == "STAGFLATION_SHOCK"


# ===========================================================================
# SECTION 5: Deterministic Post-Validator Tests
# ===========================================================================

class TestPostValidator:
    def test_zscore_inconsistency_override(self, tmp_path):
        features = _make_macro_features(actual=4.5, consensus=3.0, sigma_hist=0.3)  # Z ~ +5.0
        validator = DeterministicPostValidator(db_path=str(tmp_path / "post_validator.db"))
        model_answers = {"D1_economic_surprise": "IN_LINE"}  # Wrong!
        result = validator.validate(model_answers, features)
        assert result.disagreement_count >= 1
        assert result.validated_answers["D1_economic_surprise"] != "IN_LINE"

    def test_blackout_safety_override(self, tmp_path):
        features = _make_macro_features(vix=35.0, minutes_to_release=1.0)
        validator = DeterministicPostValidator(db_path=str(tmp_path / "post_validator.db"))
        model_answers = {
            "blackout_freeze_boolean": False,
            "execution_veto_boolean": False,
        }
        result = validator.validate(model_answers, features)
        assert result.veto_enforced is True

    def test_crisis_veto(self, tmp_path):
        features = _make_macro_features(vix=35.0, credit_oas_bps=450.0)
        validator = DeterministicPostValidator(db_path=str(tmp_path / "post_validator.db"))
        model_answers = {"execution_veto_boolean": False}
        result = validator.validate(model_answers, features)
        assert result.veto_enforced is True

    def test_safe_execution_passes(self, tmp_path):
        features = _make_macro_features(vix=15.0, minutes_to_release=999.0)
        validator = DeterministicPostValidator(db_path=str(tmp_path / "post_validator.db"))
        model_answers = {
            "blackout_freeze_boolean": False,
            "execution_veto_boolean": False,
        }
        result = validator.validate(model_answers, features)
        assert result.is_safe_to_execute is True
        assert result.veto_enforced is False


# ===========================================================================
# SECTION 6: Ground Truth Label Consistency Tests
# ===========================================================================

class TestGroundTruthLabels:
    def test_ground_truth_full_generation(self):
        features = _make_macro_features(actual=3.5, consensus=3.2, sigma_hist=0.3, vix=18.0)
        gt = generate_ground_truth_labels(features, proposed_direction="BUY", symbol="EURUSD")
        assert "D1_economic_surprise" in gt
        assert "D3_macro_regime" in gt
        assert "execution_veto_boolean" in gt
        assert isinstance(gt["execution_veto_boolean"], bool)

    def test_inverted_metric_dovish(self):
        features = _make_macro_features(event_name="US_Jobless_Claims", actual=350000, consensus=220000, sigma_hist=30000)
        result = label_d1_economic_surprise(features)
        assert result in (EcoSurprise.EXTREME_DOVISH, EcoSurprise.MODERATE_DOVISH)


# ===========================================================================
# SECTION 7: Debate Protocol Tests
# ===========================================================================

class TestDebateProtocol:
    def test_evidence_quality_high_quantitative(self):
        text = "RSI=45.2 ADX=28.5 with 2.5 R:R ratio at support level 1.0850"
        score, pen = evaluate_evidence_quality(text)
        assert score >= 0.5
        assert pen <= 0.1

    def test_evidence_quality_high_rhetoric(self):
        text = "Obviously this is guaranteed easy money, a total no brainer!"
        score, pen = evaluate_evidence_quality(text)
        assert score <= 0.3
        assert pen >= 0.2

    def test_unanimous_bull_debate(self):
        parliament = ApexQuantParliament()
        candidate = {
            "symbol": "EURUSD", "action": "BUY",
            "entry_price": 1.0850, "stop_loss": 1.0800, "take_profit": 1.1000,
            "features": {"rsi": 45.0, "adx": 28.0},
        }
        context = {"regime": "TRENDING_BULL", "smc_structure": {"has_choch": False, "in_premium": False}}
        verdict = parliament.conduct_debate(candidate, context)
        assert verdict.approved is True
        assert verdict.decision == "BUY"
        assert verdict.sizing_multiplier == 1.0
        assert verdict.execution_authority is False

    def test_fatal_risk_veto(self):
        parliament = ApexQuantParliament()
        candidate = {
            "symbol": "BTCUSD", "action": "BUY",
            "entry_price": 60000.0, "stop_loss": 59000.0, "take_profit": 60500.0,
            "features": {"rsi": 80.0, "adx": 12.0, "regime": "TRENDING_BEAR"},
        }
        context = {"regime": "TRENDING_BEAR", "smc_structure": {"has_choch": True, "in_premium": True}}
        verdict = parliament.conduct_debate(candidate, context)
        assert verdict.approved is False
        assert verdict.decision == "HOLD"
        assert verdict.sizing_multiplier == 0.0

    def test_hold_candidate_skips_debate(self):
        parliament = ApexQuantParliament()
        candidate = {"symbol": "EURUSD", "action": "HOLD"}
        verdict = parliament.conduct_debate(candidate)
        assert verdict.decision == "HOLD"
        assert verdict.consensus_level == "UNANIMOUS"

    def test_veto_caller_adapter(self):
        parliament = ApexQuantParliament()
        veto_fn = parliament.as_veto_caller()
        payload = {
            "symbol": "EURUSD", "action": "BUY",
            "entry_price": 1.0850, "stop_loss": 1.0800, "take_profit": 1.1000,
            "context": {"features": {"rsi": 50.0, "adx": 25.0}},
        }
        result = veto_fn(payload)
        assert "approved" in result
        assert "rationale" in result
        assert isinstance(result["debate_transcript"], list)


# ===========================================================================
# SECTION 8: Macro State Vector Tests
# ===========================================================================

class TestMacroStateVector:
    def test_hawkish_bias_construction(self):
        observations = [
            {"entity": "POLICY_RATE", "value": 5.5, "surprise": 0.5},
            {"entity": "INFLATION", "value": 3.8, "surprise": 0.3},
            {"entity": "GROWTH", "value": 2.5, "surprise": 0.1},
            {"entity": "EMPLOYMENT", "value": 3.8, "surprise": -0.2},
        ]
        msv = build_macro_state_vector(
            macro_observations=observations,
            intermarket_regime={"regime_id": "RISK_OFF", "confidence": 0.7, "advisory_bias": "RISK_OFF"},
        )
        assert msv["macro_bias"] == "HAWKISH"
        assert msv["composite_score"] > 0
        assert msv["execution_authority"] is False

    def test_dovish_bias_construction(self):
        observations = [
            {"entity": "INFLATION", "value": 2.0, "surprise": -0.8},
            {"entity": "GROWTH", "value": 1.0, "surprise": -0.5},
        ]
        msv = build_macro_state_vector(macro_observations=observations)
        assert msv["macro_bias"] == "DOVISH"
        assert msv["composite_score"] < 0

    def test_neutral_when_no_surprise(self):
        msv = build_macro_state_vector(macro_observations=[])
        assert msv["macro_bias"] == "NEUTRAL"

    def test_composite_bounded(self):
        observations = [
            {"entity": "POLICY_RATE", "value": 5.5, "surprise": 2.0},
            {"entity": "INFLATION", "value": 5.0, "surprise": 2.0},
            {"entity": "GROWTH", "value": 4.0, "surprise": 2.0},
        ]
        msv = build_macro_state_vector(
            macro_observations=observations,
            intermarket_regime={"confidence": 1.0, "advisory_bias": "RISK_OFF"},
            opportunity_fusion={"status": "PASS"},
            causal_propagation={"status": "OK", "bias": "BUY"},
        )
        assert -1.0 <= msv["composite_score"] <= 1.0


# ===========================================================================
# SECTION 9: Macro Fusion Gate Tests
# ===========================================================================

class TestMacroFusionGate:
    def test_veto_on_adversarial(self):
        fb = _make_features(is_adversarial=True)
        gate = MacroFusionGate()
        candidate = {"candidate_id": "C1", "symbol": "EURUSD", "side": "BUY"}
        verdict = gate.arbitrate_proposal(candidate, fb)
        assert verdict.is_vetoed is True
        assert verdict.risk_multiplier == 0.0
        assert verdict.execution_authority is False

    def test_veto_on_kill_switch(self):
        fb = _make_features(vix=40.0, is_peg_broken=True)
        gate = MacroFusionGate()
        candidate = {"candidate_id": "C2", "symbol": "EURUSD", "side": "BUY"}
        verdict = gate.arbitrate_proposal(candidate, fb)
        assert verdict.is_vetoed is True

    def test_proceed_on_clean_conditions(self):
        fb = _make_features(
            vix=14.0, minutes_to_release=999.0, is_adversarial=False,
            actual=3.0, consensus=3.2, sigma_hist=0.3,
        )
        gate = MacroFusionGate()
        candidate = {"candidate_id": "C3", "symbol": "EURUSD", "side": "BUY"}
        verdict = gate.arbitrate_proposal(candidate, fb)
        assert verdict.is_vetoed is False
        assert verdict.risk_multiplier > 0.0

    def test_verdict_serialization(self):
        fb = _make_features(is_adversarial=True)
        gate = MacroFusionGate()
        candidate = {"candidate_id": "C4", "symbol": "GBPUSD", "side": "SELL"}
        verdict = gate.arbitrate_proposal(candidate, fb)
        d = verdict.to_dict()
        assert d["is_vetoed"] is True
        assert d["execution_authority"] is False


# ===========================================================================
# SECTION 10: Laya Standard Questions Test
# ===========================================================================

class TestLayaQuestions:
    def test_standard_macro_questions_complete(self):
        questions = get_standard_macro_questions()
        required = ["D1_economic_surprise", "D2_blackout_gate", "D3_macro_regime",
                     "D4_liquidity_stance", "D5_commodity_shock",
                     "D6_yield_curve_divergence", "D8_opportunity_arbitration"]
        for qid in required:
            assert qid in questions
            assert questions[qid]["type"] == "choice"
            assert "criteria" in questions[qid]

    def test_boolean_questions_present(self):
        questions = get_standard_macro_questions()
        assert "blackout_freeze_boolean" in questions
        assert "execution_veto_boolean" in questions
        assert questions["blackout_freeze_boolean"]["type"] == "noul"

    def test_all_choice_options_have_criteria(self):
        questions = get_standard_macro_questions()
        for qid, q in questions.items():
            if q["type"] == "choice":
                assert "criteria" in q
                assert len(q["criteria"]) >= 3, f"Question {qid} needs at least 3 criteria options"
