# core/fx_portfolio_netting.py
"""Portfolio-Level FX Netting & Cross-Pair Dollar Beta Engine.

Decomposes FX and commodity positions into underlying base/quote currency legs,
aggregates net directional currency exposure, and computes cross-pair Dollar Beta
(\u03b2_USD) sensitivity to eliminate systemic USD avalanche risks while crediting
hedged, risk-reducing pairs.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


COMMODITY_MAPPINGS: Dict[str, Tuple[str, str]] = {
    "XAUUSD": ("XAU", "USD"),
    "GOLD": ("XAU", "USD"),
    "XAGUSD": ("XAG", "USD"),
    "SILVER": ("XAG", "USD"),
    "USOIL": ("OIL", "USD"),
    "UKOIL": ("OIL", "USD"),
    "BTCUSD": ("BTC", "USD"),
    "ETHUSD": ("ETH", "USD"),
}

# Empirical benchmark dollar beta (sensitivity to DXY / USD strength)
# Sign convention for Dollar Beta magnitude: positive value indicates pair's base or quote sensitivity
# Quote-USD pairs (EURUSD, GBPUSD, AUDUSD, NZDUSD, XAUUSD): USD is quote -> Long position is Short USD
# Base-USD pairs (USDJPY, USDCHF, USDCAD): USD is base -> Long position is Long USD
DEFAULT_DOLLAR_BETAS: Dict[str, float] = {
    "EURUSD": 1.00,
    "GBPUSD": 0.85,
    "AUDUSD": 0.75,
    "NZDUSD": 0.70,
    "USDCAD": 0.65,
    "USDCHF": 0.80,
    "USDJPY": 0.65,
    "XAUUSD": 0.80,
    "GOLD": 0.80,
    "XAGUSD": 0.85,
    "SILVER": 0.85,
    # Major Crosses (negligible direct USD delta)
    "EURGBP": 0.00,
    "EURJPY": 0.00,
    "GBPJPY": 0.00,
    "AUDJPY": 0.00,
    "NZDJPY": 0.00,
    "CADJPY": 0.00,
    "CHFJPY": 0.00,
    "EURAUD": 0.00,
    "EURCAD": 0.00,
    "EURCHF": 0.00,
    "GBPAUD": 0.00,
    "GBPCAD": 0.00,
    "GBPCHF": 0.00,
    "AUDNZD": 0.00,
    "AUDCAD": 0.00,
}


def normalize_symbol(symbol: str) -> str:
    """Normalize broker symbol strings by stripping suffixes (e.g. EURUSD.pro -> EURUSD)."""
    if not symbol:
        return ""
    clean = symbol.strip().upper()
    # Check commodity map directly first
    if clean in COMMODITY_MAPPINGS:
        return clean
    # Remove common delimiters and suffixes
    clean = re.sub(r"[._-].*$", "", clean)
    # If 7 chars ending in 'm' or 'c' (micro/cent accounts), strip trailing char
    if len(clean) == 7 and clean[6] in {"M", "C", "I"}:
        clean = clean[:6]
    return clean


def parse_instrument_legs(symbol: str) -> Tuple[str, str]:
    """Extract Base and Quote currency codes from an instrument symbol.
    
    Returns (base, quote). If symbol cannot be cleanly split, returns (symbol, "").
    """
    s = normalize_symbol(symbol)
    if s in COMMODITY_MAPPINGS:
        return COMMODITY_MAPPINGS[s]
    if len(s) == 6 and s.isalpha():
        return s[:3], s[3:]
    return s, ""


def decompose_currency_exposures(symbol: str, side: str, risk_amount: float) -> Dict[str, float]:
    """Decompose a trade into signed currency risk exposures.
    
    Buy BASE/QUOTE:
        +risk on BASE
        -risk on QUOTE
    Sell BASE/QUOTE:
        -risk on BASE
        +risk on QUOTE
    """
    try:
        risk = float(risk_amount)
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(risk) or risk <= 0:
        return {}
    
    direction = str(side).strip().upper()
    if direction not in {"BUY", "SELL", "LONG", "SHORT"}:
        return {}
    is_buy = direction in {"BUY", "LONG"}

    base, quote = parse_instrument_legs(symbol)
    if not quote:
        # Single leg / non-FX instrument
        return {base: risk if is_buy else -risk}

    base_sign = 1.0 if is_buy else -1.0
    quote_sign = -1.0 if is_buy else 1.0

    return {
        base: base_sign * risk,
        quote: quote_sign * risk,
    }


def calculate_portfolio_currency_exposures(
    positions: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Aggregate open positions into net and gross currency exposures.
    
    Returns:
        net_by_currency: Dict[currency -> signed net risk amount]
        gross_risk: Total absolute risk across all positions
        total_net_currency_risk: sum(|net_by_currency|) / 2 for FX pairs
        netting_benefit_ratio: 1.0 - (total_net_currency_risk / gross_risk) if gross_risk > 0 else 0.0
    """
    net_ccy: Dict[str, float] = {}
    gross_risk = 0.0

    for pos in positions:
        sym = str(pos.get("symbol", ""))
        side = str(pos.get("side", ""))
        try:
            r = float(pos.get("risk_amount", pos.get("risk", 0.0)))
        except (TypeError, ValueError):
            r = 0.0
        if not math.isfinite(r) or r <= 0:
            continue
        
        gross_risk += r
        legs = decompose_currency_exposures(sym, side, r)
        for ccy, exp in legs.items():
            net_ccy[ccy] = net_ccy.get(ccy, 0.0) + exp

    # Round minimal floating residuals
    net_by_currency = {k: round(v, 4) for k, v in net_ccy.items() if abs(v) > 1e-6}
    sum_abs_net = sum(abs(v) for v in net_by_currency.values())
    total_net_currency_risk = round(sum_abs_net / 2.0, 4) if sum_abs_net > 0 else 0.0

    netting_benefit_ratio = 0.0
    if gross_risk > 1e-6:
        # If positions perfectly offset, sum_abs_net is 0 -> ratio = 1.0 (100% hedged)
        # If no offsets at all, sum_abs_net / 2 == gross_risk -> ratio = 0.0
        netting_benefit_ratio = max(0.0, min(1.0, 1.0 - (total_net_currency_risk / gross_risk)))

    return {
        "net_by_currency": net_by_currency,
        "gross_risk": round(gross_risk, 4),
        "total_net_currency_risk": total_net_currency_risk,
        "netting_benefit_ratio": round(netting_benefit_ratio, 4),
    }


