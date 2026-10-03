"""Retail Execution Friction, Effective EV & Minimum Viable Account Model.

Implements Part1 §1.2-1.9 retail-specific cost and friction models:
    EV_retail = mu_gross - (tau * (2*f_tier + S_eff) + c_fixed / A)
    C_min = c_fixed / (mu_gross - tau*(2*f_tier + S_eff) - Hurdle_min)

Also includes:
- Dynamic Spread Model (Part1 §1.6):
    S_t = S_base * (1 + kappa_vol*(ATR_t/ATR_base - 1) + kappa_liq*(Vol_base/Vol_t - 1)) * M_session
- Spread Decomposition (Part1 §1.5):
    Quoted, Effective, Realized spreads + Adverse Selection
- Retail Broker Asymmetry Penalty (Part1 §1.4)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class RetailFrictionResult:
    """Complete retail friction analysis."""
    ev_gross: float
    ev_retail: float
    total_friction_bps: float
    friction_breakdown: Dict[str, float]
    min_viable_account: float
    is_profitable_after_costs: bool
    breakeven_win_rate_after_costs: float
    effective_spread_bps: float
    adverse_selection_bps: float
    session_multiplier: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ev_gross_bps": round(self.ev_gross * 10000, 2),
            "ev_retail_bps": round(self.ev_retail * 10000, 2),
            "total_friction_bps": round(self.total_friction_bps, 2),
            "friction_breakdown": {k: round(v, 2) for k, v in self.friction_breakdown.items()},
            "min_viable_account_usd": round(self.min_viable_account, 2),
            "is_profitable_after_costs": self.is_profitable_after_costs,
            "breakeven_win_rate_after_costs_pct": round(self.breakeven_win_rate_after_costs * 100, 2),
            "effective_spread_bps": round(self.effective_spread_bps, 2),
            "adverse_selection_bps": round(self.adverse_selection_bps, 2),
            "session_multiplier": round(self.session_multiplier, 2),
        }


class RetailFrictionEngine:
    """Retail Execution Friction & Minimum Viable Account Calculator.

    Evaluates whether a strategy is profitable after retail execution costs,
    and computes the minimum account size needed to overcome fixed costs.
    """

    # Cost tables by venue (Part1 §1.3)
    VENUE_COSTS = {
        "FX_CFD": {"spread_pips": 1.2, "taker_fee_bps": 0, "maker_fee_bps": 0,
                    "latency_ms": 150, "adverse_selection": "EXTREME"},
        "CRYPTO_VIP0": {"spread_bps": 2.0, "taker_fee_bps": 4.5, "maker_fee_bps": 1.5,
                         "latency_ms": 50, "adverse_selection": "HIGH"},
        "IBKR_EQUITIES": {"spread_bps": 1.5, "taker_fee_bps": 0.5, "maker_fee_bps": -0.2,
                           "latency_ms": 30, "adverse_selection": "MODERATE"},
    }

    # Session multipliers (Part1 §1.6)
    SESSION_MULTIPLIERS = {
        "LONDON_NY_OVERLAP": 1.0,
        "LONDON": 1.2,
        "NEW_YORK": 1.3,
        "ASIAN": 2.0,
        "ROLLOVER_5PM_EST": 3.5,
        "OFF_SESSION": 4.0,
    }

    def __init__(
        self,
        venue: str = "FX_CFD",
        base_spread_bps: float = 10.0,
        taker_fee_bps: float = 0.0,
        fixed_monthly_cost: float = 0.0,
        kappa_vol: float = 0.5,
        kappa_liq: float = 0.5,
    ):
        """
        Args:
            venue: Trading venue type.
            base_spread_bps: Base bid-ask spread in basis points.
            taker_fee_bps: Exchange/broker taker fee in bps.
            fixed_monthly_cost: Fixed operational cost per month (USD).
            kappa_vol: Volatility sensitivity for dynamic spread.
            kappa_liq: Liquidity sensitivity for dynamic spread.
        """
        self.venue = str(venue).upper()
        self.base_spread_bps = max(0.0, float(base_spread_bps))
        self.taker_fee_bps = max(0.0, float(taker_fee_bps))
        self.fixed_monthly_cost = max(0.0, float(fixed_monthly_cost))
        self.kappa_vol = float(kappa_vol)
        self.kappa_liq = float(kappa_liq)

    def calculate_retail_ev(
        self,
        mu_gross: float,
        turnover: float,
        account_size: float,
        risk_per_trade: float = 0.01,
    ) -> RetailFrictionResult:
        """Calculate retail EV after all execution friction.

        EV_retail = mu_gross - (tau * (2*f_tier + S_eff) + c_fixed / A)

        Args:
            mu_gross: Gross expected value per trade (decimal, e.g., 0.001 = 10 bps).
            turnover: Portfolio turnover rate (round trips per period).
            account_size: Account equity in USD.
            risk_per_trade: Risk per trade as fraction of equity.

        Returns:
            RetailFrictionResult with full friction breakdown.
        """
        mu = float(mu_gross)
        tau = float(turnover)
        A = max(100.0, float(account_size))

        # Effective spread (Part1 §1.5)
        effective_spread_bps = self.base_spread_bps + self.taker_fee_bps * 2
        effective_spread = effective_spread_bps / 10000.0

        # Adverse selection estimate (Part1 §1.5): ~30% of effective spread for retail
        adverse_selection_bps = effective_spread_bps * 0.30
        adverse_selection = adverse_selection_bps / 10000.0

        # Total friction per round trip
        round_trip_cost = tau * (2.0 * (self.taker_fee_bps / 10000.0) + effective_spread + adverse_selection)

        # Fixed cost drag
        fixed_drag = self.fixed_monthly_cost / A if A > 0 else 0.0

        # Total friction
        total_friction = round_trip_cost + fixed_drag
        total_friction_bps = total_friction * 10000

        # Retail EV
        ev_retail = mu - total_friction

        # Breakeven win rate after costs (Part3 §11): p* = L / (W + L)
        # Adjusted for friction: need edge > friction
        if risk_per_trade > 0:
            breakeven_win = (risk_per_trade) / (2 * risk_per_trade + total_friction) if (2 * risk_per_trade + total_friction) > 0 else 0.5
        else:
            breakeven_win = 0.5

        # Minimum viable account (Part1 §1.2)
        min_account = self._calculate_min_viable_account(mu, tau, effective_spread, adverse_selection)

        # Friction breakdown
        breakdown = {
            "spread_cost_bps": self.base_spread_bps,
            "taker_fee_bps": self.taker_fee_bps * 2,
            "adverse_selection_bps": adverse_selection_bps,
            "fixed_cost_drag_bps": fixed_drag * 10000,
        }

        return RetailFrictionResult(
            ev_gross=mu,
            ev_retail=ev_retail,
            total_friction_bps=total_friction_bps,
            friction_breakdown=breakdown,
            min_viable_account=min_account,
            is_profitable_after_costs=ev_retail > 0,
            breakeven_win_rate_after_costs=breakeven_win,
            effective_spread_bps=effective_spread_bps,
            adverse_selection_bps=adverse_selection_bps,
            session_multiplier=1.0,
        )

    def _calculate_min_viable_account(
        self,
        mu_gross: float,
        turnover: float,
        effective_spread: float,
        adverse_selection: float,
        hurdle_min: float = 0.0001,
    ) -> float:
        """C_min = c_fixed / (mu_gross - tau*(2*f_tier + S_eff) - Hurdle_min)"""
        denominator = mu_gross - turnover * (2.0 * self.taker_fee_bps / 10000.0 + effective_spread + adverse_selection) - hurdle_min
        if denominator <= 0:
            return float('inf')
        return self.fixed_monthly_cost / denominator

    def calculate_dynamic_spread(
        self,
        current_atr: float,
        baseline_atr: float,
        current_volume: float,
        baseline_volume: float,
        session: str = "LONDON_NY_OVERLAP",
    ) -> Dict[str, float]:
        """Dynamic spread model (Part1 §1.6):
        S_t = S_base * (1 + kappa_vol*(ATR_t/ATR_base - 1) + kappa_liq*(Vol_base/Vol_t - 1)) * M_session
        """
        atr_ratio = current_atr / max(1e-6, baseline_atr)
        vol_ratio = baseline_volume / max(1.0, current_volume)
        m_session = self.SESSION_MULTIPLIERS.get(session, 1.0)

        vol_component = self.kappa_vol * max(0.0, atr_ratio - 1.0)
        liq_component = self.kappa_liq * max(0.0, vol_ratio - 1.0)

        dynamic_spread = self.base_spread_bps * (1.0 + vol_component + liq_component) * m_session
        dynamic_spread = max(self.base_spread_bps * 0.5, dynamic_spread)

        return {
            "base_spread_bps": self.base_spread_bps,
            "dynamic_spread_bps": round(dynamic_spread, 2),
            "volatility_component_bps": round(self.base_spread_bps * vol_component, 2),
            "liquidity_component_bps": round(self.base_spread_bps * liq_component, 2),
            "session_multiplier": m_session,
            "session": session,
        }


# Canonical aliases
RetailExecutionFriction = RetailFrictionEngine
MinimumViableAccountCalculator = RetailFrictionEngine
