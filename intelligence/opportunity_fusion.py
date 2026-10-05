"""Deterministic opportunity fusion: TA ∩ causal ∩ event (advisory score + macro gate).

This module never grants ``execution_authority``. Macro setups fail closed
unless event window is clear and at least one regime-filtered causal edge
agrees with the proposed side.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from operations.event_policy import symbol_currencies

_APPROVED_EDGE_STATUSES = frozenset({"APPROVED", "AUTO_APPROVED", "STAT_PASS"})
_MACRO_PREFIX = "macro_"

# Explicit gated set (also any setup_id starting with macro_).
MACRO_GATED_SETUPS: Set[str] = {
    "macro_continuation_v1",
}


def is_macro_setup(setup_id: str) -> bool:
    sid = str(setup_id or "").strip().lower()
    return sid.startswith(_MACRO_PREFIX) or sid in {s.lower() for s in MACRO_GATED_SETUPS}


def _edge_sign_agrees(sign: str, side: str) -> bool:
    sign_u = str(sign or "").strip().upper()
    side_u = str(side or "").strip().upper()
    if side_u == "BUY":
        return sign_u in {"POSITIVE", "POS", "+"}
    if side_u == "SELL":
        return sign_u in {"NEGATIVE", "NEG", "-"}
    return False


def _edge_touches_symbol(edge: Dict[str, Any], symbol: str) -> bool:
    """Prefer edges whose source/target names intersect symbol currencies/asset tokens."""
    currencies = symbol_currencies(symbol)
    tokens = {t.upper() for t in currencies}
    clean = str(symbol or "").upper().replace(".", "").replace("_", "")
    if clean:
        tokens.add(clean)
    if clean.startswith("XAU") or "GOLD" in clean:
        tokens.update({"GOLD", "XAU", "XAUUSD"})
    names = [
        str(edge.get("source_name") or edge.get("source") or ""),
        str(edge.get("target_name") or edge.get("target") or ""),
        str(edge.get("source_entity_name") or ""),
        str(edge.get("target_entity_name") or ""),
    ]
    for name in names:
        upper = name.upper().replace("-", "_")
        for token in tokens:
            if token and token in upper:
                return True
    return not any(names)


def _normalize_edges(edges: Any) -> List[Dict[str, Any]]:
    if edges is None:
        return []
    if isinstance(edges, dict):
        inner = edges.get("edges") or edges.get("items") or edges.get("macro_causal_context_regime")
        if isinstance(inner, list):
            return [e for e in inner if isinstance(e, dict)]
        return []
    if not isinstance(edges, (list, tuple)):
        return []
    return [e for e in edges if isinstance(e, dict)]


def _propagation_agrees(propagation: Any, side: str) -> Dict[str, Any]:
    """Multi-hop path bias may satisfy causal_agree when status is OK and bias matches side."""
    if not isinstance(propagation, dict):
        return {"agrees": False, "path_id": None, "bias": None, "score": 0.0}
    status = str(propagation.get("status") or "").upper()
    bias = str(propagation.get("bias") or "").strip().upper()
    side_u = str(side or "").strip().upper()
    path_id = propagation.get("path_id")
    try:
        path_score = float(propagation.get("score") or 0.0)
    except (TypeError, ValueError):
        path_score = 0.0
    if not (path_score == path_score) or path_score < 0:
        path_score = 0.0
    agrees = status == "OK" and bool(path_id) and bias == side_u and path_score > 0
    return {
        "agrees": agrees,
        "path_id": path_id if agrees else None,
        "bias": bias or None,
        "score": round(path_score, 6) if agrees else 0.0,
        "hops": len(propagation.get("hops") or []) if agrees else 0,
    }


def score_opportunity(
    *,
    setup_id: str,
    side: str,
    symbol: str = "",
    event_state: Optional[Dict[str, Any]] = None,
    causal_edges: Any = None,
    causal_propagation: Any = None,
    ta_eligible: bool = True,
    ta_confidence: float = 0.0,
    adx: float = 0.0,
    macro_features: Optional[Any] = None,
    model_decision_bundle: Optional[Any] = None,
) -> Dict[str, Any]:
    """Fuse TA eligibility, event policy, and causal edge/path agreement.

    Returns PASS / WEAK / BLOCK. Macro setups BLOCK unless event_ok and causal_agree.
    ``causal_agree`` is true when a regime-filtered 1-hop edge agrees with side,
    or when multi-hop ``causal_propagation`` bias matches side (status OK).
    Non-macro setups never BLOCK solely for missing causal edges (event blackout
    remains the validator's job via high_impact_news).
    """
    setup = str(setup_id or "unclassified_v1")
    side_u = str(side or "").strip().upper()
    event_state = event_state if isinstance(event_state, dict) else {}
    edges = _normalize_edges(causal_edges)
    prop_meta = _propagation_agrees(causal_propagation, side_u)

    blackout = bool(event_state.get("blackout"))
    event_reasons = [str(r) for r in (event_state.get("reasons") or []) if r]
    event_ok = not blackout and not event_reasons

    agreeing: List[Dict[str, Any]] = []
    symbol_agreeing: List[Dict[str, Any]] = []
    malformed_edges = 0
    for edge in edges:
        status = str(edge.get("status") or edge.get("lifecycle") or "").upper()
        if status and status not in _APPROVED_EDGE_STATUSES:
            if "status" in edge or "lifecycle" in edge:
                continue
        sign = edge.get("sign")
        if sign in (None, ""):
            malformed_edges += 1
            continue
        if not _edge_sign_agrees(str(sign), side_u):
            continue
        row = {
            "edge_id": edge.get("edge_id") or edge.get("id"),
            "sign": str(sign).upper(),
            "status": status or "REGIME_FILTERED",
            "relation": edge.get("relation"),
            "source_name": edge.get("source_name") or edge.get("source"),
            "target_name": edge.get("target_name") or edge.get("target"),
            "confidence": edge.get("confidence"),
        }
        agreeing.append(row)
        if symbol and _edge_touches_symbol(edge, symbol):
            symbol_agreeing.append(row)

    edge_agree = bool(symbol_agreeing or agreeing)
    preferred = symbol_agreeing or agreeing
    causal_agree = edge_agree or bool(prop_meta["agrees"])

    ta_ok = bool(ta_eligible)
    try:
        conf = float(ta_confidence or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    try:
        adx_v = float(adx or 0.0)
    except (TypeError, ValueError):
        adx_v = 0.0
    if not (conf == conf) or conf < 0:
        conf = 0.0
    if not (adx_v == adx_v) or adx_v < 0:
        adx_v = 0.0

    parts = [
        1.0 if event_ok else 0.0,
        1.0 if causal_agree else 0.0,
        1.0 if ta_ok else 0.0,
    ]
    strength = min(1.0, (min(conf, 1.0) * 0.5) + (min(adx_v, 50.0) / 100.0))
    # Slight boost when multi-hop path agrees (still capped; never authority).
    if prop_meta["agrees"]:
        strength = min(1.0, strength + min(float(prop_meta["score"]), 0.2) * 0.25)
    score = (sum(parts) / 3.0) * 0.85 + strength * 0.15

    reasons: List[str] = []
    if not event_ok:
        reasons.append("event blackout or malformed event state")
        reasons.extend(event_reasons[:3])
    if not causal_agree:
        reasons.append("no regime-filtered causal edge or multi-hop path agrees with side")
    elif prop_meta["agrees"] and not edge_agree:
        reasons.append(f"causal agree via multi-hop path {prop_meta['path_id']}")
    if not ta_ok:
        reasons.append("setup registry not eligible")
    if malformed_edges:
        reasons.append(f"skipped {malformed_edges} edges with missing sign")

    macro = is_macro_setup(setup)
    if macro and (not event_ok or not causal_agree):
        status = "BLOCK"
        if not causal_agree:
            reasons.append(
                "macro setup requires agreeing APPROVED/STAT_PASS causal edge or OK multi-hop path"
            )
        if not event_ok:
            reasons.append("macro setup blocked during event blackout")
    elif not ta_ok:
        status = "BLOCK"
    elif event_ok and causal_agree and ta_ok:
        status = "PASS"
    else:
        status = "WEAK"

    # Deep Macro-Cognitive Arbitration via MacroFusionGate
    macro_verdict_dict: Optional[Dict[str, Any]] = None
    macro_risk_multiplier: float = 1.0
    try:
        from intelligence.macro_fusion_gate import MacroFusionGate
        from research.decision_models.feature_block import DecisionFeatureBlock

        feats = macro_features
        if feats is None and (isinstance(event_state, dict) or symbol):
            ev_dict = event_state if isinstance(event_state, dict) else {}
            z_val = ev_dict.get("z_score")
            try:
                z_float = float(z_val) if z_val is not None else None
            except (ValueError, TypeError):
                z_float = None
            sym_str = str(symbol or "").upper()
            curr = str(ev_dict.get("currency") or (sym_str[:3] if len(sym_str) >= 3 else "USD"))
            feats = DecisionFeatureBlock(
                event_name=str(ev_dict.get("event_name") or ev_dict.get("event") or "CYCLE_OPPORTUNITY"),
                currency=curr,
                tier=str(ev_dict.get("tier") or ev_dict.get("impact") or "LOW").upper(),
                z_score=z_float,
                vix=float(ev_dict.get("vix") or 16.0),
                minutes_to_release=float(ev_dict.get("minutes_to_release") or 999.0),
                blackout_window_minutes=float(ev_dict.get("blackout_window_minutes") or 90.0),
                is_adversarial=bool(ev_dict.get("is_adversarial", False)),
            )

        if feats is not None:
            gate = MacroFusionGate()
            cand = {"candidate_id": setup, "symbol": str(symbol or "").upper(), "side": side_u}
            verdict = gate.arbitrate_proposal(cand, feats, model_decision_bundle=model_decision_bundle)
            macro_verdict_dict = verdict.to_dict()
            macro_risk_multiplier = verdict.risk_multiplier
            if verdict.is_vetoed:
                status = "BLOCK"
                reasons.extend(verdict.rejection_reasons)
            elif verdict.verdict == "CONDITIONAL_HALF_RISK":
                reasons.append("macro gate: conditional half risk sizing recommended")
    except Exception as _mfg_exc:
        pass

    # Record verdict in UI state ring buffer for OpenTUI observability
    try:
        from ui.gui_api import ui_state
        entry = {
            "setup_id": setup,
            "symbol": str(symbol or "").upper(),
            "side": side_u,
            "status": status,
            "score": round(float(score), 4),
            "macro_verdict": macro_verdict_dict.get("verdict") if macro_verdict_dict else "UNCHECKED",
            "macro_risk_multiplier": macro_risk_multiplier,
            "reasons": list(reasons),
            "timestamp": int(time.time() * 1000),
        }
        if not hasattr(ui_state, "recent_fusion_results"):
            ui_state.recent_fusion_results = []
        ui_state.recent_fusion_results.append(entry)
        if len(ui_state.recent_fusion_results) > 50:
            ui_state.recent_fusion_results = ui_state.recent_fusion_results[-50:]
        if macro_verdict_dict:
            ui_state.laya_decisions_count = getattr(ui_state, "laya_decisions_count", 0) + 1
    except Exception:
        pass

    return {
        "status": status,
        "score": round(float(score), 4),
        "setup_id": setup,
        "side": side_u,
        "symbol": str(symbol or "").upper(),
        "macro_setup": macro,
        "event_ok": event_ok,
        "causal_agree": causal_agree,
        "edge_agree": edge_agree,
        "propagation_agree": bool(prop_meta["agrees"]),
        "causal_path_id": prop_meta.get("path_id"),
        "ta_ok": ta_ok,
        "agreeing_edge_ids": [e.get("edge_id") for e in preferred if e.get("edge_id") is not None][:8],
        "agreeing_edges": preferred[:5],
        "reasons": reasons,
        "macro_verdict": macro_verdict_dict,
        "macro_risk_multiplier": macro_risk_multiplier,
        "execution_authority": False,
        "limitation": (
            "Fusion score is deterministic research/gate metadata; "
            "RiskController remains the only order authority."
        ),
    }


def fusion_block_reasons(fusion: Dict[str, Any]) -> List[str]:
    """Reasons to inject into ValidationResult when fusion status is BLOCK."""
    if not isinstance(fusion, dict) or str(fusion.get("status", "")).upper() != "BLOCK":
        return []
    base = [str(r) for r in (fusion.get("reasons") or []) if r]
    if not base:
        base = ["opportunity fusion blocked proposal"]
    return [f"opportunity fusion: {r}" for r in base]
