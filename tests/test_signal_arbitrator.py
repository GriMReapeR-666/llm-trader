"""Tests for Signal Arbitration Layer — multi-source signal ranking and selection.

Covers:
- Signal extraction from state (MarketModel, UDE, Laya)
- Scoring formula (confidence, source weight, regime, causal, event)
- Arbitration verdict (best signal selection, HOLD fallback, threshold gating)
- Edge cases (empty state, all HOLD, opposing signals, ties)
"""
import ast
import inspect
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "intelligence"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from intelligence.signal_arbitrator import (
    arbitrate_signals,
    _extract_signals_from_state,
    _score_signal,
    _direction_agrees,
    _causal_support,
    _event_alignment,
    _infer_laya_direction,
    _narrative_decay_state,
    MIN_ACTIONABLE_SCORE,
    SOURCE_WEIGHTS,
    NARRATIVE_DECAY_DAMPING,
)


def _base_state(**overrides):
    state = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "strategy": "causal_continuation",
        "macro_regime": {},
        "causal_paths": [],
        "raw_hypotheses": [],
        "fused_proposal": None,
        "fusion_score": 0.0,
        "universal_decision": None,
        "decision_bundle": None,
    }
    state.update(overrides)
    return state


# ── Direction Agreement ──────────────────────────────────────────────

class TestDirectionAgrees:
    def test_same_direction(self):
        assert _direction_agrees("BUY", "BUY") == 1.0

    def test_opposite_direction(self):
        assert _direction_agrees("BUY", "SELL") == 0.2

    def test_neutral_regime(self):
        assert _direction_agrees("BUY", "NEUTRAL") == 0.6

    def test_empty_regime(self):
        assert _direction_agrees("BUY", "") == 0.6

    def test_none_regime(self):
        assert _direction_agrees("BUY", None) == 0.6


# ── Causal Support ───────────────────────────────────────────────────

class TestCausalSupport:
    def test_no_paths(self):
        assert _causal_support("BUY", []) == 0.6

    def test_agreeing_path(self):
        paths = [{"bias": "BUY", "score": 0.8}]
        assert _causal_support("BUY", paths) == 1.0

    def test_opposing_path(self):
        paths = [{"bias": "SELL", "score": 0.8}]
        assert _causal_support("BUY", paths) == 0.3

    def test_neutral_paths(self):
        paths = [{"bias": "NEUTRAL", "score": 0.5}]
        assert _causal_support("BUY", paths) == 0.6

    def test_mixed_paths_agree_wins(self):
        paths = [{"bias": "BUY"}, {"bias": "SELL"}]
        assert _causal_support("BUY", paths) == 1.0


# ── Event Alignment ──────────────────────────────────────────────────

class TestEventAlignment:
    def test_no_event(self):
        assert _event_alignment({}) == 1.0

    def test_blackout(self):
        assert _event_alignment({"blackout": True}) == 0.0

    def test_reasons(self):
        assert _event_alignment({"reasons": ["NFP in 30min"]}) == 0.3

    def test_none_input(self):
        assert _event_alignment(None) == 1.0


# ── Signal Extraction ────────────────────────────────────────────────

