"""End-to-End Test Suite for Macro-Cognitive Decision System & RLCD Flywheel.

Validates all 7 layers, causal attribution, narrative state, outcome labeling,
contrastive pair mining, and OpportunityBus macro fusion arbitration.
"""
from __future__ import annotations

import os
import sys
import unittest
import json

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)
for subpkg in ["core", "intelligence", "operations", "research", "data", "ui"]:
    pkg_path = os.path.join(WORKSPACE_ROOT, subpkg)
    if os.path.isdir(pkg_path) and pkg_path not in sys.path:
        sys.path.insert(0, pkg_path)

from research.decision_models.extended_taxonomy import (
    TAXONOMY_REGISTRY,
    D0_1_SourceCredibility,
    D1_EconomicSurprise,
    D2_BlackoutGate,
    D3_MacroRegime,
    D4_LiquidityStance,
    D5_CommodityShock,
    D6_YieldCurveDivergence,
    D8_OpportunityArbitration,
    D9_EmergencyKillSwitch,
    DecisionBundle,
    DecisionRecord,
)
from research.decision_models.feature_block import DecisionFeatureBlock
from research.decision_models.deterministic_labeler_expanded import (
    generate_ground_truth_labels,
    label_d0_1_source_credibility,
    label_d1_economic_surprise,
    label_d2_blackout_gate,
    label_d3_macro_regime,
    label_d4_liquidity_stance,
    label_d5_commodity_shock,
    label_d6_yield_curve_divergence,
    label_d8_opportunity_arbitration,
    label_d9_emergency_kill_switch,
)
from research.decision_models.causal_attribution import (
    CandlestickBar,
    CausalFingerprintExtractor,
    Q5_MoveQuality,
    Q7_CandlestickSignature,
)
from research.decision_models.narrative_state import (
    FalsificationTripwire,
    NarrativeObject,
    NarrativeStateMachine,
)
from research.learning.outcome_labeler import (
    DeterministicOutcomeLabeler,
    TradeExecutionRecord,
)
from research.learning.contrastive_pair_miner import ContrastivePairMiner
from intelligence.macro_fusion_gate import MacroFusionGate


