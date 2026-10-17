import logging
import time
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger(__name__)
import json
import os
import math
try:
    import MetaTrader5 as mt5
except ImportError:
    # Import-time portability for paper/research installs; every live
    # management path still requires an authenticated MT5 terminal.
    mt5 = None

from core.trading_safety import BOT_MAGICS, PortfolioRiskArbiter, exposure_buckets
from core.state_persistence import StoreBackedState
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple, Union


class TradingStyle(str, Enum):
    SCALP = "SCALP"
    SWING = "SWING"
    LONG_TERM = "LONG_TERM"


@dataclass
class StyleManagementConfig:
    trading_style: TradingStyle
    trailing_stop_r_mult: float
    trailing_stop_atr_mult: float
    breakeven_r_mult: float
    scale_out_r_mult: float
    scale_out_two_r_mult: float
    time_stop_seconds: int
    use_spread_trailing: bool = False
    spread_multiplier: float = 1.5
    weekly_rebalance: bool = False


RETCODE_MESSAGES = {
    10004: "REQUOTE",
    10006: "REJECT",
    10007: "CANCEL",
    10008: "PLACED",
    10009: "DONE",
    10010: "DONE_PARTIAL",
    10011: "ERROR",
    10012: "TIMEOUT",
    10013: "INVALID_PRICE",
    10014: "INVALID_STOPS",
    10015: "INVALID_VOLUME",
    10016: "MARKET_CLOSED",
    10018: "TOO_MANY_REQUESTS",
    10019: "NO_MONEY",
    10021: "NO_CHANGES",
}


def translate_retcode(retcode: Optional[int]) -> str:
    if retcode is None:
        return "UNKNOWN"
    return RETCODE_MESSAGES.get(retcode, f"RETCODE_{retcode}")


class PaperPosition:
    """Lightweight adapter wrapping a paper broker position dictionary."""
    def __init__(self, d: Dict[str, Any], ticket: int):
        self.ticket = int(ticket)
        self.symbol = str(d.get("symbol", ""))
        side = str(d.get("side", "BUY")).upper()
        self.type = 0 if side == "BUY" else 1  # 0: BUY, 1: SELL
        self.magic = int(d.get("magic", 0) or 0)
        self.volume = float(d.get("volume", 0.0) or 0.0)
        self.price_open = float(d.get("price_open", 0.0) or 0.0)
        self.sl = float(d.get("sl", 0.0) or 0.0)
        self.tp = float(d.get("tp", 0.0) or 0.0)
        self.profit = float(d.get("profit", 0.0) or 0.0)
        self.price_current = float(d.get("price_current", self.price_open) or self.price_open)
        self.time = d.get("time", time.time())
        self.comment = str(d.get("comment", "") or "")