class TestExtractSignals:
    def test_no_signals(self):
        state = _base_state()
        signals = _extract_signals_from_state(state)
        assert signals == []

    def test_market_model_buy(self):
        hyp = {
            "direction": "BUY",
            "confidence": 0.85,
            "entry_price": 1.085,
            "stop_loss": 1.080,
            "take_profit": 1.095,
            "setup_name": "oversold_bounce",
            "rationale": "RSI < 35",
            "risk_fraction": 0.01,
        }
        state = _base_state(raw_hypotheses=[hyp])
        signals = _extract_signals_from_state(state)
        assert len(signals) == 1
        assert signals[0]["source"] == "TechnicalNet"
        assert signals[0]["direction"] == "BUY"

    def test_market_model_hold_skipped(self):
        hyp = {"direction": "HOLD", "confidence": 0.5}
        state = _base_state(raw_hypotheses=[hyp])
        signals = _extract_signals_from_state(state)
        assert len(signals) == 0

    def test_ude_act_signal(self):
        ude = {"verdict": "ACT", "direction": "SELL", "confidence": 0.7}
        state = _base_state(universal_decision=ude)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 1
        assert signals[0]["source"] == "UniversalDecision"
        assert signals[0]["direction"] == "SELL"

    def test_ude_veto_skipped(self):
        ude = {"verdict": "VETO", "direction": "BUY", "confidence": 0.5}
        state = _base_state(universal_decision=ude)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 0

    def test_ude_act_with_caution_discount(self):
        ude = {"verdict": "ACT_WITH_CAUTION", "direction": "BUY", "confidence": 0.8}
        state = _base_state(universal_decision=ude)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 1
        assert signals[0]["confidence"] == pytest.approx(0.64, abs=0.01)

    def test_laya_buy_signal(self):
        bundle = {
            "veto_enforced": False,
            "risk_multiplier": 1.0,
            "overrides_applied_count": 1,
            "decisions": {
                "D8_opportunity_arbitration": {"selected_option": "STRONG_PROCEED"},
                "D3_macro_regime": {"selected_option": "RISK_ON_EXPANSION"},
                "D1_economic_surprise": {"selected_option": "NEUTRAL"},
            },
        }
        state = _base_state(decision_bundle=bundle)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 1
        assert signals[0]["source"] == "Laya"
        assert signals[0]["direction"] == "BUY"

    def test_laya_veto_skipped(self):
        bundle = {
            "veto_enforced": True,
            "risk_multiplier": 0.0,
            "decisions": {
                "D8_opportunity_arbitration": {"selected_option": "REJECT_MACRO_CONFLICT"},
            },
        }
        state = _base_state(decision_bundle=bundle)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 0

    def test_laya_conditional_half_risk(self):
        bundle = {
            "veto_enforced": False,
            "risk_multiplier": 0.5,
            "overrides_applied_count": 0,
            "decisions": {
                "D8_opportunity_arbitration": {"selected_option": "CONDITIONAL_HALF_RISK"},
                "D3_macro_regime": {"selected_option": "NORMAL_RISK_OFF"},
                "D1_economic_surprise": {"selected_option": "NEUTRAL"},
            },
        }
        state = _base_state(decision_bundle=bundle)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 1
        assert signals[0]["source"] == "Laya"
        assert signals[0]["direction"] == "SELL"
        assert signals[0]["confidence"] < 0.9  # reduced due to conditional

    def test_laya_stagflation_no_direction(self):
        bundle = {
            "veto_enforced": False,
            "risk_multiplier": 1.0,
            "overrides_applied_count": 0,
            "decisions": {
                "D8_opportunity_arbitration": {"selected_option": "STRONG_PROCEED"},
                "D3_macro_regime": {"selected_option": "STAGFLATION_SHOCK"},
                "D1_economic_surprise": {"selected_option": "NEUTRAL"},
            },
        }
        state = _base_state(decision_bundle=bundle)
        signals = _extract_signals_from_state(state)
        assert len(signals) == 0  # no clear direction in stagflation + neutral surprise

    def test_multiple_sources(self):
        hyp = {"direction": "BUY", "confidence": 0.8}
        ude = {"verdict": "ACT", "direction": "BUY", "confidence": 0.7}
        bundle = {
            "veto_enforced": False,
            "risk_multiplier": 1.0,
            "overrides_applied_count": 0,
            "decisions": {
                "D8_opportunity_arbitration": {"selected_option": "STRONG_PROCEED"},
                "D3_macro_regime": {"selected_option": "RISK_ON_EXPANSION"},
                "D1_economic_surprise": {"selected_option": "NEUTRAL"},
            },
        }
        state = _base_state(
            raw_hypotheses=[hyp],
            universal_decision=ude,
            decision_bundle=bundle,
        )
        signals = _extract_signals_from_state(state)
        assert len(signals) == 3
        sources = {s["source"] for s in signals}
        assert sources == {"TechnicalNet", "UniversalDecision", "Laya"}


# ── Scoring ──────────────────────────────────────────────────────────

