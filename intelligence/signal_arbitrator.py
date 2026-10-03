"""Signal Arbitration Layer — multi-source signal ranking and selection.

Aggregates signals from TechnicalNet, Laya Decision, Universal Decision Engine,
Debate Protocol, and OpportunityBus. Scores each by confidence × regime alignment
× causal support × event alignment. Selects the highest-scoring signal as the
fused proposal for RiskController validation.

Never grants execution_authority. Advisory only.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


# Minimum composite score for a signal to be considered actionable.
# Signals below this threshold result in HOLD.
MIN_ACTIONABLE_SCORE = 0.40

# Source weights — how much we trust each signal origin a priori.
# These are multiplied into the composite score before regime/causal adjustments.
SOURCE_WEIGHTS: Dict[str, float] = {
    "TechnicalNet": 0.70,       # Rule-based RSI/ADX — reliable but rigid
    "MarketModel.LLM": 0.85,   # LLM-routed — highest prior when available
    "Laya": 0.80,              # System 1 non-autoregressive — fast, calibrated
    "UniversalDecision": 0.75,  # Philosophical cross-domain — risk-aware
    "Debate": 0.90,            # Multi-agent adversarial — highest conviction
    "OpportunityBus": 0.65,    # Macro edge candidates — lower prior
}

# Narrative-decay conviction damping (D12_narrative_decay). When the active
# story is fading, exhausted, or invalidated, signals that DERIVE their edge
# from that story lose conviction. Only the Laya system-1 signal qualifies:
# it is built from the decision bundle whose feature block embeds the
# NarrativeStateMachine state (decay factor, evidence balance, tripwires).
# TechnicalNet/MarketModel/UDE signals price action and philosophy, not the
# narrative, so they are NOT damped. Unknown/absent D12 → no damping
# (fail-open; damping only ever reduces conviction, never boosts it).
NARRATIVE_DECAY_DAMPING: Dict[str, float] = {
    "STALE_FADING": 0.70,          # thinning conviction
    "EXHAUSTED_PRICED": 0.40,      # priced in — fade candidate
    "TRIPWIRE_INVALIDATED": 0.10,  # falsified — near-full discount
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _narrative_decay_state(decisions: Dict[str, Any]) -> Optional[str]:
    """D12 selected option from a DecisionBundle.to_dict() decisions map.

    None when the bundle predates the D12 head or carries no record.
    """
    if not isinstance(decisions, dict):
        return None
    rec = decisions.get("D12_narrative_decay")
    if not isinstance(rec, dict):
        return None
    value = str(rec.get("selected_option") or "").strip().upper()
    return value or None


def _direction_agrees(signal_dir: str, regime_bias: str) -> float:
    """Return regime alignment multiplier (0.2 - 1.0).

    1.0 = signal direction agrees with regime bias
    0.6 = neutral / no regime bias
    0.2 = signal direction opposes regime bias
    """
    s = str(signal_dir or "").strip().upper()
    r = str(regime_bias or "").strip().upper()
    if not r or r in ("NEUTRAL", "NONE", ""):
        return 0.6
    if s == r:
        return 1.0
    if s and r and s != r and s in ("BUY", "SELL") and r in ("BUY", "SELL"):
        return 0.2
    return 0.6


def _infer_laya_direction(d3_regime: str, d1_surprise: str) -> Optional[str]:
    """Infer directional signal from Laya's D3 regime + D1 surprise decisions.

    Returns BUY, SELL, or None if no clear direction.
    """
    d3 = str(d3_regime or "").upper()
    d1 = str(d1_surprise or "").upper()

    # Crisis / insufficient data → no directional signal
    if d3 in ("CRISIS_PANIC", "INSUFFICIENT_DATA", ""):
        return None

    # Strong regime signals
    if d3 == "RISK_ON_EXPANSION":
        return "BUY"
    if d3 == "NORMAL_RISK_OFF":
        return "SELL"
    if d3 == "STAGFLATION_SHOCK":
        # Stagflation is asset-dependent; use D1 surprise as tiebreaker
        if "DOVISH" in d1:
            return "BUY"
        if "HAWKISH" in d1:
            return "SELL"
        return None  # No clear direction in stagflation without surprise

    # Fallback: use D1 surprise alone
    if "EXTREME_DOVISH" in d1:
        return "BUY"
    if "EXTREME_HAWKISH" in d1:
        return "SELL"

    return None


def _causal_support(signal_side: str, causal_paths: List[Dict[str, Any]]) -> float:
    """Return causal support multiplier (0.3 - 1.0).

    1.0 = at least one causal path agrees with signal side
    0.6 = causal paths exist but neutral
    0.3 = causal paths oppose signal side
    """
    if not causal_paths:
        return 0.6
    side_u = str(signal_side or "").strip().upper()
    agrees = 0
    opposes = 0
    for path in causal_paths:
        bias = str(path.get("bias") or path.get("direction") or "").strip().upper()
        if bias == side_u:
            agrees += 1
        elif bias and bias in ("BUY", "SELL") and bias != side_u:
            opposes += 1
    if agrees > 0:
        return 1.0
    if opposes > 0:
        return 0.3
    return 0.6


def _event_alignment(event_state: Dict[str, Any]) -> float:
    """Return event window multiplier (0.0 - 1.0).

    1.0 = clear event window
    0.0 = event blackout active
    """
    if not isinstance(event_state, dict):
        return 1.0
    if bool(event_state.get("blackout")):
        return 0.0
    reasons = event_state.get("reasons") or []
    if reasons:
        return 0.3
    return 1.0


def _extract_signals_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract candidate signals from the trading cycle state."""
    signals: List[Dict[str, Any]] = []

    # 1. Raw hypotheses from MarketModel (RSI/ADX rules or LLM)
    for hyp in (state.get("raw_hypotheses") or []):
        if not isinstance(hyp, dict):
            continue
        direction = str(hyp.get("direction") or "HOLD").upper()
        if direction == "HOLD":
            continue
        source = "MarketModel.LLM" if hyp.get("model") == "LLM" else "TechnicalNet"
        signals.append({
            "source": source,
            "direction": direction,
            "confidence": _safe_float(hyp.get("confidence"), 0.5),
            "entry_price": _safe_float(hyp.get("entry_price")),
            "stop_loss": _safe_float(hyp.get("stop_loss")),
            "take_profit": _safe_float(hyp.get("take_profit")),
            "setup_name": str(hyp.get("setup_name") or "unknown"),
            "rationale": str(hyp.get("rationale") or ""),
            "risk_fraction": _safe_float(hyp.get("risk_fraction"), 0.01),
        })

    # 2. Universal Decision Engine signal
    ude = state.get("universal_decision")
    if isinstance(ude, dict):
        verdict = str(ude.get("verdict") or "").upper()
        if verdict in ("ACT", "ACT_WITH_CAUTION"):
            direction = str(ude.get("direction") or "BUY").upper()
            if direction in ("BUY", "SELL"):
                confidence = _safe_float(ude.get("confidence"), 0.5)
                # Apply philosophical risk discount for ACT_WITH_CAUTION
                if verdict == "ACT_WITH_CAUTION":
                    confidence *= 0.8
                signals.append({
                    "source": "UniversalDecision",
                    "direction": direction,
                    "confidence": confidence,
                    "entry_price": 0.0,
                    "stop_loss": 0.0,
                    "take_profit": 0.0,
                    "setup_name": "universal_philosophical",
                    "rationale": str(ude.get("rationale") or "UDE signal"),
                    "risk_fraction": 0.01,
                })

    # 3. Laya decision (from decision_bundle — DecisionBundle.to_dict() format)
    bundle = state.get("decision_bundle")
    if isinstance(bundle, dict) and isinstance(bundle.get("decisions"), dict):
        decisions = bundle["decisions"]
        veto_enforced = bool(bundle.get("veto_enforced"))
        risk_mult = _safe_float(bundle.get("risk_multiplier"), 1.0)

        # Skip if veto is active (D2 blackout, D9 kill switch, etc.)
        if not veto_enforced and risk_mult > 0.0:
            # D8 arbitration verdict determines if trade is allowed
            d8_rec = decisions.get("D8_opportunity_arbitration") or {}
            d8_verdict = str(d8_rec.get("selected_option") or "").upper()

            if d8_verdict in ("STRONG_PROCEED", "CONDITIONAL_HALF_RISK"):
                # Determine direction from D3 regime + D1 surprise
                d3_rec = decisions.get("D3_macro_regime") or {}
                d3_regime = str(d3_rec.get("selected_option") or "").upper()
                d1_rec = decisions.get("D1_economic_surprise") or {}
                d1_surprise = str(d1_rec.get("selected_option") or "").upper()

                # Direction inference: regime + surprise → BUY/SELL
                direction = _infer_laya_direction(d3_regime, d1_surprise)
                if direction:
                    # Confidence from arbitration type + risk multiplier
                    base_conf = 0.90 if d8_verdict == "STRONG_PROCEED" else 0.70
                    # Boost if Laya overrode the deterministic rule
                    overrides_count = int(bundle.get("overrides_applied_count", 0) or 0)
                    if overrides_count > 0:
                        base_conf = min(1.0, base_conf + 0.05)
                    # Apply risk multiplier as confidence discount
                    confidence = base_conf * risk_mult

                    # D12 narrative decay: damp conviction on this
                    # narrative-derived signal when the story is fading,
                    # exhausted, or invalidated by its falsification tripwire.
                    d12_state = _narrative_decay_state(decisions)
                    decay_damp = NARRATIVE_DECAY_DAMPING.get(d12_state or "")
                    if decay_damp is not None:
                        confidence *= decay_damp

                    # Extract rationale from key decisions
                    rationale_parts = []
                    if d3_regime:
                        rationale_parts.append(f"regime={d3_regime}")
                    if d1_surprise:
                        rationale_parts.append(f"surprise={d1_surprise}")
                    if overrides_count:
                        rationale_parts.append(f"overrides={overrides_count}")
                    if decay_damp is not None:
                        rationale_parts.append(
                            f"D12_narrative_decay={d12_state} conviction x{decay_damp:g}"
                        )

                    signals.append({
                        "source": "Laya",
                        "direction": direction,
                        "confidence": confidence,
                        "entry_price": 0.0,
                        "stop_loss": 0.0,
                        "take_profit": 0.0,
                        "setup_name": "laya_system1",
                        "rationale": (
                            f"Laya D8={d8_verdict}, " + ", ".join(rationale_parts)
                        ),
                        "risk_fraction": 0.01 * risk_mult,
                        "narrative_decay_state": d12_state,
                    })

    # 4. OpportunityBus candidates (from macro edges)
    # These are stored in the store, not directly in state. We skip here
    # and rely on the graph nodes to inject them into raw_hypotheses.

    return signals


