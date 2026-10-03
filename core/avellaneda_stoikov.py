"""Avellaneda-Stoikov Market Making & Reservation Pricing Engine.

Implements the classic Avellaneda-Stoikov (2008) optimal market-making model:
    Reservation Price:
        r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
    Optimal Half-Spread:
        delta*(s, q, t) = (1/2) * gamma * sigma^2 * (T - t) + (1/gamma) * ln(1 + gamma / kappa)
    Optimal Quotes:
        p_bid = r(s, q, t) - delta*
        p_ask = r(s, q, t) + delta*

Where:
    - s: Current mid price
    - q: Net inventory / exposure in normalized lot or contract units
    - gamma: Absolute risk-aversion parameter (typically 0.1 to 1.0)
    - sigma: Asset instantaneous return volatility
    - (T - t): Scalp holding horizon in hours
    - kappa: Order book liquidity density / fill intensity parameter
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class AvellanedaStoikovQuotes:
    """Optimal reservation price and quoting bracket."""
    mid_price: float
    reservation_price: float
    optimal_half_spread: float
    optimal_bid: float
    optimal_ask: float
    inventory_skew_discount: float  # r - s (negative when long inventory, positive when short)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mid_price": round(float(self.mid_price), 6),
            "reservation_price": round(float(self.reservation_price), 6),
            "optimal_half_spread": round(float(self.optimal_half_spread), 6),
            "optimal_bid": round(float(self.optimal_bid), 6),
            "optimal_ask": round(float(self.optimal_ask), 6),
            "inventory_skew_discount": round(float(self.inventory_skew_discount), 6),
        }


def calculate_reservation_price(
    mid_price: float,
    inventory_units: float,
    gamma: float = 0.5,
    volatility: float = 0.01,
    horizon_seconds: float = 900.0,
) -> float:
    """Calculates agent reservation (indifference) price r(s, q, t)."""
    s = float(mid_price)
    if not math.isfinite(s) or s <= 0.0:
        return 0.0

    q = float(inventory_units)
    g = max(float(gamma), 1e-4)
    vol = max(float(volatility), 0.0)
    t_rem = max(float(horizon_seconds), 0.0) / 3600.0  # normalize to hours

    # Inventory skew penalty: q * gamma * sigma^2 * (T - t)
    skew_penalty = q * g * (vol ** 2) * t_rem
    r = s - skew_penalty
    return float(r) if math.isfinite(r) else s


def calculate_optimal_spread(
    gamma: float = 0.5,
    volatility: float = 0.01,
    horizon_seconds: float = 900.0,
    kappa: float = 1.5,
) -> float:
    """Calculates optimal half-spread delta* = (1/2)*gamma*sigma^2*(T-t) + (1/gamma)*ln(1 + gamma/kappa)."""
    g = max(float(gamma), 1e-4)
    k = max(float(kappa), 1e-4)
    vol = max(float(volatility), 0.0)
    t_rem = max(float(horizon_seconds), 0.0) / 3600.0

    # Risk-inventory component
    risk_comp = 0.5 * g * (vol ** 2) * t_rem

    # Liquidity density / order book depth component
    try:
        density_comp = (1.0 / g) * math.log(1.0 + (g / k))
    except (ValueError, OverflowError):
        density_comp = 0.0001

    half_spread = risk_comp + density_comp
    return float(max(1e-6, half_spread)) if math.isfinite(half_spread) else 0.0001


class AvellanedaStoikovEngine:
    """Orchestrates reservation pricing and optimal limit pricing for scalp execution."""

    def __init__(
        self,
        gamma: float = 0.5,
        kappa: float = 1.5,
        default_horizon_seconds: float = 900.0,
    ):
        self.gamma = float(gamma)
        self.kappa = float(kappa)
        self.default_horizon_seconds = float(default_horizon_seconds)

    def compute_quotes(
        self,
        mid_price: float,
        inventory_units: float = 0.0,
        volatility: float = 0.01,
        horizon_seconds: Optional[float] = None,
    ) -> AvellanedaStoikovQuotes:
        """Calculates reservation price, optimal half-spread, and optimal bid/ask quotes."""
        h = float(horizon_seconds if horizon_seconds is not None else self.default_horizon_seconds)
        r = calculate_reservation_price(
            mid_price=mid_price,
            inventory_units=inventory_units,
            gamma=self.gamma,
            volatility=volatility,
            horizon_seconds=h,
        )
        half_spread = calculate_optimal_spread(
            gamma=self.gamma,
            volatility=volatility,
            horizon_seconds=h,
            kappa=self.kappa,
        )

        bid = r - half_spread
        ask = r + half_spread
        discount = r - float(mid_price)

        return AvellanedaStoikovQuotes(
            mid_price=float(mid_price),
            reservation_price=float(r),
            optimal_half_spread=float(half_spread),
            optimal_bid=float(bid),
            optimal_ask=float(ask),
            inventory_skew_discount=float(discount),
        )

    def recommend_scalp_limit_price(
        self,
        side: str,
        mid_price: float,
        inventory_units: float = 0.0,
        volatility: float = 0.01,
        horizon_seconds: Optional[float] = None,
    ) -> float:
        """Determines optimal limit price for a scalp entry based on reservation pricing.

        - If BUY: quotes at optimal bid r - delta*, demanding price improvement when inventory is already long.
        - If SELL: quotes at optimal ask r + delta*, demanding price improvement when inventory is already short.
        """
        quotes = self.compute_quotes(
            mid_price=mid_price,
            inventory_units=inventory_units,
            volatility=volatility,
            horizon_seconds=horizon_seconds,
        )
        norm_side = str(side).upper().strip()
        if norm_side == "BUY":
            # For buy, bid is below reservation price
            return min(float(mid_price), float(quotes.optimal_bid + quotes.optimal_half_spread * 0.5))
        elif norm_side == "SELL":
            # For sell, ask is above reservation price
            return max(float(mid_price), float(quotes.optimal_ask - quotes.optimal_half_spread * 0.5))
        return float(mid_price)
