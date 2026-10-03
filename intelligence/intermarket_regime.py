"""Live intermarket correlation-regime classifier (advisory only).

Compares observed pair correlation signs against ``CORRELATION_REGIMES`` from
the Economic Dynamics catalog.  Output never grants execution authority.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from economic_dynamics_catalog import CORRELATION_REGIMES, edges_for_regime


# Broker/Yahoo symbol aliases → catalog entity names used in regime pair_signs.
ENTITY_SYMBOL_PROXIES: Dict[str, Tuple[str, ...]] = {
    "DXY": ("DXY", "USDX", "DX-Y.NYB", "UUP", "USDIDX"),
    "GOLD": ("XAUUSD", "XAUUSDm", "GOLD", "XAUUSD.", "XAUEUR"),
    "WTI_OIL": ("USOIL", "XTIUSD", "WTI", "USOIL.cash", "CL", "CRUDE"),
    "EQUITIES": ("US500", "SPX500", "SPY", "USTEC", "NAS100", "US30", "SPX"),
    "VIX": ("VIX", "VIXCLS", "VOLX", "VIX.cash"),
    # Yields: broker symbols vary; include common CFD/ETF aliases.
    "REAL_YIELD": ("DFII10", "US10Yreal", "TIPS", "TIP"),
    "YIELD_CURVE_SLOPE": ("T10Y2Y", "US10Y-US02Y", "IRD_US10US2", "UST10Y", "US10Y"),
}

# Prefer these pairs for regime scoring (matches catalog CORRELATION_REGIMES).
REGIME_ENTITY_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("DXY", "GOLD"),
    ("DXY", "WTI_OIL"),
    ("GOLD", "EQUITIES"),
    ("GOLD", "WTI_OIL"),
    ("VIX", "EQUITIES"),
    ("DXY", "EQUITIES"),
    ("REAL_YIELD", "GOLD"),
    ("REAL_YIELD", "EQUITIES"),
    ("YIELD_CURVE_SLOPE", "EQUITIES"),
    ("WTI_OIL", "EQUITIES"),
)


def _sign_from_corr(value: float, *, deadzone: float = 0.15) -> str:
    if value >= deadzone:
        return "POSITIVE"
    if value <= -deadzone:
        return "NEGATIVE"
    return "NEUTRAL"


def _normalize_pair_key(a: str, b: str) -> str:
    left, right = sorted([str(a).upper(), str(b).upper()])
    return f"{left}|{right}"


def observed_pair_signs(
    correlations: Mapping[str, float],
    *,
    deadzone: float = 0.15,
) -> Dict[str, str]:
    """Accept keys like ``DXY|GOLD`` or ``gold_dxy`` style aliases."""
    out: Dict[str, str] = {}
    for key, value in correlations.items():
        try:
            corr = float(value)
        except (TypeError, ValueError):
            continue
        raw = str(key).upper().replace("_", "|").replace("-", "|")
        if "|" not in raw:
            continue
        left, right = [p for p in raw.split("|") if p][:2]
        if not left or not right:
            continue
        out[_normalize_pair_key(left, right)] = _sign_from_corr(corr, deadzone=deadzone)
    return out


def score_regime(
    regime: Mapping[str, Any],
    observed: Mapping[str, str],
) -> Dict[str, Any]:
    expected = regime.get("pair_signs") or {}
    matches = 0
    compared = 0
    details: List[Dict[str, str]] = []
    for pair, expected_sign in expected.items():
        parts = str(pair).upper().split("|")
        if len(parts) != 2:
            continue
        key = _normalize_pair_key(parts[0], parts[1])
        got = observed.get(key)
        if got is None or got == "NEUTRAL":
            continue
        compared += 1
        ok = got == str(expected_sign).upper()
        if ok:
            matches += 1
        details.append({
            "pair": key,
            "expected": str(expected_sign).upper(),
            "observed": got,
            "match": "YES" if ok else "NO",
        })
    score = (matches / compared) if compared else 0.0
    return {
        "regime_id": regime.get("regime_id"),
        "label": regime.get("label"),
        "score": score,
        "matches": matches,
        "compared": compared,
        "details": details,
        "broken_pairs": list(regime.get("broken_pairs") or []),
        "advisory_bias": list(regime.get("advisory_bias") or []),
        "execution_authority": False,
    }


def classify_intermarket_regime(
    correlations: Mapping[str, float],
    *,
    deadzone: float = 0.15,
    minimum_compared: int = 1,
) -> Dict[str, Any]:
    observed = observed_pair_signs(correlations, deadzone=deadzone)
    scored = [score_regime(reg, observed) for reg in CORRELATION_REGIMES]
    scored = [s for s in scored if int(s["compared"]) >= minimum_compared]
    scored.sort(key=lambda row: (float(row["score"]), int(row["compared"])), reverse=True)
    best = scored[0] if scored else None
    regime_id = str(best["regime_id"]) if best else "UNKNOWN"
    active_edges = edges_for_regime(regime_id) if best else []
    return {
        "regime_id": regime_id,
        "confidence": float(best["score"]) if best else 0.0,
        "observed_pair_signs": observed,
        "ranking": scored[:5],
        "active_catalog_edges": len(active_edges),
        "advisory_bias": list(best.get("advisory_bias") or []) if best else [],
        "execution_authority": False,
        "status": "CLASSIFIED" if best else "INSUFFICIENT_PAIRS",
    }


def filter_edges_for_live_regime(
    edges: Iterable[Mapping[str, Any]],
    regime_id: str,
) -> List[Dict[str, Any]]:
    """Keep edges whose active_regimes include ALL or the live regime.

    When multiple edges share a ``contradiction_group``, keep the most
    regime-specific match (exact regime over ALL).  UNKNOWN regime keeps
    transmission context but still resolves contradictions toward ALL-tagged
    priors only when no specific edge exists.
    """
    key = str(regime_id or "").upper()
    selected: List[Dict[str, Any]] = []
    for edge in edges:
        regimes = _edge_regimes(edge)
        if key == "UNKNOWN":
            if "ALL" in regimes or not regimes:
                item = dict(edge)
                item["execution_authority"] = False
                selected.append(item)
            continue
        if "ALL" in regimes or key in regimes:
            item = dict(edge)
            item["execution_authority"] = False
            selected.append(item)
    return _resolve_contradiction_groups(selected, key)


def _contradiction_group(edge: Mapping[str, Any]) -> str:
    if edge.get("contradiction_group"):
        return str(edge.get("contradiction_group") or "").strip()
    notes = edge.get("notes")
    if isinstance(notes, dict):
        return str(notes.get("contradiction_group") or "").strip()
    if isinstance(notes, str) and notes.strip().startswith("{"):
        try:
            payload = json.loads(notes)
            if isinstance(payload, dict):
                return str(payload.get("contradiction_group") or "").strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""
    return ""


def _regime_specificity(edge: Mapping[str, Any], regime_id: str) -> int:
    regimes = _edge_regimes(edge)
    if regime_id in regimes:
        return 2
    if "ALL" in regimes:
        return 1
    return 0


def _resolve_contradiction_groups(
    edges: Sequence[Mapping[str, Any]],
    regime_id: str,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    passthrough: List[Dict[str, Any]] = []
    for edge in edges:
        item = dict(edge)
        item["execution_authority"] = False
        group = _contradiction_group(item)
        if not group:
            passthrough.append(item)
            continue
        grouped.setdefault(group, []).append(item)
    resolved = list(passthrough)
    for group_edges in grouped.values():
        group_edges.sort(
            key=lambda e: (
                _regime_specificity(e, regime_id),
                float(e.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        winner = group_edges[0]
        winner["contradiction_resolved"] = True
        winner["contradiction_suppressed"] = max(0, len(group_edges) - 1)
        resolved.append(winner)
    return resolved


def _edge_regimes(edge: Mapping[str, Any]) -> set[str]:
    if edge.get("active_regimes"):
        return {str(r).upper() for r in edge.get("active_regimes") or []}
    notes = edge.get("notes")
    if isinstance(notes, dict):
        return {str(r).upper() for r in notes.get("active_regimes") or ["ALL"]}
    if isinstance(notes, str) and notes.strip().startswith("{"):
        try:
            payload = json.loads(notes)
            if isinstance(payload, dict) and payload.get("active_regimes"):
                return {str(r).upper() for r in payload.get("active_regimes") or []}
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return {"ALL"}


def resolve_entity_proxies(
    known_symbols: Sequence[str],
    *,
    proxies: Optional[Mapping[str, Sequence[str]]] = None,
) -> Dict[str, str]:
    """Map catalog entity → first matching stored symbol (case-insensitive)."""
    table = proxies or ENTITY_SYMBOL_PROXIES
    known = {str(s).upper(): str(s) for s in known_symbols if str(s or "").strip()}
    resolved: Dict[str, str] = {}
    for entity, aliases in table.items():
        for alias in aliases:
            hit = known.get(str(alias).upper())
            if hit:
                resolved[str(entity).upper()] = hit
                break
    return resolved


def entity_pair_correlations(
    symbol_pair_corrs: Mapping[str, Mapping[str, Any]],
    entity_to_symbol: Mapping[str, str],
) -> Dict[str, float]:
    """Rewrite broker-symbol pair keys into catalog entity pair keys."""
    symbol_to_entity = {str(sym).upper(): str(ent).upper() for ent, sym in entity_to_symbol.items()}
    out: Dict[str, float] = {}
    for key, payload in symbol_pair_corrs.items():
        parts = str(key).upper().split("|")
        if len(parts) != 2:
            continue
        left_ent = symbol_to_entity.get(parts[0])
        right_ent = symbol_to_entity.get(parts[1])
        if not left_ent or not right_ent or left_ent == right_ent:
            continue
        try:
            value = float(payload.get("raw_correlation")) if isinstance(payload, Mapping) else float(payload)
        except (TypeError, ValueError, AttributeError):
            continue
        out[_normalize_pair_key(left_ent, right_ent)] = value
    return out


def build_intermarket_regime_snapshot(
    correlation_risk: Any,
    timeframe: str,
    *,
    approved_edges: Optional[Sequence[Mapping[str, Any]]] = None,
    deadzone: float = 0.15,
    minimum_compared: int = 1,
) -> Dict[str, Any]:
    """Build a fail-closed TOON payload from CorrelationRisk stored returns.

    Never grants execution authority.  Missing pairs → INSUFFICIENT_PAIRS.
    """
    try:
        known = list(correlation_risk.known_symbols(timeframe) or [])
    except Exception as exc:
        return {
            "regime_id": "UNKNOWN",
            "confidence": 0.0,
            "status": "ERROR",
            "error": str(exc)[:300],
            "execution_authority": False,
        }
    entity_to_symbol = resolve_entity_proxies(known)
    if len(entity_to_symbol) < 2:
        return {
            "regime_id": "UNKNOWN",
            "confidence": 0.0,
            "status": "INSUFFICIENT_SYMBOLS",
            "resolved_proxies": entity_to_symbol,
            "known_symbol_count": len(known),
            "execution_authority": False,
        }
    symbols = list(entity_to_symbol.values())
    try:
        symbol_corrs = correlation_risk.pair_correlations(symbols, timeframe)
    except Exception as exc:
        return {
            "regime_id": "UNKNOWN",
            "confidence": 0.0,
            "status": "ERROR",
            "error": str(exc)[:300],
            "resolved_proxies": entity_to_symbol,
            "execution_authority": False,
        }
    entity_corrs = entity_pair_correlations(symbol_corrs, entity_to_symbol)
    # Prefer catalog regime pairs; keep any extra measured entity pairs.
    focused = {
        _normalize_pair_key(a, b): entity_corrs[key]
        for a, b in REGIME_ENTITY_PAIRS
        for key in [_normalize_pair_key(a, b)]
        if key in entity_corrs
    }
    focused.update(entity_corrs)
    classified = classify_intermarket_regime(
        focused, deadzone=deadzone, minimum_compared=minimum_compared,
    )
    regime_id = str(classified.get("regime_id") or "UNKNOWN")
    filtered = filter_edges_for_live_regime(approved_edges or [], regime_id)
    return {
        **classified,
        "timeframe": timeframe,
        "resolved_proxies": entity_to_symbol,
        "pair_correlations": focused,
        "symbol_pair_count": len(symbol_corrs),
        "macro_edges_in_regime": len(filtered),
        "macro_edges_total": len(list(approved_edges or [])),
        "execution_authority": False,
    }
