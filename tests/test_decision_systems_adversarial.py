"""Focused adversarial tests for the change's real entry points.

Covers:
1. UDE confidence trust boundary: out-of-range confidence from a producer must
   not poison Kelly/EV sizing arithmetic (real case: 1.3 from graph_nodes).
2. macro_state_vector degenerate inputs: None payloads, malformed rows, and
   non-finite surprises must yield finite, bounded advisory output.
3. macro_fusion_gate decision table: veto precedence, D8-conflict veto, and the
   execution-authority invariant across every path.
4. bridge round-trip (tests/test_gui_api_graph.py) + import fallback hygiene.
"""
from __future__ import annotations

import math

import pytest

from intelligence.universal_decision_engine import (
    DecisionContext,
    DecisionDomain,
    PhilosophicalPrinciple,
    UniversalDecisionEngine,
)
from intelligence.macro_state_vector import build_macro_state_vector
from intelligence.macro_fusion_gate import MacroFusionGate
from research.decision_models.feature_block import DecisionFeatureBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(confidence: float) -> DecisionContext:
    return DecisionContext(
        domain=DecisionDomain.TRADING,
        symbol="EURUSD",
        action="BUY",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        confidence=confidence,
        features={"vix": 18.0, "adx": 25.0},
    )


def _fb(**overrides) -> DecisionFeatureBlock:
    defaults = dict(
        event_name="US_CPI", currency="USD", tier="HIGH",
        actual=3.2, consensus=3.2, sigma_hist=0.3,
        vix=14.0, dxy_change_pct=0.0, yield_10y_delta_bps=0.0,
        credit_oas_bps=110.0, oil_5d_pct=0.0,
        net_liquidity_delta_30d_b=0.0, reserves_delta_b=0.0,
        sofr_rate=5.3, cot_percentile=50.0, adx=25.0, hurst=0.5,
        minutes_to_release=999.0,
        headline="in line", narrative="in line",
    )
    defaults.update(overrides)
    return DecisionFeatureBlock(**defaults)


# ---------------------------------------------------------------------------
# 1. UDE confidence boundary (Kelly/EV trust boundary)
# ---------------------------------------------------------------------------

class TestUDEConfidenceBoundary:
    def test_confidence_above_one_is_clamped(self):
        engine = UniversalDecisionEngine()
        decision = engine.evaluate(_ctx(1.3))  # the graph_nodes producer bug
        assert 0.0 <= decision.sizing_multiplier <= 1.0
        assert decision.execution_authority is False

    def test_confidence_below_zero_is_clamped(self):
        engine = UniversalDecisionEngine()
        decision = engine.evaluate(_ctx(-0.4))
        assert 0.0 <= decision.sizing_multiplier <= 1.0

    def test_sizing_monotone_in_confidence(self):
        """Kelly/EV must respond sensibly to confidence — a 1.3 vs 0.8 input
        used to yield identical (saturated) sizing, masking the producer bug."""
        engine = UniversalDecisionEngine()
        low = engine.evaluate(_ctx(0.45)).sizing_multiplier
        high = engine.evaluate(_ctx(0.85)).sizing_multiplier
        assert high >= low

    def test_confidence_nan_treated_as_no_edge(self):
        engine = UniversalDecisionEngine()
        decision = engine.evaluate(_ctx(float("nan")))
        assert 0.0 <= decision.sizing_multiplier <= 1.0

    def test_execution_authority_never_granted(self):
        engine = UniversalDecisionEngine()
        for conf in (-0.4, 0.0, 0.5, 0.9, 1.3):
            assert engine.evaluate(_ctx(conf)).execution_authority is False


# ---------------------------------------------------------------------------
# 2. macro_state_vector degenerate inputs
# ---------------------------------------------------------------------------

class TestMacroStateVectorDegenerate:
    def test_none_observations(self):
        msv = build_macro_state_vector(macro_observations=None)
        assert msv["macro_bias"] == "NEUTRAL"
        assert msv["composite_score"] == 0.0
        assert msv["entity_count"] == 0

    def test_malformed_rows_skipped(self):
        msv = build_macro_state_vector(macro_observations=[
            {"value": 5.0},            # no entity
            {"entity": ""},            # empty entity
            {"entity": "INFLATION"},   # entity but no value/surprise
            42,                        # not a dict
        ])
        assert msv["entity_count"] == 1  # only INFLATION survives
        assert math.isfinite(msv["composite_score"])

    def test_non_finite_surprises_do_not_poison_bias(self):
        msv = build_macro_state_vector(macro_observations=[
            {"entity": "POLICY_RATE", "value": 5.5, "surprise": float("nan")},
            {"entity": "INFLATION", "value": 3.0, "surprise": float("inf")},
            {"entity": "GROWTH", "value": 2.0, "surprise": float("-inf")},
        ])
        assert msv["macro_bias"] == "NEUTRAL"
        assert msv["composite_score"] == 0.0

    def test_mixed_valid_and_garbage(self):
        msv = build_macro_state_vector(macro_observations=[
            {"entity": "POLICY_RATE", "value": 5.5, "surprise": 0.5},
            {"entity": "BAD", "value": None, "surprise": None},
            {"entity": "GROWTH", "value": 2.0, "surprise": -0.2},
        ])
        assert msv["macro_bias"] in ("NEUTRAL", "HAWKISH", "DOVISH")
        assert -1.0 <= msv["composite_score"] <= 1.0

    def test_non_dict_regime_fusion_prop_are_ignored(self):
        msv = build_macro_state_vector(
            macro_observations=[],
            intermarket_regime="garbage",
            opportunity_fusion=42,
            causal_propagation=[1, 2, 3],
        )
        assert msv["regime"]["regime_id"] is None
        assert msv["fusion_status"] is None
        assert msv["causal_path_id"] is None