class TestScoring:
    def test_score_basic(self):
        signal = {
            "source": "TechnicalNet",
            "direction": "BUY",
            "confidence": 0.85,
            "entry_price": 1.085,
            "stop_loss": 1.080,
            "take_profit": 1.095,
            "setup_name": "bounce",
            "rationale": "test",
            "risk_fraction": 0.01,
        }
        regime = {"bias": "BUY"}
        causal = [{"bias": "BUY"}]
        event = {}
        result = _score_signal(signal, regime, causal, event)
        assert result["source"] == "TechnicalNet"
        assert result["direction"] == "BUY"
        assert result["composite_score"] > 0.5
        assert result["regime_alignment"] == 1.0
        assert result["causal_support"] == 1.0
        assert result["event_alignment"] == 1.0

    def test_score_penalty_opposing_regime(self):
        signal = {"source": "TechnicalNet", "direction": "BUY", "confidence": 0.5}
        regime = {"bias": "SELL"}
        result = _score_signal(signal, regime, [], {})
        assert result["regime_alignment"] == 0.2
        # Score is lower than if regime agreed
        agreeing = _score_signal(signal, {"bias": "BUY"}, [], {})
        assert result["composite_score"] < agreeing["composite_score"]

    def test_score_penalty_blackout(self):
        signal = {"source": "TechnicalNet", "direction": "BUY", "confidence": 0.5}
        result = _score_signal(signal, {}, [], {"blackout": True})
        assert result["event_alignment"] == 0.0
        # Score is lower than with clear event window
        clear = _score_signal(signal, {}, [], {})
        assert result["composite_score"] < clear["composite_score"]

    def test_llm_source_higher_weight(self):
        signal_llm = {"source": "MarketModel.LLM", "direction": "BUY", "confidence": 0.85}
        signal_rule = {"source": "TechnicalNet", "direction": "BUY", "confidence": 0.85}
        regime = {"bias": "BUY"}
        score_llm = _score_signal(signal_llm, regime, [], {})["composite_score"]
        score_rule = _score_signal(signal_rule, regime, [], {})["composite_score"]
        assert score_llm > score_rule


# ── Full Arbitration ─────────────────────────────────────────────────

class TestArbitrateSignals:
    def test_empty_state_returns_hold(self):
        state = _base_state()
        result = arbitrate_signals(state)
        assert result["verdict"] == "HOLD"
        assert result["best_signal"] is None

    def test_single_buy_signal_passes(self):
        hyp = {"direction": "BUY", "confidence": 0.9, "entry_price": 1.085,
               "stop_loss": 1.080, "take_profit": 1.095}
        state = _base_state(raw_hypotheses=[hyp])
        result = arbitrate_signals(state, regime={"bias": "BUY"})
        assert result["verdict"] == "BUY"
        assert result["best_signal"] is not None
        assert result["fused_proposal"] is not None
        assert result["fused_proposal"]["direction"] == "BUY"

    def test_opposite_signals_pick_best(self):
        buy_hyp = {"direction": "BUY", "confidence": 0.6, "entry_price": 1.085,
                    "stop_loss": 1.080, "take_profit": 1.095}
        sell_hyp = {"direction": "SELL", "confidence": 0.9, "entry_price": 1.085,
                     "stop_loss": 1.090, "take_profit": 1.075}
        state = _base_state(raw_hypotheses=[buy_hyp, sell_hyp])
        result = arbitrate_signals(state, regime={"bias": "SELL"})
        assert result["verdict"] == "SELL"
        assert result["best_signal"]["source"] == "TechnicalNet"

    def test_low_confidence_below_threshold(self):
        # Very low confidence + opposing regime + opposing causal + blackout
        # should push score below MIN_ACTIONABLE_SCORE
        hyp = {"direction": "BUY", "confidence": 0.05}
        state = _base_state(raw_hypotheses=[hyp])
        result = arbitrate_signals(
            state,
            regime={"bias": "SELL"},
            causal_paths=[{"bias": "SELL"}],
            event_state={"blackout": True},
        )
        assert result["verdict"] == "HOLD"
        assert result["best_signal"] is None

    def test_all_scored_returned(self):
        hyp1 = {"direction": "BUY", "confidence": 0.9}
        hyp2 = {"direction": "SELL", "confidence": 0.7}
        state = _base_state(raw_hypotheses=[hyp1, hyp2])
        result = arbitrate_signals(state)
        assert len(result["all_scored"]) == 2

    def test_fused_proposal_has_symbol(self):
        hyp = {"direction": "BUY", "confidence": 0.9, "entry_price": 1.085,
               "stop_loss": 1.080, "take_profit": 1.095}
        state = _base_state(raw_hypotheses=[hyp], symbol="XAUUSD")
        result = arbitrate_signals(state)
        assert result["fused_proposal"]["symbol"] == "XAUUSD"

    def test_arbitration_audit_populated(self):
        hyp = {"direction": "BUY", "confidence": 0.9, "entry_price": 1.085,
               "stop_loss": 1.080, "take_profit": 1.095}
        state = _base_state(raw_hypotheses=[hyp])
        result = arbitrate_signals(state)
        assert len(result["arbitration_audit"]) > 0

    def test_ude_and_market_model_compete(self):
        hyp = {"direction": "BUY", "confidence": 0.8, "entry_price": 1.085,
               "stop_loss": 1.080, "take_profit": 1.095}
        ude = {"verdict": "ACT", "direction": "SELL", "confidence": 0.9}
        state = _base_state(raw_hypotheses=[hyp], universal_decision=ude)
        result = arbitrate_signals(state, regime={"bias": "SELL"})
        # SELL should win due to regime alignment + high UDE confidence
        assert result["verdict"] == "SELL"
        assert result["best_signal"]["source"] == "UniversalDecision"

    def test_laya_and_technicalnet_compete(self):
        hyp = {"direction": "SELL", "confidence": 0.8, "entry_price": 1.085,
                "stop_loss": 1.090, "take_profit": 1.075}
        bundle = {
            "veto_enforced": False,
            "risk_multiplier": 1.0,
            "overrides_applied_count": 0,
            "decisions": {
                "D8_opportunity_arbitration": {"selected_option": "STRONG_PROCEED"},
                "D3_macro_regime": {"selected_option": "RISK_ON_EXPANSION"},
                "D1_economic_surprise": {"selected_option": "NEUTRAL"},
            },
        }
        state = _base_state(raw_hypotheses=[hyp], decision_bundle=bundle)
        result = arbitrate_signals(state, regime={"bias": "BUY"})
        # Laya should win due to higher source weight + regime alignment
        assert result["verdict"] == "BUY"
        assert result["best_signal"]["source"] == "Laya"


