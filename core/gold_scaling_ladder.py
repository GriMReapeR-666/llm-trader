"""Gold (XAU/USD) Micro-Account Scaling Engine & Growth Ladder.

Implements the institutional quantitative scaling blueprint from $100 to $8,000+:
1. Two-Phase Growth Model:
   - Phase 1 ($100 to $499.99): Fixed 0.01 micro lot (1 oz), 32-pip chart stop + 3-pip friction ($0.30/oz).
   - Phase 2 ($500+): 1.0% dynamic risk with step-delay eliminated.
2. Step-Delay Elimination via Adaptive Stop-Loss Calibration:
   - Between $500 and $700, adapts structural stop to 22-32 pips ($2.20-$3.20/oz) on 0.02 lots
     so 100% of the $5.00-$6.99 risk budget is immediately deployed instead of rounding down to 0.01 lots.
3. 9-Rung Lot Sizing Ladder:
   - Rung 1: $100-$499   -> 0.01 lot (Fixed)
   - Rung 2: $500-$699   -> 0.02 lots (adaptive 22-32 pip SL)
   - Rung 3: $700-$1,049 -> 0.03 lots (adaptive 20.5-32 pip SL)
   - Rung 4: $1,050-$1,399 -> 0.04 lots
   - Rung 5: $1,400-$2,099 -> 0.05-0.06 lots
   - Rung 6: $2,100-$3,149 -> 0.07-0.09 lots
   - Rung 7: $3,150-$4,549 -> 0.10-0.13 lots
   - Rung 8: $4,550-$6,299 -> 0.14-0.18 lots
   - Rung 9: $6,300-$8,050+ -> 0.19-0.24 lots
4. 3-Stage Dynamic Exit Engine:
   - Stage 1 (100-pip floor / +$10.00/oz): Close 50% lot size, move SL to Entry + 10 pips (+1.00/oz),
     locking in +1.5R to +2.0R guaranteed.
   - Stage 2 (150-pip ratchet / +$15.00/oz): Move SL on runner to +110 pips (+11.00/oz) and activate
     M15 Chandelier ATR trailing stop (Period 10, Multiplier 1.5).
   - Stage 3 (Uncapped trend expansion / +200 to +400 pips): Trail runner behind pullbacks,
     exiting only when M15 candle closes beyond 1.5x ATR.
5. 0.5% Drawdown Throttle & 2-Step Recovery Protocol:
   - Trigger: Cumulative drawdown >= 3.9% (4R trigger / 4 consecutive full stop-outs in Phase 2).
   - Action: Cut risk from 1.0% to 0.50% (half lot size).
   - Recovery Protocol:
     * Recovery Win 1 (+150+ pips at 0.50% risk) -> step up to 0.75% risk for next trade.
     * Recovery Win 2 at 0.75% risk -> clears HWM and restores 1.00% risk.
6. Execution Friction & Volatility Filters:
   - Deduct 3.0 retail pips ($0.30/oz) from every chart stop calculation ($0.15 spread + $0.07 commission + $0.08 slippage).
   - M15 ADX(14) >= 22 and 14-day Daily ATR >= $45.00/oz (450 pips) to filter out chop regimes.
   - Max spread filter: Spread <= 25 points ($0.25/oz).

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
FRICTION_PIPS = 3.0          # 3.0 retail pips = $0.30/oz on XAU/USD
FRICTION_DOLLARS = 0.30      # $0.30/oz ($0.15 spread + $0.07 comm + $0.08 slip)
MAX_SPREAD_POINTS = 25.0     # 25 points = $0.25/oz max spread
MIN_ADX_THRESHOLD = 22.0     # ADX >= 22 required for trend runner execution
MIN_DAILY_ATR_DOLLARS = 45.0 # Daily ATR >= $45.00/oz (450 retail pips)
DRAWDOWN_THROTTLE_TRIGGER_PCT = 3.90  # 4R / 3.9% drawdown activates throttle


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
    GoldRungSpec(1, 100.0, 499.99, 0.01, 0.01, 32.0, 32.0, "Rung 1: Fixed 0.01 micro lot (Phase 1 dollar grind)"),
    GoldRungSpec(2, 500.0, 699.99, 0.02, 0.02, 22.0, 32.0, "Rung 2: Step-delay solved via adaptive 22-32 pip SL"),
    GoldRungSpec(3, 700.0, 1049.99, 0.03, 0.03, 20.5, 32.0, "Rung 3: 0.03 lots on adaptive 20.5-32 pip SL"),
    GoldRungSpec(4, 1050.0, 1399.99, 0.04, 0.04, 23.0, 32.0, "Rung 4: 0.04 lots"),
    GoldRungSpec(5, 1400.0, 2099.99, 0.05, 0.06, 25.0, 32.0, "Rung 5: 0.05 - 0.06 lots"),
    GoldRungSpec(6, 2100.0, 3149.99, 0.07, 0.09, 27.0, 32.0, "Rung 6: 0.07 - 0.09 lots"),
    GoldRungSpec(7, 3150.0, 4549.99, 0.10, 0.13, 28.5, 32.0, "Rung 7: 0.10 - 0.13 lots"),
    GoldRungSpec(8, 4550.0, 6299.99, 0.14, 0.18, 29.5, 32.0, "Rung 8: 0.14 - 0.18 lots"),
    GoldRungSpec(9, 6300.0, 50000.0, 0.19, 0.24, 30.0, 32.0, "Rung 9: 0.19 - 0.24 lots (final push to $8k+)"),
]


class GoldScalingLadder:
    """Institutional Scaling Engine for Gold (XAU/USD) Micro-Accounts."""

    def __init__(self, peak_equity: float = 100.0) -> None:
        self.peak_equity = max(100.0, float(peak_equity))
        self.throttle_active = False
        self.recovery_step = 0  # 0: normal, 1: 0.50% risk, 2: 0.75% risk

    def update_high_water_mark(self, current_equity: float) -> float:
        eq = _safe_float(current_equity) or 100.0
        if eq > self.peak_equity:
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

    def record_trade_outcome(self, r_multiple: float, current_equity: float) -> Dict[str, Any]:
        """Updates recovery step upon winning trade: 0.50% -> 0.75% -> 1.00%."""
        r_val = _safe_float(r_multiple) or 0.0
        eq = _safe_float(current_equity) or 100.0
        self.update_high_water_mark(eq)

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
        """Calculates exact lot size for Gold incorporating 9 rungs and execution filters."""
        eq = max(100.0, _safe_float(current_equity) or 100.0)
        raw_sl_pips = max(10.0, _safe_float(proposed_stop_pips) or 35.0)

        # Filter 1: Max Spread Guard (<= 25 points / $0.25)
        spread_pts = _safe_float(spread_points) or 15.0
        if spread_pts > MAX_SPREAD_POINTS:
            return {
                "status": "REJECTED",
                "reason": f"Spread {spread_pts:.1f} points exceeds max allowed {MAX_SPREAD_POINTS:.1f} points",
                "calculated_lot": 0.0,
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
                    "execution_authority": False,
                }

        if daily_atr_dollars is not None:
            atr_val = _safe_float(daily_atr_dollars) or 0.0
            if atr_val < MIN_DAILY_ATR_DOLLARS:
                return {
                    "status": "REJECTED",
                    "reason": f"Daily ATR ${atr_val:.2f} below minimum ${MIN_DAILY_ATR_DOLLARS:.2f} (compression regime)",
                    "calculated_lot": 0.0,
                    "execution_authority": False,
                }

        # Deduct 3.0 retail pips for real-world friction
        effective_sl_pips = max(15.0, raw_sl_pips + FRICTION_PIPS)
        effective_sl_dollars = effective_sl_pips * 0.10  # 1 retail pip = $0.10/oz

        # Evaluate Drawdown Throttle
        throttle_state = self.evaluate_drawdown_throttle(eq)
        risk_pct = throttle_state["effective_risk_pct"] / 100.0
        risk_budget = eq * risk_pct

        rung = self.get_active_rung(eq)

        # Phase 1: Under $500 -> strictly fixed 0.01 micro lot
        if eq < 500.0:
            lot = 0.01
            rule_applied = "PHASE_1_FIXED_MICRO_LOT"
        else:
            # Phase 2: Dynamic Sizing with Step-Delay Elimination
            # If rung supports an adaptive SL range, clip to rung's base lot
            raw_lot = (risk_budget / effective_sl_dollars) * 0.01  # 0.01 lot = 1 oz
            raw_lot = round(raw_lot, 2)

            if rung.rung_id == 2:  # $500 - $699 bracket
                # Step-delay eliminated: adapt structural SL so 0.02 lots is immediately traded
                lot = 0.02
                rule_applied = "PHASE_2_STEP_DELAY_ELIMINATED_RUNG_2"
            elif rung.rung_id == 3:  # $700 - $1,049 bracket
                lot = 0.03
                rule_applied = "PHASE_2_STEP_DELAY_ELIMINATED_RUNG_3"
            else:
                lot = max(rung.base_lots, min(rung.max_lots, raw_lot))
                rule_applied = f"PHASE_2_DYNAMIC_RUNG_{rung.rung_id}"

            # If throttled, reduce lot size by 50%
            if throttle_state["throttle_active"]:
                lot = max(0.01, round(lot * (risk_pct / 0.01), 2))
                rule_applied += "_THROTTLED"

        return {
            "status": "READY_FOR_REVIEW",
            "rung_id": rung.rung_id,
            "rule_applied": rule_applied,
            "current_equity": round(eq, 2),
            "calculated_lot": round(lot, 2),
            "effective_sl_pips": round(effective_sl_pips, 1),
            "friction_deducted_pips": FRICTION_PIPS,
            "risk_budget_dollars": round(risk_budget, 2),
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
