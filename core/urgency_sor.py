"""Module 03: Urgency-Aware Smart Order Router, Elastic Weight Consolidation & Synthetic Stealth Execution.

Implements institutional execution upgrades:
1. Alpha Decay Urgency Scoring:
       U = sigma_inst * (|OFI| / Depth) * exp(-lambda * t)
2. Urgency-Aware Smart Order Routing (SOR):
       - U >= 0.70: Aggressive Taker / Immediate-Or-Cancel (IOC)
       - U <= 0.30: Passive Maker Limit at P_micro with Quote Fading
       - Neutral: Standard Limit
3. Elastic Weight Consolidation (EWC) Regularizer:
       L_EWC(theta) = sum_i (lambda / 2) * F_i * (theta_i - theta_{i,old})^2
4. Synthetic Stealth Execution Daemon:
       Client-side execution daemon triggering market exits only upon volume-weighted bar close,
       eliminating visible resting stops vulnerable to institutional liquidity sweeps.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np


def calculate_alpha_decay_urgency_score(
    instantaneous_volatility: float,
    order_flow_imbalance: float,
    order_book_depth: float,
    elapsed_time_seconds: float = 0.0,
    decay_lambda: float = 0.10,
) -> float:
    """Calculates Alpha Decay Urgency Score U in [0, 1]:
    U = sigma_inst * (|OFI| / Depth) * exp(-lambda * t)
    """
    sigma = float(instantaneous_volatility)
    ofi = abs(float(order_flow_imbalance))
    depth = max(float(order_book_depth), 1.0)
    t = max(float(elapsed_time_seconds), 0.0)
    lam = max(float(decay_lambda), 1e-4)

    if not all(math.isfinite(x) for x in (sigma, ofi, depth, t, lam)) or sigma <= 0.0:
        return 0.0

    raw_urgency = sigma * (ofi / depth) * math.exp(-lam * t)
    # Normalize with sigmoid-like clamping to [0, 1]
    norm_urgency = 1.0 - math.exp(-raw_urgency * 10.0)
    return float(max(0.0, min(1.0, norm_urgency)))


class UrgencySmartOrderRouter:
    """Smart Order Router that dynamically selects between Taker (IOC) and Maker (Micro-Price) execution."""

    def __init__(
        self,
        high_urgency_threshold: float = 0.70,
        low_urgency_threshold: float = 0.30,
    ):
        self.high_urgency_threshold = float(high_urgency_threshold)
        self.low_urgency_threshold = float(low_urgency_threshold)

    def route_execution_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        urgency_score: float,
        best_bid: float,
        best_ask: float,
        bid_depth: float = 100.0,
        ask_depth: float = 100.0,
    ) -> Dict[str, Any]:
        """Routes order to the optimal execution algorithm based on Alpha Urgency Score."""
        u = float(urgency_score)
        side_norm = str(side).upper()

        # Volume-weighted micro price
        tot_depth = bid_depth + ask_depth
        p_micro = (bid_depth * best_ask + ask_depth * best_bid) / tot_depth if tot_depth > 0 else (best_bid + best_ask) / 2.0

        if u >= self.high_urgency_threshold:
            # Fast alpha / breakout -> Aggressive Taker IOC
            order_type = "MARKET_IOC"
            target_price = best_ask if side_norm in ("BUY", "LONG") else best_bid
            execution_strategy = "AGGRESSIVE_TAKER_CROSS_SPREAD"
        elif u <= self.low_urgency_threshold:
            # Slow alpha / mean-reversion -> Passive Maker Limit at P_micro
            order_type = "LIMIT_MAKER"
            target_price = round(p_micro, 5)
            execution_strategy = "PASSIVE_MAKER_MICRO_PRICE_WITH_FADING"
        else:
            # Neutral -> Best Bid/Ask Limit
            order_type = "LIMIT_STANDARD"
            target_price = best_bid if side_norm in ("BUY", "LONG") else best_ask
            execution_strategy = "STANDARD_LIMIT_QUEUE"

        return {
            "symbol": symbol,
            "side": side_norm,
            "volume": float(volume),
            "urgency_score": round(u, 4),
            "order_type": order_type,
            "target_price": float(target_price),
            "micro_price": round(float(p_micro), 5),
            "execution_strategy": execution_strategy,
            "is_taker": u >= self.high_urgency_threshold,
        }

    def route_external_alert(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        urgency_score: float,
        best_bid: float,
        best_ask: float,
        source: str = "tradingview",
        bid_depth: float = 100.0,
        ask_depth: float = 100.0,
    ) -> Dict[str, Any]:
        """External-alert routing branch (TradingView et al).

        External webhook alerts are never allowed to originate aggressive
        execution: at HIGH urgency they route as a standard limit at the
        entry price (no IOC taker), and at MEDIUM/LOW they are surfaced as
        advisory-only proposals (no order routed). Deterministic vetoes
        downstream still apply; this branch just guarantees the alert can
        never cross the spread under its own urgency.
        """
        src = str(source or "").strip().lower()
        if src != "tradingview":
            # Non-TV sources keep deterministic routing semantics.
            return self.route_execution_order(
                symbol=symbol, side=side, volume=volume, price=price,
                urgency_score=urgency_score, best_bid=best_bid,
                best_ask=best_ask, bid_depth=bid_depth, ask_depth=ask_depth)

        side_norm = str(side).upper()
        u = float(urgency_score)
        tot_depth = bid_depth + ask_depth
        p_micro = (bid_depth * best_ask + ask_depth * best_bid) / tot_depth if tot_depth > 0 else (best_bid + best_ask) / 2.0

        if u >= self.high_urgency_threshold:
            routed = {
                "symbol": symbol,
                "side": side_norm,
                "volume": float(volume),
                "urgency_score": round(u, 4),
                "order_type": "LIMIT_STANDARD",
                "target_price": float(price),
                "micro_price": round(float(p_micro), 5),
                "execution_strategy": "EXTERNAL_ALERT_STANDARD_LIMIT",
                "is_taker": False,
                "source": src,
            }
        else:
            routed = {
                "symbol": symbol,
                "side": side_norm,
                "volume": 0.0,
                "urgency_score": round(u, 4),
                "order_type": None,
                "target_price": float(price),
                "micro_price": round(float(p_micro), 5),
                "execution_strategy": "EXTERNAL_ALERT_ADVISORY_ONLY",
                "is_taker": False,
                "source": src,
            }
        return routed


from core.ewc_forgetting_prevention import ElasticWeightConsolidationRegularizer


class SyntheticStealthExecutionDaemon:
    """Client-side stealth daemon executing stops without exposing orders to institutional liquidity sweeps."""

    @staticmethod
    def evaluate_synthetic_trigger(
        current_price: float,
        stop_loss_price: Optional[float],
        take_profit_price: Optional[float],
        side: str,
        current_bar_volume: float = 100.0,
        min_bar_volume_confirmation: float = 50.0,
    ) -> Dict[str, Any]:
        """Evaluates whether synthetic stop or profit trigger has fired on volume-confirmed close."""
        p = float(current_price)
        vol = float(current_bar_volume)
        min_vol = float(min_bar_volume_confirmation)
        side_norm = str(side).upper()

        is_volume_confirmed = vol >= min_vol

        sl_triggered = False
        tp_triggered = False
        reason = "HOLDING"

        if side_norm in ("BUY", "LONG"):
            if stop_loss_price is not None and p <= float(stop_loss_price):
                sl_triggered = True
                reason = "SYNTHETIC_STOP_LOSS_CONFIRMED"
            elif take_profit_price is not None and p >= float(take_profit_price):
                tp_triggered = True
                reason = "SYNTHETIC_TAKE_PROFIT_CONFIRMED"
        else:  # SELL / SHORT
            if stop_loss_price is not None and p >= float(stop_loss_price):
                sl_triggered = True
                reason = "SYNTHETIC_STOP_LOSS_CONFIRMED"
            elif take_profit_price is not None and p <= float(take_profit_price):
                tp_triggered = True
                reason = "SYNTHETIC_TAKE_PROFIT_CONFIRMED"

        should_execute = (sl_triggered or tp_triggered) and is_volume_confirmed

        return {
            "should_execute": should_execute,
            "sl_triggered": sl_triggered,
            "tp_triggered": tp_triggered,
            "synthetic_stop_fired": sl_triggered and is_volume_confirmed,
            "synthetic_tp_fired": tp_triggered and is_volume_confirmed,
            "is_volume_confirmed": is_volume_confirmed,
            "reason": reason if should_execute else "HOLDING",
            "current_price": p,
        }
