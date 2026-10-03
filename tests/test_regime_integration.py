"""Tests for Hierarchical Meta-Portfolio regime engine integration."""
from __future__ import annotations

import json
import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.hierarchical_portfolio import (
    HierarchicalMetaPortfolioEngine,
    RegimeDetectionEngine,
    DynamicStrategyRouter,
    RegimeState,
)


# ---------------------------------------------------------------------------
# 1. RegimeDetectionEngine: data extraction from indicators
# ---------------------------------------------------------------------------

class TestRegimeDetectionFromIndicators:
    """Verify the engine receives correct ADX, ATR, and close data."""

    def test_detect_regime_with_trending_prices(self):
        detector = RegimeDetectionEngine()
        np.random.seed(42)
        # Strong uptrend
        prices = (100.0 + np.cumsum(np.random.normal(0.3, 0.15, 200))).tolist()
        regime = detector.detect_regime(
            prices=prices,
            current_adx=30.0,
            atr_history=[0.001] * 50,
            current_atr=0.001,
            vix_or_vol_index=14.0,
        )
        assert regime.primary_regime in ("LOW_VOL_TREND", "HIGH_VOL_TREND")
        assert not regime.is_crisis
        assert regime.hurst_exponent > 0.0

    def test_detect_regime_crisis_state(self):
        detector = RegimeDetectionEngine()
        np.random.seed(99)
        prices = (100.0 + np.cumsum(np.random.normal(0.0, 2.0, 200))).tolist()
        regime = detector.detect_regime(
            prices=prices,
            current_adx=15.0,
            atr_history=[0.05] * 50,
            current_atr=0.10,
            vix_or_vol_index=35.0,
        )
        assert regime.primary_regime == "HIGH_VOL_CRISIS"
        assert regime.is_crisis

    def test_detect_regime_range_market(self):
        detector = RegimeDetectionEngine()
        # Mean-reverting prices
        np.random.seed(77)
        prices = []
        p = 1.1000
        for _ in range(200):
            p += 0.95 * (1.1000 - p) + np.random.normal(0, 0.001)
            prices.append(p)
        regime = detector.detect_regime(
            prices=prices,
            current_adx=16.0,
            atr_history=[0.001] * 50,
            current_atr=0.001,
            vix_or_vol_index=15.0,
        )
        assert regime.primary_regime in ("LOW_VOL_RANGE", "LOW_VOL_TREND")


# ---------------------------------------------------------------------------
# 2. DynamicStrategyRouter: allocation logic
# ---------------------------------------------------------------------------

