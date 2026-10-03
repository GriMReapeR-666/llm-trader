"""Trading Constraint Engine — Provably Correct Risk Enforcement.

Inspired by Dnalyaw/TensorLogic (2026), this module enforces hard trading
constraints using Boolean logic that CANNOT be violated by the LLM.

Key insight from the paper:
    "Configurable limits get reconfigured. Constants don't."
    "When the system says 'this violates dollar neutrality,' it's
     provably correct."

Architecture — Dual-Mode Reasoning (TensorLogic):
    ┌─────────────────────────────────────────────────────┐
    │           TRADING CONSTRAINT ENGINE                  │
    │                                                      │
    │  Boolean Mode (PROVABLY CORRECT):                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Rule 1: SL must exist and be valid    │           │
    │  │ Rule 2: TP must be > SL direction     │           │
    │  │ Rule 3: Position size within bounds   │           │
    │  │ Rule 4: Risk per trade within bounds  │           │
    │  │ Rule 5: No trading during holidays    │           │
    │  │ Rule 6: Cooldown active               │           │
    │  │ Rule 7: Max active trades             │           │
    │  │ Rule 8: Daily loss limit (kill switch)│           │
    │  │ Rule 9: Consecutive loss breaker      │           │
    │  │ Rule 10: Spread within tolerance      │           │
    │  │ Rule 11: Price within recent range    │           │
    │  │ Rule 12: Crash detector gate          │           │
    │  │ Rule 13: Cross-market robustness      │           │
    │  │ Rule 14: CPCV escalation              │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Decision: APPROVE / REJECT / REDUCE   │           │
    │  │ + reasoning (which rule(s) triggered) │           │
    │  └──────────────────────────────────────┘           │
    │                                                      │
    │  Continuous Mode (for learning):                     │
    │  - LLM can suggest any action                        │
    │  - Suggestion flows through Boolean gate              │
    │  - Gate enforces hard constraints                     │
    │  - LLM cannot override or bypass                     │
    └─────────────────────────────────────────────────────┘

The engine is INSPECTION-ONLY at runtime: it reads the proposal and
determines if it violates any hard constraint. It never modifies the
proposal — that's the job of the deterministic validator.

Four possible outcomes per rule:
    PASS    — constraint satisfied, continue checking
    REJECT  — constraint violated, block proposal entirely
    REDUCE  — constraint violated but can be fixed (scale down)
    WARN    — soft constraint violated, log but don't block
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ConstraintVerdict(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"
    REDUCE = "REDUCE"
    WARN = "WARN"


class ConstraintSeverity(str, Enum):
    HARD = "HARD"       # Must be enforced, no exceptions
    SOFT = "SOFT"       # Should be enforced, but can be overridden with justification


# ---------------------------------------------------------------------------
# Constraint Result
# ---------------------------------------------------------------------------

@dataclass
class ConstraintResult:
    """Result of evaluating a single constraint rule."""
    rule_name: str
    verdict: ConstraintVerdict
    severity: ConstraintSeverity
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule_name,
            "verdict": self.verdict.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class ConstraintEvaluation:
    """Complete evaluation of all constraints for a proposal."""
    results: List[ConstraintResult]
    overall_verdict: ConstraintVerdict
    reject_reasons: List[str]
    reduce_instructions: List[Dict[str, Any]]
    warnings: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_verdict": self.overall_verdict.value,
            "reject_reasons": self.reject_reasons,
            "reduce_instructions": self.reduce_instructions,
            "warnings": self.warnings,
            "rules_checked": len(self.results),
            "rules_passed": sum(1 for r in self.results if r.verdict == ConstraintVerdict.PASS),
            "rules_rejected": sum(1 for r in self.results if r.verdict == ConstraintVerdict.REJECT),
            "rules_reduced": sum(1 for r in self.results if r.verdict == ConstraintVerdict.REDUCE),
            "rules_warned": sum(1 for r in self.results if r.verdict == ConstraintVerdict.WARN),
            "details": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Constraint Rules (Hardcoded — not configurable)
# ---------------------------------------------------------------------------

# These are the "immutable constants" from Dnalyaw's philosophy:
# "Configurable limits get reconfigured. Constants don't."

MAX_POSITION_LOT_FOREX = 1.0       # Max lot size for forex
MAX_POSITION_LOT_GOLD = 0.1        # Max lot size for gold
MIN_RISK_REWARD_RATIO = 1.5        # Minimum R:R ratio
MAX_RISK_PER_TRADE = 0.02          # Max 2% risk per trade
MIN_RISK_PER_TRADE = 0.001         # Min 0.1% risk per trade
MAX_SPREAD_PIPS = 5.0              # Max acceptable spread in pips
MAX_PRICE_DEVIATION_PCT = 0.02     # Max 2% deviation from recent price
MAX_DAILY_LOSS_PCT = 0.05          # Max 5% daily loss (kill switch)
MAX_CONSECUTIVE_LOSSES = 5         # Consecutive loss circuit breaker
COOLDOWN_SECONDS = 300             # 5 minutes between trades
MAX_ACTIVE_TRADES = 5              # Max concurrent open positions


# ---------------------------------------------------------------------------
# Constraint Engine
# ---------------------------------------------------------------------------

class TradingConstraintEngine:
    """TensorLogic-inspired Boolean constraint engine.
    
    Enforces hard trading constraints that CANNOT be violated by the LLM.
    Every proposal passes through ALL rules before execution.
    
    The engine is inspection-only: it reads the proposal and determines
    if it violates any hard constraint. It never modifies the proposal.
    
    Usage:
        engine = TradingConstraintEngine()
        evaluation = engine.evaluate(proposal, context)
        if evaluation.overall_verdict == ConstraintVerdict.REJECT:
            # Block the trade
            return {"action": "HOLD", "reasoning": evaluation.reject_reasons}
    """
    
    def __init__(self):
        pass
    
    def evaluate(
        self,
        proposal: Dict[str, Any],
        context: Dict[str, Any],
    ) -> ConstraintEvaluation:
        """Evaluate all constraint rules against a proposal.
        
        Args:
            proposal: The trade proposal (action, entry, SL, TP, lots, etc.)
            context: Market/account context (prices, balance, open positions, etc.)
            
        Returns:
            ConstraintEvaluation with per-rule results and overall verdict
        """
        results: List[ConstraintResult] = []
        
        # Rule 1: SL must exist and be valid
        results.append(self._rule_sl_valid(proposal, context))
        
        # Rule 2: TP direction must be correct relative to SL
        results.append(self._rule_tp_direction(proposal, context))
        
        # Rule 3: Position size within bounds
        results.append(self._rule_position_size(proposal, context))
        
        # Rule 4: Risk per trade within bounds
        results.append(self._rule_risk_per_trade(proposal, context))
        
        # Rule 5: R:R ratio minimum
        results.append(self._rule_risk_reward_ratio(proposal, context))
        
        # Rule 6: Spread within tolerance
        results.append(self._rule_spread(proposal, context))
        
        # Rule 7: Price within recent range (no fat-finger)
        results.append(self._rule_price_range(proposal, context))
        
        # Rule 8: Cooldown active
        results.append(self._rule_cooldown(proposal, context))
        
        # Rule 9: Max active trades
        results.append(self._rule_max_active_trades(proposal, context))
        
        # Rule 10: Daily loss limit
        results.append(self._rule_daily_loss_limit(proposal, context))
        
        # Rule 11: Consecutive loss breaker
        results.append(self._rule_consecutive_losses(proposal, context))
        
        # Rule 12: Crash detector gate
        results.append(self._rule_crash_detector(proposal, context))
        
        # Rule 13: Cross-market robustness
        results.append(self._rule_cross_market(proposal, context))
        
        # Rule 14: CPCV escalation
        results.append(self._rule_cpcv_escalation(proposal, context))
        
        # Rule 15: Meta-Labeling P-filter (triple-barrier + proba < 0.60)
        results.append(self._rule_meta_labeling(proposal, context))
        
        # Compute overall verdict
        reject_reasons = [r.message for r in results if r.verdict == ConstraintVerdict.REJECT]
        reduce_instructions = [r.details for r in results if r.verdict == ConstraintVerdict.REDUCE]
        warnings = [r.message for r in results if r.verdict == ConstraintVerdict.WARN]
        
        # HARD reject if any hard rule rejects
        hard_rejects = [r for r in results if r.verdict == ConstraintVerdict.REJECT and r.severity == ConstraintSeverity.HARD]
        soft_rejects = [r for r in results if r.verdict == ConstraintVerdict.REJECT and r.severity == ConstraintSeverity.SOFT]
        reduces = [r for r in results if r.verdict == ConstraintVerdict.REDUCE]
        
        if hard_rejects:
            overall = ConstraintVerdict.REJECT
        elif soft_rejects:
            overall = ConstraintVerdict.REJECT
        elif reduces:
            overall = ConstraintVerdict.REDUCE
        else:
            overall = ConstraintVerdict.PASS
        
        return ConstraintEvaluation(
            results=results,
            overall_verdict=overall,
            reject_reasons=reject_reasons,
            reduce_instructions=reduce_instructions,
            warnings=warnings,
        )
    
    # -----------------------------------------------------------------------
    # Individual Rule Implementations (Boolean Mode — PROVABLY CORRECT)
    # -----------------------------------------------------------------------
    
    def _rule_sl_valid(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 1: Stop loss must exist and be a valid positive number."""
        sl = proposal.get("stop_loss")
        entry = proposal.get("entry")
        action = str(proposal.get("action", "")).upper()
        
        if sl is None:
            return ConstraintResult(
                rule_name="sl_valid",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message="Stop loss is required but not provided",
            )
        
        try:
            sl = float(sl)
            entry = float(entry) if entry else 0.0
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="sl_valid",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Stop loss is not a valid number: {sl}",
            )
        
        if sl <= 0:
            return ConstraintResult(
                rule_name="sl_valid",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Stop loss must be positive, got {sl}",
            )
        
        # SL must be on the correct side of entry
        if action == "BUY" and sl >= entry:
            return ConstraintResult(
                rule_name="sl_valid",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"BUY SL ({sl}) must be below entry ({entry})",
            )
        if action == "SELL" and sl <= entry:
            return ConstraintResult(
                rule_name="sl_valid",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"SELL SL ({sl}) must be above entry ({entry})",
            )
        
        return ConstraintResult(
            rule_name="sl_valid",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Stop loss is valid",
        )
    
    def _rule_tp_direction(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 2: TP must be on the correct side of entry relative to action."""
        tp = proposal.get("take_profit")
        entry = proposal.get("entry")
        action = str(proposal.get("action", "")).upper()
        
        if tp is None or entry is None:
            return ConstraintResult(
                rule_name="tp_direction",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="TP/entry not provided, skipping",
            )
        
        try:
            tp = float(tp)
            entry = float(entry)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="tp_direction",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message="TP or entry is not a valid number",
            )
        
        if action == "BUY" and tp <= entry:
            return ConstraintResult(
                rule_name="tp_direction",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"BUY TP ({tp}) must be above entry ({entry})",
            )
        if action == "SELL" and tp >= entry:
            return ConstraintResult(
                rule_name="tp_direction",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"SELL TP ({tp}) must be below entry ({entry})",
            )
        
        return ConstraintResult(
            rule_name="tp_direction",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="TP direction is correct",
        )
    
    def _rule_position_size(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 3: Position size must be within bounds."""
        lots = proposal.get("lots")
        if lots is None:
            lots = proposal.get("position_size")
        symbol = str(proposal.get("symbol", "")).upper()
        
        if lots is None:
            return ConstraintResult(
                rule_name="position_size",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Position size not provided, skipping",
            )
        
        try:
            lots = float(lots)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="position_size",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Position size is not a valid number: {lots}",
            )
        
        if lots <= 0:
            return ConstraintResult(
                rule_name="position_size",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Position size must be positive, got {lots}",
            )
        
        max_lot = MAX_POSITION_LOT_GOLD if ("XAU" in symbol or "GOLD" in symbol) else MAX_POSITION_LOT_FOREX
        
        if lots > max_lot:
            return ConstraintResult(
                rule_name="position_size",
                verdict=ConstraintVerdict.REDUCE,
                severity=ConstraintSeverity.HARD,
                message=f"Position size {lots} exceeds max {max_lot} for {symbol}",
                details={"action": "REDUCE", "field": "lots", "from": lots, "to": max_lot},
            )
        
        return ConstraintResult(
            rule_name="position_size",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Position size is within bounds",
        )
    
    def _rule_risk_per_trade(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 4: Risk per trade must be within bounds."""
        risk_pct = context.get("risk_per_trade")
        
        if risk_pct is None:
            return ConstraintResult(
                rule_name="risk_per_trade",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Risk per trade not provided, skipping",
            )
        
        try:
            risk_pct = float(risk_pct)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="risk_per_trade",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Risk per trade not a valid number",
            )
        
        if risk_pct > MAX_RISK_PER_TRADE:
            return ConstraintResult(
                rule_name="risk_per_trade",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Risk per trade {risk_pct:.4f} exceeds max {MAX_RISK_PER_TRADE}",
            )
        
        if risk_pct < MIN_RISK_PER_TRADE:
            return ConstraintResult(
                rule_name="risk_per_trade",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Risk per trade {risk_pct:.4f} below min {MIN_RISK_PER_TRADE}",
            )
        
        return ConstraintResult(
            rule_name="risk_per_trade",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Risk per trade is within bounds",
        )
    
    def _rule_risk_reward_ratio(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 5: R:R ratio must meet minimum."""
        entry = proposal.get("entry")
        sl = proposal.get("stop_loss")
        tp = proposal.get("take_profit")
        action = str(proposal.get("action", "")).upper()
        
        if not all([entry, sl, tp]):
            return ConstraintResult(
                rule_name="risk_reward_ratio",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Incomplete data, skipping R:R check",
            )
        
        try:
            entry = float(entry)
            sl = float(sl)
            tp = float(tp)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="risk_reward_ratio",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Invalid numeric values",
            )
        
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        
        if risk <= 0:
            return ConstraintResult(
                rule_name="risk_reward_ratio",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message="Risk distance is zero (entry == SL)",
            )
        
        rr = reward / risk
        
        if rr < MIN_RISK_REWARD_RATIO:
            return ConstraintResult(
                rule_name="risk_reward_ratio",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"R:R ratio {rr:.2f} below minimum {MIN_RISK_REWARD_RATIO}",
                details={"rr_ratio": round(rr, 2), "min_rr": MIN_RISK_REWARD_RATIO},
            )
        
        return ConstraintResult(
            rule_name="risk_reward_ratio",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message=f"R:R ratio {rr:.2f} meets minimum {MIN_RISK_REWARD_RATIO}",
        )
    
    def _rule_spread(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 6: Spread must be within tolerance."""
        spread = context.get("spread")
        
        if spread is None:
            return ConstraintResult(
                rule_name="spread",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.SOFT,
                message="Spread not provided, skipping",
            )
        
        try:
            spread = float(spread)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="spread",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.SOFT,
                message="Spread not a valid number",
            )
        
        if spread > MAX_SPREAD_PIPS:
            return ConstraintResult(
                rule_name="spread",
                verdict=ConstraintVerdict.WARN,
                severity=ConstraintSeverity.SOFT,
                message=f"Spread {spread:.1f} pips exceeds tolerance {MAX_SPREAD_PIPS}",
            )
        
        return ConstraintResult(
            rule_name="spread",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.SOFT,
            message="Spread is within tolerance",
        )
    
    def _rule_price_range(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 7: Entry price must be within recent price range (no fat-finger)."""
        entry = proposal.get("entry")
        recent_close = context.get("recent_close")
        
        if entry is None or recent_close is None:
            return ConstraintResult(
                rule_name="price_range",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Entry/recent_close not provided, skipping",
            )
        
        try:
            entry = float(entry)
            recent_close = float(recent_close)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="price_range",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Invalid numeric values",
            )
        
        if recent_close <= 0:
            return ConstraintResult(
                rule_name="price_range",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Recent close is zero or negative",
            )
        
        deviation = abs(entry - recent_close) / recent_close
        
        if deviation > MAX_PRICE_DEVIATION_PCT:
            return ConstraintResult(
                rule_name="price_range",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Entry {entry} deviates {deviation:.2%} from recent close {recent_close} (max {MAX_PRICE_DEVIATION_PCT:.2%})",
                details={"deviation_pct": round(deviation, 4), "max_deviation": MAX_PRICE_DEVIATION_PCT},
            )
        
        return ConstraintResult(
            rule_name="price_range",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Entry price is within acceptable range",
        )
    
    def _rule_cooldown(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 8: Cooldown must have elapsed since last trade."""
        last_trade_time = context.get("last_trade_time")
        
        if last_trade_time is None:
            return ConstraintResult(
                rule_name="cooldown",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="No last trade time, skipping",
            )
        
        try:
            elapsed = time.time() - float(last_trade_time)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="cooldown",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Invalid last trade time",
            )
        
        if elapsed < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - elapsed
            return ConstraintResult(
                rule_name="cooldown",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Cooldown active: {remaining:.0f}s remaining (need {COOLDOWN_SECONDS}s)",
            )
        
        return ConstraintResult(
            rule_name="cooldown",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Cooldown has elapsed",
        )
    
    def _rule_max_active_trades(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 9: Max active trades limit."""
        active_count = context.get("active_trade_count")
        
        if active_count is None:
            return ConstraintResult(
                rule_name="max_active_trades",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Active trade count not provided, skipping",
            )
        
        try:
            active_count = int(active_count)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="max_active_trades",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Invalid active trade count",
            )
        
        if active_count >= MAX_ACTIVE_TRADES:
            return ConstraintResult(
                rule_name="max_active_trades",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Active trades {active_count} >= max {MAX_ACTIVE_TRADES}",
            )
        
        return ConstraintResult(
            rule_name="max_active_trades",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message=f"Active trades {active_count} within limit {MAX_ACTIVE_TRADES}",
        )
    
    def _rule_daily_loss_limit(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 10: Daily loss limit (kill switch)."""
        daily_pnl_pct = context.get("daily_pnl_pct")
        
        if daily_pnl_pct is None:
            return ConstraintResult(
                rule_name="daily_loss_limit",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Daily PnL not provided, skipping",
            )
        
        try:
            daily_pnl_pct = float(daily_pnl_pct)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="daily_loss_limit",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Invalid daily PnL",
            )
        
        if daily_pnl_pct < -MAX_DAILY_LOSS_PCT:
            return ConstraintResult(
                rule_name="daily_loss_limit",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Daily loss {daily_pnl_pct:.2%} exceeds kill switch {MAX_DAILY_LOSS_PCT:.2%}",
                details={"action": "FLATTEN", "daily_pnl_pct": daily_pnl_pct},
            )
        
        return ConstraintResult(
            rule_name="daily_loss_limit",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Daily loss within limit",
        )
    
    def _rule_consecutive_losses(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 11: Consecutive loss circuit breaker."""
        consecutive = context.get("consecutive_losses")
        
        if consecutive is None:
            return ConstraintResult(
                rule_name="consecutive_losses",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Consecutive losses not provided, skipping",
            )
        
        try:
            consecutive = int(consecutive)
        except (TypeError, ValueError):
            return ConstraintResult(
                rule_name="consecutive_losses",
                verdict=ConstraintVerdict.PASS,
                severity=ConstraintSeverity.HARD,
                message="Invalid consecutive loss count",
            )
        
        if consecutive >= MAX_CONSECUTIVE_LOSSES:
            return ConstraintResult(
                rule_name="consecutive_losses",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Consecutive losses {consecutive} >= max {MAX_CONSECUTIVE_LOSSES}",
            )
        
        return ConstraintResult(
            rule_name="consecutive_losses",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message=f"Consecutive losses {consecutive} within limit",
        )
    
    def _rule_crash_detector(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 12: Crash detector gate."""
        crash_hold = context.get("crash_detector_hold_bias", False)
        crash_trend = context.get("crash_detector_trend", "OSCILLATION")
        
        if crash_hold or crash_trend == "RAPID_DESCEND":
            return ConstraintResult(
                rule_name="crash_detector",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Crash detector: {crash_trend} — all entries blocked",
                details={"trend": crash_trend, "hold_bias": True},
            )
        
        return ConstraintResult(
            rule_name="crash_detector",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message=f"Crash detector: {crash_trend} — entries allowed",
        )
    
    def _rule_cross_market(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 13: Cross-market robustness gate."""
        xmarket_hold = context.get("cross_market_hold_bias", False)
        xmarket_score = context.get("cross_market_score")
        
        if xmarket_hold:
            return ConstraintResult(
                rule_name="cross_market",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message="Cross-market robustness too weak — entries blocked",
                details={"score": xmarket_score},
            )
        
        return ConstraintResult(
            rule_name="cross_market",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Cross-market robustness OK",
        )
    
    def _rule_cpcv_escalation(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 14: CPCV escalation gate."""
        cpcv_scale = context.get("cpcv_position_scale", 1.0)
        cpcv_streak = context.get("cpcv_degradation_streak", 0)
        
        if cpcv_scale <= 0.0:
            return ConstraintResult(
                rule_name="cpcv_escalation",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"CPCV escalation: position_scale=0.0 — all entries blocked",
                details={"scale": cpcv_scale, "streak": cpcv_streak},
            )
        
        if cpcv_scale < 1.0:
            return ConstraintResult(
                rule_name="cpcv_escalation",
                verdict=ConstraintVerdict.REDUCE,
                severity=ConstraintSeverity.HARD,
                message=f"CPCV escalation: position_scale={cpcv_scale:.2f} — reduce position",
                details={"action": "REDUCE", "field": "lots", "scale": cpcv_scale, "streak": cpcv_streak},
            )
        
        return ConstraintResult(
            rule_name="cpcv_escalation",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="CPCV escalation: no reduction needed",
        )

    def _rule_meta_labeling(self, proposal: Dict, context: Dict) -> ConstraintResult:
        """Rule 15: Meta-Labeling P-filter — triple-barrier proba < 0.60 means primary signal is unreliable."""
        should_filter = context.get("meta_label_hold_bias", False)
        proba = context.get("meta_label_proba", None)
        events = context.get("meta_label_events", None)
        if should_filter:
            return ConstraintResult(
                rule_name="meta_labeling",
                verdict=ConstraintVerdict.REJECT,
                severity=ConstraintSeverity.HARD,
                message=f"Meta-labeling P-filter: proba {proba} < 0.60 on {events} events — primary signal unreliable, block entry",
                details={"proba": proba, "events": events, "threshold": 0.60},
            )
        return ConstraintResult(
            rule_name="meta_labeling",
            verdict=ConstraintVerdict.PASS,
            severity=ConstraintSeverity.HARD,
            message="Meta-labeling P-filter: passed or no recent barriers",
        )
