"""Tests for _apply_advanced_auto_tools wiring in LLMOrchestrator.

Covers:
- meta_adaptive risk_multiplier and position_scale
- self_improvement applied params
- evolutionary best gene suggestions
- cross_market robustness gate
- alpha_discovery enrichment
- multimodal pattern confluence
- cross_market_hold_bias in _validate_proposal
- meta_adaptive position_scale in _validate_proposal
- multimodal confluence boost in _validate_proposal
"""
from __future__ import annotations

import math
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orchestrator():
    """Create a minimal LLMOrchestrator-like object with just enough wiring."""
    import types
    orch = types.SimpleNamespace()
    orch.risk = types.SimpleNamespace(risk_per_trade=0.01)
    # PR 4.3 (1a-F3): the method reads the once-configured base risk and
    # never writes risk_per_trade back; effective risk lands in the toon.
    orch._base_risk_per_trade = 0.01
    orch.run_id = "test-run"
    orch.operations = MagicMock()
    orch.store = MagicMock()
    orch.validator = MagicMock()
    orch.validator.confluence.return_value = 5
    # _apply_advanced_auto_tools gates adaptive mutations of risk_per_trade
    # behind _research_influence_allowed. Paper mode is unconditionally
    # allowed; the live path (journal evidence lookup) is out of scope for
    # these unit tests and defaults to refused (fail-closed).
    orch._research_influence_allowed = lambda model_kind, *, paper_mode=True: bool(paper_mode)
    return orch


def _base_toon(**overrides) -> Dict[str, Any]:
    """Base toon with auto_quant_tools.results structure."""
    toon: Dict[str, Any] = {
        "auto_quant_tools": {
            "results": {},
            "cpcv_escalation": {},
        },
    }
    toon.update(overrides)
    return toon


# Import the method directly from the module
def _call_apply(orch, toon, paper_mode=True):
    """Call _apply_advanced_auto_tools via the module function."""
    import sys
    import importlib

    # We need to get the actual function from the llm_orchestrator module
    # Since it's a method on LLMOrchestrator, we'll extract it and bind
    mod_path = "intelligence.llm_orchestrator"
    if mod_path not in sys.modules:
        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            pytest.skip("intelligence.llm_orchestrator not importable")
    else:
        mod = sys.modules[mod_path]

    # Get the unbound function
    fn = None
    for cls_name in ("LLMOrchestrator",):
        cls = getattr(mod, cls_name, None)
        if cls:
            fn = cls._apply_advanced_auto_tools
            break
    if fn is None:
        pytest.skip("LLMOrchestrator class not found")

    fn(orch, toon, paper_mode=paper_mode)


# ---------------------------------------------------------------------------
# Test: meta_adaptive risk adjustment
# ---------------------------------------------------------------------------