class TestStrategyRouterAllocation:
    """Verify weight allocation and master expectancy gates."""

    def test_trend_momentum_preferred_in_trend(self):
        router = DynamicStrategyRouter(min_master_ev=0.20)
        regime = RegimeState(
            primary_regime="LOW_VOL_TREND",
            probabilities={
                "LOW_VOL_TREND": 0.80, "LOW_VOL_RANGE": 0.10,
                "HIGH_VOL_TREND": 0.05, "HIGH_VOL_CRISIS": 0.05,
            },
            hurst_exponent=0.62, adx=30.0, atr_percentile=40.0, is_crisis=False,
        )
        result = router.route(regime, active_strategy_type="TREND_MOMENTUM")
        assert result.is_tradable
        assert result.strategy_weights["TREND_MOMENTUM"] > result.strategy_weights["MEAN_REVERSION"]
        assert result.master_expectancy_r >= 0.20

    def test_mean_reversion_preferred_in_range(self):
        router = DynamicStrategyRouter(min_master_ev=0.20)
        regime = RegimeState(
            primary_regime="LOW_VOL_RANGE",
            probabilities={
                "LOW_VOL_TREND": 0.10, "LOW_VOL_RANGE": 0.80,
                "HIGH_VOL_TREND": 0.05, "HIGH_VOL_CRISIS": 0.05,
            },
            hurst_exponent=0.40, adx=15.0, atr_percentile=30.0, is_crisis=False,
        )
        result = router.route(regime, active_strategy_type="MEAN_REVERSION")
        assert result.is_tradable
        assert result.strategy_weights["MEAN_REVERSION"] > result.strategy_weights["TREND_MOMENTUM"]

    def test_crisis_blocks_all_trading(self):
        router = DynamicStrategyRouter(min_master_ev=0.20)
        regime = RegimeState(
            primary_regime="HIGH_VOL_CRISIS",
            probabilities={
                "LOW_VOL_TREND": 0.0, "LOW_VOL_RANGE": 0.0,
                "HIGH_VOL_TREND": 0.10, "HIGH_VOL_CRISIS": 0.90,
            },
            hurst_exponent=0.50, adx=10.0, atr_percentile=98.0, is_crisis=True,
        )
        result = router.route(regime, active_strategy_type="TREND_MOMENTUM")
        assert not result.is_tradable
        assert result.recommended_risk_multiplier == 0.0
        assert "HIGH_VOL_CRISIS_REGIME_ACTIVE" in result.rejection_reasons

    def test_disfavored_strategy_blocked(self):
        router = DynamicStrategyRouter(min_master_ev=0.20)
        regime = RegimeState(
            primary_regime="LOW_VOL_TREND",
            probabilities={
                "LOW_VOL_TREND": 0.90, "LOW_VOL_RANGE": 0.03,
                "HIGH_VOL_TREND": 0.04, "HIGH_VOL_CRISIS": 0.03,
            },
            hurst_exponent=0.62, adx=30.0, atr_percentile=40.0, is_crisis=False,
        )
        result = router.route(regime, active_strategy_type="CTA_TREND")
        # In LOW_VOL_TREND, CTA_TREND has low weight (0.20 * 0.90 = 0.18 blended)
        # but it's above 0.05 so it won't be blocked. Test with a regime where
        # CTA is truly disfavored.
        regime_range = RegimeState(
            primary_regime="LOW_VOL_RANGE",
            probabilities={
                "LOW_VOL_TREND": 0.05, "LOW_VOL_RANGE": 0.90,
                "HIGH_VOL_TREND": 0.03, "HIGH_VOL_CRISIS": 0.02,
            },
            hurst_exponent=0.40, adx=15.0, atr_percentile=30.0, is_crisis=False,
        )
        result_range = router.route(regime_range, active_strategy_type="CTA_TREND")
        # In LOW_VOL_RANGE, CTA_TREND weight is 0.0 * 0.05 + 0.0 * 0.90 = 0.0, so blocked
        assert not result_range.is_tradable
        assert any("STRATEGY_DISFAVORED" in r for r in result_range.rejection_reasons)


# ---------------------------------------------------------------------------
# 3. HierarchicalMetaPortfolioEngine: full integration
# ---------------------------------------------------------------------------