def _score_signal(
    signal: Dict[str, Any],
    regime: Dict[str, Any],
    causal_paths: List[Dict[str, Any]],
    event_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Score a single signal with regime, causal, and event adjustments."""
    source = str(signal.get("source") or "Unknown")
    direction = str(signal.get("direction") or "HOLD").upper()
    base_confidence = _safe_float(signal.get("confidence"), 0.5)

    # Source prior weight
    source_weight = SOURCE_WEIGHTS.get(source, 0.5)

    # Regime alignment
    regime_bias = str(regime.get("bias") or regime.get("direction") or "").strip().upper()
    regime_mult = _direction_agrees(direction, regime_bias)

    # Causal support
    causal_mult = _causal_support(direction, causal_paths)

    # Event alignment
    event_mult = _event_alignment(event_state)

    # Composite score: weighted average of components
    composite = (
        base_confidence * 0.40
        + source_weight * 0.20
        + regime_mult * 0.15
        + causal_mult * 0.15
        + event_mult * 0.10
    )

    return {
        "source": source,
        "direction": direction,
        "confidence": round(base_confidence, 4),
        "source_weight": round(source_weight, 4),
        "regime_alignment": round(regime_mult, 4),
        "causal_support": round(causal_mult, 4),
        "event_alignment": round(event_mult, 4),
        "composite_score": round(composite, 4),
        "narrative_decay_state": signal.get("narrative_decay_state"),
        "setup_name": signal.get("setup_name", "unknown"),
        "rationale": signal.get("rationale", ""),
        "entry_price": signal.get("entry_price", 0.0),
        "stop_loss": signal.get("stop_loss", 0.0),
        "take_profit": signal.get("take_profit", 0.0),
        "risk_fraction": signal.get("risk_fraction", 0.01),
    }


def arbitrate_signals(
    state: Dict[str, Any],
    regime: Optional[Dict[str, Any]] = None,
    causal_paths: Optional[List[Dict[str, Any]]] = None,
    event_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Arbitrate multiple signals and select the best one.

    Args:
        state: Full TradingCycleState dict
        regime: Macro regime dict with 'bias' or 'direction' field
        causal_paths: List of causal path dicts
        event_state: Event policy state dict

    Returns:
        Dict with 'best_signal', 'all_scored', 'arbitration_audit'
    """
    regime = regime or state.get("macro_regime", {})
    causal_paths = causal_paths or state.get("causal_paths", [])
    event_state = event_state or {}

    # Extract all candidate signals
    signals = _extract_signals_from_state(state)

    if not signals:
        return {
            "best_signal": None,
            "all_scored": [],
            "arbitration_audit": ["SignalArbitrator: No actionable signals found"],
            "verdict": "HOLD",
        }

    # Surface narrative-decay damping in the audit trail (applied inside
    # extraction to the narrative-derived Laya signal only).
    _d12 = _narrative_decay_state(
        (state.get("decision_bundle") or {}).get("decisions")
        if isinstance(state.get("decision_bundle"), dict) else None
    )
    if _d12 in NARRATIVE_DECAY_DAMPING and any(
        s.get("source") == "Laya" for s in signals
    ):
        damped_conf = next(
            s["confidence"] for s in signals if s.get("source") == "Laya"
        )
        audit_note = (
            f"SignalArbitrator: D12 narrative decay {_d12} -> Laya conviction "
            f"damped to {damped_conf:.4f} "
            f"(factor {NARRATIVE_DECAY_DAMPING[_d12]:g})"
        )
    else:
        audit_note = None

    # Score each signal
    scored = [_score_signal(sig, regime, causal_paths, event_state) for sig in signals]

    # Sort by composite score descending
    scored.sort(key=lambda x: x["composite_score"], reverse=True)

    best = scored[0]
    best_score = best["composite_score"]

    # Apply minimum threshold
    if best_score < MIN_ACTIONABLE_SCORE:
        return {
            "best_signal": None,
            "all_scored": scored,
            "arbitration_audit": [
                f"SignalArbitrator: Best signal ({best['source']}) "
                f"score {best_score:.4f} below threshold {MIN_ACTIONABLE_SCORE}",
                f"SignalArbitrator: Defaulting to HOLD",
            ],
            "verdict": "HOLD",
        }

    # Build the fused proposal from the best signal
    fused_proposal = {
        "symbol": state.get("symbol", ""),
        "direction": best["direction"],
        "entry_price": best["entry_price"],
        "stop_loss": best["stop_loss"],
        "take_profit": best["take_profit"],
        "confidence": best["confidence"],
        "timeframe": state.get("timeframe", "M15"),
        "setup_name": best["setup_name"],
        "rationale": (
            f"Signal Arbitration: {best['source']} selected with "
            f"composite score {best_score:.4f} "
            f"(confidence={best['confidence']:.2f}, "
            f"regime={best['regime_alignment']:.2f}, "
            f"causal={best['causal_support']:.2f}, "
            f"event={best['event_alignment']:.2f}). "
            f"Beat {len(scored) - 1} other candidate(s). "
            f"Original: {best['rationale']}"
        ),
        "risk_fraction": best["risk_fraction"],
    }

    audit = [
        f"SignalArbitrator: Evaluated {len(signals)} signal(s)",
        f"SignalArbitrator: Winner = {best['source']} "
        f"(score={best_score:.4f}, dir={best['direction']})",
    ]
    if audit_note:
        audit.insert(1, audit_note)
    for i, s in enumerate(scored[:5]):
        audit.append(
            f"SignalArbitrator:   #{i+1} {s['source']} "
            f"score={s['composite_score']:.4f} dir={s['direction']}"
        )

    return {
        "best_signal": best,
        "all_scored": scored,
        "fused_proposal": fused_proposal,
        "fusion_score": best_score,
        "arbitration_audit": audit,
        "verdict": best["direction"],
    }
