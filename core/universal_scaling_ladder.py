"""Universal Multi-Asset Scaling Ladder & Per-Trader Goal Planning Engine.

Extends the institutional 3-Stage scaling architecture across ALL financial instruments:
- Forex (EURUSD, GBPUSD, USDJPY, AUDUSD, etc.)
- Precious Metals (XAUUSD Gold, XAGUSD Silver)
- Energy & Commodities (USOIL, UKOIL)
- Equity Indices (US30, NAS100, SPX500, GER40)
- Cryptocurrencies (BTCUSD, ETHUSD)

Key Capabilities:
1. 3-Stage Position Sizing & Compounding Architecture:
   - Stage 1: Linear Cash-Flow Grind ($100 to $500, or C_0 to 5*C_0) -> Fixed 0.01 micro lot.
   - Stage 2: Micro-Lot Rounding Bridge ($500 to $1,000, or 5*C_0 to 10*C_0) ->
     Nearest-step rounding (0.015 midpoint threshold) + 1.33% risk cap + TP extension.
   - Stage 3: Full 1% Dynamic Risk Compounding ($1,000 to $8,000 / $10,000+) -> Fine-grained 0.01 increments.
2. Hysteresis Band ($25 buffer / 3% band): Eliminates tier chatter on drawdowns.
3. Daily 2R Loss Circuit Breaker: Halts trading for 24h if 2 losing trades occur in a day.
4. Drawdown Throttle (3.9% / 4R trigger): Cuts risk to 0.50%, recovers via 0.75% -> 1.00%.
5. Per-Trader Goal Planning & Projection Engine:
   - Computes required net winning trades, win rate hurdles, and month-by-month milestones
     from small accounts ($100) to target wealth ($8,000 - $10,000+).

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib & math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.symbol_calibration import SymbolCalibrationEngine, SymbolSpec

EXECUTION_AUTHORITY = False
DEFAULT_HYSTERESIS_BUFFER = 25.0
DEFAULT_STAGE_2_MAX_RISK_PCT = 1.334
DRAWDOWN_THROTTLE_TRIGGER_PCT = 3.90
DAILY_CIRCUIT_BREAKER_MAX_LOSSES = 2


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class MilestoneScheduleItem:
    month_index: int
    month_name: str
    starting_equity: float
    ending_equity: float
    monthly_growth_pct: float
    active_lot_size: float
    cumulative_net_wins: float


@dataclass
class TraderGoalPlan:
    """Comprehensive milestone and progression roadmap for a trader."""
    trader_id: str
    symbol: str
    starting_equity: float
    target_equity: float
    time_horizon_months: int
    weekly_target_pips: float
    risk_per_trade_pct: float
    average_win_pips: float
    average_stop_pips: float

    # Calculated milestones
    total_net_winning_trades_required: float
    stage_1_net_wins: float
    stage_2_net_wins: float
    stage_3_net_wins: float
    required_net_wins_per_month: float
    required_net_wins_per_week: float
    monthly_schedule: List[MilestoneScheduleItem]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "READY_FOR_REVIEW",
            "trader_id": self.trader_id,
            "symbol": self.symbol,
            "starting_equity": round(self.starting_equity, 2),
            "target_equity": round(self.target_equity, 2),
            "time_horizon_months": self.time_horizon_months,
            "weekly_target_pips": round(self.weekly_target_pips, 1),
            "risk_per_trade_pct": round(self.risk_per_trade_pct, 2),
            "average_win_pips": round(self.average_win_pips, 1),
            "average_stop_pips": round(self.average_stop_pips, 1),
            "total_net_winning_trades_required": round(self.total_net_winning_trades_required, 2),
            "stage_1_net_wins": round(self.stage_1_net_wins, 2),
            "stage_2_net_wins": round(self.stage_2_net_wins, 2),
            "stage_3_net_wins": round(self.stage_3_net_wins, 2),
            "required_net_wins_per_month": round(self.required_net_wins_per_month, 2),
            "required_net_wins_per_week": round(self.required_net_wins_per_week, 2),
            "monthly_schedule": [
                {
                    "month": m.month_name,
                    "start_equity": round(m.starting_equity, 2),
                    "end_equity": round(m.ending_equity, 2),
                    "growth_pct": round(m.monthly_growth_pct, 1),
                    "active_lot_size": round(m.active_lot_size, 3),
                    "cumulative_net_wins": round(m.cumulative_net_wins, 2),
                }
                for m in self.monthly_schedule
            ],
            "execution_authority": False,
        }


class UniversalScalingLadder:
    """Multi-asset micro-account scaling engine and goal planner."""

    def __init__(
        self,
        symbol: str = "EURUSD",
        peak_equity: float = 100.0,
        hysteresis_buffer: float = DEFAULT_HYSTERESIS_BUFFER,
    ) -> None:
        self.symbol = symbol.upper()
        self.spec: SymbolSpec = SymbolCalibrationEngine.get_spec(self.symbol)
        self.peak_equity = max(100.0, float(peak_equity))
        self.hysteresis_buffer = max(5.0, float(hysteresis_buffer))

        # Throttle & recovery state
        self.throttle_active = False
        self.recovery_step = 0  # 0: normal, 1: 0.50%, 2: 0.75%

        # Hysteresis state machine
        self.active_lot_tier: float = self.spec.min_lot
        self.current_tier_threshold: float = 0.0

        # Daily 2R circuit breaker
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
        r_val = _safe_float(r_multiple) or 0.0
        pnl_val = _safe_float(pnl_dollars) or 0.0
        eq = _safe_float(current_equity) or 100.0
        self.update_high_water_mark(eq)
        self.record_daily_trade(r_val, pnl_val, now)

        if self.throttle_active:
            if r_val >= 2.0:
                if self.recovery_step == 1:
                    self.recovery_step = 2
                elif self.recovery_step == 2:
                    self.recovery_step = 0
                    self.throttle_active = False
            elif r_val < 0.0:
                self.recovery_step = 1

        return {
            "status": "READY_FOR_REVIEW",
            "r_multiple": r_val,
            "throttle_active": self.throttle_active,
            "recovery_step": self.recovery_step,
            "circuit_breaker_active": self.circuit_breaker_active,
            "execution_authority": False,
        }

    def calculate_lot_size(
        self,
        current_equity: float,
        proposed_stop_pips: float,
        *,
        spread_pips: Optional[float] = None,
        adx_14: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Calculates precise position sizing across any symbol using the 3-stage architecture."""
        eq = max(10.0, _safe_float(current_equity) or 100.0)
        stop_pips = max(5.0, _safe_float(proposed_stop_pips) or 30.0)

        # Spread filter
        spread = float(spread_pips) if spread_pips is not None else 1.0
        if spread > self.spec.max_spread_pips:
            return {
                "status": "REJECTED",
                "reason": f"Spread {spread:.1f} pips exceeds {self.symbol} max limit {self.spec.max_spread_pips:.1f} pips",
                "calculated_lot": 0.0,
                "stage": 0,
                "circuit_breaker_active": self.circuit_breaker_active,
                "hysteresis_active": False,
                "execution_authority": False,
            }

        # Daily 2R circuit breaker
        self._check_day_rollover()
        if self.circuit_breaker_active:
            return {
                "status": "REJECTED",
                "reason": "Daily 2R Loss Circuit Breaker active (2 losing trades or daily loss limit reached). Trading halted for 24h.",
                "calculated_lot": 0.0,
                "stage": 0,
                "circuit_breaker_active": True,
                "hysteresis_active": False,
                "execution_authority": False,
            }

        # Dollar risk per 0.01 micro lot
        pip_val_micro = self.spec.pip_value_usd_per_standard_lot * 0.01
        sl_dollars_per_micro_lot = stop_pips * pip_val_micro

        # Evaluate Drawdown Throttle
        throttle_state = self.evaluate_drawdown_throttle(eq)
        risk_pct = throttle_state["effective_risk_pct"] / 100.0
        risk_budget = eq * risk_pct

        hysteresis_active = False

        # -------------------------------------------------------------
        # Universal 3-Stage Position Sizing Architecture
        # -------------------------------------------------------------
        # 1. Hysteresis Check: If account achieved a higher tier, hold it within the buffer
        if self.active_lot_tier > self.spec.min_lot and eq >= (self.current_tier_threshold - self.hysteresis_buffer):
            hysteresis_active = True
            lot = self.active_lot_tier
            stage = 2
            stage_name = "STAGE_2_MICRO_LOT_BRIDGE"
            recommended_tp_pips = stop_pips * 4.0
            tp_extension_active = False
            rule_applied = "STAGE_2_HYSTERESIS_ACTIVE"

        elif eq < 500.0:
            # Stage 1: Linear Cash-Flow Grind ($100 -> $500)
            stage = 1
            stage_name = "STAGE_1_LINEAR_GROWTH"
            lot = self.spec.min_lot
            recommended_tp_pips = stop_pips * 4.0  # 1:4 R:R target
            tp_extension_active = False
            rule_applied = "PHASE_1_FIXED_MICRO_LOT"
            self.active_lot_tier = self.spec.min_lot
            self.current_tier_threshold = 0.0

        elif eq < 1000.0:
            # Stage 2: Micro-Lot Rounding Bridge ($500 -> $1,000)
            stage = 2
            stage_name = "STAGE_2_MICRO_LOT_BRIDGE"

            # Nearest-Step Lot Rounding (0.015 Midpoint Threshold)
            raw_units = round((eq * 0.01) / max(0.01, sl_dollars_per_micro_lot), 6)
            nearest_units = math.floor(raw_units + 0.50)
            nearest_lot = round(nearest_units * 0.01, 2)

            # 1.33% Risk Cap Check
            effective_risk = (nearest_units * sl_dollars_per_micro_lot) / eq
            if effective_risk > (DEFAULT_STAGE_2_MAX_RISK_PCT / 100.0):
                nearest_lot = max(self.spec.min_lot, round((nearest_units - 1) * 0.01, 2))

            # Upgrade threshold for 0.02 lots
            threshold_02 = (1.50 * sl_dollars_per_micro_lot) / 0.01

            lot = max(self.spec.min_lot, nearest_lot)
            if lot > self.active_lot_tier:
                self.active_lot_tier = lot
                if lot == 0.02:
                    self.current_tier_threshold = threshold_02

            if lot == self.spec.min_lot:
                recommended_tp_pips = stop_pips * 5.0  # TP extension to cross threshold quickly
                tp_extension_active = True
                rule_applied = "STAGE_2A_TP_EXTENSION"
            else:
                recommended_tp_pips = stop_pips * 4.0
                tp_extension_active = False
                rule_applied = "STAGE_2B_STEP_DELAY_ELIMINATED_NEAREST_STEP"

        else:
            # Stage 3: Full 1% Dynamic Risk Compounding ($1,000+)
            stage = 3
            stage_name = "STAGE_3_DYNAMIC_COMPOUNDING"
            recommended_tp_pips = stop_pips * 4.0
            tp_extension_active = False

            raw_units = round(risk_budget / max(0.01, sl_dollars_per_micro_lot), 6)
            nearest_units = math.floor(raw_units + 0.50)
            calculated_lot = round(nearest_units * 0.01, 2)
            candidate_lot = max(self.spec.min_lot, min(self.spec.max_lot, calculated_lot))

            # Hysteresis Band Check for Stage 3
            if self.active_lot_tier > self.spec.min_lot and candidate_lot < self.active_lot_tier:
                if eq >= (self.current_tier_threshold - self.hysteresis_buffer):
                    hysteresis_active = True
                    lot = self.active_lot_tier
                else:
                    lot = candidate_lot
                    self.active_lot_tier = lot
                    self.current_tier_threshold = eq
            else:
                lot = candidate_lot
                if lot > self.active_lot_tier:
                    self.active_lot_tier = lot
                    self.current_tier_threshold = eq

            rule_applied = "STAGE_3_DYNAMIC_COMPOUNDING"

        # Apply drawdown throttle cut if active
        if throttle_state["throttle_active"]:
            lot = max(self.spec.min_lot, round(lot * (risk_pct / 0.01), 2))
            rule_applied += "_THROTTLED"

        return {
            "status": "READY_FOR_REVIEW",
            "symbol": self.symbol,
            "rule_applied": rule_applied,
            "stage": stage,
            "stage_name": stage_name,
            "current_equity": round(eq, 2),
            "calculated_lot": round(lot, 2),
            "stop_pips": round(stop_pips, 1),
            "risk_budget_dollars": round(risk_budget, 2),
            "dollar_risk_at_stop": round((lot / 0.01) * sl_dollars_per_micro_lot, 2),
            "recommended_tp_pips": round(recommended_tp_pips, 1),
            "tp_extension_active": tp_extension_active,
            "hysteresis_active": hysteresis_active,
            "circuit_breaker_active": self.circuit_breaker_active,
            "throttle_active": throttle_state["throttle_active"],
            "effective_risk_pct": round(risk_pct * 100.0, 2),
            "execution_authority": False,
        }

    @classmethod
    def create_trader_goal_plan(
        cls,
        trader_id: str,
        symbol: str = "EURUSD",
        starting_equity: float = 100.0,
        target_equity: float = 8000.0,
        time_horizon_months: int = 8,
        average_win_pips: float = 120.0,
        average_stop_pips: float = 30.0,
        weekly_target_pips: float = 170.0,
    ) -> TraderGoalPlan:
        """Generates an end-to-end month-by-month compounding execution roadmap."""
        c0 = max(50.0, float(starting_equity))
        c_target = max(c0 * 2.0, float(target_equity))
        spec = SymbolCalibrationEngine.get_spec(symbol)
        pip_val_micro = spec.pip_value_usd_per_standard_lot * 0.01

        # Dollar profit per win in Stage 1 (0.01 lot)
        profit_per_win_s1 = average_win_pips * pip_val_micro
        stage_1_target = min(c_target, max(c0, 500.0))
        stage_1_net_wins = (stage_1_target - c0) / max(0.50, profit_per_win_s1)

        # Stage 2: $500 -> $1,000 (average lot 0.02)
        stage_2_target = min(c_target, max(stage_1_target, 1000.0))
        profit_per_win_s2 = average_win_pips * pip_val_micro * 2.0
        stage_2_net_wins = (stage_2_target - stage_1_target) / max(1.0, profit_per_win_s2)

        # Stage 3: Full 1% compounding from $1,000 to target
        # R-multiple per win = average_win_pips / average_stop_pips (e.g. 120/30 = 4.0R = +4.0% per trade)
        r_mult = max(1.5, average_win_pips / max(5.0, average_stop_pips))
        growth_per_trade = 0.01 * r_mult  # e.g. 0.04 (4%)
        if c_target > stage_2_target:
            stage_3_net_wins = math.log(c_target / stage_2_target) / math.log(1.0 + growth_per_trade)
        else:
            stage_3_net_wins = 0.0

        total_net_wins = stage_1_net_wins + stage_2_net_wins + stage_3_net_wins
        months = max(1, int(time_horizon_months))
        net_wins_per_month = total_net_wins / months
        net_wins_per_week = net_wins_per_month / 4.33

        # Generate month-by-month schedule
        monthly_schedule: List[MilestoneScheduleItem] = []
        month_names = ["Month 1", "Month 2", "Month 3", "Month 4", "Month 5", "Month 6", "Month 7", "Month 8", "Month 9", "Month 10", "Month 11", "Month 12"]
        current_eq = c0
        cum_wins = 0.0

        for m_idx in range(months):
            m_start = current_eq
            wins_this_month = net_wins_per_month
            cum_wins += wins_this_month

            # Approximate lot size active this month
            if current_eq < 500.0:
                m_lot = 0.01
                profit = wins_this_month * profit_per_win_s1
                current_eq += profit
            elif current_eq < 1000.0:
                m_lot = 0.02
                profit = wins_this_month * profit_per_win_s2
                current_eq += profit
            else:
                m_lot = round((current_eq * 0.01) / (average_stop_pips * pip_val_micro * 100.0), 2)
                m_lot = max(0.03, m_lot)
                current_eq = current_eq * ((1.0 + growth_per_trade) ** wins_this_month)

            m_end = min(c_target * 1.25, current_eq)
            growth_pct = ((m_end - m_start) / m_start) * 100.0 if m_start > 0 else 0.0
            name = month_names[m_idx] if m_idx < len(month_names) else f"Month {m_idx + 1}"

            monthly_schedule.append(
                MilestoneScheduleItem(
                    month_index=m_idx + 1,
                    month_name=name,
                    starting_equity=m_start,
                    ending_equity=m_end,
                    monthly_growth_pct=growth_pct,
                    active_lot_size=m_lot,
                    cumulative_net_wins=cum_wins,
                )
            )

        return TraderGoalPlan(
            trader_id=trader_id,
            symbol=symbol,
            starting_equity=c0,
            target_equity=c_target,
            time_horizon_months=months,
            weekly_target_pips=weekly_target_pips,
            risk_per_trade_pct=1.0,
            average_win_pips=average_win_pips,
            average_stop_pips=average_stop_pips,
            total_net_winning_trades_required=total_net_wins,
            stage_1_net_wins=stage_1_net_wins,
            stage_2_net_wins=stage_2_net_wins,
            stage_3_net_wins=stage_3_net_wins,
            required_net_wins_per_month=net_wins_per_month,
            required_net_wins_per_week=net_wins_per_week,
            monthly_schedule=monthly_schedule,
        )