# ── Source Weights ───────────────────────────────────────────────────

class TestSourceWeights:
    def test_debate_highest_weight(self):
        assert SOURCE_WEIGHTS["Debate"] > SOURCE_WEIGHTS["MarketModel.LLM"]

    def test_llm_higher_than_rules(self):
        assert SOURCE_WEIGHTS["MarketModel.LLM"] > SOURCE_WEIGHTS["TechnicalNet"]

    def test_all_weights_in_range(self):
        for source, weight in SOURCE_WEIGHTS.items():
            assert 0.0 < weight <= 1.0, f"{source} weight {weight} out of range"


# ── Threshold ────────────────────────────────────────────────────────

class TestThreshold:
    def test_min_actionable_score_reasonable(self):
        assert 0.0 < MIN_ACTIONABLE_SCORE < 1.0

    def test_exactly_at_threshold(self):
        state = _base_state(raw_hypotheses=[
            {"direction": "BUY", "confidence": 0.5, "entry_price": 1.0,
             "stop_loss": 0.99, "take_profit": 1.02}
        ])
        result = arbitrate_signals(state, regime={"bias": "BUY"})
        # Should be above threshold with regime alignment
        assert result["verdict"] == "BUY"


# ── D12 Narrative-Decay Conviction Damping ───────────────────────────

def _laya_bundle(d12_value=None, d8="STRONG_PROCEED", risk_mult=1.0,
                 overrides=0, d3="RISK_ON_EXPANSION", d1="NEUTRAL"):
    """Decision bundle dict with an optional D12 record (to_dict() format)."""
    decisions = {
        "D8_opportunity_arbitration": {"selected_option": d8},
        "D3_macro_regime": {"selected_option": d3},
        "D1_economic_surprise": {"selected_option": d1},
    }
    if d12_value is not None:
        decisions["D12_narrative_decay"] = {"selected_option": d12_value}
    return {
        "veto_enforced": False,
        "risk_multiplier": risk_mult,
        "overrides_applied_count": overrides,
        "decisions": decisions,
    }


