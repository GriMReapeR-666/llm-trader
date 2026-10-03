"""Broker-Agnostic Position Manager — Rule-Based Trade Management.

Replaces LLM suggestion-based management with deterministic rules:
  1. Breakeven: move SL to entry + buffer at +1R
  2. Trailing Stop: trail at ATR-based distance behind price
  3. Partial Close: lock profits at configurable R-levels
  4. Time Stop: exit stagnant positions after N bars
  5. Full Close: on regime change or risk limit breach

All decisions are PURELY RULE-BASED — no LLM involved.
Each action is logged and journaled for auditability.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("PositionManager")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class PositionManagerConfig:
    """All tunables for rule-based position management."""
    # Breakeven
    breakeven_r_mult: float = 1.0       # Move SL to entry at +1R
    breakeven_atr_buffer: float = 0.1   # Buffer above entry (in ATR units)

    # Trailing stop
    trailing_enabled: bool = True
    trailing_atr_mult: float = 1.5      # Trail distance = ATR × this
    trailing_min_profit_r: float = 0.5  # Only trail after +0.5R

    # Partial close (scale-out ladder)
    partial_close_enabled: bool = True
    partial_close_levels: List[Tuple[float, float]] = field(
        default_factory=lambda: [
            (2.0, 0.50),   # At +2R → close 50%
            (3.0, 0.50),   # At +3R → close 50% of remainder
            (4.0, 0.50),   # At +4R → close 50% of remainder
        ]
    )

    # Time stop
    time_stop_enabled: bool = True
    time_stop_bars: int = 48            # Exit after 48 bars (12h on M15)
    time_stop_min_r: float = 0.2        # Only exit if R < 0.2

    # ATR adaptivity
    atr_period: int = 14                # For ATR-based trailing distance

    # Triune Brain (P16 Jolley 2026) - Neocortex Macro Minimum Hold
    neocortex_min_hold_bars: int = 24   # Minimum viable hold window for macro/causal theses
    neocortex_immune_to_regime: bool = False # If True, neocortex positions survive routine regime jitter within retention window


# ---------------------------------------------------------------------------
# Position state tracking
# ---------------------------------------------------------------------------

@dataclass
class ManagedPosition:
    """Internal state for a position being managed."""
    ticket: int
    symbol: str
    side: str                           # "BUY" or "SELL"
    entry_price: float
    initial_sl: float
    current_sl: float
    take_profit: float
    volume: float
    initial_risk_distance: float        # |entry - initial_sl|
    entry_time: float                   # Unix timestamp
    entry_bar: int                      # Bar number at entry
    is_neocortex: bool = False          # P16 Jolley 2026: Macro/causal reasoning thesis trade
    # State
    breakeven_applied: bool = False
    partial_exits_done: int = 0         # How many partial close levels hit
    trailing_active: bool = False
    last_trail_update: float = 0.0
    management_log: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ManagementAction:
    """A single management action to execute."""
    action: str             # "MODIFY_SL", "PARTIAL_CLOSE", "FULL_CLOSE"
    ticket: int
    symbol: str
    new_sl: Optional[float] = None
    close_fraction: Optional[float] = None
    reason: str = ""
    r_multiple: float = 0.0
    atr: float = 0.0
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Core position manager
# ---------------------------------------------------------------------------

class PositionManager:
    """Rule-based position manager — no LLM dependency."""

    def __init__(self, config: Optional[PositionManagerConfig] = None):
        self.config = config or PositionManagerConfig()
        self.positions: Dict[int, ManagedPosition] = {}
        self._action_log: List[ManagementAction] = []

    # --- Position registration ---

    def register_position(
        self,
        ticket: int,
        symbol: str,
        side: str,
        entry_price: float,
        sl: float,
        tp: float,
        volume: float,
        atr: float,
        entry_bar: int = 0,
        is_neocortex: bool = False,
    ) -> ManagedPosition:
        """Register a new position for management."""
        risk_dist = abs(entry_price - sl) if sl > 0 else atr * 1.5
        if risk_dist <= 0:
            risk_dist = atr * 1.5

        pos = ManagedPosition(
            ticket=ticket,
            symbol=symbol,
            side=side.upper(),
            entry_price=entry_price,
            initial_sl=sl,
            current_sl=sl,
            take_profit=tp,
            volume=volume,
            initial_risk_distance=risk_dist,
            entry_time=time.time(),
            entry_bar=entry_bar,
            is_neocortex=is_neocortex,
        )
        self.positions[ticket] = pos
        logger.info("Registered position #%d %s %s @ %.5f SL=%.5f (risk=%.5f, neocortex=%s)",
                     ticket, side, symbol, entry_price, sl, risk_dist, is_neocortex)
        return pos

    def unregister_position(self, ticket: int) -> None:
        """Remove a position from management (after close)."""
        self.positions.pop(ticket, None)

    # --- Main management tick ---

    def manage(
        self,
        current_price: float,
        atr: float,
        bar_number: int = 0,
        regime_changed: bool = False,
    ) -> List[ManagementAction]:
        """Run all management rules on all tracked positions.

        Returns list of actions to execute (caller handles broker/paper).
        """
        actions: List[ManagementAction] = []

        for ticket, pos in list(self.positions.items()):
            pos_actions = self._manage_single(pos, current_price, atr, bar_number, regime_changed)
            actions.extend(pos_actions)

        return actions

    def _manage_single(
        self,
        pos: ManagedPosition,
        current_price: float,
        atr: float,
        bar_number: int,
        regime_changed: bool,
    ) -> List[ManagementAction]:
        """Run all rules on a single position."""
        actions: List[ManagementAction] = []
        now = time.time()

        # Compute current R-multiple
        if pos.side == "BUY":
            profit_dist = current_price - pos.entry_price
        else:
            profit_dist = pos.entry_price - current_price

        r_mult = profit_dist / pos.initial_risk_distance if pos.initial_risk_distance > 0 else 0.0
        r_mult = round(r_mult, 8)  # Avoid floating-point precision issues

        # --- 0. Regime change → close all (unless neocortex retention active and configured immune) ---
        if regime_changed:
            bars_held = bar_number - pos.entry_bar if bar_number > 0 else 0
            retention_active = pos.is_neocortex and bars_held < self.config.neocortex_min_hold_bars
            if retention_active and self.config.neocortex_immune_to_regime:
                logger.info(
                    "PositionManager: Ticket #%d neocortex retention active (%d/%d bars); preserving across regime change",
                    pos.ticket, bars_held, self.config.neocortex_min_hold_bars
                )
                self._log_action(pos, "NEOCORTEX_REGIME_PRESERVED", r_mult, atr)
            else:
                actions.append(ManagementAction(
                    action="FULL_CLOSE", ticket=pos.ticket, symbol=pos.symbol,
                    reason="regime_change", r_multiple=r_mult, atr=atr, timestamp=now,
                ))
                self._log_action(pos, "REGIME_CHANGE_CLOSE", r_mult, atr)
                return actions

        # --- 1. Breakeven: move SL to entry + buffer at +1R ---
        breakeven_just_applied = False
        if not pos.breakeven_applied and r_mult >= self.config.breakeven_r_mult:
            buffer = atr * self.config.breakeven_atr_buffer
            if pos.side == "BUY":
                new_sl = pos.entry_price + buffer
                if new_sl > pos.current_sl:
                    actions.append(ManagementAction(
                        action="MODIFY_SL", ticket=pos.ticket, symbol=pos.symbol,
                        new_sl=new_sl, reason="breakeven",
                        r_multiple=r_mult, atr=atr, timestamp=now,
                    ))
                    pos.current_sl = new_sl
                    pos.breakeven_applied = True
                    breakeven_just_applied = True
                    self._log_action(pos, "BREAKEVEN", r_mult, atr, new_sl=new_sl)
            else:  # SELL
                new_sl = pos.entry_price - buffer
                if pos.current_sl == 0 or new_sl < pos.current_sl:
                    actions.append(ManagementAction(
                        action="MODIFY_SL", ticket=pos.ticket, symbol=pos.symbol,
                        new_sl=new_sl, reason="breakeven",
                        r_multiple=r_mult, atr=atr, timestamp=now,
                    ))
                    pos.current_sl = new_sl
                    pos.breakeven_applied = True
                    breakeven_just_applied = True
                    self._log_action(pos, "BREAKEVEN", r_mult, atr, new_sl=new_sl)

        # --- 2. Partial close (scale-out ladder) ---
        if self.config.partial_close_enabled:
            for level_idx, (level_r, close_frac) in enumerate(self.config.partial_close_levels):
                if pos.partial_exits_done <= level_idx and r_mult >= level_r:
                    # Only if enough volume remains
                    remaining = pos.volume * (0.5 ** pos.partial_exits_done)
                    close_vol = remaining * close_frac
                    if close_vol > 0.001:  # Min 0.001 lot
                        actions.append(ManagementAction(
                            action="PARTIAL_CLOSE", ticket=pos.ticket, symbol=pos.symbol,
                            close_fraction=close_frac,
                            reason=f"scale_out_{level_r}R",
                            r_multiple=r_mult, atr=atr, timestamp=now,
                        ))
                        pos.partial_exits_done += 1
                        self._log_action(pos, f"PARTIAL_CLOSE_{level_r}R", r_mult, atr,
                                          close_fraction=close_frac)

        # --- 3. Trailing stop (skip if breakeven just applied this tick) ---
        if self.config.trailing_enabled and r_mult >= self.config.trailing_min_profit_r and not breakeven_just_applied:
            trail_dist = atr * self.config.trailing_atr_mult

            if pos.side == "BUY":
                proposed_sl = current_price - trail_dist
                # Only trail up, and only after breakeven
                if proposed_sl > pos.current_sl and proposed_sl > pos.entry_price:
                    actions.append(ManagementAction(
                        action="MODIFY_SL", ticket=pos.ticket, symbol=pos.symbol,
                        new_sl=proposed_sl, reason="trailing_stop",
                        r_multiple=r_mult, atr=atr, timestamp=now,
                    ))
                    pos.current_sl = proposed_sl
                    pos.trailing_active = True
                    self._log_action(pos, "TRAILING_STOP", r_mult, atr, new_sl=proposed_sl)
            else:  # SELL
                proposed_sl = current_price + trail_dist
                if (pos.current_sl == 0 or proposed_sl < pos.current_sl) and proposed_sl < pos.entry_price:
                    actions.append(ManagementAction(
                        action="MODIFY_SL", ticket=pos.ticket, symbol=pos.symbol,
                        new_sl=proposed_sl, reason="trailing_stop",
                        r_multiple=r_mult, atr=atr, timestamp=now,
                    ))
                    pos.current_sl = proposed_sl
                    pos.trailing_active = True
                    self._log_action(pos, "TRAILING_STOP", r_mult, atr, new_sl=proposed_sl)

        # --- 4. OU Time Stop (half-life based, Part3 §10.1) ---
        if self.config.time_stop_enabled:
            bars_held = bar_number - pos.entry_bar if bar_number > 0 else 0
            # Triune Brain (P16 Jolley 2026 §4.2): Macro minimum hold prevents limbic override / premature stagnation exit
            if pos.is_neocortex and bars_held < self.config.neocortex_min_hold_bars:
                self._log_action(pos, "NEOCORTEX_RETENTION_HELD", r_mult, atr)
            else:
                # Standard time stop
                standard_timeout = bars_held >= self.config.time_stop_bars and r_mult < self.config.time_stop_min_r

                # OU half-life time stop: close if duration > 2 * lambda
                ou_timeout = False
                try:
                    from core.ou_time_stop import OUTimeStopEngine
                    ou_engine = OUTimeStopEngine(multiplier=2.5)
                    # Use management log prices as proxy for price series
                    price_history = [pos.entry_price]
                    if pos.management_log:
                        for entry in pos.management_log[-20:]:
                            if "new_sl" in entry:
                                price_history.append(entry["new_sl"])
                    price_history.append(current_price)
                    if len(price_history) >= 20:
                        ou_params = ou_engine.estimate_ou_parameters(price_history)
                        ou_result = ou_engine.evaluate_time_stop(ou_params, bars_held=bars_held)
                        ou_timeout = ou_result.is_stagnant
                except Exception:
                    pass

                if standard_timeout or ou_timeout:
                    reason = f"ou_time_stop_{bars_held}_bars" if ou_timeout else f"time_stop_{bars_held}_bars"
                    actions.append(ManagementAction(
                        action="FULL_CLOSE", ticket=pos.ticket, symbol=pos.symbol,
                        reason=reason, r_multiple=r_mult, atr=atr, timestamp=now,
                    ))
                    self._log_action(pos, "OU_TIME_STOP" if ou_timeout else "TIME_STOP", r_mult, atr)

        return actions

    # --- Rotation Management (P16 Jolley 2026 §4.2) ---

    def can_rotate(self, ticket: int, bar_number: int = 0) -> Tuple[bool, str]:
        """Check if position is eligible for rotation by a competing signal.

        Under Jolley 2026 §4.2 (The Retention Problem), neocortex-originated trades
        have a minimum viable hold window preventing premature limbic rotation.
        """
        pos = self.positions.get(ticket)
        if not pos:
            return True, "position_not_found"
        bars_held = bar_number - pos.entry_bar if bar_number > 0 else 0
        if pos.is_neocortex and bars_held < self.config.neocortex_min_hold_bars:
            return False, f"neocortex_retention_active_{bars_held}_{self.config.neocortex_min_hold_bars}_bars"
        return True, "ok"

    def request_rotation_exit(
        self, ticket: int, bar_number: int = 0, reason: str = "rotation"
    ) -> Optional[ManagementAction]:
        """Request a rotational exit for a position, subject to neocortex retention guards."""
        can_rot, guard_reason = self.can_rotate(ticket, bar_number)
        pos = self.positions.get(ticket)
        if not pos:
            return None
        if not can_rot:
            self._log_action(pos, "ROTATION_BLOCKED_NEOCORTEX", 0.0, 0.0)
            logger.info("Rotation blocked for ticket #%d: %s", ticket, guard_reason)
            return None

        now = time.time()
        action = ManagementAction(
            action="FULL_CLOSE",
            ticket=pos.ticket,
            symbol=pos.symbol,
            reason=reason,
            timestamp=now,
        )
        self._action_log.append(action)
        self._log_action(pos, "ROTATION_CLOSE", 0.0, 0.0)
        return action

    # --- Utility ---

    def get_position_state(self, ticket: int) -> Optional[ManagedPosition]:
        return self.positions.get(ticket)

    def get_all_actions(self) -> List[ManagementAction]:
        return list(self._action_log)

    def _log_action(
        self, pos: ManagedPosition, action_type: str, r_mult: float, atr: float,
        new_sl: float = 0.0, close_fraction: float = 0.0,
    ) -> None:
        entry = {
            "action": action_type,
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "r_multiple": round(r_mult, 4),
            "atr": round(atr, 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if new_sl > 0:
            entry["new_sl"] = new_sl
        if close_fraction > 0:
            entry["close_fraction"] = close_fraction
        pos.management_log.append(entry)

    def summary(self) -> Dict[str, Any]:
        """Summary of all managed positions."""
        positions = {}
        for ticket, pos in self.positions.items():
            positions[ticket] = {
                "symbol": pos.symbol,
                "side": pos.side,
                "entry": pos.entry_price,
                "current_sl": pos.current_sl,
                "breakeven": pos.breakeven_applied,
                "trailing": pos.trailing_active,
                "partial_exits": pos.partial_exits_done,
                "log_count": len(pos.management_log),
            }
        return {
            "active_positions": len(self.positions),
            "positions": positions,
            "total_actions": len(self._action_log),
        }
