"""Typed causal-edge → registered setup mapping (no substring heuristics).

Maps use exact relation + entity_type pairs and tokenized entity names
(underscore-separated identity tokens), never raw ``"FOO" in name`` scans as
the primary rule.  Outputs remain advisory: ``execution_authority`` is always
False.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, FrozenSet, Optional, Tuple

try:
    from setup_registry import SETUPS
except ImportError:
    from research.setup_registry import SETUPS


# (relation, target_entity_type) → setup_id
RELATION_TARGET_TYPE_SETUP: Dict[Tuple[str, str], str] = {
    ("SPURS_VOLATILITY", "MARKET_STRUCTURE"): "range_reversal_v1",
    ("SPURS_VOLATILITY", "VOLATILITY"): "range_reversal_v1",
    ("DRIVES_CURRENCY_STRENGTH", "ASSET_REGIME"): "macro_continuation_v1",
    ("DRIVES_CURRENCY_STRENGTH", "CURRENCY"): "macro_continuation_v1",
    ("STRENGTHENS", "ASSET_REGIME"): "macro_continuation_v1",
    ("STRENGTHENS", "CURRENCY"): "macro_continuation_v1",
    ("WEAKENS", "ASSET_REGIME"): "macro_continuation_v1",
    ("WEAKENS", "CURRENCY"): "macro_continuation_v1",
    ("DRIVES_TREND", "MARKET_STRUCTURE"): "trend_pullback_v1",
    ("DRIVES_TREND", "EQUITY"): "trend_pullback_v1",
    ("POLICY_SHOCK", "MARKET_STRUCTURE"): "macro_continuation_v1",
    ("POLICY_SHOCK", "CURRENCY"): "macro_continuation_v1",
    ("LIQUIDITY_SWEEP", "MARKET_STRUCTURE"): "liquidity_sweep_reversal_v1",
    ("RANGE_BREAKOUT", "MARKET_STRUCTURE"): "asian_range_breakout_v1",
    # Economic-dynamics catalog relations
    ("SAFE_HAVEN_BID", "COMMODITY"): "macro_continuation_v1",
    ("SAFE_HAVEN_BID", "CURRENCY"): "macro_continuation_v1",
    ("INVERSE_PRICES", "COMMODITY"): "macro_continuation_v1",
    ("SUPPLY_SHOCK", "COMMODITY"): "macro_continuation_v1",
    ("RISK_OFF_SELL", "EQUITY"): "trend_pullback_v1",
    ("RISK_PROXY", "CURRENCY"): "macro_continuation_v1",
    ("RISK_PROXY", "ASSET_REGIME"): "macro_continuation_v1",
    ("CARRY_FLOW", "CURRENCY"): "macro_continuation_v1",
    ("FUNDING_UNWIND", "CURRENCY"): "macro_continuation_v1",
    ("PRESSURES", "CURRENCY"): "macro_continuation_v1",
    ("PRESSURES", "EQUITY"): "trend_pullback_v1",
    ("WIDENS", "CREDIT"): "range_reversal_v1",
    ("DRIVES_POLICY", "MONETARY_POLICY"): "macro_continuation_v1",
    ("PASSES_THROUGH", "CREDIT"): "macro_continuation_v1",
    ("LIQUIDITY_BETA", "ASSET_REGIME"): "macro_continuation_v1",
}

# target_entity_type defaults when relation is unknown but type is registered
TARGET_TYPE_DEFAULT_SETUP: Dict[str, str] = {
    "ASSET_REGIME": "macro_continuation_v1",
    "MARKET_STRUCTURE": "range_reversal_v1",
    "CURRENCY": "macro_continuation_v1",
    "COMMODITY": "macro_continuation_v1",
    "EQUITY": "trend_pullback_v1",
    "VOLATILITY": "range_reversal_v1",
}

# Transmission / policy nodes are intentionally NOT setup-eligible. They remain
# in MacroGraph for causal context and regime filtering only.
RESEARCH_TRANSMISSION_TYPES: FrozenSet[str] = frozenset({
    "MONETARY_POLICY", "LIQUIDITY", "CREDIT", "MACRO_INDICATOR",
    "YIELD_CURVE", "SENTIMENT", "GEOPOLITICAL", "EVENT_CLASS",
})


def is_research_transmission(target_type: str) -> bool:
    return str(target_type or "").strip().upper() in RESEARCH_TRANSMISSION_TYPES


def _tokens(*parts: str) -> FrozenSet[str]:
    tokens: set[str] = set()
    for part in parts:
        for token in str(part or "").upper().replace("-", "_").split("_"):
            if token:
                tokens.add(token)
    return frozenset(tokens)


def resolve_causal_setup(
    *,
    relation: str,
    source_type: str,
    target_type: str,
    source_name: str = "",
    target_name: str = "",
) -> Dict[str, Any]:
    """Resolve a registered setup_id from typed causal identity fields."""
    relation_key = str(relation or "").strip().upper()
    source_type_key = str(source_type or "").strip().upper()
    target_type_key = str(target_type or "").strip().upper()
    source_tokens = _tokens(source_name, source_type_key)
    target_tokens = _tokens(target_name, target_type_key)

    # 1) Exact relation + target type registry (primary).
    mapped = RELATION_TARGET_TYPE_SETUP.get((relation_key, target_type_key))
    method = "relation_target_type"
    if mapped is None:
        # 2) Structured token rules (identity tokens only, not substrings).
        if "ASIAN" in source_tokens and "BREAKOUT" in target_tokens:
            mapped = "asian_range_breakout_v1"
            method = "token_asian_breakout"
        elif {"LIQUIDITY", "SWEEP"} <= target_tokens or relation_key == "LIQUIDITY_SWEEP":
            mapped = "liquidity_sweep_reversal_v1"
            method = "token_liquidity_sweep"
        elif "TREND" in target_tokens or relation_key == "DRIVES_TREND":
            mapped = "trend_pullback_v1"
            method = "token_trend"
        elif "BREAKOUT" in target_tokens and "VOLATILITY" in target_tokens:
            mapped = "range_reversal_v1"
            method = "token_volatility_breakout"
        else:
            mapped = TARGET_TYPE_DEFAULT_SETUP.get(target_type_key)
            method = "target_type_default" if mapped else "unclassified"

    if mapped is None or mapped not in SETUPS:
        reason = "no typed causal→setup mapping matched a registered setup"
        if is_research_transmission(target_type_key):
            reason = "transmission/research node (not a tradable setup target)"
        return {
            "setup_id": "unclassified_v1",
            "eligible": False,
            "method": method,
            "reason": reason,
            "research_transmission": is_research_transmission(target_type_key),
            "execution_authority": False,
        }

    return {
        "setup_id": mapped,
        "eligible": True,
        "method": method,
        "reason": f"mapped via {method}",
        "spec": asdict(SETUPS[mapped]),
        "research_transmission": False,
        "execution_authority": False,
    }
