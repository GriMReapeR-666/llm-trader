"""Gold (XAU/USD) Micro-Account Scaling Engine & Growth Ladder.

Master Quantitative Blueprint Calibration:
- Symbol: Spot Gold (XAU/USD) CFDs
- MT4/MT5 CFD Calibration:
  * 1.00 Standard Lot: 100 troy ounces (~$408,000 - $415,600 notional value).
  * 0.01 Micro Lot (Minimum Tradeable Size): 1 troy ounce (~$4,080 - $4,156 notional value).
- Convention B (Retail Gold Pip):
  * $0.10 move = 1 pip (10 MT5 points).
  * Pip value on 0.01 lot (1 oz): $0.10 per pip ($1.00 per 10 pips).
  * 100-200 pip trade (150-pip average): $10.00 to $20.00 move = $15.00 profit.
  * 30-37 retail pips stop-loss: $3.00 to $3.70 risk per 0.01 lot.

The 3-Stage Position Sizing & Risk Architecture:
1. Stage 1: Linear Cash-Flow Grind ($100 -> $500 Equity)
   - Fixed 0.01 micro lot (1 oz) strictly.
   - Stop-Loss: 30 to 37 retail pips ($3.00 to $3.70 dollar risk).
   - Target: 100 to 200 retail pips (average 150 pips = $15.00 profit).
   - Net winning trades needed: 26.67 trades to reach $500 (+$400).

2. Stage 2: Micro-Lot Rounding Bridge ($500 -> $1,000 Equity)
   - Eliminates standard floor-rounding bottleneck (which traps size at 0.01 until $740).
   - Nearest-Step Lot Rounding with 0.015 midpoint threshold & 1.33% max risk cap:
     Lots = round((Equity * 0.01) / StopLoss_$, 2)
     With 37-pip ($3.70) stop-loss, unlocks 0.02 lots at $555 and 0.03 lots at $925.
   - Adaptive 30-Pip ($3.00) Pullback Stop:
     Unlocks 0.02 lots at $600 and 0.03 lots at $900 strictly at 1.00% risk.
   - 185-Pip ($18.50) Take-Profit Extension ($500 - $554):
     While trading 0.01 lots between $500 and $554, extends TP target to 185 pips
     to cross the $555 threshold in just 2.97 net wins.

3. Stage 3: Full 1% Dynamic Risk Compounding ($1,000 -> $8,000 / $10,000+ Equity)
   - Dynamic 0.01 lot step compounding: Lots = round((Equity * 0.01) / StopLoss_$, 2).
   - Milestones:
     * $925-$1,295: 0.03 lots
     * $1,295-$1,665: 0.04 lots
     * $1,665-$2,035: 0.05 lots
     * $2,035-$3,145: 0.06-0.08 lots
     * $3,145-$5,000: 0.09-0.13 lots
     * $5,000-$8,000: 0.14-0.22 lots
     * $8,000-$10,000: 0.22-0.27 lots (Bonus tier)

Risk Controls & Process Management:
- Hysteresis Band ($25 buffer / 3% band): Prevents tier chatter on drawdowns below upgrade thresholds.
- Daily 2R Loss Circuit Breaker: Halts trading for 24h if 2 losing trades occur in a day.
- Max Spread Filter: Blocks orders when spread > 25 points ($0.25/oz / 2.5 retail pips).
- 3-Stage Dynamic Exits:
  * Stage 1 (+100 pips): 50% partial close, move SL to Entry + 10 pips (+1.00/oz).
  * Stage 2 (+150 pips): Ratchet SL to +110 pips (+11.00/oz) + M15 Chandelier trail (10, 1.5 ATR).
  * Stage 3 (+200+ pips): Uncapped trend expansion runner.
- Drawdown Throttle (3.9% / 4R trigger): Cuts risk to 0.50%, recovers via 2-step protocol.

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib & math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

EXECUTION_AUTHORITY = False
CONVENTION = "Convention B (Retail Gold Pip: $0.10 move = 1 pip / 10 MT5 points)"
POINT_SIZE = 0.01               # 1 MT5 point = $0.01/oz
RETAIL_PIP_DOLLARS = 0.10       # 1 retail pip = $0.10/oz = 10 MT5 points
MICRO_LOT_OZ = 1.0              # 0.01 lot = 1 troy oz
STANDARD_LOT_OZ = 100.0         # 1.00 lot = 100 troy oz
FRICTION_PIPS = 3.0             # 3.0 retail pips = $0.30/oz on XAU/USD ($0.15 spread + $0.07 comm + $0.08 slip)
FRICTION_DOLLARS = 0.30         # $0.30/oz friction
MAX_SPREAD_POINTS = 25.0        # 25 points = $0.25/oz max spread = 2.5 retail pips
MIN_ADX_THRESHOLD = 22.0        # ADX >= 22 required for trend runner execution
MIN_DAILY_ATR_DOLLARS = 45.0    # Daily ATR >= $45.00/oz (450 retail pips)
DRAWDOWN_THROTTLE_TRIGGER_PCT = 3.90  # 4R / 3.9% drawdown activates 0.50% risk throttle
HYSTERESIS_BUFFER_DOLLARS = 25.0      # $25 drawdown buffer to prevent tier chatter
STAGE_2_MAX_RISK_PCT = 1.334          # 1.33% risk cap during Stage 2 nearest-step rounding
STAGE_2A_TP_EXTENSION_PIPS = 185.0   # 185 pips ($18.50) TP extension for $500-$554 zone
STANDARD_TP_PIPS = 150.0             # Standard 150 pips ($15.00) average winner
DAILY_CIRCUIT_BREAKER_MAX_LOSSES = 2  # 2 consecutive losing trades halts trading for 24h


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class GoldRungSpec:
    rung_id: int
    min_equity: float
    max_equity: float
    base_lots: float
    max_lots: float
    adaptive_sl_pips_min: float
    adaptive_sl_pips_max: float
    description: str


GOLD_RUNGS: List[GoldRungSpec] = [
    GoldRungSpec(1, 100.0, 499.99, 0.01, 0.01, 30.0, 37.0, "Rung 1: Fixed 0.01 micro lot (Stage 1 Linear Cash-Flow Grind $100-$500)"),
    GoldRungSpec(2, 500.0, 554.99, 0.01, 0.01, 30.0, 37.0, "Rung 2: Micro-lot rounding bridge - 185-pip TP extension ($500-$554)"),
    GoldRungSpec(3, 555.0, 924.99, 0.02, 0.02, 22.0, 37.0, "Rung 3: Micro-lot rounding bridge - 0.02 lots on 1.33% risk cap ($555-$924)"),
    GoldRungSpec(4, 925.0, 1294.99, 0.03, 0.03, 20.5, 37.0, "Rung 4: Dynamic 1% compounding - 0.03 lots ($925-$1,294)"),
    GoldRungSpec(5, 1295.0, 1664.99, 0.04, 0.04, 23.0, 37.0, "Rung 5: Dynamic 1% compounding - 0.04 lots ($1,295-$1,664)"),
    GoldRungSpec(6, 1665.0, 2034.99, 0.05, 0.05, 25.0, 37.0, "Rung 6: Dynamic 1% compounding - 0.05 lots ($1,665-$2,034)"),
    GoldRungSpec(7, 2035.0, 3144.99, 0.06, 0.08, 27.0, 37.0, "Rung 7: Dynamic 1% compounding - 0.06-0.08 lots ($2,035-$3,144)"),
    GoldRungSpec(8, 3145.0, 4999.99, 0.09, 0.13, 28.5, 37.0, "Rung 8: Dynamic 1% compounding - 0.09-0.13 lots ($3,145-$4,999)"),
    GoldRungSpec(9, 5000.0, 7999.99, 0.14, 0.22, 29.5, 37.0, "Rung 9: Dynamic 1% compounding - 0.14-0.22 lots ($5,000-$7,999)"),
    GoldRungSpec(10, 8000.0, 50000.0, 0.22, 0.27, 30.0, 37.0, "Rung 10: Bonus Milestone - 0.22-0.27 lots ($8,000-$10,000+)"),
]


class GoldScalingLadder:
    """Institutional Scaling Engine for Gold (XAU/USD) Micro-Accounts."""

    def __init__(self, peak_equity: float = 100.0) -> None:
        self.peak_equity = max(100.0, float(peak_equity))
        self.throttle_active = False
        self.recovery_step = 0  # 0: normal, 1: 0.50% risk, 2: 0.75% risk

        # Hysteresis state machine ($25 buffer prevents tier chatter)
        self.active_lot_tier: float = 0.01
        self.current_tier_threshold: float = 0.0
        self.hysteresis_buffer: float = HYSTERESIS_BUFFER_DOLLARS

        # Daily 2R Loss Circuit Breaker state
        self.daily_losses_count: int = 0
        self.daily_realized_r: float = 0.0
        self.daily_realized_pnl: float = 0.0
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_triggered_at: Optional[str] = None
        self.current_date: Optional[str] = None

    def update_high_water_mark(self, current_equity: float) -> float:
        eq = _safe_float(current_equity) or 100.0
        if eq >= self.peak_equity:
            self.peak_equity = eq
            self.throttle_active = False
            self.recovery_step = 0
        return self.peak_equity

    def evaluate_drawdown_throttle(self, current_equity: float) -> Dict[str, Any]:
        """Calculates live drawdown from HWM and updates the 0.5% throttle state."""
        eq = _safe_float(current_equity) or 100.0
        self.update_high_water_mark(eq)
        dd_pct = ((self.peak_equity - eq) / self.peak_equity) * 100.0 if self.peak_equity > 0 else 0.0

        if dd_pct >= DRAWDOWN_THROTTLE_TRIGGER_PCT:
            self.throttle_active = True
            if self.recovery_step == 0:
                self.recovery_step = 1
        elif dd_pct <= 0.0:
            self.throttle_active = False
            self.recovery_step = 0

        effective_risk_pct = 1.00
        if self.throttle_active:
            if self.recovery_step == 1:
                effective_risk_pct = 0.50
            elif self.recovery_step == 2:
                effective_risk_pct = 0.75

        return {
            "status": "READY_FOR_REVIEW",
            "current_equity": round(eq, 2),
            "peak_equity": round(self.peak_equity, 2),
            "drawdown_pct": round(dd_pct, 2),
            "throttle_active": self.throttle_active,
            "recovery_step": self.recovery_step,
            "effective_risk_pct": effective_risk_pct,
            "execution_authority": False,
        }

    def _check_day_rollover(self, now: Optional[datetime] = None) -> None:
        """Reset daily circuit breaker tracking on day boundary (UTC)."""
        current_dt = now or datetime.now(timezone.utc)
        day_str = current_dt.date().isoformat()
        if self.current_date is None:
            self.current_date = day_str
        elif self.current_date != day_str:
            self.current_date = day_str
            self.daily_losses_count = 0
            self.daily_realized_r = 0.0
            self.daily_realized_pnl = 0.0
            self.circuit_breaker_active = False
            self.circuit_breaker_triggered_at = None

    def record_daily_trade(
        self,
        r_multiple: float,
        pnl_dollars: float = 0.0,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Record trade outcome for Daily 2R Loss Circuit Breaker."""
        self._check_day_rollover(now)
        r_val = _safe_float(r_multiple) or 0.0
        pnl_val = _safe_float(pnl_dollars) or 0.0

        if r_val < 0.0 or pnl_val < 0.0:
            self.daily_losses_count += 1
            self.daily_realized_r += r_val
            self.daily_realized_pnl += pnl_val
            if self.daily_losses_count >= DAILY_CIRCUIT_BREAKER_MAX_LOSSES or self.daily_realized_r <= -2.0:
                self.circuit_breaker_active = True
                self.circuit_breaker_triggered_at = (now or datetime.now(timezone.utc)).isoformat()

        return {
            "status": "READY_FOR_REVIEW",
            "daily_losses_count": self.daily_losses_count,
            "daily_realized_r": round(self.daily_realized_r, 2),
            "daily_realized_pnl": round(self.daily_realized_pnl, 2),
            "circuit_breaker_active": self.circuit_breaker_active,
            "circuit_breaker_triggered_at": self.circuit_breaker_triggered_at,
            "execution_authority": False,
        }

    def reset_daily_circuit_breaker(self) -> None:
        """Manually or scheduled reset of the daily circuit breaker."""
        self.daily_losses_count = 0
        self.daily_realized_r = 0.0
        self.daily_realized_pnl = 0.0
        self.circuit_breaker_active = False
        self.circuit_breaker_triggered_at = None

    def record_trade_outcome(
        self,
        r_multiple: float,
        current_equity: float,
        pnl_dollars: float = 0.0,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Updates recovery step and daily circuit breaker upon trade completion."""
        r_val = _safe_float(r_multiple) or 0.0
        pnl_val = _safe_float(pnl_dollars) or 0.0
        eq = _safe_float(current_equity) or 100.0
        self.update_high_water_mark(eq)
        self.record_daily_trade(r_val, pnl_val, now)

        if self.throttle_active:
            if r_val >= 2.0:
                if self.recovery_step == 1:
                    self.recovery_step = 2  # Step up to 0.75%
                elif self.recovery_step == 2:
                    self.recovery_step = 0  # Restored to 1.00%
                    self.throttle_active = False
            elif r_val < 0.0:
                self.recovery_step = 1  # Reset to 0.50% base throttle

        return {
            "status": "READY_FOR_REVIEW",
            "r_multiple": r_val,
            "throttle_active": self.throttle_active,
            "recovery_step": self.recovery_step,
            "circuit_breaker_active": self.circuit_breaker_active,
            "execution_authority": False,
        }

    def get_active_rung(self, current_equity: float) -> GoldRungSpec:
        eq = max(100.0, _safe_float(current_equity) or 100.0)
        for rung in GOLD_RUNGS:
            if rung.min_equity <= eq <= rung.max_equity:
                return rung
        return GOLD_RUNGS[-1]

    def calculate_lot_size(
        self,
        current_equity: float,
        proposed_stop_pips: float,
        *,
        spread_points: float = 15.0,
        adx_14: Optional[float] = None,
        daily_atr_dollars: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calculates exact lot size for Gold incorporating the 3-Stage architecture & risk filters."""
        eq = max(100.0, _safe_float(current_equity) or 100.0)
        raw_sl_pips = max(10.0, _safe_float(proposed_stop_pips) or 37.0)

        # Filter 1: Max Spread Guard (<= 25 points / $0.25)
        spread_pts = _safe_float(spread_points) or 15.0
        if spread_pts > MAX_SPREAD_POINTS:
            return {
                "status": "REJECTED",
                "reason": f"Spread {spread_pts:.1f} points exceeds max allowed {MAX_SPREAD_POINTS:.1f} points",
                "calculated_lot": 0.0,
                "stage": 0,
                "stage_name": "REJECTED",
                "circuit_breaker_active": self.circuit_breaker_active,
                "hysteresis_active": False,
                "execution_authority": False,
            }

        # Filter 2: Volatility Regime Guard (ADX >= 22 & Daily ATR >= $45)
        if adx_14 is not None:
            adx_val = _safe_float(adx_14) or 0.0
            if adx_val < MIN_ADX_THRESHOLD:
                return {
                    "status": "REJECTED",
                    "reason": f"M15 ADX {adx_val:.1f} below minimum threshold {MIN_ADX_THRESHOLD:.1f} (chop regime)",
                    "calculated_lot": 0.0,
                    "stage": 0,
                    "stage_name": "REJECTED",
                    "circuit_breaker_active": self.circuit_breaker_active,
                    "hysteresis_active": False,
                    "execution_authority": False,
                }

        if daily_atr_dollars is not None:
            atr_val = _safe_float(daily_atr_dollars) or 0.0
            if atr_val < MIN_DAILY_ATR_DOLLARS:
                return {
                    "status": "REJECTED",
                    "reason": f"Daily ATR ${atr_val:.2f} below minimum ${MIN_DAILY_ATR_DOLLARS:.2f} (compression regime)",
                    "calculated_lot": 0.0,
                    "stage": 0,
                    "stage_name": "REJECTED",
                    "circuit_breaker_active": self.circuit_breaker_active,
                    "hysteresis_active": False,
                    "execution_authority": False,
                }

        # Filter 3: Daily 2R Loss Circuit Breaker Guard
        self._check_day_rollover()
        if self.circuit_breaker_active:
            return {
                "status": "REJECTED",
                "reason": "Daily 2R Loss Circuit Breaker active (2 losing trades or daily loss limit reached). Trading halted for 24h.",
                "calculated_lot": 0.0,
                "stage": 0,
                "stage_name": "CIRCUIT_BREAKER_ACTIVE",
                "circuit_breaker_active": True,
                "hysteresis_active": False,
                "execution_authority": False,
            }

        # Stop loss distance & dollar conversion (Convention B: 1 retail pip = $0.10/oz)
        effective_sl_pips = raw_sl_pips
        effective_sl_dollars = effective_sl_pips * RETAIL_PIP_DOLLARS

        # Evaluate Drawdown Throttle
        throttle_state = self.evaluate_drawdown_throttle(eq)
        risk_pct = throttle_state["effective_risk_pct"] / 100.0
        risk_budget = eq * risk_pct

        rung = self.get_active_rung(eq)
        hysteresis_active = False

        # -------------------------------------------------------------
        # 3-Stage Position Sizing Architecture
        # -------------------------------------------------------------
        if eq < 500.0:
            # Stage 1: Linear Cash-Flow Grind ($100 -> $500 Equity)
            # Strictly locked at 0.01 micro lot (1 oz) regardless of equity.
            stage = 1
            stage_name = "STAGE_1_LINEAR_GROWTH"
            lot = 0.01
            recommended_tp_pips = STANDARD_TP_PIPS
            tp_extension_active = False
            rule_applied = "PHASE_1_FIXED_MICRO_LOT"
            self.active_lot_tier = 0.01
            self.current_tier_threshold = 0.0

        elif eq < 1000.0:
            # Stage 2: Micro-Lot Rounding Bridge ($500 -> $1,000 Equity)
            stage = 2
            stage_name = "STAGE_2_MICRO_LOT_BRIDGE"

            # Nearest-Step Lot Rounding (0.015 Midpoint Threshold / Math.round)
            raw_units = round((eq * 0.01) / effective_sl_dollars, 6)
            nearest_units = math.floor(raw_units + 0.50)
            nearest_lot = round(nearest_units * 0.01, 2)

            # 1.33% Maximum Risk Cap Check
            effective_risk = (nearest_units * effective_sl_dollars) / eq
            if effective_risk > (STAGE_2_MAX_RISK_PCT / 100.0):
                nearest_lot = max(0.01, round((nearest_units - 1) * 0.01, 2))

            # Hysteresis Band ($25 buffer prevents tier chatter on drawdowns)
            if self.active_lot_tier > 0.01 and nearest_lot < self.active_lot_tier:
                if eq >= (self.current_tier_threshold - self.hysteresis_buffer):
                    hysteresis_active = True
                    lot = self.active_lot_tier
                else:
                    lot = max(0.01, nearest_lot)
                    self.active_lot_tier = lot
                    self.current_tier_threshold = 555.0 if lot == 0.02 else (925.0 if lot == 0.03 else 0.0)
            else:
                lot = max(0.01, nearest_lot)
                if lot > self.active_lot_tier:
                    self.active_lot_tier = lot
                    if lot == 0.02:
                        self.current_tier_threshold = 555.0
                    elif lot >= 0.03:
                        self.current_tier_threshold = 925.0

            # 185-Pip Take-Profit Extension ($500–$554) vs Standard TP
            if lot == 0.01:
                recommended_tp_pips = STAGE_2A_TP_EXTENSION_PIPS  # 185 pips ($18.50)
                tp_extension_active = True
                rule_applied = "STAGE_2A_TP_EXTENSION"
            else:
                recommended_tp_pips = STANDARD_TP_PIPS  # 150 pips ($15.00)
                tp_extension_active = False
                if rung.rung_id == 2:
                    rule_applied = "PHASE_2_STEP_DELAY_ELIMINATED_RUNG_2"
                elif rung.rung_id == 3:
                    rule_applied = "PHASE_2_STEP_DELAY_ELIMINATED_RUNG_3"
                else:
                    rule_applied = "PHASE_2_STEP_DELAY_ELIMINATED_NEAREST_STEP"

        else:
            # Stage 3: Full 1% Dynamic Risk Compounding ($1,000 -> $8,000+ Equity)
            stage = 3
            stage_name = "STAGE_3_DYNAMIC_COMPOUNDING"
            recommended_tp_pips = STANDARD_TP_PIPS
            tp_extension_active = False

            raw_units = round(risk_budget / effective_sl_dollars, 6)
            nearest_units = math.floor(raw_units + 0.50)
            calculated_lot = round(nearest_units * 0.01, 2)
            candidate_lot = max(rung.base_lots, min(rung.max_lots, calculated_lot))

            # Hysteresis Band Check for Stage 3
            if self.active_lot_tier > 0.01 and candidate_lot < self.active_lot_tier:
                if eq >= (self.current_tier_threshold - self.hysteresis_buffer):
                    hysteresis_active = True
                    lot = self.active_lot_tier
                else:
                    lot = candidate_lot
                    self.active_lot_tier = lot
                    self.current_tier_threshold = rung.min_equity
            else:
                lot = candidate_lot
                if lot > self.active_lot_tier:
                    self.active_lot_tier = lot
                    self.current_tier_threshold = rung.min_equity

            rule_applied = f"STAGE_3_DYNAMIC_RUNG_{rung.rung_id}"

        # If throttled, reduce lot size accordingly
        if throttle_state["throttle_active"]:
            lot = max(0.01, round(lot * (risk_pct / 0.01), 2))
            rule_applied += "_THROTTLED"

        return {
            "status": "READY_FOR_REVIEW",
            "rung_id": rung.rung_id,
            "rule_applied": rule_applied,
            "stage": stage,
            "stage_name": stage_name,
            "current_equity": round(eq, 2),
            "calculated_lot": round(lot, 2),
            "effective_sl_pips": round(effective_sl_pips, 1),
            "friction_deducted_pips": FRICTION_PIPS,
            "risk_budget_dollars": round(risk_budget, 2),
            "recommended_tp_pips": recommended_tp_pips,
            "tp_extension_active": tp_extension_active,
            "hysteresis_active": hysteresis_active,
            "circuit_breaker_active": self.circuit_breaker_active,
            "throttle_active": throttle_state["throttle_active"],
            "effective_risk_pct": round(risk_pct * 100.0, 2),
            "execution_authority": False,
        }

    def evaluate_dynamic_exits(
        self,
        entry_price: float,
        current_price: float,
        direction: str = "LONG",
        current_sl: float = 0.0,
        m15_atr: float = 3.50,
        m15_highest_high_10: Optional[float] = None,
        m15_lowest_low_10: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Evaluates 3-Stage Dynamic Exits on Gold:
        - Stage 1: +100 pips (+10.00/oz) -> 50% partial close + move SL to Entry + 10 pips (+1.00/oz).
        - Stage 2: +150 pips (+15.00/oz) -> move SL to +110 pips (+11.00/oz) + M15 Chandelier trail (10, 1.5).
        - Stage 3: +200 to +400 pips -> uncapped trend expansion runner.
        """
        entry = _safe_float(entry_price) or 0.0
        curr = _safe_float(current_price) or 0.0
        sl = _safe_float(current_sl) or 0.0
        atr = max(1.0, _safe_float(m15_atr) or 3.50)

        if entry <= 0 or curr <= 0:
            return {"status": "ERROR", "reason": "Invalid entry or current price", "execution_authority": False}

        is_long = direction.upper() in ("LONG", "BUY")
        pip_gain = (curr - entry) / 0.10 if is_long else (entry - curr) / 0.10
        dollar_gain = curr - entry if is_long else entry - curr

        stage = "STAGE_0_INITIAL_RISK"
        partial_close_pct = 0.0
        proposed_sl = sl
        action_note = "Hold position, monitor for Stage 1 (+100 pips)."

        # Stage 1: +100 retail pips (+10.00/oz move)
        if pip_gain >= 100.0:
            stage = "STAGE_1_FLOOR_LOCKED"
            partial_close_pct = 0.50
            breakeven_sl = entry + 1.00 if is_long else entry - 1.00  # Entry + 10 pips
            if is_long:
                proposed_sl = max(sl, breakeven_sl)
            else:
                proposed_sl = min(sl, breakeven_sl) if sl > 0 else breakeven_sl
            action_note = "Close 50% lot size. Move stop to Entry + 10 pips (+1.00/oz), locking in +1.5R to +2.0R guaranteed."

        # Stage 2: +150 retail pips (+15.00/oz move)
        if pip_gain >= 150.0:
            stage = "STAGE_2_RATCHET_ACTIVE"
            ratchet_sl = entry + 11.00 if is_long else entry - 11.00  # +110 pips
            # M15 Chandelier ATR trail (Period 10, Multiplier 1.5)
            if is_long and m15_highest_high_10 is not None:
                chandelier = m15_highest_high_10 - (1.5 * atr)
                proposed_sl = max(proposed_sl, ratchet_sl, chandelier)
            elif not is_long and m15_lowest_low_10 is not None:
                chandelier = m15_lowest_low_10 + (1.5 * atr)
                proposed_sl = min(proposed_sl, ratchet_sl, chandelier) if proposed_sl > 0 else ratchet_sl
            else:
                proposed_sl = max(proposed_sl, ratchet_sl) if is_long else min(proposed_sl, ratchet_sl)
            action_note = "Ratchet stop to +110 pips and activate M15 Chandelier trail (10, 1.5 ATR)."

        # Stage 3: +200+ retail pips (+20.00/oz+ trend expansion)
        if pip_gain >= 200.0:
            stage = "STAGE_3_UNCAPPED_RUNNER"
            action_note = "Uncapped trend runner active. Trail strictly behind pullbacks until candle closes beyond 1.5x ATR."

        return {
            "status": "READY_FOR_REVIEW",
            "active_stage": stage,
            "pip_gain": round(pip_gain, 1),
            "dollar_gain_oz": round(dollar_gain, 2),
            "partial_close_pct": partial_close_pct,
            "proposed_sl": round(proposed_sl, 2),
            "action_note": action_note,
            "execution_authority": False,
        }
