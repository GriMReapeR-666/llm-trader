"""Module 04: Microstructure, Almgren-Chriss Market Impact & Transaction Cost Engine.

Implements institutional market friction models:
1. Volume-Weighted Micro-Price:
       P_micro = (Q_bid * P_ask + Q_ask * P_bid) / (Q_bid + Q_ask)
2. Square-Root Law of Market Impact (Barra / Bouchaud):
       I_total = Y * sigma_daily * sqrt(Q / V_ADV)
3. Almgren-Chriss Permanent and Temporary Price Impact:
       I_perm = gamma * sigma * P_0 * (Q / V_ADV)
       I_temp = eta * sigma * P_0 * (v / V_bar)^alpha
4. Endogenous Dynamic Spread Simulation:
       S_t = S_base * [1 + alpha*(sigma / bar_sigma) + beta*(bar_V / V) + gamma*I_event] * M_session
5. Explicit Fee Schedules (Broker commissions, SEC Section 31, FINRA TAF)
6. Implementation Shortfall (Perold 1988)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


def calculate_volume_weighted_micro_price(
    best_bid: float,
    best_ask: float,
    bid_depth: float,
    ask_depth: float,
) -> float:
    """Calculates Volume-Weighted Micro-Price:
    P_micro = (Q_bid * P_ask + Q_ask * P_bid) / (Q_bid + Q_ask)
    """
    bb = float(best_bid)
    ba = float(best_ask)
    qb = float(bid_depth)
    qa = float(ask_depth)

    tot_depth = qb + qa
    if tot_depth <= 0:
        return (bb + ba) / 2.0
    return float((qb * ba + qa * bb) / tot_depth)


def calculate_endogenous_dynamic_spread(
    base_spread: float,
    current_volatility: float,
    baseline_volatility: float,
    current_volume: float,
    baseline_volume: float,
    is_high_impact_event: bool = False,
    session_multiplier: float = 1.0,
    vol_sensitivity_alpha: float = 0.75,
    liquidity_sensitivity_beta: float = 0.50,
    event_multiplier_gamma: float = 2.0,
) -> float:
    """Calculates endogenous dynamic bid-ask spread:
    S_t = S_base * [1 + alpha * max(0, sigma_t/bar_sigma - 1) + beta * max(0, bar_V/V_t - 1) + gamma * I_event] * M_session
    """
    s_base = float(base_spread)
    c_vol = float(current_volatility)
    b_vol = max(1e-6, float(baseline_volatility))
    c_v = max(1.0, float(current_volume))
    b_v = max(1.0, float(baseline_volume))
    m_sess = float(session_multiplier)

    vol_mult = vol_sensitivity_alpha * max(0.0, (c_vol / b_vol) - 1.0)
    liq_mult = liquidity_sensitivity_beta * max(0.0, (b_v / c_v) - 1.0)
    event_mult = event_multiplier_gamma if is_high_impact_event else 0.0

    dynamic_spread = s_base * (1.0 + vol_mult + liq_mult + event_mult) * m_sess
    return float(max(s_base * 0.50, dynamic_spread))


def calculate_bouchaud_square_root_market_impact(
    daily_volatility: float,
    order_size_shares: float,
    average_daily_volume_shares: float,
    market_structure_constant_y: float = 0.50,
) -> float:
    """Square-Root Law of Total Market Impact (Bouchaud / Barra):
    I_total = Y * sigma_daily * sqrt(Q / V_ADV)
    """
    sigma = float(daily_volatility)
    q = max(0.0, float(order_size_shares))
    adv = max(1000.0, float(average_daily_volume_shares))
    y = float(market_structure_constant_y)

    return float(y * sigma * math.sqrt(q / adv))


def calculate_almgren_chriss_price_impact(
    daily_volatility: float,
    arrival_price: float,
    order_size_shares: float,
    average_daily_volume_shares: float,
    bar_volume_shares: float = 1000.0,
    execution_horizon_bars: int = 1,
    permanent_gamma: float = 0.314,
    temporary_eta: float = 0.142,
    temporary_alpha: float = 0.50,
) -> Tuple[float, float]:
    """Calculates Almgren-Chriss Permanent and Temporary Price Impact per share:
    Permanent Impact: I_perm = gamma * sigma * P_0 * (Q / V_ADV)
    Temporary Impact: I_temp = eta * sigma * P_0 * (v / V_bar)^alpha  where v = Q / T
    """
    sigma = float(daily_volatility)
    p0 = float(arrival_price)
    q = max(0.0, float(order_size_shares))
    v_adv = max(1000.0, float(average_daily_volume_shares))
    v_bar = max(1.0, float(bar_volume_shares))
    t = max(1, int(execution_horizon_bars))

    v_rate = q / float(t)
    i_perm = permanent_gamma * sigma * p0 * (q / v_adv)
    i_temp = temporary_eta * sigma * p0 * np.power(v_rate / v_bar, temporary_alpha)

    return float(i_perm), float(i_temp)


def calculate_explicit_regulatory_and_broker_fees(
    order_size_shares: float,
    execution_notional: float,
    side: str,
    asset_class: str = "EQUITY",
    commission_per_share: float = 0.0035,
    sec_fee_rate: float = 0.0000278,
    finra_taf_rate: float = 0.000166,
) -> float:
    """Calculates explicit broker commissions, exchange routing, and regulatory fees."""
    q = float(order_size_shares)
    notional = float(execution_notional)
    side_norm = str(side).upper()
    asset = str(asset_class).upper()

    if asset == "EQUITY":
        broker_comm = q * commission_per_share
        sec_fee = (notional * sec_fee_rate) if side_norm in ("SELL", "SHORT") else 0.0
        finra_fee = (q * finra_taf_rate) if side_norm in ("SELL", "SHORT") else 0.0
        return float(broker_comm + sec_fee + finra_fee)
    elif asset == "FUTURES":
        # $1.50 per contract all-in
        return float(q * 1.50)
    elif asset == "CRYPTO":
        # 4 bps taker fee
        return float(notional * 0.0004)
    else:  # FOREX
        # $3.00 per standard lot per side
        return float(q * 3.00)


def calculate_implementation_shortfall_breakdown(
    decision_price: float,
    execution_price: float,
    order_size_shares: float,
    side: str,
    explicit_fees: float = 0.0,
) -> Dict[str, float]:
    """Calculates realized Implementation Shortfall (Perold 1988):
    IS_dollar = side * (P_exec - P_decision) * Q + Explicit Fees
    IS_bps = (IS_dollar / Notional) * 10,000
    """
    p0 = float(decision_price)
    pexec = float(execution_price)
    q = float(order_size_shares)
    fees = float(explicit_fees)
    side_norm = str(side).upper()

    direction = 1.0 if side_norm in ("BUY", "LONG") else -1.0
    price_diff = pexec - p0
    slippage_dollar = direction * price_diff * q
    total_shortfall_dollar = slippage_dollar + fees
    notional = p0 * q
    shortfall_bps = (total_shortfall_dollar / max(1.0, notional)) * 10000.0

    return {
        "slippage_dollar": round(float(slippage_dollar), 2),
        "fees_dollar": round(float(fees), 2),
        "total_shortfall_dollar": round(float(total_shortfall_dollar), 2),
        "shortfall_basis_points": round(float(shortfall_bps), 2),
    }


# ---------------------------------------------------------------------------
# Dataclasses & TCA Engine Class
# ---------------------------------------------------------------------------

@dataclass
class OrderContext:
    symbol: str = "AAPL"
    side: str = "BUY"  # "BUY" or "SELL"
    order_type: str = "MARKET"  # "MARKET" or "LIMIT"
    shares: float = 100.0  # Quantity Q
    decision_price: float = 100.0  # P_decision
    urgency: float = 0.50  # U in [0, 1]
    execution_horizon_bars: int = 1
    asset_class: str = "EQUITY"


@dataclass
class MarketContext:
    best_bid: float = 99.98
    best_ask: float = 100.02
    bid_depth: float = 500.0
    ask_depth: float = 500.0
    daily_volume: float = 1000000.0  # V_ADV
    daily_volatility: float = 0.02  # sigma_daily
    is_high_impact_news: bool = False
    session_multiplier: float = 1.0
    current_volume: float = 1000.0
    avg_bar_volume: float = 1000.0
    current_atr: float = 1.0
    baseline_atr: float = 1.0


@dataclass
class CostBreakdown:
    micro_price: float
    quoted_spread: float
    half_spread_cost: float
    permanent_impact: float
    temporary_impact: float
    total_market_impact: float
    broker_commission: float
    exchange_and_sec_fees: float
    expected_execution_price: float
    expected_slippage_pips: float
    total_cost_dollar: float
    total_cost_basis_points: float
    total_cost_r_multiple: float  # Expressed in 1R units


class TransactionCostEngine:
    """Institutional Transaction Cost Analysis (TCA) and Market Impact Engine."""

    def __init__(
        self,
        commission_per_share: float = 0.0035,
        sec_fee_rate: float = 0.0000278,
        finra_taf_rate: float = 0.000166,
        gamma_perm_coeff: float = 0.314,
        eta_temp_coeff: float = 0.142,
        impact_exponent_alpha: float = 0.50,
        y_square_root_coeff: float = 0.50,
    ):
        self.commission_per_share = commission_per_share
        self.sec_fee_rate = sec_fee_rate
        self.finra_taf_rate = finra_taf_rate
        self.gamma_perm_coeff = gamma_perm_coeff
        self.eta_temp_coeff = eta_temp_coeff
        self.impact_exponent_alpha = impact_exponent_alpha
        self.y_square_root_coeff = y_square_root_coeff

    def calculate_micro_price(self, best_bid: float, best_ask: float, bid_depth: float, ask_depth: float) -> float:
        return calculate_volume_weighted_micro_price(best_bid, best_ask, bid_depth, ask_depth)

    def calculate_dynamic_spread(
        self,
        base_spread: float,
        current_volatility: float,
        mean_volatility: float,
        current_volume: float,
        mean_volume: float,
        is_news_event: bool = False,
        session_multiplier: float = 1.0,
    ) -> float:
        return calculate_endogenous_dynamic_spread(
            base_spread=base_spread,
            current_volatility=current_volatility,
            baseline_volatility=mean_volatility,
            current_volume=current_volume,
            baseline_volume=mean_volume,
            is_high_impact_event=is_news_event,
            session_multiplier=session_multiplier,
        )

    def calculate_square_root_impact(self, shares: float, daily_volume: float, daily_volatility: float) -> float:
        return calculate_bouchaud_square_root_market_impact(
            daily_volatility=daily_volatility,
            order_size_shares=shares,
            average_daily_volume_shares=daily_volume,
            market_structure_constant_y=self.y_square_root_coeff,
        )

    def calculate_almgren_chriss_impact(
        self,
        shares: float,
        daily_volume: float,
        daily_volatility: float,
        execution_window_fraction: float = 0.10,
    ) -> Tuple[float, float]:
        arrival_price = 100.0  # Normalized unit price if not supplied
        return calculate_almgren_chriss_price_impact(
            daily_volatility=daily_volatility,
            arrival_price=arrival_price,
            order_size_shares=shares,
            average_daily_volume_shares=daily_volume,
            bar_volume_shares=daily_volume * max(0.01, execution_window_fraction),
            permanent_gamma=self.gamma_perm_coeff,
            temporary_eta=self.eta_temp_coeff,
            temporary_alpha=self.impact_exponent_alpha,
        )

    def estimate_costs(
        self,
        order: OrderContext,
        market: MarketContext,
        one_r_dollar: float = 100.0,
    ) -> CostBreakdown:
        """Compute full institutional transaction cost breakdown for an order."""
        micro_price = calculate_volume_weighted_micro_price(
            market.best_bid, market.best_ask, market.bid_depth, market.ask_depth
        )
        quoted_spread = max(0.00001, market.best_ask - market.best_bid)

        # Half-spread friction
        if order.order_type.upper() == "LIMIT":
            half_spread_cost = 0.0  # Maker captures or avoids spread
        else:
            half_spread_cost = quoted_spread / 2.0

        # Almgren-Chriss impact
        i_perm, i_temp = calculate_almgren_chriss_price_impact(
            daily_volatility=market.daily_volatility,
            arrival_price=order.decision_price,
            order_size_shares=order.shares,
            average_daily_volume_shares=market.daily_volume,
            bar_volume_shares=market.avg_bar_volume,
            execution_horizon_bars=order.execution_horizon_bars,
            permanent_gamma=self.gamma_perm_coeff,
            temporary_eta=self.eta_temp_coeff,
            temporary_alpha=self.impact_exponent_alpha,
        )
        total_market_impact = i_perm + i_temp

        # Regulatory and broker commissions
        notional = order.shares * order.decision_price
        commission = order.shares * self.commission_per_share
        exchange_sec_fees = 0.0
        if order.side.upper() in ("SELL", "SHORT"):
            exchange_sec_fees = (notional * self.sec_fee_rate) + (order.shares * self.finra_taf_rate)

        # Directional impact on expected price
        direction = 1.0 if order.side.upper() in ("BUY", "LONG") else -1.0
        expected_exec_price = order.decision_price + direction * (half_spread_cost + total_market_impact)
        slippage_pips = abs(expected_exec_price - order.decision_price)

        total_cost_dollar = (half_spread_cost + total_market_impact) * order.shares + commission + exchange_sec_fees
        cost_bps = (total_cost_dollar / max(1.0, notional)) * 10000.0
        cost_r = total_cost_dollar / max(1.0, float(one_r_dollar))

        return CostBreakdown(
            micro_price=round(micro_price, 5),
            quoted_spread=round(quoted_spread, 5),
            half_spread_cost=round(half_spread_cost, 5),
            permanent_impact=round(i_perm, 5),
            temporary_impact=round(i_temp, 5),
            total_market_impact=round(total_market_impact, 5),
            broker_commission=round(commission, 2),
            exchange_and_sec_fees=round(exchange_sec_fees, 4),
            expected_execution_price=round(expected_exec_price, 5),
            expected_slippage_pips=round(slippage_pips, 5),
            total_cost_dollar=round(total_cost_dollar, 2),
            total_cost_basis_points=round(cost_bps, 2),
            total_cost_r_multiple=round(cost_r, 4),
        )

    def calculate_implementation_shortfall(
        self,
        decision_price: float,
        execution_price: float,
        shares: float,
        side: str,
        fees: float = 0.0,
    ) -> Dict[str, float]:
        return calculate_implementation_shortfall_breakdown(
            decision_price=decision_price,
            execution_price=execution_price,
            order_size_shares=shares,
            side=side,
            explicit_fees=fees,
        )