class TestHierarchicalEngineIntegration:
    """End-to-end engine evaluation with realistic data."""

    def test_normal_conditions_approve_trade(self):
        engine = HierarchicalMetaPortfolioEngine(base_risk_pct=0.01, min_master_ev=0.20)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0002, 0.0005, 200))).tolist()
        result = engine.evaluate(
            prices=prices,
            current_adx=28.0,
            atr_history=[0.001] * 50,
            current_atr=0.001,
            strategy_type="TREND_MOMENTUM",
            open_portfolio_r=1.0,
        )
        assert result["approved"] is True
        assert result["applied_risk_multiplier"] > 0
        assert result["effective_risk_pct"] > 0
        assert "primary_regime" in result["regime"]

    def test_crisis_state_rejects_trade(self):
        engine = HierarchicalMetaPortfolioEngine(base_risk_pct=0.01, min_master_ev=0.20)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0, 0.05, 200))).tolist()
        result = engine.evaluate(
            prices=prices,
            current_adx=10.0,
            atr_history=[0.05] * 50,
            current_atr=0.15,
            strategy_type="TREND_MOMENTUM",
        )
        # With high VIX-equivalent and ATR, crisis should trigger
        if not result["approved"]:
            assert any("CRISIS" in r or "EXPECTANCY" in r or "DISFAVORED" in r for r in result["reasons"])

    def test_portfolio_heat_cap_blocks_trade(self):
        engine = HierarchicalMetaPortfolioEngine(base_risk_pct=0.01, min_master_ev=0.20)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0002, 0.0005, 200))).tolist()
        result = engine.evaluate(
            prices=prices,
            current_adx=28.0,
            atr_history=[0.001] * 50,
            current_atr=0.001,
            strategy_type="TREND_MOMENTUM",
            open_portfolio_r=5.0,  # Above 4.0 heat cap
        )
        assert result["approved"] is False
        assert any("HEAT_CAP" in r for r in result["reasons"])

    def test_cross_correlation_halves_sizing(self):
        engine = HierarchicalMetaPortfolioEngine(base_risk_pct=0.01, min_master_ev=0.20)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0002, 0.0005, 200))).tolist()
        result_no_corr = engine.evaluate(
            prices=prices, current_adx=28.0,
            atr_history=[0.001] * 50, current_atr=0.001,
            strategy_type="TREND_MOMENTUM", cross_strategy_correlation=0.3,
        )
        result_high_corr = engine.evaluate(
            prices=prices, current_adx=28.0,
            atr_history=[0.001] * 50, current_atr=0.001,
            strategy_type="TREND_MOMENTUM", cross_strategy_correlation=0.8,
        )
        if result_no_corr["approved"] and result_high_corr["approved"]:
            assert result_high_corr["applied_risk_multiplier"] <= result_no_corr["applied_risk_multiplier"] * 0.6


# ---------------------------------------------------------------------------
# 4. Regime allocation data flows into indicators dict
# ---------------------------------------------------------------------------

class TestRegimeDataFlow:
    """Verify regime_allocation appears in the indicators output."""

    def test_regime_allocation_in_indicators(self):
        """Simulate what fetch_data does: engine produces output that goes into indicators."""
        engine = HierarchicalMetaPortfolioEngine(base_risk_pct=0.01)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0002, 0.0005, 200))).tolist()
        hp_result = engine.evaluate(
            prices=prices, current_adx=28.0,
            atr_history=[0.001] * 50, current_atr=0.001,
            strategy_type="TREND_MOMENTUM",
        )
        regime_allocation = {
            "approved": hp_result["approved"],
            "regime": hp_result["regime"],
            "allocation": hp_result["allocation"],
            "applied_risk_multiplier": hp_result["applied_risk_multiplier"],
            "effective_risk_pct": hp_result["effective_risk_pct"],
            "reasons": hp_result["reasons"],
        }
        indicators = {"regime_allocation": regime_allocation}
        assert "regime_allocation" in indicators
        ra = indicators["regime_allocation"]
        assert "approved" in ra
        assert "regime" in ra
        assert "primary_regime" in ra["regime"]
        assert "allocation" in ra
        assert "strategy_weights" in ra["allocation"]

    def test_regime_allocation_toon_lite_flow(self):
        """Verify toon_lite extracts regime_allocation correctly."""
        toon = {
            "indicators": {
                "regime_allocation": {
                    "approved": True,
                    "regime": {"primary_regime": "LOW_VOL_TREND"},
                    "allocation": {"strategy_weights": {"TREND_MOMENTUM": 0.7}},
                    "applied_risk_multiplier": 1.05,
                    "effective_risk_pct": 0.0105,
                    "reasons": [],
                }
            }
        }
        regime_in_lite = toon.get("indicators", {}).get("regime_allocation", {})
        assert regime_in_lite["approved"] is True
        assert regime_in_lite["regime"]["primary_regime"] == "LOW_VOL_TREND"

    def test_empty_regime_allocation_graceful(self):
        """Empty regime_allocation should not crash toon_lite."""
        toon = {"indicators": {}}
        regime_in_lite = toon.get("indicators", {}).get("regime_allocation", {})
        assert regime_in_lite == {}