class TestNarrativeDecayDamping:
    def test_reader_returns_none_without_d12(self):
        assert _narrative_decay_state({}) is None
        assert _narrative_decay_state(None) is None

    def test_reader_extracts_value(self):
        bundle = _laya_bundle("STALE_FADING")
        assert _narrative_decay_state(bundle["decisions"]) == "STALE_FADING"

    def test_reader_case_insensitive_and_tolerant(self):
        rec = {"selected_option": " exhausted_priced "}
        assert _narrative_decay_state({"D12_narrative_decay": rec}) == "EXHAUSTED_PRICED"
        assert _narrative_decay_state({"D12_narrative_decay": {"selected_option": ""}}) is None

    def test_fading_damps_laya_confidence(self):
        base = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle()))[0]["confidence"]
        damped = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle("STALE_FADING")))[0]
        assert damped["confidence"] == pytest.approx(base * 0.70, rel=1e-6)
        assert damped["narrative_decay_state"] == "STALE_FADING"
        assert "D12_narrative_decay=STALE_FADING" in damped["rationale"]

    def test_exhausted_damps_harder(self):
        base = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle()))[0]["confidence"]
        damped = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle("EXHAUSTED_PRICED")))[0]
        assert damped["confidence"] == pytest.approx(base * 0.40, rel=1e-6)

    def test_tripwire_invalidated_nearly_zeroes_conviction(self):
        damped = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle("TRIPWIRE_INVALIDATED")))[0]
        assert damped["confidence"] < 0.10
        assert damped["confidence"] > 0.0  # damp, never silent drop

    def test_fresh_and_abstention_pass_through_undamped(self):
        base = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle()))[0]["confidence"]
        for v in ("FRESH_CONFIRMED", "ACTIVE_FRESH", "INSUFFICIENT_DATA"):
            sig = _extract_signals_from_state(
                _base_state(decision_bundle=_laya_bundle(v)))[0]
            assert sig["confidence"] == pytest.approx(base, rel=1e-6)

    def test_legacy_bundle_without_d12_undamped(self):
        base = _extract_signals_from_state(
            _base_state(decision_bundle=_laya_bundle()))[0]["confidence"]
        legacy = _laya_bundle()
        legacy["decisions"].pop("D12_narrative_decay", None)
        sig = _extract_signals_from_state(_base_state(decision_bundle=legacy))[0]
        assert sig["confidence"] == pytest.approx(base, rel=1e-6)

    def test_damping_targets_only_laya_source(self):
        """TechnicalNet/MarketModel/UDE signals are not narrative-derived."""
        state = _base_state(
            raw_hypotheses=[{
                "direction": "BUY", "confidence": 0.85, "entry_price": 1.0,
                "stop_loss": 0.99, "take_profit": 1.02,
            }],
            decision_bundle=_laya_bundle("EXHAUSTED_PRICED"),
        )
        signals = {s["source"]: s for s in _extract_signals_from_state(state)}
        assert signals["TechnicalNet"]["confidence"] == pytest.approx(0.85, rel=1e-6)
        assert signals["Laya"]["confidence"] < 0.5

    def test_damped_laya_can_lose_to_undamped_technical(self):
        """End-to-end: an exhausted narrative lets a strong technical signal win."""
        state = _base_state(
            raw_hypotheses=[{
                "direction": "BUY", "confidence": 0.80, "entry_price": 1.0,
                "stop_loss": 0.99, "take_profit": 1.02, "setup_name": "trend",
            }],
            decision_bundle=_laya_bundle("EXHAUSTED_PRICED"),
        )
        result = arbitrate_signals(state, regime={"bias": "BUY"})
        assert result["best_signal"]["source"] == "TechnicalNet"
        assert any("D12 narrative decay EXHAUSTED_PRICED" in line
                   for line in result["arbitration_audit"])

    def test_damped_confidence_carries_into_fused_proposal(self):
        """Damping flows through scoring into the fused proposal's confidence."""
        undamped = arbitrate_signals(
            _base_state(decision_bundle=_laya_bundle(
                None, d8="CONDITIONAL_HALF_RISK", risk_mult=0.5)),
            regime={"bias": "BUY"},
        )
        damped = arbitrate_signals(
            _base_state(decision_bundle=_laya_bundle(
                "TRIPWIRE_INVALIDATED", d8="CONDITIONAL_HALF_RISK", risk_mult=0.5)),
            regime={"bias": "BUY"},
        )
        assert damped["best_signal"]["confidence"] == pytest.approx(0.035, abs=1e-6)
        assert damped["fusion_score"] < undamped["fusion_score"]
        assert damped["best_signal"]["narrative_decay_state"] == "TRIPWIRE_INVALIDATED"

    def test_audit_note_absent_when_no_damping(self):
        result = arbitrate_signals(
            _base_state(decision_bundle=_laya_bundle()), regime={"bias": "BUY"})
        assert not any("narrative decay" in line.lower()
                       for line in result["arbitration_audit"])

    def test_damping_table_covers_only_decay_states(self):
        assert set(NARRATIVE_DECAY_DAMPING) == {
            "STALE_FADING", "EXHAUSTED_PRICED", "TRIPWIRE_INVALIDATED"}
        for factor in NARRATIVE_DECAY_DAMPING.values():
            assert 0.0 < factor < 1.0


