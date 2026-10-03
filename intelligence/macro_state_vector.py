"""Structured macro state vector replacing headline fundamental_score.

Combines entity-bound observations, intermarket regime, fusion, and propagation
into one advisory research object. Never grants execution authority.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _latest_by_entity(items: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(items, list):
        return out
    for row in items:
        if not isinstance(row, dict):
            continue
        entity = str(row.get("entity") or "").strip().upper()
        if not entity:
            continue
        out[entity] = {
            "entity": entity,
            "value": row.get("value"),
            "previous": row.get("previous"),
            "surprise": row.get("surprise"),
            "provider": row.get("provider"),
            "series": row.get("series"),
            "captured_at": row.get("captured_at"),
        }
    return out


def _surprise_bias(surprise: Any) -> str:
    try:
        value = float(surprise)
    except (TypeError, ValueError, OverflowError):
        return "NEUTRAL"
    if not math.isfinite(value) or abs(value) < 1e-12:
        return "NEUTRAL"
    return "HAWKISH" if value > 0 else "DOVISH"


def build_macro_state_vector(
    *,
    macro_observations: Any = None,
    intermarket_regime: Optional[Dict[str, Any]] = None,
    opportunity_fusion: Optional[Dict[str, Any]] = None,
    causal_propagation: Optional[Dict[str, Any]] = None,
    legacy_news_score: int = 0,
) -> Dict[str, Any]:
    """Build advisory MacroStateVector from PIT observations and regime context."""
    obs_items = macro_observations
    if isinstance(macro_observations, dict):
        obs_items = macro_observations.get("items") or macro_observations.get("recent") or []
    entities = _latest_by_entity(obs_items)

    policy = entities.get("POLICY_RATE") or entities.get("POLICY RATE") or {}
    inflation = entities.get("INFLATION") or {}
    growth = entities.get("GROWTH") or {}
    employment = entities.get("EMPLOYMENT") or {}
    vix = entities.get("VIX") or {}
    yield_slope = entities.get("YIELD_CURVE_SLOPE") or {}
    real_yield = entities.get("REAL_YIELD") or {}
    credit = entities.get("CREDIT_SPREAD") or {}
    money_supply = entities.get("MONEY_SUPPLY") or {}
    nom_bond = entities.get("NOMINAL_BOND_YIELD") or {}
    dxy = entities.get("DXY") or {}
    gold = entities.get("GOLD") or {}
    oil = entities.get("WTI_OIL") or {}
    cli = entities.get("COMPOSITE_LEADING_INDICATOR") or {}
    housing = entities.get("HOUSING_ACTIVITY") or {}
    liquidity = entities.get("US_NET_LIQUIDITY") or {}
    shipping = entities.get("SHIPPING_FLOW") or {}

    regime = intermarket_regime if isinstance(intermarket_regime, dict) else {}
    fusion = opportunity_fusion if isinstance(opportunity_fusion, dict) else {}
    prop = causal_propagation if isinstance(causal_propagation, dict) else {}

    policy_surprise = policy.get("surprise")
    inflation_surprise = inflation.get("surprise")
    growth_surprise = growth.get("surprise")

    components = {
        "policy_rate": {
            "value": policy.get("value"),
            "surprise": policy_surprise,
            "bias": _surprise_bias(policy_surprise),
        },
        "inflation": {
            "value": inflation.get("value"),
            "surprise": inflation_surprise,
            "bias": _surprise_bias(inflation_surprise),
        },
        "growth": {
            "value": growth.get("value"),
            "surprise": growth_surprise,
            "bias": _surprise_bias(growth_surprise),
        },
        "employment": {
            "value": employment.get("value"),
            "surprise": employment.get("surprise"),
            "bias": _surprise_bias(employment.get("surprise")),
        },
        "volatility": {
            "value": vix.get("value"),
            "surprise": vix.get("surprise"),
        },
        "yield_curve_slope": {
            "value": yield_slope.get("value"),
            "surprise": yield_slope.get("surprise"),
        },
        "real_yield": {
            "value": real_yield.get("value"),
            "surprise": real_yield.get("surprise"),
        },
        "credit_spread": {
            "value": credit.get("value"),
            "surprise": credit.get("surprise"),
        },
        "money_supply": {
            "value": money_supply.get("value"),
            "surprise": money_supply.get("surprise"),
        },
        "nominal_bond_yield": {
            "value": nom_bond.get("value"),
            "surprise": nom_bond.get("surprise"),
        },
        "dxy": {
            "value": dxy.get("value"),
            "surprise": dxy.get("surprise"),
        },
        "gold": {
            "value": gold.get("value"),
            "surprise": gold.get("surprise"),
        },
        "wti_oil": {
            "value": oil.get("value"),
            "surprise": oil.get("surprise"),
        },
        "leading_indicator": {
            "value": cli.get("value"),
            "surprise": cli.get("surprise"),
        },
        "housing": {
            "value": housing.get("value"),
            "surprise": housing.get("surprise"),
        },
        "net_liquidity": {
            "value": liquidity.get("value"),
            "surprise": liquidity.get("surprise"),
        },
        "shipping_flow": {
            "value": shipping.get("value"),
            "surprise": shipping.get("surprise"),
        },
    }

    hawkish = sum(1 for c in components.values() if c.get("bias") == "HAWKISH")
    dovish = sum(1 for c in components.values() if c.get("bias") == "DOVISH")
    macro_bias = "HAWKISH" if hawkish > dovish else "DOVISH" if dovish > hawkish else "NEUTRAL"

    # Advisory composite in [-1, 1] — research only, not order permission.
    composite = 0.0
    if macro_bias == "HAWKISH":
        composite += 0.35
    elif macro_bias == "DOVISH":
        composite -= 0.35
    try:
        composite += max(-0.2, min(0.2, float(legacy_news_score) * 0.05))
    except (TypeError, ValueError, OverflowError):
        pass
    regime_conf = 0.0
    try:
        regime_conf = float(regime.get("confidence") or 0.0)
    except (TypeError, ValueError, OverflowError):
        regime_conf = 0.0
    if str(regime.get("advisory_bias") or "").upper() == "RISK_OFF":
        composite -= 0.15 * min(1.0, regime_conf)
    elif str(regime.get("advisory_bias") or "").upper() == "RISK_ON":
        composite += 0.15 * min(1.0, regime_conf)
    fusion_status = str(fusion.get("status") or "").upper()
    if fusion_status == "PASS":
        composite += 0.1
    elif fusion_status == "BLOCK":
        composite -= 0.1
    if str(prop.get("status") or "").upper() == "OK" and prop.get("path_id"):
        try:
            composite += 0.05 * (1.0 if str(prop.get("bias")) == "BUY" else -1.0 if str(prop.get("bias")) == "SELL" else 0.0)
        except (TypeError, ValueError, OverflowError):
            pass
    composite = max(-1.0, min(1.0, composite))

    return {
        "schema_version": "macro_state_vector_v2",
        "macro_bias": macro_bias,
        "composite_score": round(composite, 4),
        "entity_count": len(entities),
        "components": components,
        "regime": {
            "regime_id": regime.get("regime_id"),
            "confidence": regime.get("confidence"),
            "advisory_bias": regime.get("advisory_bias"),
            "status": regime.get("status"),
        },
        "fusion_status": fusion.get("status"),
        "causal_path_id": prop.get("path_id"),
        "causal_path_bias": prop.get("bias"),
        "legacy_news_score": int(legacy_news_score or 0),
        "headline": (
            f"macro_bias={macro_bias} regime={regime.get('regime_id', 'UNKNOWN')} "
            f"fusion={fusion.get('status', 'N/A')} "
            f"yield_slope={components.get('yield_curve_slope', {}).get('value', 'N/A')} "
            f"credit={components.get('credit_spread', {}).get('value', 'N/A')} "
            f"liquidity={components.get('money_supply', {}).get('value', 'N/A')}"
        ),
        "execution_authority": False,
        "limitation": (
            "MacroStateVector is advisory research context; "
            "DeterministicValidator and RiskController remain memory-blind for orders."
        ),
    }
