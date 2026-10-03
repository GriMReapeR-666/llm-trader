"""
transaction_cost_engine.py
Production Transaction Cost & Implementation Shortfall Engine
Source: Module 04 - Transaction Cost Engine & Market Impact
"""
from dataclasses import dataclass
from typing import Dict, Literal
import numpy as np


@dataclass
class MarketContext:
    arrival_price: float
    daily_volatility: float
    adv: float
    current_volume: float
    avg_bar_volume: float
    current_atr: float
    baseline_atr: float
    is_high_impact_event: bool = False


@dataclass
class OrderContext:
    order_size: float
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    execution_horizon_bars: int = 1
    asset_class: Literal["EQUITY", "FUTURES", "FOREX", "CRYPTO"] = "EQUITY"


@dataclass
class CostBreakdown:
    arrival_price: float
    effective_execution_price: float
    half_spread_cost: float
    permanent_impact_cost: float
    temporary_impact_cost: float
    explicit_fees: float
    total_dollar_cost: float
    implementation_shortfall_bps: float
    details: Dict[str, float]


class TransactionCostEngine:
    def __init__(
        self,
        base_half_spread_bps: float = 1.5,
        almgren_gamma: float = 0.314,
        almgren_eta: float = 0.142,
        almgren_alpha: float = 0.5,
    ):
        self.base_half_spread_bps = base_half_spread_bps
        self.gamma = almgren_gamma
        self.eta = almgren_eta
        self.alpha = almgren_alpha
        self.vol_sensitivity = 0.75
        self.liquidity_sensitivity = 0.50
        self.event_penalty = 2.0
        self.broker_comm_per_share = 0.0035
        self.exchange_routing_fee_per_share = 0.0015
        self.sec_fee_rate = 0.0000278
        self.finra_taf_per_share = 0.000166

    def compute_dynamic_spread(self, order: OrderContext, market: MarketContext) -> float:
        base_spread_price = market.arrival_price * (self.base_half_spread_bps / 10000.0)
        vol_mult = self.vol_sensitivity * max(0.0, (market.current_atr / market.baseline_atr) - 1.0)
        liq_mult = self.liquidity_sensitivity * max(0.0, (market.avg_bar_volume / max(1.0, market.current_volume)) - 1.0)
        event_mult = self.event_penalty if market.is_high_impact_event else 0.0
        dynamic_spread = base_spread_price * (1.0 + vol_mult + liq_mult + event_mult)
        if order.order_type == "LIMIT":
            return -0.25 * dynamic_spread
        return dynamic_spread

    def compute_market_impact(self, order: OrderContext, market: MarketContext) -> tuple[float, float]:
        p0 = market.arrival_price
        sigma = market.daily_volatility
        q = order.order_size
        v_adv = market.adv
        v_bar = market.avg_bar_volume
        t = max(1, order.execution_horizon_bars)
        v_rate = q / float(t)
        i_perm = self.gamma * sigma * p0 * (q / v_adv)
        i_temp = self.eta * sigma * p0 * np.power(v_rate / max(1.0, v_bar), self.alpha)
        return float(i_perm), float(i_temp)

    def compute_explicit_costs(self, order: OrderContext, exec_notional: float) -> float:
        q = order.order_size
        comm = q * self.broker_comm_per_share
        exchange_fee = q * self.exchange_routing_fee_per_share if order.order_type == "MARKET" else -q * 0.0010
        reg_fees = 0.0
        if order.side == "SELL":
            sec_fee = exec_notional * self.sec_fee_rate
            finra_fee = min(8.30, q * self.finra_taf_per_share)
            reg_fees = sec_fee + finra_fee
        return float(comm + exchange_fee + reg_fees)

    def calculate_implementation_shortfall(self, order: OrderContext, market: MarketContext) -> CostBreakdown:
        direction = 1.0 if order.side == "BUY" else -1.0
        half_spread_per_share = self.compute_dynamic_spread(order, market)
        i_perm, i_temp = self.compute_market_impact(order, market)
        effective_impact_per_share = (0.5 * i_perm) + i_temp
        exec_price = market.arrival_price + direction * (half_spread_per_share + effective_impact_per_share)
        spread_dollar = half_spread_per_share * order.order_size
        perm_impact_dollar = (0.5 * i_perm) * order.order_size
        temp_impact_dollar = i_temp * order.order_size
        exec_notional = exec_price * order.order_size
        explicit_fees = self.compute_explicit_costs(order, exec_notional)
        total_dollar_friction = abs(exec_price - market.arrival_price) * order.order_size + explicit_fees
        benchmark_notional = market.arrival_price * order.order_size
        is_bps = (total_dollar_friction / benchmark_notional) * 10000.0
        return CostBreakdown(
            arrival_price=market.arrival_price,
            effective_execution_price=exec_price,
            half_spread_cost=spread_dollar,
            permanent_impact_cost=perm_impact_dollar,
            temporary_impact_cost=temp_impact_dollar,
            explicit_fees=explicit_fees,
            total_dollar_cost=total_dollar_friction,
            implementation_shortfall_bps=is_bps,
            details={
                "dynamic_half_spread_per_share": half_spread_per_share,
                "perm_impact_per_share": i_perm,
                "temp_impact_per_share": i_temp,
                "exec_notional": exec_notional,
            },
        )
