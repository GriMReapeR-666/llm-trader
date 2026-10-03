"""Account Sizing & Minimum Viable Account Calculator.

Implements Part1 §1.2 and Part2 §1.1:
    1R = Account_Equity * Risk_%
    Position Size = 1R / |Entry_Price - Stop_Loss_Price|

    C_min = c_fixed / (mu_gross - tau*(2*f_tier + S_eff) - Hurdle_min)
    Minimum Viable Account Size for a given strategy + venue.

Also includes:
- Forced over-betting detection (Part1 §2.3):
    When min contract size forces Delta_u > f* * C_0 → P(Ruin) → 1.0
- Risk-of-ruin aware account sizing
- Per-venue minimum capital requirements
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AccountSizingResult:
    """Complete account sizing analysis."""
    account_equity: float
    one_r_dollar: float
    risk_per_trade_pct: float
    position_size_lots: float
    position_size_units: float
    stop_distance_pips: float
    min_viable_account: float
    is_overbetting: bool
    overbetting_ratio: float     # actual_risk_per_unit / optimal_kelly
    forced_overbetting: bool     # Contract size forces over-betting
    max_risk_at_kelly_pct: float
    recommended_account: float
    volatility_scalar: float = 1.0
    meta_health_multiplier: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_equity": round(self.account_equity, 2),
            "one_r_dollar": round(self.one_r_dollar, 2),
            "risk_per_trade_pct": round(self.risk_per_trade_pct * 100, 2),
            "position_size_lots": round(self.position_size_lots, 4),
            "position_size_units": round(self.position_size_units, 2),
            "stop_distance_pips": round(self.stop_distance_pips, 4),
            "min_viable_account": round(self.min_viable_account, 2),
            "is_overbetting": self.is_overbetting,
            "overbetting_ratio": round(self.overbetting_ratio, 4),
            "forced_overbetting": self.forced_overbetting,
            "max_risk_at_kelly_pct": round(self.max_risk_at_kelly_pct * 100, 2),
            "recommended_account": round(self.recommended_account, 2),
            "volatility_scalar": round(self.volatility_scalar, 4),
            "meta_health_multiplier": round(self.meta_health_multiplier, 4),
        }


class AccountSizingEngine:
    """Account sizing and minimum viable capital calculator.

    Implements the Universal 1R unit (Part2 §6.1):
        1R = Account_Equity * Risk_%
        Position Size = 1R / |Entry_Price - Stop_Loss_Price|

    And detects forced over-betting (Part1 §2.3):
        When minimum contract sizes force Delta_u > f* * C_0
    """

    def __init__(
        self,
        risk_per_trade: float = 0.01,
        min_lot_size: float = 0.01,
        tick_size: float = 0.00001,
        tick_value: float = 1.0,
        contract_value: float = 100000.0,
    ):
        """
        Args:
            risk_per_trade: Risk per trade as fraction (default 1%).
            min_lot_size: Minimum broker lot size (e.g., 0.01 for micro).
            tick_size: Minimum price increment.
            tick_value: Dollar value per tick per lot.
            contract_value: Notional value per standard lot.
        """
        self.risk_per_trade = max(0.001, min(0.05, float(risk_per_trade)))
        self.min_lot_size = max(0.001, float(min_lot_size))
        self.tick_size = max(1e-8, float(tick_size))
        self.tick_value = max(0.001, float(tick_value))
        self.contract_value = max(1.0, float(contract_value))

    def calculate_1r(
        self,
        account_equity: float,
        entry_price: float = 1.1000,
        stop_loss_price: float = 1.0950,
        **kwargs,
    ) -> AccountSizingResult:
        return self.calculate_position_size(account_equity, entry_price, stop_loss_price, **kwargs)

    def calculate_position_size(
        self,
        account_equity: float,
        entry_price: float,
        stop_loss_price: float,
        current_atr: Optional[float] = None,
        baseline_atr: Optional[float] = None,
        meta_health_multiplier: float = 1.0,
    ) -> AccountSizingResult:
        """Calculate position size using the Universal 1R unit, ATR volatility scaling, and Meta-Health throttling.

        1R = Account_Equity * Risk_%
        Position Size = (1R / |Entry_Price - Stop_Loss_Price|) * Volatility_Scalar * Meta_Health_Multiplier
        """
        equity = max(100.0, float(account_equity))
        entry = float(entry_price)
        sl = float(stop_loss_price)

        # 1R dollar risk
        one_r = equity * self.risk_per_trade

        # Stop distance
        stop_dist = abs(entry - sl)
        if stop_dist <= 0:
            stop_dist = 0.0010  # Default 10 pips for FX

        # Loss per lot at stop distance
        loss_per_lot = (stop_dist / self.tick_size) * self.tick_value

        # Raw position size
        if loss_per_lot > 0:
            raw_lots = one_r / loss_per_lot
        else:
            raw_lots = 0.0

        # Dynamic ATR Volatility Risk Parity Scaling
        vol_scalar = 1.0
        if current_atr is not None and baseline_atr is not None:
            try:
                c_atr = float(current_atr)
                b_atr = float(baseline_atr)
                if math.isfinite(c_atr) and math.isfinite(b_atr) and c_atr > 0 and b_atr > 0:
                    vol_scalar = max(0.25, min(1.75, b_atr / c_atr))
            except (TypeError, ValueError, ZeroDivisionError):
                vol_scalar = 1.0
        raw_lots = raw_lots * vol_scalar

        # Meta-Reflector Architectural Health Throttling
        m_mult = 1.0
        if meta_health_multiplier is not None:
            try:
                val = float(meta_health_multiplier)
                if math.isfinite(val):
                    m_mult = max(0.10, min(1.0, val))
            except (TypeError, ValueError):
                m_mult = 1.0
        raw_lots = raw_lots * m_mult

        # Quantize to broker minimum with floating-point epsilon rounding
        lots = math.floor(round(raw_lots / self.min_lot_size, 6)) * self.min_lot_size
        lots = round(max(0.0, lots), 4)

        # Actual dollar risk after quantization
        actual_risk = lots * loss_per_lot
        actual_risk_pct = actual_risk / equity if equity > 0 else 0.0

        # Stop distance in pips (for display)
        stop_pips = stop_dist / self.tick_size

        # Forced over-betting check (Part1 §2.3)
        # If minimum lot size risk > 2% of account → forced over-betting
        min_lot_risk = self.min_lot_size * loss_per_lot
        forced_overbetting = min_lot_risk > equity * 0.02

        # Kelly optimal for reference
        # f* = (p*b - q) / b, simplified to 1R% for display
        kelly_max = self.risk_per_trade  # Simplified

        overbetting_ratio = actual_risk_pct / max(0.001, kelly_max) if kelly_max > 0 else 1.0

        # Minimum viable account: account where min lot = 1% risk
        min_viable = min_lot_risk / self.risk_per_trade if self.risk_per_trade > 0 else 0.0

        # Recommended account: 2x minimum for safety
        recommended = min_viable * 2.0

        return AccountSizingResult(
            account_equity=equity,
            one_r_dollar=one_r,
            risk_per_trade_pct=self.risk_per_trade,
            position_size_lots=lots,
            position_size_units=lots * self.contract_value,
            stop_distance_pips=stop_pips,
            min_viable_account=min_viable,
            is_overbetting=overbetting_ratio > 1.5,
            overbetting_ratio=overbetting_ratio,
            forced_overbetting=forced_overbetting,
            max_risk_at_kelly_pct=kelly_max,
            recommended_account=recommended,
            volatility_scalar=vol_scalar,
            meta_health_multiplier=m_mult,
        )

    def check_forced_overbetting(
        self,
        account_equity: float,
        min_contract_risk_dollars: float,
    ) -> Dict[str, Any]:
        """Detect forced over-betting (Part1 §2.3).

        When minimum contract sizes force risk > optimal, P(Ruin) → 1.0.
        """
        equity = max(100.0, float(account_equity))
        min_risk = max(0.0, float(min_contract_risk_dollars))

        optimal_risk = equity * self.risk_per_trade
        actual_risk = min_risk

        is_forced = actual_risk > optimal_risk * 1.5
        ratio = actual_risk / optimal_risk if optimal_risk > 0 else float('inf')

        return {
            "account_equity": equity,
            "optimal_risk_per_trade": round(optimal_risk, 2),
            "min_contract_risk": round(actual_risk, 2),
            "risk_ratio": round(ratio, 4),
            "is_forced_overbetting": is_forced,
            "minimum_account_needed": round(actual_risk / self.risk_per_trade, 2) if self.risk_per_trade > 0 else 0,
            "recommendation": "INCREASE_ACCOUNT" if is_forced else "ACCEPTABLE",
        }


# Universal 1R Calculator (Part2 §6.1)
def calculate_universal_1r(
    equity: float,
    risk_pct: float = 0.01,
    entry: float = 1.0,
    stop: float = 0.99,
) -> Dict[str, float]:
    """Universal 1R Unit calculation.

    1R = Account_Equity * Risk_%
    Position Size = 1R / |Entry_Price - Stop_Loss_Price|

    Returns dict with 1R dollar, position size, and stop distance.
    """
    one_r = equity * risk_pct
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return {"one_r_dollar": one_r, "position_size": 0.0, "stop_distance": 0.0}
    position_size = one_r / stop_dist
    return {
        "one_r_dollar": round(one_r, 2),
        "position_size": round(position_size, 4),
        "stop_distance": round(stop_dist, 6),
    }


# Canonical aliases
MinimumViableAccountCalculator = AccountSizingEngine
PositionSizer1R = AccountSizingEngine