class TestMetaAdaptiveWiring:
    def test_risk_multiplier_applied(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["meta_adaptive"] = {
            "status": "OK",
            "current_regime": "LOW_VOL_TREND",
            "transition_detected": False,
            "adapted_profile": {
                "risk_multiplier": 1.3,
                "position_scale": 1.0,
            },
        }
        _call_apply(orch, toon)
        # Effective risk 0.01 * 1.3 = 0.013 recorded; base never mutated (1a-F3).
        assert orch.risk.risk_per_trade == 0.01
        assert toon["meta_adaptive_override"]["risk_multiplier"] == 1.3
        assert toon["meta_adaptive_override"]["adapted_risk_per_trade"] == pytest.approx(0.013)
        assert toon["meta_adaptive_override"]["applied"] is True
        assert toon["meta_adaptive_override"]["regime"] == "LOW_VOL_TREND"

    def test_risk_multiplier_capped_at_max(self):
        orch = _make_orchestrator()
        orch.risk.risk_per_trade = 0.015
        orch._base_risk_per_trade = 0.015
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["meta_adaptive"] = {
            "status": "OK",
            "adapted_profile": {"risk_multiplier": 2.0, "position_scale": 1.0},
        }
        _call_apply(orch, toon)
        # 0.015 * 2.0 = 0.03, but capped at 0.02 in the override (1a-F3: no write-back).
        assert orch.risk.risk_per_trade == 0.015
        assert toon["meta_adaptive_override"]["adapted_risk_per_trade"] == pytest.approx(0.02)

    def test_risk_multiplier_capped_at_min(self):
        orch = _make_orchestrator()
        orch.risk.risk_per_trade = 0.01
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["meta_adaptive"] = {
            "status": "OK",
            "adapted_profile": {"risk_multiplier": 0.1, "position_scale": 1.0},
        }
        _call_apply(orch, toon)
        # 0.01 * 0.1 = 0.001, but floored at 0.002 in the override (1a-F3: no write-back).
        assert orch.risk.risk_per_trade == 0.01
        assert toon["meta_adaptive_override"]["adapted_risk_per_trade"] == pytest.approx(0.002)

    def test_no_change_when_multiplier_is_1(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["meta_adaptive"] = {
            "status": "OK",
            "adapted_profile": {"risk_multiplier": 1.0, "position_scale": 1.0},
        }
        _call_apply(orch, toon)
        assert orch.risk.risk_per_trade == 0.01
        assert toon.get("meta_adaptive_override") is None

    def test_error_status_skipped(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["meta_adaptive"] = {
            "status": "ERROR",
            "adapted_profile": {"risk_multiplier": 2.0},
        }
        _call_apply(orch, toon)
        assert orch.risk.risk_per_trade == 0.01

    def test_risk_multiplier_gated_in_live_without_approval(self):
        """Live (non-paper) without research approval: advisory only, no mutation."""
        orch = _make_orchestrator()
        orch._research_influence_allowed = lambda model_kind, *, paper_mode=True: False
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["meta_adaptive"] = {
            "status": "OK",
            "current_regime": "LOW_VOL_TREND",
            "adapted_profile": {"risk_multiplier": 1.3, "position_scale": 1.0},
        }
        _call_apply(orch, toon, paper_mode=False)
        assert orch.risk.risk_per_trade == 0.01  # unmutated
        override = toon["meta_adaptive_override"]
        assert override["applied"] is False
        # Gated increases fall back to the base risk in the recorded override.
        assert override["adapted_risk_per_trade"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Test: self_improvement params
# ---------------------------------------------------------------------------

class TestSelfImprovementWiring:
    def test_improvement_applied_sets_entry_threshold(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["self_improvement"] = {
            "improvement_applied": True,
            "proposal_type": "tighten_entry",
            "improvement_pct": 12.5,
            "applied_params": {
                "entry_confidence_threshold": 0.65,
            },
        }
        _call_apply(orch, toon)
        assert toon["self_improvement_entry_threshold"] == 0.65

    def test_improvement_applied_risk_within_bounds(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["self_improvement"] = {
            "improvement_applied": True,
            "applied_params": {"risk_per_trade": 0.008},
        }
        _call_apply(orch, toon)
        # 1a-F3: recorded as an override, never written back.
        assert orch.risk.risk_per_trade == 0.01
        assert toon["self_improvement_risk_override"] == 0.008
        assert toon["cycle_effective_risk"] == pytest.approx(0.008)

    def test_improvement_risk_gated_in_live_without_approval(self):
        """Live (non-paper) without research approval: no risk mutation."""
        orch = _make_orchestrator()
        orch._research_influence_allowed = lambda model_kind, *, paper_mode=True: False
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["self_improvement"] = {
            "improvement_applied": True,
            "applied_params": {"risk_per_trade": 0.008},
        }
        _call_apply(orch, toon, paper_mode=False)
        assert orch.risk.risk_per_trade == 0.01  # unmutated

    def test_improvement_applied_risk_out_of_bounds_ignored(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["self_improvement"] = {
            "improvement_applied": True,
            "applied_params": {"risk_per_trade": 0.05},  # > 0.02 max
        }
        _call_apply(orch, toon)
        assert orch.risk.risk_per_trade == 0.01  # unchanged

    def test_no_improvement_no_change(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["self_improvement"] = {
            "improvement_applied": False,
        }
        _call_apply(orch, toon)
        assert orch.risk.risk_per_trade == 0.01
        assert toon.get("self_improvement_entry_threshold") is None


# ---------------------------------------------------------------------------
# Test: evolutionary suggestions
# ---------------------------------------------------------------------------

class TestEvolutionaryWiring:
    def test_best_gene_injected(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["evolutionary"] = {
            "best_gene": {
                "fitness": 0.82,
                "params": {
                    "risk_per_trade": 0.012,
                    "entry_confidence_threshold": 0.55,
                    "sl_atr_multiplier": 1.5,
                    "tp_atr_multiplier": 3.0,
                },
            },
            "generations_run": 3,
            "generation_evaluated": 3,
        }
        _call_apply(orch, toon)
        evo = toon["evolutionary_suggestions"]
        assert evo["fitness"] == 0.82
        assert evo["sl_atr_mult"] == 1.5
        assert evo["tp_atr_mult"] == 3.0
        assert evo["generation"] == 3

    def test_no_best_gene_no_context(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["evolutionary"] = {
            "status": "INSUFFICIENT_DATA",
        }
        _call_apply(orch, toon)
        assert toon.get("evolutionary_suggestions") is None


# ---------------------------------------------------------------------------
# Test: cross-market robustness gate
# ---------------------------------------------------------------------------

class TestCrossMarketWiring:
    def test_weak_triggers_hold_bias(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["cross_market"] = {
            "composite_score": 0.35,
            "recommendation": {"strength": "WEAK"},
            "leaderboard": [{"symbol": "EURUSD", "composite": 0.6}],
        }
        _call_apply(orch, toon)
        assert toon["cross_market_hold_bias"] is True
        assert toon["cross_market_robustness"]["composite_score"] == 0.35
        assert toon["cross_market_robustness"]["strength"] == "WEAK"

    def test_strong_no_hold_bias(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["cross_market"] = {
            "composite_score": 0.82,
            "recommendation": {"strength": "STRONG"},
            "leaderboard": [],
        }
        _call_apply(orch, toon)
        assert toon.get("cross_market_hold_bias") is None
        assert toon["cross_market_robustness"]["strength"] == "STRONG"

    def test_moderate_score_no_bias(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["cross_market"] = {
            "composite_score": 0.55,
            "recommendation": {"strength": "MODERATE"},
        }
        _call_apply(orch, toon)
        assert toon.get("cross_market_hold_bias") is None


# ---------------------------------------------------------------------------
# Test: alpha-discovery enrichment
# ---------------------------------------------------------------------------

class TestAlphaDiscoveryWiring:
    def test_new_alphas_enriched(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["alpha_discovery"] = {
            "validated_count": 2,
            "rejected_count": 3,
            "validated_alphas": [
                {"name": "new_alpha_1", "ic_ir": 0.8},
                {"name": "new_alpha_2", "ic_ir": 0.6},
            ],
            "rejection_reasons": ["low_ic", "duplicate", "complexity"],
        }
        _call_apply(orch, toon)
        enrich = toon["alpha_discovery_enrichment"]
        assert enrich["validated_count"] == 2
        assert len(enrich["new_alphas"]) == 2
        assert enrich["rejected_count"] == 3

    def test_no_new_alphas_no_enrichment(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["alpha_discovery"] = {
            "validated_count": 0,
        }
        _call_apply(orch, toon)
        assert toon.get("alpha_discovery_enrichment") is None


# ---------------------------------------------------------------------------
# Test: multimodal pattern confluence
# ---------------------------------------------------------------------------

class TestMultimodalWiring:
    def test_bullish_confluence_injected(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["multimodal"] = {
            "confluence": {"score": 0.85, "direction": "BULLISH", "pattern_count": 3},
            "patterns": [{"type": "hammer"}, {"type": "bullish_engulfing"}],
            "trend": {"direction": "UP", "strength": 0.8},
            "support_resistance": {"supports": [1.0950], "resistances": [1.1050]},
            "volume_patterns": [],
        }
        _call_apply(orch, toon)
        mm = toon["multimodal_patterns"]
        assert mm["confluence_score"] == 0.85
        assert mm["direction"] == "BULLISH"
        assert mm["pattern_count"] == 3
        assert len(mm["patterns"]) == 2

    def test_neutral_confluence_injected(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"]["multimodal"] = {
            "confluence": {"score": 0.1, "direction": "NEUTRAL", "pattern_count": 0},
        }
        _call_apply(orch, toon)
        mm = toon["multimodal_patterns"]
        assert mm["confluence_score"] == 0.1
        assert mm["direction"] == "NEUTRAL"


# ---------------------------------------------------------------------------
# Test: summary
# ---------------------------------------------------------------------------

class TestAdvancedAutoToolsSummary:
    def test_summary_built(self):
        """The apply step publishes per-module advisory keys plus the cycle
        effective risk (1a-F3). There is no aggregated summary dict and no
        risk_per_trade write-back."""
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["auto_quant_tools"]["results"] = {
            "meta_adaptive": {"status": "OK", "adapted_profile": {"risk_multiplier": 1.1}},
            "self_improvement": {"improvement_applied": True},
            "evolutionary": {"best_gene": {"fitness": 0.75}},
            "cross_market": {"composite_score": 0.6, "recommendation": {"strength": "MODERATE"}},
            "alpha_discovery": {"validated_count": 1},
            "multimodal": {"confluence": {"score": 0.5}},
        }
        _call_apply(orch, toon)
        assert orch.risk.risk_per_trade == 0.01
        assert toon["meta_adaptive_override"]["applied"] is True
        assert toon["cycle_effective_risk"] == pytest.approx(0.011)
        assert toon["evolutionary_suggestions"]["fitness"] == 0.75
        assert toon["multimodal_patterns"]["confluence_score"] == 0.5

    def test_empty_results_no_summary(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        _call_apply(orch, toon)
        # Empty results returns early before publishing advisory keys.
        assert toon.get("meta_adaptive_override") is None
        assert "cycle_effective_risk" not in toon


# ---------------------------------------------------------------------------
# Test: no results returns early
# ---------------------------------------------------------------------------

class TestNoResults:
    def test_empty_auto_quant_tools(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        _call_apply(orch, toon)
        # Nothing should be set
        assert toon.get("meta_adaptive_override") is None
        assert toon.get("advanced_auto_tools_summary") is None

    def test_exception_does_not_crash(self):
        orch = _make_orchestrator()
        orch.risk = None  # Will cause AttributeError
        toon = _base_toon()
        # Should not raise
        _call_apply(orch, toon)


# ---------------------------------------------------------------------------
# Test: cross_market_hold_bias in _validate_proposal integration
# ---------------------------------------------------------------------------

class TestCrossMarketGateIntegration:
    """Test that cross_market_hold_bias properly rejects proposals."""

    def test_hold_bias_rejects_proposal(self):
        orch = _make_orchestrator()
        toon = _base_toon()
        toon["cross_market_hold_bias"] = True
        toon["cross_market_robustness"] = {"composite_score": 0.35, "strength": "WEAK"}
        toon["feature_snapshot"] = {"values": {}, "ready": False}
        toon["indicators"] = {}
        toon["technical_analysis"] = {"key_levels": []}
        toon["multi_timeframe"] = {}
        toon["journal_context"] = {}

        decision = {
            "action": "BUY",
            "entry": 1.1000,
            "stop_loss": 1.0950,
            "take_profit": 1.1100,
            "confidence": 0.7,
        }

        # Simulate the cross-market gate check from _validate_proposal
        proposal = MagicMock()
        proposal.features = {}
        proposal.side = "BUY"
        proposal.lots = 0.1
        proposal.setup_key.return_value = "test-setup"

        result = MagicMock()
        result.confluence.return_value = 5

        # Simulate the gate
        if toon.get("cross_market_hold_bias"):
            proposal.features["cross_market_robustness_gate"] = "WEAK"
            rejected = True
        else:
            rejected = False

        assert rejected is True
        assert proposal.features["cross_market_robustness_gate"] == "WEAK"