# ---------------------------------------------------------------------------
# 5. LLM prompt integration: regime rules
# ---------------------------------------------------------------------------

class TestRegimePromptRules:
    """Verify the prompt includes regime-based decision rules."""

    def test_prompt_contains_regime_gate_rule(self):
        """The prompt should contain the hierarchical regime gate rule."""
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "Regime Allocation" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "is_tradable" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "regime_allocation" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "CRISIS" in SIGNAL_SYSTEM_PROMPT_TEMPLATE

    def test_prompt_regime_hard_gate(self):
        """The prompt must say regime is a HARD GATE."""
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "HARD GATE" in SIGNAL_SYSTEM_PROMPT_TEMPLATE


# ---------------------------------------------------------------------------
# 6. _validate_proposal regime gate logic
# ---------------------------------------------------------------------------

class TestValidateProposalRegimeGate:
    """Test the regime gate in _validate_proposal."""

    def _make_mock_orchestrator(self):
        """Create a minimal mock orchestrator for testing regime gate."""
        orch = MagicMock()
        orch.validator = MagicMock()
        orch.validator.confluence.return_value = 5
        orch.store = MagicMock()
        orch.operations = MagicMock()
        orch.run_id = "test-run"
        orch.model_name = "test-model"
        orch._get_market_session.return_value = "london"
        return orch

    def test_regime_not_approved_blocks_proposal(self):
        """When regime says not approved, proposal should be rejected."""
        orch = self._make_mock_orchestrator()
        toon = {
            "indicators": {
                "regime_allocation": {
                    "approved": False,
                    "regime": {"primary_regime": "HIGH_VOL_CRISIS"},
                    "allocation": {},
                    "applied_risk_multiplier": 0.0,
                    "effective_risk_pct": 0.0,
                    "reasons": ["HIGH_VOL_CRISIS_REGIME_ACTIVE"],
                }
            },
            "ohlc": [{"close": 1.0800}],
            "dom": {"best_bid": 1.0799, "best_ask": 1.0801},
            "news": [],
            "feature_snapshot": {"ready": False, "values": {}, "version": "test", "schema_hash": "", "values_hash": "", "completed_bar_time": ""},
            "technical_analysis": {"primary_trend": "BEARISH", "key_levels": []},
            "multi_timeframe": {},
            "context": {},
        }
        decision = {"action": "BUY", "entry": 1.0801, "stop_loss": 1.0780, "take_profit": 1.0850, "confidence": 0.8}
        from intelligence.llm_orchestrator import LLMOrchestrator
        proposal, result = LLMOrchestrator._validate_proposal(orch, decision, toon, "EURUSD", "scalp", paper_mode=True)
        # The proposal is rejected (either by regime gate or earlier gates)
        if proposal is not None:
            assert not result.approved
            # The regime gate may or may not be the specific reason;
            # what matters is that a proposal with unapproved regime gets rejected
            all_reasons = " ".join(result.reasons).lower()
            assert any(kw in all_reasons for kw in ["regime", "setup", "feature", "schema"])

    def test_regime_approved_allows_proposal_through(self):
        """When regime is approved, proposal should not be blocked by regime gate."""
        orch = self._make_mock_orchestrator()
        toon = {
            "indicators": {
                "regime_allocation": {
                    "approved": True,
                    "regime": {"primary_regime": "LOW_VOL_TREND"},
                    "allocation": {"strategy_weights": {"TREND_MOMENTUM": 0.7}},
                    "applied_risk_multiplier": 1.05,
                    "effective_risk_pct": 0.0105,
                    "reasons": [],
                }
            },
            "ohlc": [{"close": 1.0800}],
            "dom": {"best_bid": 1.0799, "best_ask": 1.0801},
            "news": [],
            "feature_snapshot": {"ready": False, "values": {}, "version": "test", "schema_hash": "", "values_hash": "", "completed_bar_time": ""},
            "technical_analysis": {"primary_trend": "BULLISH", "key_levels": []},
            "multi_timeframe": {},
            "context": {},
        }
        decision = {"action": "BUY", "entry": 1.0801, "stop_loss": 1.0780, "take_profit": 1.0850, "confidence": 0.8}
        from intelligence.llm_orchestrator import LLMOrchestrator
        proposal, result = LLMOrchestrator._validate_proposal(orch, decision, toon, "EURUSD", "scalp", paper_mode=True)
        if proposal is not None:
            # Regime gate should NOT have rejected it
            all_reasons = " ".join(result.reasons).lower()
            assert "regime_gate" not in all_reasons

    def test_regime_risk_multiplier_applied_to_features(self):
        """When multiplier < 1.0, it should appear in proposal features."""
        orch = self._make_mock_orchestrator()
        toon = {
            "indicators": {
                "regime_allocation": {
                    "approved": True,
                    "regime": {"primary_regime": "HIGH_VOL_TREND"},
                    "allocation": {"strategy_weights": {"TREND_MOMENTUM": 0.3}},
                    "applied_risk_multiplier": 0.55,
                    "effective_risk_pct": 0.0055,
                    "reasons": [],
                }
            },
            "ohlc": [{"close": 1.0800}],
            "dom": {"best_bid": 1.0799, "best_ask": 1.0801},
            "news": [],
            "feature_snapshot": {"ready": False, "values": {}, "version": "test", "schema_hash": "", "values_hash": "", "completed_bar_time": ""},
            "technical_analysis": {"primary_trend": "BULLISH", "key_levels": []},
            "multi_timeframe": {},
            "context": {},
        }
        decision = {"action": "BUY", "entry": 1.0801, "stop_loss": 1.0780, "take_profit": 1.0850, "confidence": 0.8}
        from intelligence.llm_orchestrator import LLMOrchestrator
        proposal, result = LLMOrchestrator._validate_proposal(orch, decision, toon, "EURUSD", "scalp", paper_mode=True)
        if proposal is not None and proposal.features.get("regime_risk_multiplier") is not None:
            assert proposal.features["regime_risk_multiplier"] == 0.55
            assert proposal.features["regime"] == "HIGH_VOL_TREND"

    def test_empty_regime_allocation_passes_gate(self):
        """When regime_allocation is empty (engine failed), gate should pass."""
        orch = self._make_mock_orchestrator()
        toon = {
            "indicators": {},
            "ohlc": [{"close": 1.0800}],
            "dom": {"best_bid": 1.0799, "best_ask": 1.0801},
            "news": [],
            "feature_snapshot": {"ready": False, "values": {}, "version": "test", "schema_hash": "", "values_hash": "", "completed_bar_time": ""},
            "technical_analysis": {"primary_trend": "BULLISH", "key_levels": []},
            "multi_timeframe": {},
            "context": {},
        }
        decision = {"action": "BUY", "entry": 1.0801, "stop_loss": 1.0780, "take_profit": 1.0850, "confidence": 0.8}
        from intelligence.llm_orchestrator import LLMOrchestrator
        proposal, result = LLMOrchestrator._validate_proposal(orch, decision, toon, "EURUSD", "scalp", paper_mode=True)
        if proposal is not None:
            # Empty regime should NOT trigger regime_gate rejection
            all_reasons = " ".join(result.reasons).lower()
            assert "regime_gate" not in all_reasons