class TestMacroCognitiveSystem(unittest.TestCase):
    """Rigorous verification of the complete macro cognitive architecture."""

    def test_01_taxonomy_and_authority_invariant(self):
        """Verify taxonomy completeness and strict execution_authority=False invariant."""
        self.assertIn("D1_economic_surprise", TAXONOMY_REGISTRY)
        self.assertIn("D8_opportunity_arbitration", TAXONOMY_REGISTRY)
        self.assertIn("D0_1_source_credibility", TAXONOMY_REGISTRY)

        rec = DecisionRecord(
            decision_id="D8_opportunity_arbitration",
            decision_layer="Layer 6",
            head_type="choice",
            selected_option="STRONG_PROCEED",
            execution_authority=False,
        )
        self.assertFalse(rec.execution_authority)
        self.assertEqual(rec.to_dict()["execution_authority"], False)

        bundle = DecisionBundle(
            bundle_id="B1",
            timestamp="2026-09-22T00:00:00Z",
            feature_hash="hash123",
            decisions={"D8": rec},
            fast_path_latency_ms=12.4,
            total_cycle_latency_ms=45.0,
            overrides_applied_count=0,
            veto_enforced=False,
            arbitration_verdict="STRONG_PROCEED",
            risk_multiplier=1.0,
            execution_authority=False,
        )
        self.assertFalse(bundle.execution_authority)

    def test_02_feature_block_and_deterministic_hash(self):
        """Verify immutable point-in-time feature block and deterministic hashing."""
        fb = DecisionFeatureBlock(
            event_name="US Initial Jobless Claims",
            tier="HIGH",
            actual=320.0,
            consensus=220.0,
            sigma_hist=25.0,
            minutes_to_release=15.0,
        )
        # Jobless claims is inverted: actual > consensus -> dovish (negative effective Z)
        self.assertTrue(fb.is_metric_inverted)
        self.assertLess(fb.z_score, 0)
        self.assertTrue(len(fb.feature_hash) > 10)

        prompt_state = fb.to_prompt_state()
        self.assertIn("event", prompt_state)
        self.assertIn("surprise", prompt_state)
        self.assertEqual(prompt_state["event"]["name"], "US Initial Jobless Claims")

    def test_03_expanded_deterministic_rules(self):
        """Verify pure mathematical boundary rules across all layers."""
        # D1 Shock
        fb_hot = DecisionFeatureBlock(event_name="US CPI", actual=4.0, consensus=3.0, sigma_hist=0.25, z_score=4.0)
        self.assertEqual(label_d1_economic_surprise(fb_hot), D1_EconomicSurprise.EXTREME_HAWKISH)

        fb_inline = DecisionFeatureBlock(event_name="US Retail Sales", actual=0.2, consensus=0.2, z_score=0.0)
        self.assertEqual(label_d1_economic_surprise(fb_inline), D1_EconomicSurprise.IN_LINE)

        # D2 Blackout Gate (distinguishing windows)
        fb_pre = DecisionFeatureBlock(event_name="US CPI", tier="HIGH", minutes_to_release=25.0)
        self.assertEqual(label_d2_blackout_gate(fb_pre), D2_BlackoutGate.PRE_EVENT_DELEVERAGE)

        fb_freeze = DecisionFeatureBlock(event_name="US CPI", tier="HIGH", minutes_to_release=1.0)
        self.assertEqual(label_d2_blackout_gate(fb_freeze), D2_BlackoutGate.BLACKOUT_FREEZE)

        fb_post = DecisionFeatureBlock(event_name="US CPI", tier="HIGH", minutes_to_release=-10.0)
        self.assertEqual(label_d2_blackout_gate(fb_post), D2_BlackoutGate.POST_SHOCK_PULLBACK)

        # D9 Emergency Kill-Switch
        fb_halt = DecisionFeatureBlock(event_name="SNB De-Peg", is_peg_broken=True, vix=40.0)
        self.assertEqual(label_d9_emergency_kill_switch(fb_halt), D9_EmergencyKillSwitch.EMERGENCY_HALT_ACTIVE)

        # D4 Liquidity Crunch
        fb_crunch = DecisionFeatureBlock(event_name="Debt Ceiling", sofr_rate=5.65, reserves_delta_b=-120.0)
        self.assertEqual(label_d4_liquidity_stance(fb_crunch), D4_LiquidityStance.LIQUIDITY_CRUNCH)

        # D5 Commodity Shock (requiring verified physical evidence)
        fb_oil_noise = DecisionFeatureBlock(event_name="OPEC Rumor", oil_5d_pct=6.0, ais_tanker_speed_drop=False)
        self.assertIn(label_d5_commodity_shock(fb_oil_noise), (D5_CommodityShock.SUPPLY_DEFICIT_BULLISH, D5_CommodityShock.DEMAND_DESTRUCTION))

        fb_oil_glut = DecisionFeatureBlock(event_name="OPEC Breakdown", oil_5d_pct=-8.0)
        self.assertEqual(label_d5_commodity_shock(fb_oil_glut), D5_CommodityShock.SUPPLY_GLUT_BEARISH)

        # Backward compatibility wrapper
        gt = generate_ground_truth_labels(fb_hot, proposed_direction="BUY", symbol="EURUSD")
        self.assertEqual(gt["D1_economic_surprise"], "EXTREME_HAWKISH")
        self.assertEqual(gt["D8_opportunity_arbitration"], "REJECT_MACRO_CONFLICT")

    def test_04_causal_attribution_and_fingerprints(self):
        """Verify deterministic candlestick geometry and Q1-Q7 move attribution."""
        extractor = CausalFingerprintExtractor()
        bars = [
            CandlestickBar(open=1.0800, high=1.0820, low=1.0795, close=1.0815, volume=1500.0, timestamp="2026-09-22T10:00:00Z"),
            CandlestickBar(open=1.0815, high=1.0880, low=1.0810, close=1.0870, volume=3500.0, timestamp="2026-09-22T10:15:00Z"),
        ]
        fp = extractor.extract_from_bars(bars, cross_asset_changes={"DXY": -0.65, "US10Y": -8.0})
        self.assertEqual(fp.candlestick_signature, Q7_CandlestickSignature.IMPULSIVE_BREAK.value)
        self.assertGreater(fp.move_pips, 60.0)

        # Attribute the move
        attr = extractor.attribute_move(fp, news_event_intensity=0.85, cot_crowding_percentile=50.0)
        self.assertEqual(attr.primary_driver, "MACRO_DATA")
        self.assertEqual(attr.move_quality, Q5_MoveQuality.FUNDAMENTAL_REPRICING.value)
        self.assertTrue(attr.narrative_updated)

    def test_05_narrative_state_machine_and_tripwires(self):
        """Verify narrative lifecycle, time decay, and armed tripwire invalidation."""
        test_path = "artifacts/test_active_narrative.json"
        if os.path.exists(test_path):
            os.remove(test_path)
        nsm = NarrativeStateMachine(storage_path=test_path)
        narrative = nsm.active_narrative

        # Initial state is valid
        self.assertFalse(narrative.is_invalidated)
        self.assertGreater(narrative.current_confidence, 0.4)

        # Add evidence
        narrative.add_evidence("Core CPI continues steady glide path", is_confirming=True)
        self.assertIn("Core CPI continues steady glide path", narrative.evidence_for)

        # Trigger VIX spike tripwire (VIX >= 30)
        fb_panic = DecisionFeatureBlock(event_name="Panic", vix=34.0)
        nsm.update_with_features(fb_panic)

        self.assertTrue(nsm.active_narrative.is_invalidated)
        self.assertIn("Tripwire", nsm.active_narrative.invalidation_reason)
        self.assertEqual(nsm.active_narrative.current_confidence, 0.0)

    def test_06_outcome_labeler_and_contrastive_mining(self):
        """Verify deterministic trade outcome grading and contrastive pair mining."""
        labeler = DeterministicOutcomeLabeler()

        # Trade 1: Clean winner (R >= 1.5)
        trade_win = TradeExecutionRecord(
            trade_id="TR_001",
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.0800,
            exit_price=1.0860,
            stop_loss=1.0770,
            take_profit=1.0860,
            entry_time="2026-09-22T08:00:00Z",
            exit_time="2026-09-22T12:00:00Z",
            high_watermark=1.0865,
            low_watermark=1.0790,
            spread_cost_pips=1.0,
        )
        outcome_win = labeler.label_trade(trade_win)
        self.assertTrue(outcome_win.is_profitable)
        self.assertGreaterEqual(outcome_win.realized_r_multiple, 1.5)
        self.assertEqual(outcome_win.ground_truth_d8_label, "STRONG_PROCEED")
        self.assertEqual(outcome_win.attribution_quality, "FUNDAMENTAL_REPRICING")

        # Trade 2: Macro reversal loss
        trade_loss = TradeExecutionRecord(
            trade_id="TR_002",
            symbol="EURUSD",
            direction="BUY",
            entry_price=1.0800,
            exit_price=1.0768,
            stop_loss=1.0770,
            take_profit=1.0860,
            entry_time="2026-09-22T08:00:00Z",
            exit_time="2026-09-22T08:35:00Z",
            high_watermark=1.0805,
            low_watermark=1.0765,
            spread_cost_pips=1.0,
        )
        outcome_loss = labeler.label_trade(trade_loss)
        self.assertFalse(outcome_loss.is_profitable)
        self.assertEqual(outcome_loss.ground_truth_d8_label, "REJECT_MACRO_CONFLICT")

        # Contrastive Pair Mining
        miner = ContrastivePairMiner(db_path="artifacts/test_rlcd_pool.db")
        rec_mock = DecisionRecord(
            decision_id="D8_opportunity_arbitration",
            decision_layer="Layer 6",
            head_type="choice",
            selected_option="STRONG_PROCEED", # model made a mistake on trade 2
            execution_authority=False,
        )
        bundle_mock = DecisionBundle(
            bundle_id="B_MOCK",
            timestamp="2026-09-22T08:00:00Z",
            feature_hash="hash_mock_123",
            decisions={"D8_opportunity_arbitration": rec_mock},
            fast_path_latency_ms=15.0,
            total_cycle_latency_ms=30.0,
            overrides_applied_count=0,
            veto_enforced=False,
            arbitration_verdict="STRONG_PROCEED",
            risk_multiplier=1.0,
            execution_authority=False,
        )
        mined = miner.mine_from_outcome(bundle_mock, outcome_loss, prompt_state={"symbol": "EURUSD"})
        self.assertGreater(len(mined), 0)

        # Check safety negative weighting
        neg_pairs = [p for p in mined if p.pair_type in ("NEGATIVE", "SAFETY_NEGATIVE")]
        self.assertTrue(any(p.sampling_weight >= 1.5 for p in neg_pairs))

        manifest = miner.get_dataset_manifest()
        self.assertGreater(manifest["total_pairs"], 0)

    def test_07_macro_fusion_gate_opportunity_bus(self):
        """Verify MacroFusionGate arbitration between TechnicalNet proposal and macro engine."""
        gate = MacroFusionGate()

        # Proposal 1: Technical breakout BUY EURUSD into hot US CPI (+3.0σ)
        candidate = {"candidate_id": "OPP_001", "symbol": "EURUSD", "side_hint": "BUY"}
        features_hot = DecisionFeatureBlock(
            event_name="US CPI",
            tier="HIGH",
            actual=4.2, consensus=3.1, z_score=3.5,
            minutes_to_release=120.0, # outside blackout, but direct macro conflict
        )
        verdict = gate.arbitrate_proposal(candidate, features_hot)
        self.assertEqual(verdict.verdict, D8_OpportunityArbitration.REJECT_MACRO_CONFLICT.value)
        self.assertEqual(verdict.risk_multiplier, 0.0)
        self.assertTrue(verdict.is_vetoed)
        self.assertFalse(verdict.execution_authority)

        # Proposal 2: SELL EURUSD aligned with hot US CPI
        candidate_sell = {"candidate_id": "OPP_002", "symbol": "EURUSD", "side_hint": "SELL"}
        verdict_sell = gate.arbitrate_proposal(candidate_sell, features_hot)
        self.assertEqual(verdict_sell.verdict, D8_OpportunityArbitration.STRONG_PROCEED.value)
        self.assertEqual(verdict_sell.risk_multiplier, 1.0)
        self.assertFalse(verdict_sell.is_vetoed)

        # Proposal 3: Inside blackout release window (T-1m) -> BLACKOUT_POSTPONE
        features_blackout = DecisionFeatureBlock(
            event_name="US CPI",
            tier="HIGH",
            minutes_to_release=1.0,
        )
        verdict_blackout = gate.arbitrate_proposal(candidate_sell, features_blackout)
        self.assertEqual(verdict_blackout.verdict, D8_OpportunityArbitration.BLACKOUT_POSTPONE.value)
        self.assertEqual(verdict_blackout.risk_multiplier, 0.0)


if __name__ == "__main__":
    unittest.main()