# ---------------------------------------------------------------------------
# 3. macro_fusion_gate decision table
# ---------------------------------------------------------------------------

class TestMacroFusionGateTable:
    def test_kill_switch_beats_clean_d8(self):
        """D9 veto must dominate even when D8 arbitration would proceed."""
        fb = _fb(vix=40.0, is_peg_broken=True)
        verdict = MacroFusionGate().arbitrate_proposal(
            {"candidate_id": "X", "symbol": "EURUSD", "side": "BUY"}, fb)
        assert verdict.is_vetoed is True
        assert verdict.risk_multiplier == 0.0
        assert any("Emergency halt" in r for r in verdict.rejection_reasons)

    def test_blackout_maps_to_postpone_verdict(self):
        fb = _fb(minutes_to_release=1.0, tier="HIGH")
        verdict = MacroFusionGate().arbitrate_proposal(
            {"candidate_id": "X", "symbol": "EURUSD", "side": "BUY"}, fb)
        assert verdict.is_vetoed is True
        assert verdict.verdict == "BLACKOUT_POSTPONE"

    def test_non_blackout_veto_maps_to_reject(self):
        fb = _fb(is_adversarial=True)
        verdict = MacroFusionGate().arbitrate_proposal(
            {"candidate_id": "X", "symbol": "EURUSD", "side": "BUY"}, fb)
        assert verdict.is_vetoed is True
        assert verdict.verdict == "REJECT_MACRO_CONFLICT"

    def test_d8_macro_conflict_vetoes_clean_gates(self):
        """Extreme hawkish surprise against a BUY must veto via D8 alone."""
        fb = _fb(actual=4.0, consensus=3.0, sigma_hist=0.3)
        verdict = MacroFusionGate().arbitrate_proposal(
            {"candidate_id": "X", "symbol": "EURUSD", "side": "BUY"}, fb)
        assert verdict.is_vetoed is True
        assert verdict.risk_multiplier == 0.0
        assert any("Macro conflict" in r for r in verdict.rejection_reasons)

    def test_side_hint_precedence(self):
        fb = _fb(actual=4.0, consensus=3.0, sigma_hist=0.3)
        verdict = MacroFusionGate().arbitrate_proposal(
            {"candidate_id": "X", "symbol": "EURUSD", "side_hint": "BUY"}, fb)
        assert verdict.proposed_side == "BUY"

    def test_invariant_across_all_paths(self):
        gate = MacroFusionGate()
        cases = [
            _fb(),                                      # clean
            _fb(vix=40.0),                              # kill switch
            _fb(minutes_to_release=1.0, tier="HIGH"),   # blackout
            _fb(is_adversarial=True),                   # spoof
            _fb(actual=4.0, consensus=3.0, sigma_hist=0.3),  # macro conflict
        ]
        for fb in cases:
            verdict = gate.arbitrate_proposal(
                {"candidate_id": "X", "symbol": "EURUSD", "side": "BUY"}, fb)
            assert verdict.execution_authority is False
            assert verdict.risk_multiplier in (0.0, 0.5, 1.0)

    def test_verdict_dict_roundtrip_keys(self):
        fb = _fb(is_adversarial=True)
        d = MacroFusionGate().arbitrate_proposal(
            {"candidate_id": "X", "symbol": "EURUSD", "side": "SELL"}, fb).to_dict()
        for key in ("verdict", "risk_multiplier", "is_vetoed", "rejection_reasons",
                    "feature_hash", "execution_authority"):
            assert key in d


# ---------------------------------------------------------------------------
# 4. Import-fallback hygiene (the change's unsatisfiable-fallback defect)
# ---------------------------------------------------------------------------

class TestImportFallbackTargets:
    def test_fallback_modules_resolve_as_specified(self):
        """Every module named in autonomous_causal_engine's package-qualified
        fallback must actually exist at that dotted path (regression: the
        fallback named intelligence.broker_replay / .edge_lifecycle, which have
        always lived in research/)."""
        import importlib
        import re
        with open("intelligence/autonomous_causal_engine.py", "r", encoding="utf-8") as fh:
            source = fh.read()
        m = re.search(r"except ImportError:\n((?:    (?:from|import) .*\n)+)", source)
        assert m, "fallback block not found"
        for line in m.group(1).strip().splitlines():
            match = re.match(r"\s*from ([\w.]+) import", line)
            if not match:
                continue
            module = importlib.import_module(match.group(1))
            assert module is not None