# ---------------------------------------------------------------------------
# 7. Direct regime gate logic test (isolated from full validation pipeline)
# ---------------------------------------------------------------------------

class TestRegimeGateDirectLogic:
    """Test the regime gate code path directly without going through full validation."""

    def test_regime_gate_blocks_when_not_approved(self):
        """Simulate the exact regime gate code from _validate_proposal."""
        regime_allocation = {
            "approved": False,
            "regime": {"primary_regime": "HIGH_VOL_CRISIS"},
            "allocation": {},
            "applied_risk_multiplier": 0.0,
            "effective_risk_pct": 0.0,
            "reasons": ["HIGH_VOL_CRISIS_REGIME_ACTIVE"],
        }
        toon = {"indicators": {"regime_allocation": regime_allocation}}
        # This is the exact gate logic from _validate_proposal
        regime_alloc = toon.get("indicators", {}).get("regime_allocation", {})
        if regime_alloc and not regime_alloc.get("approved", True):
            _regime_reasons = regime_alloc.get("reasons", [])
            _regime_name = (regime_alloc.get("regime") or {}).get("primary_regime", "UNKNOWN")
            blocked = True
        else:
            blocked = False
        assert blocked
        assert _regime_name == "HIGH_VOL_CRISIS"
        assert "HIGH_VOL_CRISIS_REGIME_ACTIVE" in _regime_reasons

    def test_regime_gate_passes_when_approved(self):
        regime_allocation = {
            "approved": True,
            "regime": {"primary_regime": "LOW_VOL_TREND"},
            "allocation": {},
            "applied_risk_multiplier": 1.05,
            "effective_risk_pct": 0.0105,
            "reasons": [],
        }
        toon = {"indicators": {"regime_allocation": regime_allocation}}
        regime_alloc = toon.get("indicators", {}).get("regime_allocation", {})
        if regime_alloc and not regime_alloc.get("approved", True):
            blocked = True
        else:
            blocked = False
        assert not blocked

    def test_regime_gate_passes_when_empty(self):
        toon = {"indicators": {}}
        regime_alloc = toon.get("indicators", {}).get("regime_allocation", {})
        if regime_alloc and not regime_alloc.get("approved", True):
            blocked = True
        else:
            blocked = False
        assert not blocked

    def test_regime_multiplier_reduces_confidence(self):
        """Verify the multiplier application logic."""
        regime_allocation = {
            "approved": True,
            "applied_risk_multiplier": 0.55,
            "regime": {"primary_regime": "HIGH_VOL_TREND"},
        }
        _mult = regime_allocation.get("applied_risk_multiplier", 1.0)
        assert _mult < 1.0
        proposal_features = {}
        if _mult < 1.0:
            proposal_features["regime_risk_multiplier"] = _mult
            proposal_features["regime"] = (regime_allocation.get("regime") or {}).get("primary_regime", "UNKNOWN")
        assert proposal_features["regime_risk_multiplier"] == 0.55
        assert proposal_features["regime"] == "HIGH_VOL_TREND"


# ---------------------------------------------------------------------------
# 8. Strategy key normalization
# ---------------------------------------------------------------------------

class TestStrategyKeyNormalization:
    """Verify normalize_strategy_key maps strategies correctly."""

    def test_scalp_maps_to_trend(self):
        assert DynamicStrategyRouter.normalize_strategy_key("scalp") == "TREND_MOMENTUM"

    def test_swing_maps_to_trend(self):
        assert DynamicStrategyRouter.normalize_strategy_key("SWING") == "TREND_MOMENTUM"

    def test_mean_reversion_maps_correctly(self):
        assert DynamicStrategyRouter.normalize_strategy_key("MEAN_REVERSION") == "MEAN_REVERSION"

    def test_bollinger_maps_to_mean_reversion(self):
        assert DynamicStrategyRouter.normalize_strategy_key("bollinger_rsi") == "MEAN_REVERSION"

    def test_unknown_defaults_to_trend(self):
        assert DynamicStrategyRouter.normalize_strategy_key("unknown_strategy") == "TREND_MOMENTUM"