class TestInferLayaDirection:
    """Regression + behavior pins for _infer_laya_direction.

    A duplicated copy of _direction_agrees' body once lived after the
    function's final ``return None`` — unreachable dead code referencing
    undefined locals (signal_dir/regime_bias). These tests pin the actual
    decision contract AND the module's AST shape (no statements after the
    last return in the function body), so a paste-back regression fails
    immediately instead of hiding as unreachable code.
    """

    def test_strong_regime_signals(self):
        assert _infer_laya_direction("RISK_ON_EXPANSION", "") == "BUY"
        assert _infer_laya_direction("NORMAL_RISK_OFF", "") == "SELL"

    def test_crisis_and_insufficient_data_have_no_direction(self):
        for d3 in ("CRISIS_PANIC", "INSUFFICIENT_DATA", ""):
            # Even an extreme surprise must NOT rescue a crisis regime.
            assert _infer_laya_direction(d3, "EXTREME_DOVISH") is None

    def test_stagflation_uses_surprise_tiebreaker(self):
        assert _infer_laya_direction("STAGFLATION_SHOCK", "EXTREME_DOVISH") == "BUY"
        assert _infer_laya_direction("STAGFLATION_SHOCK", "EXTREME_HAWKISH") == "SELL"
        assert _infer_laya_direction("STAGFLATION_SHOCK", "IN_LINE") is None
        assert _infer_laya_direction("STAGFLATION_SHOCK", "") is None

    def test_fallback_extreme_surprise_only(self):
        assert _infer_laya_direction("UNKNOWN_REGIME", "EXTREME_DOVISH") == "BUY"
        assert _infer_laya_direction("UNKNOWN_REGIME", "EXTREME_HAWKISH") == "SELL"
        assert _infer_laya_direction("UNKNOWN_REGIME", "MODERATELY_DOVISH") is None

    def test_case_insensitive_and_none_tolerant(self):
        assert _infer_laya_direction("risk_on_expansion", None) == "BUY"
        assert _infer_laya_direction(None, None) is None
        # An EMPTY/None regime is 'insufficient data' by contract: even an
        # extreme surprise yields no direction (the surprise fallback applies
        # only to recognized-but-unknown regimes, see the UNKNOWN pin above).
        assert _infer_laya_direction(None, "extreme_hawkish") is None

    def test_no_dead_tail_after_final_return(self):
        """AST pin: the ONLY bare ``return None`` is the function's LAST
        statement — the pasted _direction_agrees fragment this test replaces
        (statements after the final return) fails here as a dead tail.
        """
        source = inspect.getsource(_infer_laya_direction)
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        body = fn.body
        assert body, "function body must not be empty"
        assert isinstance(body[-1], ast.Return), \
            "final statement of _infer_laya_direction must be a return"
        assert isinstance(body[-1].value, ast.Constant) \
            and body[-1].value.value is None, \
            "terminal return must be the bare None"
        none_returns = [s for s in body
                        if isinstance(s, ast.Return)
                        and isinstance(s.value, ast.Constant)
                        and s.value.value is None]
        assert none_returns == [body[-1]], \
            "statements exist after the terminal 'return None' (dead tail)"