class TradeManager(StoreBackedState):
    store_key = "trade_manager_state"
    conditions_key = "trade_manager_conditions"

    def __init__(self, client, risk_manager, managed_magics=None, store=None, arbiter=None, paper_broker=None, state_file=None, conditions_file=None, time_stop_seconds: int = None):
        self.client = client
        self.risk = risk_manager
        self.store = store
        # Portfolio arbiter gates scale-in risk the same way as new entries.
        self.arbiter = arbiter
        self.paper_broker = paper_broker
        if self.paper_broker is None and client is not None and type(client).__name__ == "PaperBroker":
            self.paper_broker = client
        # Never manage manually-opened trades.  Initial bot orders are tagged
        # by the execution engine and only those magic numbers are eligible.
        self.managed_magics = set(managed_magics or BOT_MAGICS)
        self.log = logging.getLogger("TradeManager")
        
        # Configuration — R-multiple management ladder:
        #   +1R -> breakeven, +2R -> close 50%, +3R -> close another 25%
        #   (half of the remainder), remainder trails at 1.5R distance.
        self.use_trailing_stop = True
        self.trailing_stop_r_mult = 1.5    # Trail remaining position at 1.5R
        self.breakeven_r_mult = 1.0        # Move stop to breakeven at +1R
        self.scale_out_r_mult = 2.0        # Close 50% at +2R
        self.scale_out_two_r_mult = 3.0    # Close 25% more at +3R
        # Legacy ATR multipliers retained for fallback when initial risk is
        # unmeasurable (missing/zero SL).
        self.trailing_stop_atr_mult = 2.5
        self.breakeven_atr_mult = 1.5
        self.partial_tp_enabled = True     # Enabled Feature (scale-out ladder)
        self.time_stop_enabled = True      # Enabled Asymmetric Exit: Time-based stop
        self.time_stop_seconds = time_stop_seconds if time_stop_seconds is not None else 4 * 3600
        self.enable_pyramiding = True      # Enabled Feature
        
        self.conditions_file = conditions_file if conditions_file is not None else "trade_conditions.json"
        self.state_file = state_file if state_file is not None else "trade_manager_state.json"
        self.active_conditions = {}
        self.partial_exits = set() # Track tickets that have been scaled out (stage 1: 50%)
        self.partial_exits_stage2 = set() # Track tickets that completed stage 2 (25% more)
        self.initial_risk: Dict[int, float] = {} # Entry->initial-SL distance per ticket (R basis)
        self.pyramid_entries = set() # Track tickets that have been scaled in
        self.history = [] # Track trade action history

        # Style-Specific Post-Entry Management Configuration:
        self.default_style = TradingStyle.SWING
        self.position_styles: Dict[int, str] = {}
        self.style_configs: Dict[str, StyleManagementConfig] = {
            TradingStyle.SCALP.value: StyleManagementConfig(
                trading_style=TradingStyle.SCALP,
                trailing_stop_r_mult=0.75,
                trailing_stop_atr_mult=1.0,
                breakeven_r_mult=0.5,
                scale_out_r_mult=1.0,
                scale_out_two_r_mult=1.5,
                time_stop_seconds=900,  # 15 minutes minute-stop
                use_spread_trailing=True,
                spread_multiplier=1.5,
                weekly_rebalance=False,
            ),
            TradingStyle.SWING.value: StyleManagementConfig(
                trading_style=TradingStyle.SWING,
                trailing_stop_r_mult=1.5,
                trailing_stop_atr_mult=2.5,
                breakeven_r_mult=1.0,
                scale_out_r_mult=2.0,
                scale_out_two_r_mult=3.0,
                time_stop_seconds=4 * 3600,  # 4 hours
                use_spread_trailing=False,
                spread_multiplier=0.0,
                weekly_rebalance=False,
            ),
            TradingStyle.LONG_TERM.value: StyleManagementConfig(
                trading_style=TradingStyle.LONG_TERM,
                trailing_stop_r_mult=3.0,
                trailing_stop_atr_mult=4.0,
                breakeven_r_mult=2.0,
                scale_out_r_mult=3.0,
                scale_out_two_r_mult=5.0,
                time_stop_seconds=7 * 86400,  # 7 days
                use_spread_trailing=False,
                spread_multiplier=0.0,
                weekly_rebalance=True,
            ),
        }

        self.load_conditions()
        self.load_state()

    def register_position_style(self, ticket: int, style: Union[TradingStyle, str]) -> None:
        """Register the trading style for a specific position ticket."""
        style_val = style.value if isinstance(style, TradingStyle) else str(style).upper()
        if style_val not in self.style_configs:
            style_val = TradingStyle.SWING.value
        self.position_styles[int(ticket)] = style_val
        self.save_state()

    def get_position_style(self, pos) -> str:
        """Infer or retrieve the trading style for a position."""
        ticket = int(getattr(pos, "ticket", 0) or 0)
        if ticket in self.position_styles:
            return self.position_styles[ticket]
        comment = str(getattr(pos, "comment", "") or "").lower()
        if "scalp" in comment:
            return TradingStyle.SCALP.value
        if "long" in comment or "macro" in comment:
            return TradingStyle.LONG_TERM.value
        if "swing" in comment or "pair" in comment or "kalman" in comment:
            return TradingStyle.SWING.value
        return self.default_style.value

    def get_style_config(self, pos) -> StyleManagementConfig:
        """Get the management configuration for this position's style."""
        style_key = self.get_position_style(pos)
        return self.style_configs.get(style_key, self.style_configs[TradingStyle.SWING.value])

    def attach_safety(self, store=None, arbiter=None) -> None:
        """Bind the authoritative journal and portfolio arbiter after startup."""
        if store is not None:
            self.store = store
        if arbiter is not None:
            self.arbiter = arbiter
        elif self.store is not None and self.arbiter is None:
            self.arbiter = PortfolioRiskArbiter(self.store)
        if self.store is not None:
            self.load_state()

    def attach_paper_broker(self, paper_broker) -> None:
        """Bind a paper broker instance for paper trading position management."""
        self.paper_broker = paper_broker

    def _gateway(self):
        """The OrderGateway whose transport matches this manager (review D1).

        Uses the process-wide gateway when its store and transport (paper book
        vs live terminal) match this manager; otherwise a private gateway bound
        to this manager's branch. The shared gateway is never reconfigured or
        switched between paper and live per call.
        """
        from core.order_gateway import OrderGateway, get_default_gateway
        want_paper = self.paper_broker is not None
        try:
            gw = get_default_gateway()
            if (getattr(gw.store, "path", None) == getattr(self.store, "path", None)
                    and gw.is_paper == want_paper
                    and (not want_paper or gw.adapter.paper is self.paper_broker)):
                return gw
        except RuntimeError:
            pass
        private = getattr(self, "_private_gateway", None)
        if (private is None or private.is_paper != want_paper
                or (want_paper and private.adapter.paper is not self.paper_broker)):
            private = OrderGateway(self.store, mode="PAPER" if want_paper else "LIVE", arbiter=self.arbiter)
            if want_paper:
                private.bind_paper_broker(self.paper_broker)
            self._private_gateway = private
        return private

    def _get_positions(self, symbol: Optional[str] = None, ticket: Optional[int] = None) -> List[Any]:
        """Fetch open positions from either paper broker or live MT5 terminal."""
        if self.paper_broker is not None:
            res = []
            for t, p in list(getattr(self.paper_broker, "positions", {}).items()):
                if ticket is not None and int(t) != int(ticket):
                    continue
                if symbol is not None and str(p.get("symbol", "")) != str(symbol):
                    continue
                res.append(PaperPosition(p, int(t)))
            return res
        if not self.client or mt5 is None:
            return []
        try:
            if ticket is not None:
                raw = mt5.positions_get(ticket=ticket)
            elif symbol is not None:
                raw = mt5.positions_get(symbol=symbol)
            else:
                raw = mt5.positions_get()
            return list(raw or [])
        except Exception as exc:
            self.log.warning("positions_get failed: %s", exc)
            return []

    def _record_management(self, ticket: int, event_type: str, payload: Dict[str, Any], *,
                           retcode: Optional[int] = None,
                           requested_volume: Optional[float] = None,
                           filled_volume: Optional[float] = None) -> None:
        """Persist management evidence without granting any execution rights."""
        if self.store is None:
            return
        retcode_str = translate_retcode(retcode) if retcode is not None else None
        data = {"manager": "TradeManager", "retcode_label": retcode_str, **(payload or {})}
        self.store.log_lifecycle_event(int(ticket), "", event_type, "position_manager", data)
        self.store.record_execution_report(
            event_type=event_type, position_ticket=int(ticket), retcode=retcode,
            requested_volume=requested_volume, filled_volume=filled_volume,
            payload=data,
        )

    def monitor_positions(self, symbol: str, current_price: float, atr: float, indicators: Dict[str, Any] = None):
        """
        Monitor and manage open positions for a symbol.
        Apply Trailing SL and Breakeven logic across Paper, Demo, and Live modes.
        """
        if not self.client and not self.paper_broker:
            return

        # GAP-8: daily loss limit must flatten open risk, not only block entries.
        if self.enforce_protective_flatten():
            return

        positions = self._get_positions(symbol=symbol)
        if not positions:
            return

        for pos in positions:
            if not self._owns(pos):
                if self.store is not None:
                    self.store.record_manual_activity(
                        "UNOWNED_POSITION_OBSERVED", ticket=int(getattr(pos, "ticket", 0) or 0),
                        symbol=str(getattr(pos, "symbol", "") or ""),
                        side="BUY" if getattr(pos, "type", None) == getattr(mt5, "POSITION_TYPE_BUY", 0) else "SELL",
                        magic=getattr(pos, "magic", None), payload={"manager": "TradeManager", "action": "observe_only"},
                    )
                continue
            self._manage_position(pos, current_price, atr)
            # LLM conditional_scenarios are advisory-only; this observes matches
            # without granting close authority.
            self._check_programmatic_conditions(symbol, current_price, pos)
            # --- StealthDaemon: check synthetic stop/TP triggers ---
            try:
                from core.urgency_sor import SyntheticStealthExecutionDaemon
                _sl = float(getattr(pos, 'sl', 0) or 0)
                _tp = float(getattr(pos, 'tp', 0) or 0)
                _side = 'BUY' if getattr(pos, 'type', 0) == 0 else 'SELL'
                _vol = float(getattr(pos, 'volume', 0) or 0)
                synth = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
                    current_price=current_price, stop_loss_price=_sl if _sl > 0 else None,
                    take_profit_price=_tp if _tp > 0 else None, side=_side)
                if synth.get('synthetic_stop_fired') or synth.get('synthetic_tp_fired'):
                    logging.getLogger('TradeManager').info(
                        'StealthDaemon trigger: %s ticket=%s',
                        'STOP' if synth.get('synthetic_stop_fired') else 'TP',
                        getattr(pos, 'ticket', '?'))
            except Exception:
                pass
            if self.partial_tp_enabled:
                self._check_scale_out(pos, current_price, atr)
            if self.time_stop_enabled:
                self._check_time_stop(pos, current_price, atr)
            if self.enable_pyramiding and indicators:
                self._check_pyramid_entry(pos, current_price, atr, indicators)

    def enforce_protective_flatten(self) -> bool:
        """Close all owned positions when risk manager requests protective flatten.

        Returns True when a flatten was required (caller should skip scale-in).
        """
        if self.risk is None:
            return False
        # Re-evaluate live equity so flatten is not only a stale flag.
        try:
            self.risk.check_daily_drawdown()
        except Exception as exc:
            log.warning("Drawdown check failed during flatten scan: %s", exc)
        reason = None
        if hasattr(self.risk, "pending_flatten_reason"):
            reason = self.risk.pending_flatten_reason()
        if not reason:
            return False

        all_positions = self._get_positions()
        if not all_positions:
            if hasattr(self.risk, "clear_flatten_request"):
                self.risk.clear_flatten_request()
            return True

        closed_any = False
        for pos in all_positions:
            if not self._owns(pos):
                continue
            self.log.critical(
                "Protective flatten #%s %s due to %s",
                getattr(pos, "ticket", "?"), getattr(pos, "symbol", "?"), reason,
            )
            authorized, auth_reason = self._authorize_management_action(
                "FULL_CLOSE", pos, volume=float(getattr(pos, "volume", 0) or 0),
                extra={"protective_flatten": True, "flatten_reason": reason},
            )
            if not authorized:
                self.log.error("Protective flatten refused for #%s: %s", pos.ticket, auth_reason)
                continue
            self._close_position(pos.ticket, str(pos.symbol), f"ProtectFlatten:{reason}"[:31])
            closed_any = True

        remaining = [
            p for p in self._get_positions()
            if self._owns(p)
        ]
        if not remaining and hasattr(self.risk, "clear_flatten_request"):
            self.risk.clear_flatten_request()
        elif closed_any:
            self.log.warning("Protective flatten incomplete; %s owned positions remain", len(remaining))
        return True

    def _authorize_management_action(
        self,
        action: str,
        pos,
        *,
        volume: float,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Safety gate for management orders before gateway dispatch.

        Scale-in (PYRAMID) must pass portfolio open-risk limits. Risk-reducing
        closes still require ownership + kill-switch-aware authorization logging.
        """
        action = str(action or "").upper()
        if not self._owns(pos):
            return False, "unowned position"
        try:
            vol = float(volume)
        except (TypeError, ValueError, OverflowError):
            return False, "invalid volume"
        if not math.isfinite(vol) or vol <= 0:
            return False, "non-positive volume"

        payload = {
            "action": action,
            "ticket": int(getattr(pos, "ticket", 0) or 0),
            "symbol": str(getattr(pos, "symbol", "") or ""),
            "volume": vol,
            **(extra or {}),
        }

        if action == "PYRAMID":
            if self.store is not None and self.store.is_kill_switch_active():
                self._record_management(pos.ticket, "PYRAMID_REJECTED", {
                    **payload, "error": "kill switch active",
                }, requested_volume=vol)
                return False, "kill switch active"
            if self.risk is not None:
                if hasattr(self.risk, "check_consecutive_loss_breaker") and not self.risk.check_consecutive_loss_breaker():
                    return False, "consecutive loss circuit breaker"
                if not self.risk.check_daily_drawdown():
                    return False, "daily drawdown limit"
                if not self.risk.check_active_trade_limit():
                    return False, "active trade limit"
            side = "BUY" if getattr(pos, "type", None) == getattr(mt5, "ORDER_TYPE_BUY", 0) else "SELL"
            symbol = str(getattr(pos, "symbol", "") or "")
            equity = None
            try:
                acc = mt5.account_info()
                equity = float(acc.equity) if acc else None
            except Exception:
                equity = None
            if equity is None and self.risk is not None:
                try:
                    balance = getattr(self.risk, "balance", None)
                    equity = float(balance) if balance is not None else None
                except (TypeError, ValueError, OverflowError):
                    equity = None
            if equity is None or not math.isfinite(float(equity)) or float(equity) <= 0:
                return False, "equity unavailable"
            equity = float(equity)
            # Estimate added dollar risk from parent stop distance * add volume ratio.
            parent_vol = float(getattr(pos, "volume", 0) or 0)
            try:
                risk_fraction = float(getattr(self.risk, "risk_per_trade", 0.01) or 0.01) if self.risk is not None else 0.01
            except (TypeError, ValueError, OverflowError):
                risk_fraction = 0.01
            if not math.isfinite(risk_fraction) or risk_fraction <= 0:
                risk_fraction = 0.01
            if parent_vol > 0:
                risk_amount = float(equity) * risk_fraction * (vol / parent_vol)
            else:
                risk_amount = float(equity) * risk_fraction * 0.5
            if not math.isfinite(risk_amount) or risk_amount < 0:
                return False, "invalid pyramid risk estimate"
            arbiter = self.arbiter
            if arbiter is None and self.store is not None:
                arbiter = PortfolioRiskArbiter(self.store)
                self.arbiter = arbiter
            if arbiter is None:
                self._record_management(pos.ticket, "PYRAMID_REJECTED", {
                    **payload, "error": "portfolio arbiter unavailable",
                }, requested_volume=vol)
                return False, "portfolio arbiter unavailable"
            buckets = sorted(exposure_buckets(symbol, side))
            primary = buckets[0] if buckets else symbol
            permitted, permit_reason = arbiter.permit(equity, risk_amount, bucket=primary)
            payload["permit_reason"] = permit_reason
            payload["proposed_risk"] = risk_amount
            if not permitted:
                self._record_management(pos.ticket, "PYRAMID_REJECTED", {
                    **payload, "error": permit_reason,
                }, requested_volume=vol)
                return False, permit_reason
            self._record_management(pos.ticket, "PYRAMID_AUTHORIZED", payload, requested_volume=vol)
            return True, "approved"

        if action in {"PARTIAL_CLOSE", "FULL_CLOSE"}:
            # Risk-reducing exits remain allowed under kill switch / daily loss —
            # those states exist to stop adding risk, not to trap open exposure.
            self._record_management(pos.ticket, f"{action}_AUTHORIZED", payload, requested_volume=vol)
            return True, "approved"

        return False, f"unknown management action {action}"

    def load_state(self):
        """Load trade manager state (partial exits & pyramids).

        Single transport: store -> file (one-time migration) -> empty.
        """
        try:
            state = self._load_state_dict() or {}
            self.partial_exits = {int(ticket) for ticket in state.get("partial_exits", [])}
            self.partial_exits_stage2 = {int(ticket) for ticket in state.get("partial_exits_stage2", [])}
            self.initial_risk = {int(k): float(v) for k, v in (state.get("initial_risk", {}) or {}).items()}
            self.pyramid_entries = {int(ticket) for ticket in state.get("pyramid_entries", [])}
            self.position_styles = {int(k): str(v) for k, v in (state.get("position_styles", {}) or {}).items()}
            self.log.info("Loaded durable state: %s scaled-out, %s pyramided, %s styled", len(self.partial_exits), len(self.pyramid_entries), len(self.position_styles))
        except Exception as e:
            self.log.warning(f"Failed to load state: {e}")

    def save_state(self):
        """Save trade manager state to the store + file mirror"""
        try:
            state = {
                "partial_exits": sorted(int(ticket) for ticket in self.partial_exits),
                "partial_exits_stage2": sorted(int(ticket) for ticket in self.partial_exits_stage2),
                "initial_risk": {str(int(k)): float(v) for k, v in list(self.initial_risk.items())[-500:]},
                "pyramid_entries": sorted(int(ticket) for ticket in self.pyramid_entries),
                "position_styles": {str(int(k)): str(v) for k, v in list(self.position_styles.items())[-500:]},
            }
            self._save_state_dict(state)
        except Exception as e:
            self.log.warning(f"Failed to save state: {e}")

    def _initial_risk_for(self, pos, atr: float) -> float:
        """Entry-to-initial-stop distance (R basis), captured before any
        breakeven/trailing move mutates pos.sl. Falls back to ATR heuristics
        when the initial stop is missing or already mutated."""
        ticket = int(getattr(pos, "ticket", 0) or 0)
        cached = self.initial_risk.get(ticket)
        if cached and cached > 0:
            return float(cached)
        sl = float(getattr(pos, "sl", 0) or 0)
        open_price = float(getattr(pos, "price_open", 0) or 0)
        if sl > 0 and open_price > 0:
            risk = abs(open_price - sl)
            if risk > 0:
                self.initial_risk[ticket] = risk
                self.save_state()
                return risk
        return max(float(atr or 0.0), 0.0001)

    def _check_scale_out(self, pos, current_price: float, atr: float):
        """
        Style-parameterized scale-out ladder:
        - SCALP: Quick partial close (+1.0R / +1.5R) with low ATR guard
        - SWING: Standard ladder (+2.0R / +3.0R) with 2x ATR guard
        - LONG_TERM: Extended ladder (+3.0R / +5.0R) with 3x ATR guard
        """
        cfg = self.get_style_config(pos)
        profit_points = 0
        if pos.type == mt5.ORDER_TYPE_BUY:
            profit_points = current_price - pos.price_open
        elif pos.type == mt5.ORDER_TYPE_SELL:
            profit_points = pos.price_open - current_price

        risk_dist = self._initial_risk_for(pos, atr)
        if risk_dist <= 0:
            return
        r_mult = profit_points / risk_dist

        if not self.partial_tp_enabled:
            return

        atr_guard = 0.5 * atr if cfg.trading_style == TradingStyle.SCALP else (
            3.0 * atr if cfg.trading_style == TradingStyle.LONG_TERM else 2.0 * atr
        )

        if pos.ticket not in self.partial_exits:
            # Stage 1 trigger
            if r_mult >= cfg.scale_out_r_mult and profit_points >= atr_guard:
                self.log.info(f"Scale Out Triggered for #{pos.ticket} ({cfg.trading_style.value}): Profit {profit_points:.5f} ({r_mult:.2f}R) -> close 50%")
                self._execute_partial_close(pos, fraction=0.5)
        elif pos.ticket not in self.partial_exits_stage2:
            # Stage 2 trigger
            if r_mult >= cfg.scale_out_two_r_mult:
                self.log.info(f"Scale-Out Stage 2 Triggered for #{pos.ticket} ({cfg.trading_style.value}): {r_mult:.2f}R -> close 25% more")
                self._execute_partial_close(pos, fraction=0.5, stage=2)

    def _check_time_stop(self, pos, current_price: float, atr: float):
        """Asymmetric Exit: Parameterized by TradingStyle.
        - SCALP: Tight minute-based time-stop (e.g. 15 min) for stagnant scalps
        - SWING: Multi-hour time-stop (e.g. 4 hr) for stagnant swings
        - LONG_TERM: Weekly rebalance check (7 days) and macro regime stop
        """
        import time as _time_mod
        open_time = getattr(pos, "time", 0) or 0
        if open_time <= 0:
            return

        cfg = self.get_style_config(pos)
        elapsed = _time_mod.time() - open_time
        if elapsed < cfg.time_stop_seconds:
            return

        # Calculate PnL in R
        risk_dist = abs(pos.price_open - pos.sl) if pos.sl > 0 else (atr * 1.5)
        if risk_dist <= 0:
            risk_dist = max(atr, 0.0001)

        profit_points = (current_price - pos.price_open) if pos.type == mt5.ORDER_TYPE_BUY else (pos.price_open - current_price)
        r_mult = profit_points / risk_dist

        # Style-specific exits:
        if cfg.weekly_rebalance and elapsed >= 7 * 86400:
            if r_mult < 0.20:
                self.log.info(
                    f"📅 Weekly Rebalance Exit for #{pos.ticket} ({cfg.trading_style.value}): Stagnant {elapsed / 86400:.1f}d ({r_mult:.2f}R)."
                )
                authorized, auth_reason = self._authorize_management_action(
                    "FULL_CLOSE", pos, volume=float(getattr(pos, "volume", 0) or 0),
                    extra={"weekly_rebalance_exit": True, "elapsed_seconds": elapsed, "r_multiple": r_mult, "style": cfg.trading_style.value},
                )
                if authorized:
                    self._close_position(pos.ticket, str(pos.symbol), "WeeklyRebalanceExit")
                return

        # Stagnation check
        momentum_threshold = 0.20 if cfg.trading_style == TradingStyle.SCALP else 0.50
        if r_mult < momentum_threshold:
            self.log.info(
                f"⏱️ Time Stop Triggered for #{pos.ticket} ({cfg.trading_style.value}): Stagnant {elapsed:.0f}s ({r_mult:.2f}R). Exiting early."
            )
            authorized, auth_reason = self._authorize_management_action(
                "FULL_CLOSE", pos, volume=float(getattr(pos, "volume", 0) or 0),
                extra={"time_stop_exit": True, "elapsed_seconds": elapsed, "r_multiple": r_mult, "style": cfg.trading_style.value},
            )
            if authorized:
                self._close_position(pos.ticket, str(pos.symbol), f"{cfg.trading_style.value}TimeStopExit")
            else:
                self.log.warning(f"Time stop exit refused for #{pos.ticket}: {auth_reason}")

    def _execute_partial_close(self, pos, fraction: float = 0.5, stage: int = 1):
        """Close `fraction` of the current position volume (scale-out ladder)."""
        try:
            if not self._owns(pos):
                if self.store is not None:
                    self.store.record_manual_activity("REFUSED_PARTIAL_CLOSE", ticket=int(pos.ticket), symbol=str(pos.symbol),
                                                      magic=getattr(pos, "magic", None), payload={"reason": "unowned"})
                return
            vol_to_close = self._normalize_volume(pos.symbol, float(pos.volume) * fraction, max_volume=float(pos.volume))
            if vol_to_close <= 0:
                self.log.warning("Partial-close volume rejected by broker volume specification for #%s", pos.ticket)
                return

            authorized, auth_reason = self._authorize_management_action(
                "PARTIAL_CLOSE", pos, volume=vol_to_close,
            )
            if not authorized:
                self.log.warning("Partial close blocked by safety gate for #%s: %s", pos.ticket, auth_reason)
                return

            if self.paper_broker is not None:
                paper_price = getattr(pos, "price_current", getattr(pos, "price_open", 0.0))
                if mt5 is not None:
                    try:
                        tick = mt5.symbol_info_tick(pos.symbol)
                        if tick:
                            paper_price = tick.bid if pos.type == 0 else tick.ask
                    except Exception:
                        pass
                res = self.paper_broker.close_position(
                    ticket=pos.ticket, price=paper_price, volume=vol_to_close, magic=getattr(pos, "magic", 0)
                )
                if res and res.get("retcode") == 10009:
                    self.log.info(f"✅ Partial Close Paper Success: {vol_to_close} lots closed for #{pos.ticket}")
                    self._record_management(pos.ticket, "PARTIAL_CLOSE_FILLED", {
                        "ticket": pos.ticket, "closed_volume": vol_to_close, "paper": True
                    })
                    if stage >= 2:
                        self.partial_exits_stage2.add(pos.ticket)
                    else:
                        self.partial_exits.add(pos.ticket)
                    self.save_state()
                    return
                else:
                    self.log.warning(f"Partial Close Paper Failed: {res}")
                    return

            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                self._record_management(pos.ticket, "PARTIAL_CLOSE_REJECTED", {
                    "ticket": pos.ticket, "error": "quote unavailable",
                }, requested_volume=vol_to_close)
                return
            filling_mode = self._broker_filling_mode(pos.symbol)
            if filling_mode is None:
                self._record_management(pos.ticket, "PARTIAL_CLOSE_REJECTED", {
                    "ticket": pos.ticket, "error": "broker filling mode unavailable",
                }, requested_volume=vol_to_close)
                return

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": vol_to_close,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": pos.ticket,
                "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
                "magic": pos.magic,
                "type_filling": filling_mode,
                "comment": "partial_tp"
            }
            
            self._record_management(pos.ticket, "PARTIAL_CLOSE_REQUESTED", {
                "ticket": pos.ticket, "original_volume": pos.volume, "close_volume": vol_to_close, "remaining_volume": round(pos.volume - vol_to_close, 2)
            })

            # Single-door dispatch: risk-reducing partial exit via OrderGateway.
            _gw_res = self._gateway().submit_exit(
                int(pos.ticket), str(pos.symbol), volume=vol_to_close,
                comment="partial_tp", mt5_client=mt5, owned_magics=self.managed_magics,
            )
            res = _gw_res.details.get("result")
            if _gw_res.success and (res is None or getattr(res, "retcode", 10009) == mt5.TRADE_RETCODE_DONE):
                self.log.info(f"✅ Partial Close Success: {vol_to_close} lots closed for #{pos.ticket}")
                self._record_management(pos.ticket, "PARTIAL_CLOSE_FILLED", {
                    "ticket": pos.ticket, "closed_volume": vol_to_close
                })
                if stage >= 2:
                    self.partial_exits_stage2.add(pos.ticket)
                else:
                    self.partial_exits.add(pos.ticket)
                self.save_state()
            else:
                self.log.warning(f"Partial Close Failed: {getattr(res, 'comment', None) if res else _gw_res.error}")
                self._record_management(pos.ticket, "PARTIAL_CLOSE_REJECTED", {
                    "ticket": pos.ticket, "error": getattr(res, "comment", None) if res else (_gw_res.error or "Unknown")
                })
                
        except Exception as e:
            self.log.error(f"Partial Close Error: {e}")

    def _manage_position(self, pos, current_price: float, atr: float):
        ticket = pos.ticket
        pos_type = pos.type
        open_price = pos.price_open
        current_sl = pos.sl
        current_tp = pos.tp

        # R basis captured from the initial stop (before any BE/trail move).
        risk_dist = self._initial_risk_for(pos, atr)
        cfg = self.get_style_config(pos)
        be_r = cfg.breakeven_r_mult
        trail_r = cfg.trailing_stop_r_mult

        # 1. Breakeven Check — style-parameterized threshold
        symbol = getattr(pos, "symbol", "")
        if any(g in symbol.upper() for g in ("XAU", "GOLD")):
            try:
                from core.gold_scaling_ladder import GoldScalingLadder
                g_ladder = GoldScalingLadder()
                direction = "LONG" if pos_type == mt5.ORDER_TYPE_BUY else "SHORT"
                exit_eval = g_ladder.evaluate_dynamic_exits(
                    entry_price=open_price,
                    current_price=current_price,
                    direction=direction,
                    current_sl=current_sl,
                    m15_atr=atr,
                )
                if exit_eval.get("partial_close_pct", 0.0) >= 0.50 and float(getattr(pos, "volume", 0.0) or 0.0) >= 0.02:
                    stage_key = f"gold_stage1_closed_{ticket}"
                    if not getattr(self, stage_key, False):
                        setattr(self, stage_key, True)
                        self._execute_partial_close(pos, fraction=0.50, stage=1)
                new_gold_sl = float(exit_eval.get("proposed_sl", 0.0) or 0.0)
                if new_gold_sl > 0:
                    if pos_type == mt5.ORDER_TYPE_BUY and new_gold_sl > current_sl:
                        self._record_management(ticket, "GOLD_DYNAMIC_SL_UPDATE", {"old_sl": current_sl, "new_sl": new_gold_sl, "stage": exit_eval.get("active_stage")})
                        self._modify_sl(ticket, new_gold_sl, exit_eval.get("active_stage", "GoldTrailing"))
                    elif pos_type == mt5.ORDER_TYPE_SELL and (current_sl == 0 or new_gold_sl < current_sl):
                        self._record_management(ticket, "GOLD_DYNAMIC_SL_UPDATE", {"old_sl": current_sl, "new_sl": new_gold_sl, "stage": exit_eval.get("active_stage")})
                        self._modify_sl(ticket, new_gold_sl, exit_eval.get("active_stage", "GoldTrailing"))
            except Exception as _g_exit_exc:
                self.log.debug("Gold dynamic exit evaluation skipped: %s", _g_exit_exc)

        if pos_type == mt5.ORDER_TYPE_BUY:
            profit_dist = current_price - open_price
            if profit_dist > (risk_dist * be_r):
                # Move SL to Entry (plus small buffer)
                new_sl = open_price + (atr * 0.1) 
                if current_sl < new_sl: # Only move up
                    self._record_management(ticket, "BREAKEVEN_TRIGGERED", {"old_sl": current_sl, "new_sl": new_sl, "atr": atr, "profit_dist": profit_dist, "style": cfg.trading_style.value})
                    self._modify_sl(ticket, new_sl, "Breakeven")
                    
        elif pos_type == mt5.ORDER_TYPE_SELL:
            profit_dist = open_price - current_price
            if profit_dist > (risk_dist * be_r):
                # Move SL to Entry (minus small buffer)
                new_sl = open_price - (atr * 0.1)
                if current_sl == 0 or current_sl > new_sl: # Only move down
                    self._record_management(ticket, "BREAKEVEN_TRIGGERED", {"old_sl": current_sl, "new_sl": new_sl, "atr": atr, "profit_dist": profit_dist, "style": cfg.trading_style.value})
                    self._modify_sl(ticket, new_sl, "Breakeven")

        # 2. Trailing Stop — style parameterized (spread-based for SCALP, ATR/R for SWING/LONG_TERM)
        if self.use_trailing_stop:
            if cfg.use_spread_trailing:
                # Tight spread-based trailing for SCALP
                spread = 0.0002
                if self.paper_broker is not None:
                    try:
                        tick = mt5.symbol_info_tick(symbol) if mt5 else None
                        if tick and tick.bid and tick.ask:
                            spread = abs(tick.ask - tick.bid)
                    except Exception:
                        pass
                elif mt5 is not None:
                    try:
                        tick = mt5.symbol_info_tick(symbol)
                        if tick and tick.bid and tick.ask:
                            spread = abs(tick.ask - tick.bid)
                    except Exception:
                        pass
                trail_dist = max(spread * cfg.spread_multiplier, risk_dist * trail_r)
            else:
                trail_dist = risk_dist * trail_r

            if pos_type == mt5.ORDER_TYPE_BUY:
                proposed_sl = current_price - trail_dist
                # Only move SL up, never down
                if proposed_sl > current_sl and proposed_sl > open_price:
                    self._record_management(ticket, "TRAILING_STOP_TRIGGERED", {"old_sl": current_sl, "new_sl": proposed_sl, "atr": atr, "current_price": current_price, "style": cfg.trading_style.value})
                    self._modify_sl(ticket, proposed_sl, "Trailing")
                    
            elif pos_type == mt5.ORDER_TYPE_SELL:
                proposed_sl = current_price + trail_dist
                # Only move SL down, never up
                if (current_sl == 0 or proposed_sl < current_sl) and proposed_sl < open_price:
                    self._record_management(ticket, "TRAILING_STOP_TRIGGERED", {"old_sl": current_sl, "new_sl": proposed_sl, "atr": atr, "current_price": current_price, "style": cfg.trading_style.value})
                    self._modify_sl(ticket, proposed_sl, "Trailing")

    def _check_pyramid_entry(self, pos, current_price: float, atr: float, indicators: Dict[str, Any]):
        """
        Check for Scaling In (Pyramiding) opportunity.
        Criteria: Profit > 1.5R AND Strong Trend (ADX > 25).
        """
        if pos.ticket in self.pyramid_entries:
            return # Already scaled in
            
        # 1. Check Trend Strength
        # Assuming indicators dict has 'adx' (Series or float)
        # If indicators coming from 'toon["indicators"]', they are lists
        adx_val = 0
        if "adx" in indicators:
            val = indicators["adx"]
            if isinstance(val, list) and len(val) > 0: adx_val = val[-1]
            elif isinstance(val, (float, int)): adx_val = val
        
        if adx_val < 25:
            return # Trend not strong enough
            
        # 2. Check Profit > 1.5R
        profit_points = 0
        if pos.type == mt5.ORDER_TYPE_BUY:
            profit_points = current_price - pos.price_open
        elif pos.type == mt5.ORDER_TYPE_SELL:
            profit_points = pos.price_open - current_price
            
        risk_dist = abs(pos.price_open - pos.sl) if pos.sl > 0 else (atr * 1.5)
        if risk_dist == 0: risk_dist = atr
        
        if profit_points > (1.5 * risk_dist) and profit_points > (1.5 * atr):
            # 3. Check Risk Limits (Max Trades) — pyramid also hits arbiter in execute.
            try:
                if not self.risk.check_active_trade_limit():
                    return
            except Exception: return 
            
            self.log.info(f"Pyramid Triggered for #{pos.ticket}: Profit > 1.5R & ADX {adx_val:.1f}")
            self._execute_pyramid_order(pos)

    def _owns(self, pos) -> bool:
        return getattr(pos, "magic", None) in self.managed_magics

    def _normalize_volume(self, symbol: str, requested: float, *, max_volume: Optional[float] = None) -> float:
        """Normalize a management volume using the broker's symbol contract."""
        try:
            requested_value = float(requested)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        info = None
        if mt5 is not None and self.paper_broker is None:
            try:
                info = mt5.symbol_info(symbol)
            except Exception:
                info = None
        if not info:
            if self.paper_broker is not None or mt5 is None:
                step = 0.01
                minimum = 0.01
                maximum = float(max_volume) if max_volume is not None else requested_value
                if maximum < minimum:
                    if requested_value > 0:
                        logging.getLogger("TradeManager").warning(
                            "Volume normalization for %s: requested %.4f is below minimum %.4f (step %.4f, max %.4f) -> normalized to 0.0",
                            symbol, requested_value, minimum, step, maximum,
                        )
                    return 0.0
                bounded = min(requested_value, maximum)
                steps = math.floor((bounded + step * 1e-9) / step)
                normalized = round(steps * step, 2)
                if normalized < minimum:
                    if requested_value > 0:
                        logging.getLogger("TradeManager").warning(
                            "Volume normalization for %s: requested %.4f is below minimum %.4f (step %.4f, max %.4f) -> normalized to 0.0",
                            symbol, requested_value, minimum, step, maximum,
                        )
                    return 0.0
                return normalized
            return 0.0
        try:
            minimum = float(getattr(info, "volume_min", getattr(info, "trade_volume_min", 0.0)) or 0.0)
            maximum = float(getattr(info, "volume_max", getattr(info, "trade_volume_max", 0.0)) or 0.0)
            step = float(getattr(info, "volume_step", getattr(info, "trade_volume_step", 0.0)) or 0.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if (not math.isfinite(minimum) or not math.isfinite(maximum) or
                not math.isfinite(step) or minimum <= 0 or maximum < minimum or step <= 0):
            return 0.0
        if max_volume is not None:
            try:
                maximum = min(maximum, float(max_volume))
            except (TypeError, ValueError, OverflowError):
                return 0.0
        if maximum < minimum:
            if requested_value > 0:
                logging.getLogger("TradeManager").warning(
                    "Volume normalization for %s: requested %.4f is below minimum %.4f (step %.4f, max %.4f) -> normalized to 0.0",
                    symbol, requested_value, minimum, step, maximum,
                )
            return 0.0
        bounded = min(requested_value, maximum)
        steps = math.floor((bounded + step * 1e-9) / step)
        normalized = steps * step
        precision = max(0, int(math.ceil(-math.log10(step))) + 2) if step < 1 else 2
        normalized = round(normalized, precision)
        if normalized < minimum - 1e-12 or normalized <= 0:
            if requested_value > 0:
                logging.getLogger("TradeManager").warning(
                    "Volume normalization for %s: requested %.4f is below minimum %.4f (step %.4f, max %.4f) -> normalized to 0.0",
                    symbol, requested_value, minimum, step, maximum,
                )
            return 0.0
        return normalized

    def _broker_filling_mode(self, symbol: str, info=None) -> Optional[int]:
        """Select a market filling mode from the broker's symbol bitmask.

        MT5 exposes ``SYMBOL_FILLING_MODE`` as flags (FOK=1, IOC=2,
        BOC=4), while order-request constants use different integer values.
        Prefer IOC when advertised, then FOK; never guess a mode when the
        broker contract is absent or malformed.
        """
        try:
            contract = info if info is not None else mt5.symbol_info(symbol)
            if not contract:
                return None
            flags = int(getattr(contract, "filling_mode", -1))
        except (TypeError, ValueError, OverflowError):
            return None
        if flags < 0:
            return None
        if flags & 2:
            return int(getattr(mt5, "ORDER_FILLING_IOC", 1))
        if flags & 1:
            return int(getattr(mt5, "ORDER_FILLING_FOK", 0))
        return None

    def _execute_pyramid_order(self, pos):
        """Open new position (0.5x volume) only after portfolio risk authorization."""
        try:
            if os.getenv("PYRAMIDING_ENABLED", "0") != "1":
                self.log.info("Pyramiding disabled by default (PYRAMIDING_ENABLED=0) for #%s", getattr(pos, "ticket", None))
                if self.store is not None:
                    self.store.record_manual_activity("REFUSED_PYRAMID", ticket=int(pos.ticket), symbol=str(pos.symbol),
                                                      magic=getattr(pos, "magic", None), payload={"reason": "pyramiding_disabled_by_default"})
                return

            if not self._owns(pos):
                if self.store is not None:
                    self.store.record_manual_activity("REFUSED_PYRAMID", ticket=int(pos.ticket), symbol=str(pos.symbol),
                                                      magic=getattr(pos, "magic", None), payload={"reason": "unowned"})
                return
            # 1a-F8: paper-mode pyramiding must never reach the real broker.
            # This guard used to return unconditionally here, which also blocked
            # the safe paper fill further down and left the branch below
            # unreachable: a paper pyramid was refused outright instead of being
            # placed in the paper book. Paper books are simulated locally and
            # never touch mt5, so the live-broker protection is provided by
            # routing below through self.paper_broker, not by refusing here.
            # Live/DEMO pyramiding still requires gateway evidence and fails
            # closed there (the intent carries validation_result=None).
            vol = self._normalize_volume(pos.symbol, float(pos.volume) * 0.5, max_volume=float(pos.volume))
            if vol <= 0:
                self.log.warning("Pyramid volume rejected by broker volume specification for #%s", pos.ticket)
                return

            authorized, auth_reason = self._authorize_management_action(
                "PYRAMID", pos, volume=vol,
                extra={"parent_sl": getattr(pos, "sl", None), "parent_tp": getattr(pos, "tp", None)},
            )
            if not authorized:
                self.log.warning("Pyramid blocked by safety gate for #%s: %s", pos.ticket, auth_reason)
                return
            
            # SL at Parent's current SL
            sl = pos.sl
            
            # Record pyramid request before sending
            self._record_management(pos.ticket, "PYRAMID_REQUESTED", {
                "parent_ticket": pos.ticket, "volume": vol,
                "side": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                "symbol": pos.symbol,
            }, requested_volume=vol)

            # Paper mode: route exclusively to paper broker and NEVER call mt5.order_send
            if self.paper_broker is not None:
                paper_price = getattr(pos, "price_current", getattr(pos, "price_open", 0.0))
                if mt5 is not None:
                    try:
                        tick = mt5.symbol_info_tick(pos.symbol)
                        if tick:
                            paper_price = tick.ask if pos.type == 0 else tick.bid
                    except Exception:
                        pass
                res = None
                side_str = "BUY" if pos.type == 0 else "SELL"
                if hasattr(self.paper_broker, "market_order"):
                    res = self.paper_broker.market_order(
                        symbol=pos.symbol,
                        side=side_str,
                        volume=vol,
                        price=paper_price,
                        sl=sl,
                        tp=pos.tp,
                        comment="pyramid_entry",
                        magic=int(getattr(pos, "magic", 0) or 0),
                    )
                elif hasattr(self.paper_broker, "place_order"):
                    res = self.paper_broker.place_order(
                        symbol=pos.symbol,
                        volume=vol,
                        order_type=side_str,
                        price=paper_price,
                        sl=sl,
                        tp=pos.tp,
                        magic=int(getattr(pos, "magic", 0) or 0),
                        comment="pyramid_entry",
                    )
                self.log.info(f"✅ Pyramid Paper Success: {vol} lots added for #{pos.ticket}")
                self.pyramid_entries.add(pos.ticket)
                new_t = res.get("order") if isinstance(res, dict) else None
                self._record_management(pos.ticket, "PYRAMID_FILLED", {
                    "parent_ticket": pos.ticket, "volume": vol,
                    "new_ticket": new_t,
                    "paper": True,
                }, requested_volume=vol, filled_volume=vol)
                self.save_state()
                return
            
            # Open Market Order via Client
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                self._record_management(pos.ticket, "PYRAMID_REJECTED", {
                    "parent_ticket": pos.ticket, "volume": vol, "error": "quote unavailable",
                }, requested_volume=vol)
                return
            filling_mode = self._broker_filling_mode(pos.symbol)
            if filling_mode is None:
                self._record_management(pos.ticket, "PYRAMID_REJECTED", {
                    "parent_ticket": pos.ticket, "volume": vol,
                    "error": "broker filling mode unavailable",
                }, requested_volume=vol)
                return
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": vol,
                "type": pos.type,
                "price": tick.ask if pos.type == mt5.ORDER_TYPE_BUY else tick.bid,
                "sl": sl,
                "tp": pos.tp,
                "magic": pos.magic,
                "comment": "pyramid_entry",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": filling_mode,
            }
            
            # Open Market Order via the single-door gateway. Pyramiding has no
            # DeterministicValidator evidence, so the gateway fails closed
            # until Phase 4 gives it a validated proposal (1a-F8/1a-F9).
            from core.order_gateway import OrderIntent
            _pyr_intent = OrderIntent(
                intent_key=f"pyramid:{getattr(pos, 'ticket', 0)}:{getattr(pos, 'symbol', '')}",
                symbol=str(pos.symbol), side="BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
                volume=float(vol), price=float(tick.ask if pos.type == mt5.ORDER_TYPE_BUY else tick.bid),
                sl=float(sl) if sl else 0.0, tp=float(getattr(pos, "tp", 0.0) or 0.0),
                risk_amount=0.0, setup_key="", order_type="MARKET",
                magic=int(getattr(pos, "magic", 0) or 0), comment="pyramid_entry",
                validation_result=None,
            )
            _gw_res = self._gateway().submit_entry(_pyr_intent, mt5_request=dict(request), mt5_client=mt5)
            res = _gw_res.details.get("result")
            if _gw_res.success and res and res.retcode == mt5.TRADE_RETCODE_DONE:
                self.log.info(f"✅ Pyramid Success: {vol} lots added for #{pos.ticket}")
                self.pyramid_entries.add(pos.ticket)
                self._record_management(pos.ticket, "PYRAMID_FILLED", {
                    "parent_ticket": pos.ticket, "volume": vol,
                    "new_ticket": getattr(res, "order", None),
                }, retcode=res.retcode, requested_volume=vol, filled_volume=vol)
                if self.risk is not None and hasattr(self.risk, "register_trade"):
                    try:
                        self.risk.register_trade(str(pos.symbol))
                    except Exception as exc:
                        log.warning("Failed to register closed trade for %s: %s", pos.symbol, exc)
                self.save_state()
            else:
                _pyr_err = getattr(res, "comment", None) if res else (_gw_res.error or "Unknown")
                self.log.warning(f"Pyramid Failed: {_pyr_err}")
                self._record_management(pos.ticket, "PYRAMID_REJECTED", {
                    "parent_ticket": pos.ticket, "volume": vol,
                    "error": _pyr_err,
                }, retcode=getattr(res, "retcode", None), requested_volume=vol)
                
        except Exception as e:
            self.log.error(f"Pyramid Error: {e}")

    def get_position_context(self, ticket: int) -> Dict[str, Any]:
        """
        Get detailed context for a specific position for LLM analysis.
        Returns: R-multiple, Duration, PnL, etc.
        """
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                return {}
            
            p = positions[0]
            
            # Calculate Risk (Distance to SL)
            risk_dist = abs(p.price_open - p.sl) if p.sl > 0 else 0.0
            
            # Calculate R-Multiple (Current Profit / Risk)
            r_multiple = 0.0
            profit_points = 0.0
            
            if p.type == mt5.ORDER_TYPE_BUY:
                profit_points = (mt5.symbol_info_tick(p.symbol).bid - p.price_open)
            else:
                profit_points = (p.price_open - mt5.symbol_info_tick(p.symbol).ask)
                
            if risk_dist > 0:
                r_multiple = profit_points / risk_dist
            
            # Duration
            # Note: p.time is a timestamp (seconds)
            import time
            duration_sec = time.time() - p.time
            duration_min = duration_sec / 60
            
            return {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": p.volume,
                "open_price": p.price_open,
                "current_price": mt5.symbol_info_tick(p.symbol).bid if p.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick(p.symbol).ask,
                "sl": p.sl,
                "tp": p.tp,
                "pnl": p.profit,
                "r_multiple": round(r_multiple, 2),
                "duration_minutes": int(duration_min),
                "risk_distance": risk_dist
            }
        except Exception as e:
            self.log.error(f"Error getting position context for {ticket}: {e}")
            return {}

    # ---------- Public Actions for LLM ----------
    def execute_modify_sl(self, ticket: int, new_sl: float, reason: str) -> bool:
        """Public wrapper for SL modification"""
        positions = mt5.positions_get(ticket=ticket)
        if not positions or not self._owns(positions[0]):
            if self.store is not None:
                self.store.record_manual_activity("REFUSED_SL_UPDATE", ticket=int(ticket), payload={"reason": reason})
            self.log.warning("Refused SL update for unmanaged ticket %s", ticket)
            return False
        return self._modify_sl(ticket, new_sl, reason)

    def execute_partial_close(self, ticket: int, percentage: float, reason: str) -> bool:
        """
        Public wrapper for Partial Close.
        percentage: 0.0 to 1.0 (e.g., 0.5 for 50%)
        """
        try:
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                self.log.warning(f"Position {ticket} not found for partial close.")
                return False
            
            pos = positions[0]
            if not self._owns(pos):
                if self.store is not None:
                    self.store.record_manual_activity("REFUSED_PARTIAL_CLOSE", ticket=int(ticket), symbol=str(getattr(pos, "symbol", "") or ""), payload={"reason": reason})
                self.log.warning("Refused partial close for unmanaged ticket %s", ticket)
                return False
            try:
                percentage_value = float(percentage)
            except (TypeError, ValueError, OverflowError):
                percentage_value = math.nan
            if not math.isfinite(percentage_value) or percentage_value <= 0 or percentage_value > 1:
                self.log.warning("Invalid partial-close percentage for #%s", ticket)
                return False
            vol_to_close = self._normalize_volume(pos.symbol, float(pos.volume) * percentage_value,
                                                  max_volume=float(pos.volume))
            if vol_to_close <= 0:
                self.log.warning(f"Calculated partial volume {vol_to_close} too small.")
                return False
            authorized, auth_reason = self._authorize_management_action(
                "PARTIAL_CLOSE", pos, volume=vol_to_close, extra={"reason": reason, "percentage": percentage_value},
            )
            if not authorized:
                self.log.warning("AI partial close blocked by safety gate for #%s: %s", ticket, auth_reason)
                return False
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                self.log.warning("Partial-close quote unavailable for #%s", ticket)
                return False
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": vol_to_close,
                "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": pos.ticket,
                "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
                "magic": getattr(pos, "magic", 0),
                "comment": f"AI: {reason}"[:31] # Max comment length
            }
            
            # Single-door dispatch: AI partial close via OrderGateway.
            _gw_res = self._gateway().submit_exit(
                int(ticket), str(getattr(pos, "symbol", "")), volume=float(vol_to_close),
                comment=f"AI: {reason}"[:31], mt5_client=mt5, owned_magics=self.managed_magics,
            )
            res = _gw_res.details.get("result")
            if _gw_res.success:
                self.log.info(f"✅ AI Partial Close ({percentage*100}%): {vol_to_close} lots on #{ticket}. Reason: {reason}")
                self.partial_exits.add(ticket)
                self.save_state()
                self._record_management(ticket, "PARTIAL_CLOSE", {
                    "symbol": pos.symbol, "reason": reason, "percentage": percentage,
                    "requested_volume": vol_to_close, "filled_volume": vol_to_close,
                }, retcode=getattr(res, "retcode", None) if res else 10009, requested_volume=vol_to_close,
                   filled_volume=vol_to_close)
                return True
            else:
                _pc_err = getattr(res, "comment", None) if res else (_gw_res.error or "Unknown")
                self.log.warning(f"AI Partial Close Failed: {_pc_err}")
                self._record_management(ticket, "PARTIAL_CLOSE_ERROR", {
                    "symbol": pos.symbol, "reason": reason, "percentage": percentage,
                    "requested_volume": vol_to_close,
                    "broker_comment": getattr(res, "comment", "") if res else "no response",
                }, retcode=getattr(res, "retcode", None) if res else None,
                   requested_volume=vol_to_close)
                return False
                
        except Exception as e:
            self.log.error(f"AI Partial Close Error: {e}")
            return False

    def execute_close_full(self, ticket: int, reason: str) -> bool:
        """Public wrapper for Full Close"""
        try:
            positions = self._get_positions(ticket=ticket)
            if not positions or not self._owns(positions[0]):
                if self.store is not None:
                    self.store.record_manual_activity("REFUSED_FULL_CLOSE", ticket=int(ticket), payload={"reason": reason})
                self.log.warning("Refused full close for unmanaged ticket %s", ticket)
                return False
            pos = positions[0]
            authorized, auth_reason = self._authorize_management_action(
                "FULL_CLOSE", pos, volume=float(getattr(pos, "volume", 0) or 0),
                extra={"reason": reason},
            )
            if not authorized:
                self.log.warning("Full close blocked by safety gate for #%s: %s", ticket, auth_reason)
                return False
            self._close_position(ticket, str(getattr(pos, "symbol", "") or ""), f"AI: {reason}"[:31])
            return True
        except Exception as e:
            self.log.error(f"AI Full Close Error: {e}")
            return False

    def _modify_sl(self, ticket: int, new_sl: float, reason: str) -> bool:
        """Execute SL modification across Paper, Demo, and Live modes."""
        try:
            # Validate position exists
            positions = self._get_positions(ticket=ticket)
            if not positions or not self._owns(positions[0]):
                if self.store is not None:
                    self.store.record_manual_activity("REFUSED_SL_UPDATE", ticket=int(ticket), payload={"reason": "unowned_or_missing"})
                return False
            pos = positions[0]
            try:
                requested_sl = float(new_sl)
            except (TypeError, ValueError):
                requested_sl = float("nan")
            if not math.isfinite(requested_sl) or requested_sl <= 0:
                self.log.warning("Refused invalid SL for #%s", ticket)
                return False

            if self.paper_broker is not None:
                updated = self.paper_broker.modify_position_sltp(
                    symbol=pos.symbol, sl=requested_sl, tp=pos.tp, magic=getattr(pos, "magic", 0)
                )
                if updated > 0:
                    current_sl = float(getattr(pos, "sl", 0) or 0)
                    self.log.info(f"Updated Paper {reason} SL for Ticket {ticket} to {requested_sl:.5f}")
                    self._record_management(ticket, "SL_MODIFY_ACCEPTED", {
                        "ticket": ticket, "old_sl": current_sl, "new_sl": requested_sl, "reason": reason, "paper": True
                    })
                    if self.store is not None:
                        self.store.log_lifecycle_event(int(ticket), "", "MANAGED", "position_manager", {
                            "action": "UPDATE_SL", "reason": reason, "new_sl": requested_sl, "paper": True
                        })
                    return True
                return False

            info = mt5.symbol_info(pos.symbol)
            tick = mt5.symbol_info_tick(pos.symbol)
            bid = float(getattr(tick, "bid", 0) or 0) if tick else 0.0
            ask = float(getattr(tick, "ask", 0) or 0) if tick else 0.0
            point = float(getattr(info, "point", 0) or 0) if info else 0.0
            stops = float(getattr(info, "trade_stops_level", 0) or 0) * point if info else 0.0
            freeze = float(getattr(info, "trade_freeze_level", 0) or 0) * point if info else 0.0
            minimum_distance = max(stops, freeze)
            is_buy = getattr(pos, "type", None) == getattr(mt5, "POSITION_TYPE_BUY", getattr(mt5, "ORDER_TYPE_BUY", 0))
            reference = bid if is_buy else ask
            on_protective_side = requested_sl < reference if is_buy else requested_sl > reference
            if reference <= 0 or not on_protective_side or (minimum_distance > 0 and abs(reference - requested_sl) < minimum_distance):
                self.log.warning("Refused SL inside broker stop/freeze distance for #%s", ticket)
                if self.store is not None:
                    self.store.log_lifecycle_event(int(ticket), "", "MANAGED_REJECTED", "position_manager", {
                        "action": "UPDATE_SL", "reason": "invalid_protective_side_or_broker_distance", "requested_sl": requested_sl,
                        "minimum_distance": minimum_distance,
                    })
                return False
            current_sl = float(getattr(pos, "sl", 0) or 0)
            if is_buy and current_sl > 0 and requested_sl <= current_sl:
                return False
            if not is_buy and current_sl > 0 and requested_sl >= current_sl:
                return False

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "sl": requested_sl,
                "tp": pos.tp, # Keep existing TP
                "symbol": pos.symbol,
                "magic": getattr(pos, "magic", 0),
            }
            
            self._record_management(ticket, "SL_MODIFY_REQUESTED", {
                "ticket": ticket, "old_sl": current_sl, "new_sl": requested_sl, "reason": reason, "atr": None,
                "stop_level": stops, "freeze_level": freeze
            })
            
            # Single-door dispatch: protective SL update via OrderGateway
            # (gateway re-enforces only-tighten + broker distance held above).
            _side = "BUY" if is_buy else "SELL"
            _gw_res = self._gateway().modify_protection(
                int(ticket), str(pos.symbol), new_sl=float(requested_sl),
                new_tp=float(getattr(pos, "tp", 0.0) or 0.0),
                current_sl=float(current_sl), side=_side, mt5_client=mt5,
            )
            res = _gw_res.details.get("result")
            if _gw_res.success:
                self.log.info(f"Updated {reason} SL for Ticket {ticket} to {requested_sl:.5f}")
                self._record_management(ticket, "SL_MODIFY_ACCEPTED", {
                    "ticket": ticket, "old_sl": current_sl, "new_sl": requested_sl, "reason": reason
                }, retcode=getattr(res, "retcode", None))
                if self.store is not None:
                    self.store.log_lifecycle_event(int(ticket), "", "MANAGED", "position_manager", {
                        "action": "UPDATE_SL", "reason": reason, "new_sl": requested_sl,
                        "retcode": getattr(res, "retcode", None),
                    })
                    self.store.record_execution_report(
                        event_type="POSITION_MODIFY", position_ticket=int(ticket),
                        retcode=getattr(res, "retcode", None), payload={
                            "action": "UPDATE_SL", "reason": reason,
                            "new_sl": requested_sl, "old_sl": current_sl,
                        })
                return True
            else:
                _sl_err = getattr(res, "comment", None) if res else (_gw_res.error or "Unknown")
                self.log.warning(f"Failed to update {reason} SL for {ticket}: {_sl_err}")
                self._record_management(ticket, "SL_MODIFY_REJECTED", {
                    "ticket": ticket, "old_sl": current_sl, "new_sl": requested_sl, "reason": reason, "error": _sl_err
                }, retcode=getattr(res, "retcode", None) if res else None)
                if self.store is not None:
                    self.store.log_lifecycle_event(int(ticket), "", "MANAGED_ERROR", "position_manager", {
                        "action": "UPDATE_SL", "reason": reason, "new_sl": requested_sl,
                        "retcode": getattr(res, "retcode", None) if res else None,
                    })
                    self.store.record_execution_report(
                        event_type="POSITION_MODIFY_ERROR", position_ticket=int(ticket),
                        retcode=getattr(res, "retcode", None) if res else None,
                        payload={"action": "UPDATE_SL", "reason": reason,
                                 "new_sl": requested_sl,
                                 "broker_comment": getattr(res, "comment", "") if res else "no response"},
                    )
                return False
        except Exception as e:
            self.log.error(f"Modify SL Error: {e}")
            return False

    # ---------- Smart Exits (Programmatic Conditions) ----------
    def register_conditions(self, symbol: str, conditions: List[Dict[str, str]]):
        """Persist LLM conditional scenarios as advisory-only watchlist items.

        LLM text must never arm auto-closes. Deterministic trailing / breakeven /
        protective flatten in ``monitor_positions`` remain the only exit actuators.
        """
        sanitized: List[Dict[str, Any]] = []
        for item in conditions or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["execution_authority"] = False
            row["status"] = "ADVISORY_ONLY"
            sanitized.append(row)
        self.active_conditions[symbol] = sanitized
        self.log.info(
            "Registered %s advisory smart conditions for %s (not executable)",
            len(sanitized), symbol,
        )
        self.save_conditions()

    def _check_programmatic_conditions(self, symbol: str, current_price: float, pos):
        """Observe registered advisory conditions; never close from LLM text."""
        if not self.active_conditions:
            return

        conditions = self.active_conditions.get(symbol, [])
        for scenario in list(conditions):
            if not isinstance(scenario, dict):
                continue
            raw_cond = str(scenario.get("condition", "") or "").lower()
            action = str(scenario.get("action", "") or "").lower()

            triggered = False
            try:
                if "price >" in raw_cond:
                    target = float(raw_cond.split(">")[1].strip())
                    if current_price > target:
                        triggered = True
                elif "price <" in raw_cond:
                    target = float(raw_cond.split("<")[1].strip())
                    if current_price < target:
                        triggered = True
            except ValueError:
                continue

            if not triggered:
                continue
            # Architecture rule: LLM hypotheses stay journalled. Do not call
            # _close_position / execute_* from conditional_scenarios.
            self.log.info(
                "Advisory smart-exit matched (not executed): %s -> %s ticket=%s",
                raw_cond, action, getattr(pos, "ticket", None),
            )
            if self.store is not None:
                self.store.log_lifecycle_event(
                    int(getattr(pos, "ticket", 0) or 0) or None,
                    "",
                    "ADVISORY",
                    "llm_conditional_scenario",
                    {
                        "symbol": symbol,
                        "condition": raw_cond,
                        "action": action,
                        "price": current_price,
                        "execution_authority": False,
                    },
                )
            scenario["status"] = "MATCHED_ADVISORY"
            scenario["matched_at_price"] = current_price
            self.save_conditions()

    def _close_position(self, ticket: int, symbol: str, comment: str):
        """Close a specific position after safety authorization across Paper, Demo, and Live."""
        pos_list = self._get_positions(ticket=ticket)
        if not pos_list:
            return
        pos = pos_list[0]
        if not self._owns(pos):
            if self.store is not None:
                self.store.record_manual_activity(
                    "REFUSED_FULL_CLOSE", ticket=int(ticket), symbol=symbol,
                    magic=getattr(pos, "magic", None), payload={"reason": "unowned", "comment": comment},
                )
            return
        authorized, auth_reason = self._authorize_management_action(
            "FULL_CLOSE", pos, volume=float(getattr(pos, "volume", 0) or 0),
            extra={"comment": comment},
        )
        if not authorized:
            self.log.warning("Close blocked by safety gate for #%s: %s", ticket, auth_reason)
            return

        if self.paper_broker is not None:
            paper_price = getattr(pos, "price_current", getattr(pos, "price_open", 0.0))
            if mt5 is not None:
                try:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        paper_price = tick.bid if pos.type == 0 else tick.ask
                except Exception:
                    pass
            res = self.paper_broker.close_position(
                ticket=ticket, price=paper_price, volume=pos.volume, magic=getattr(pos, "magic", 0)
            )
            if res and res.get("retcode") == 10009:
                self.log.info(f"Closed Paper #{ticket}: {comment}")
                self.history.append({
                    "ticket": ticket, "symbol": symbol, "action": "CLOSE",
                    "volume": pos.volume, "price": paper_price, "comment": comment,
                    "pnl": res.get("profit", 0.0), "paper": True
                })
                self._record_management(ticket, "FULL_CLOSE", {
                    "symbol": symbol, "comment": comment, "price": paper_price,
                    "requested_volume": pos.volume, "filled_volume": pos.volume,
                    "pnl_observation": res.get("profit", 0.0), "paper": True,
                }, retcode=10009, requested_volume=pos.volume, filled_volume=pos.volume)
            else:
                self.log.warning(f"Failed to close Paper #{ticket}: {res}")
                self._record_management(ticket, "FULL_CLOSE_ERROR", {
                    "symbol": symbol, "comment": comment, "price": paper_price,
                    "requested_volume": pos.volume, "error": str(res), "paper": True,
                }, requested_volume=pos.volume)
            return

        # MT5 Demo / Live Path
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            self.log.warning(f"Could not get tick for {symbol}, using position current price")
            price = getattr(pos, "price_current", 0.0)
        else:
            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        filling_mode = self._broker_filling_mode(symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": ticket,
            "price": price,
            "magic": getattr(pos, "magic", 0),
            "comment": comment
        }
        if filling_mode is not None:
            request["type_filling"] = filling_mode

        # Single-door dispatch: full close via OrderGateway (risk-reducing).
        _gw_res = self._gateway().submit_exit(
            int(ticket), str(symbol), volume=float(getattr(pos, "volume", 0) or 0),
            comment=str(comment)[:31], mt5_client=mt5, owned_magics=self.managed_magics,
        )
        res = _gw_res.details.get("result")
        if _gw_res.success:
            self.log.info(f"Closed {ticket}: {comment}")
            # Add to history
            self.history.append({
                "ticket": ticket,
                "symbol": symbol,
                "action": "CLOSE",
                "volume": pos.volume,
                "price": price,
                "comment": comment,
                "pnl": getattr(pos, "profit", 0.0)
            })
            self._record_management(ticket, "FULL_CLOSE", {
                "symbol": symbol, "comment": comment, "price": price,
                "requested_volume": pos.volume, "filled_volume": pos.volume,
                "pnl_observation": getattr(pos, "profit", None),
            }, retcode=getattr(res, "retcode", None), requested_volume=pos.volume,
               filled_volume=pos.volume)
        else:
            _close_err = getattr(res, "comment", None) if res else (_gw_res.error or "Unknown")
            self.log.warning(f"Failed to close {ticket}: {_close_err}")
            self._record_management(ticket, "FULL_CLOSE_ERROR", {
                "symbol": symbol, "comment": comment, "price": price,
                "requested_volume": pos.volume,
                "broker_comment": getattr(res, "comment", "") if res else (_gw_res.error or "no response"),
            }, retcode=getattr(res, "retcode", None) if res else None,
               requested_volume=pos.volume)

    def load_conditions(self):
        """Load smart conditions from the store + file mirror"""
        try:
            data = self._load_state_dict(key=self.conditions_key, path=self.conditions_file)
            if isinstance(data, dict):
                self.active_conditions = data
                self.log.info(f"Loaded active conditions for {len(self.active_conditions)} symbols")
        except Exception as e:
            self.log.warning(f"Failed to load conditions: {e}")

    def save_conditions(self):
        """Save smart conditions to the store + file mirror"""
        try:
            self._save_state_dict(dict(self.active_conditions), key=self.conditions_key, path=self.conditions_file)
        except Exception as e:
            self.log.warning(f"Failed to save conditions: {e}")