def calculate_dollar_beta_exposure(
    positions: Sequence[Mapping[str, Any]],
    custom_betas: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    """Calculate directional Dollar Beta (\u03b2_USD) risk across positions.
    
    A positive Dollar Beta position profits from USD strengthening (Long USD).
    A negative Dollar Beta position profits from USD weakening (Short USD).
    
    Formula:
        For Quote-USD (e.g. EURUSD): Long EURUSD is Short USD -> sign = -1
        For Base-USD (e.g. USDJPY): Long USDJPY is Long USD -> sign = +1
        Net Dollar Beta Risk = \u2211 (risk_i \u00d7 usd_direction_i \u00d7 \u03b2_i)
    """
    betas = {**DEFAULT_DOLLAR_BETAS, **(custom_betas or {})}
    net_dollar_beta_risk = 0.0
    gross_dollar_beta_risk = 0.0
    positions_breakdown: List[Dict[str, Any]] = []

    for pos in positions:
        sym = str(pos.get("symbol", ""))
        side = str(pos.get("side", ""))
        try:
            r = float(pos.get("risk_amount", pos.get("risk", 0.0)))
        except (TypeError, ValueError):
            r = 0.0
        if not math.isfinite(r) or r <= 0:
            continue

        clean_sym = normalize_symbol(sym)
        base, quote = parse_instrument_legs(clean_sym)
        beta_mag = betas.get(clean_sym, 0.0)

        # Determine USD direction (+1 = Long USD, -1 = Short USD, 0 = Neutral)
        is_buy = str(side).upper() in {"BUY", "LONG"}
        usd_direction = 0.0
        if base == "USD":
            usd_direction = 1.0 if is_buy else -1.0
        elif quote == "USD":
            usd_direction = -1.0 if is_buy else 1.0

        pos_beta_risk = r * usd_direction * beta_mag
        net_dollar_beta_risk += pos_beta_risk
        gross_dollar_beta_risk += r * abs(beta_mag)

        positions_breakdown.append({
            "symbol": sym,
            "side": side,
            "risk": r,
            "base": base,
            "quote": quote,
            "dollar_beta": beta_mag,
            "usd_direction": usd_direction,
            "dollar_beta_risk": round(pos_beta_risk, 4),
        })

    return {
        "net_dollar_beta_risk": round(net_dollar_beta_risk, 4),
        "gross_dollar_beta_risk": round(gross_dollar_beta_risk, 4),
        "abs_net_dollar_beta_risk": round(abs(net_dollar_beta_risk), 4),
        "positions_breakdown": positions_breakdown,
    }


def evaluate_fx_netting_permit(
    open_positions: Sequence[Mapping[str, Any]],
    proposed_symbol: str,
    proposed_side: str,
    proposed_risk: float,
    equity: float,
    max_net_usd_beta_fraction: float = 0.025,
    max_single_currency_fraction: float = 0.03,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Evaluate whether a proposed trade is safe under net currency & dollar-beta caps.
    
    Key Features:
    1. Hedging Recognition: If a trade reduces an existing one-sided currency concentration,
       it is flagged as a risk-reducing hedge.
    2. Dollar Beta Avalanche Gate: Blocks trades that push the net USD directional beta
       beyond `max_net_usd_beta_fraction` of equity.
    3. Single Currency Cap: Blocks trades that push net exposure to any single currency
       beyond `max_single_currency_fraction` of equity.
    """
    try:
        eq = float(equity)
        p_risk = float(proposed_risk)
    except (TypeError, ValueError):
        return False, "invalid non-numeric equity or risk", {}
    if not (math.isfinite(eq) and math.isfinite(p_risk)) or eq <= 0 or p_risk <= 0:
        return False, "non-finite or non-positive equity/risk", {}

    # Current baseline
    current_exposures = calculate_portfolio_currency_exposures(open_positions)
    current_beta = calculate_dollar_beta_exposure(open_positions)

    # Proposed position combined
    proposed_pos = {"symbol": proposed_symbol, "side": proposed_side, "risk_amount": p_risk}
    candidate_positions = list(open_positions) + [proposed_pos]

    candidate_exposures = calculate_portfolio_currency_exposures(candidate_positions)
    candidate_beta = calculate_dollar_beta_exposure(candidate_positions)

    # Check 1: Dollar Beta Exposure
    max_beta_risk = eq * float(max_net_usd_beta_fraction)
    cand_net_beta = candidate_beta["abs_net_dollar_beta_risk"]
    curr_net_beta = current_beta["abs_net_dollar_beta_risk"]

    # Check 2: Single Currency Exposure
    max_ccy_risk = eq * float(max_single_currency_fraction)
    candidate_net_ccy = candidate_exposures["net_by_currency"]

    # Determine if trade is risk-reducing hedge
    is_hedging = (cand_net_beta < curr_net_beta - 1e-4) or (
        candidate_exposures["total_net_currency_risk"] < current_exposures["total_net_currency_risk"] - 1e-4
    )

    details = {
        "current_net_dollar_beta": current_beta["net_dollar_beta_risk"],
        "candidate_net_dollar_beta": candidate_beta["net_dollar_beta_risk"],
        "max_net_dollar_beta": round(max_beta_risk, 4),
        "is_hedging": is_hedging,
        "candidate_net_by_currency": candidate_net_ccy,
        "max_single_currency_risk": round(max_ccy_risk, 4),
        "netting_benefit_ratio": candidate_exposures["netting_benefit_ratio"],
    }

    # If it is a risk-reducing hedge on dollar beta, allow it even if single-currency is near threshold
    if is_hedging and cand_net_beta <= max_beta_risk:
        # Check that it doesn't blow out an unrelated cross currency
        violating_ccy = [c for c, exp in candidate_net_ccy.items() if abs(exp) > max_ccy_risk * 1.25]
        if not violating_ccy:
            return True, "approved (hedging risk reduction)", details

    # Dollar Beta Gate
    if cand_net_beta > max_beta_risk:
        return False, f"net dollar beta limit exceeded: {cand_net_beta:.2f} > {max_beta_risk:.2f}", details

    # Single Currency Gate
    for ccy, exp in candidate_net_ccy.items():
        if abs(exp) > max_ccy_risk:
            return False, f"net currency risk limit exceeded for {ccy}: {abs(exp):.2f} > {max_ccy_risk:.2f}", details

    return True, "approved", details
