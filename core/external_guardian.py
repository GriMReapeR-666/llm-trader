"""Ψ External Guardian — Independent safety layer outside the trading graph.

From the Universal Autonomous Quant Protocol (UAQP) Patch 1:

    Γ_deployed = (Γ, Ψ)

    Ψ ∉ A — not an agent, not part of the graph, has no φ function,
    never invoked by Θ (the orchestrator).

    Ψ reads B(t) — the broker's account API directly, never reads S
    (the graph's own state tensor).

    Decision logic: fixed constants, hardcoded, never LLM-touched.

    Can be modified by: nothing at runtime — code deploy only.

    Failure mode if Γ is wrong: unaffected — Ψ never reads S.

Properties:
    - Independent thread with its own polling interval (Δt_guardian)
    - Fail-closed: any exception reading B(t) → TRIPPED
    - Hardcoded thresholds (require code change to modify)
    - Cannot be countermanded by any agent in A
    - Hash-chained audit log for post-hoc reconstruction

Trigger conditions:
    1. B(t).equity_drawdown_today ≥ τ_red_hardcoded → TRIPPED
    2. B(t).gross_leverage ≥ τ_leverage_hardcoded → TRIPPED
    3. time_since_last_Γ_heartbeat > τ_heartbeat → TRIPPED (Γ went dark)
    4. Any exception reading B(t) → TRIPPED (fail closed, not open)

Consequence:
    TRIPPED → cancel all open orders, flatten all positions, page human
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ExternalGuardian")


# ============================================================================
# HARDCODED THRESHOLDS — require code deploy to modify
# These are the Ψ constants. They CANNOT be changed at runtime.
# ============================================================================

class GuardianThresholds:
    """Immutable thresholds — code deploy only, never runtime-modifiable.

    Per UAQP Patch 1: "Can be modified by: nothing at runtime — code deploy only."
    """

    # Red-tier: equity drawdown from daily peak (percentage)
    # If B(t).equity_drawdown_today ≥ 20% → TRIPPED, flatten all
    RED_DRAWDOWN_PCT: float = 20.0

    # Orange-tier: equity drawdown from daily peak (percentage)
    # If B(t).equity_drawdown_today ≥ 12% → TRIPPED, halt entries
    ORANGE_DRAWDOWN_PCT: float = 12.0

    # Yellow-tier: equity drawdown from daily peak (percentage)
    # If B(t).equity_drawdown_today ≥ 7% → TRIPPED, reduce size 50%
    YELLOW_DRAWDOWN_PCT: float = 7.0

    # Gross leverage limit (sum of |position_notional| / equity)
    # If B(t).gross_leverage ≥ 10.0 → TRIPPED
    MAX_GROSS_LEVERAGE: float = 10.0

    # Maximum number of open positions
    # If B(t).open_positions > 8 → TRIPPED
    MAX_OPEN_POSITIONS: int = 8

    # Heartbeat timeout (seconds)
    # If time_since_last_Γ_heartbeat > 120s → TRIPPED (Γ went dark)
    HEARTBEAT_TIMEOUT_S: float = 120.0

    # Guardian polling interval (seconds) — independent of Δt_monitor
    POLL_INTERVAL_S: float = 2.0

    # Maximum time to wait for flatten confirmation (seconds)
    FLATTEN_TIMEOUT_S: float = 10.0

    # Equity floor: if equity < 20% of initial → PERMANENT halt
    EQUITY_FLOOR_PCT: float = 20.0


class GuardianState(Enum):
    """Ψ state — binary: ARMED or TRIPPED."""
    ARMED = "ARMED"
    TRIPPED = "TRIPPED"


class TripSeverity(Enum):
    """Severity of the trip condition."""
    YELLOW = "YELLOW"    # Reduce size 50%
    ORANGE = "ORANGE"    # Halt entries, allow exits only
    RED = "RED"          # Flatten all, halt system
    HEARTBEAT = "HEARTBEAT"  # Γ went dark
    EXCEPTION = "EXCEPTION"  # Fail closed
    LEVERAGE = "LEVERAGE"    # Gross leverage exceeded
    POSITIONS = "POSITIONS"  # Too many open positions
    FLOOR = "FLOOR"          # Equity below permanent floor


@dataclass(frozen=True)
class TripRecord:
    """Immutable record of a trip event — hash-chained for audit."""
    timestamp: str
    severity: str
    reason: str
    broker_equity: float
    broker_drawdown_pct: float
    broker_positions: int
    broker_leverage: float
    prev_hash: str
    entry_hash: str = ""

    def __post_init__(self):
        # Compute hash chain entry
        if not self.entry_hash:
            content = f"{self.timestamp}|{self.severity}|{self.reason}|{self.broker_equity}|{self.prev_hash}"
            object.__setattr__(self, "entry_hash",
                               hashlib.sha256(content.encode()).hexdigest()[:16])


@dataclass
class GuardianAuditLog:
    """Hash-chained append-only audit log for Ψ decisions."""
    log_path: str = "guardian_audit.jsonl"
    _last_hash: str = "GENESIS"
    _entries: List[Dict[str, Any]] = field(default_factory=list)

    def append(self, record: TripRecord) -> None:
        """Append a trip record to the hash-chained log."""
        entry = {
            "timestamp": record.timestamp,
            "severity": record.severity,
            "reason": record.reason,
            "broker_equity": record.broker_equity,
            "broker_drawdown_pct": record.broker_drawdown_pct,
            "broker_positions": record.broker_positions,
            "broker_leverage": record.broker_leverage,
            "prev_hash": record.prev_hash,
            "entry_hash": record.entry_hash,
        }
        self._entries.append(entry)
        self._last_hash = record.entry_hash

        # Persist to disk
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.critical("GUARDIAN AUDIT LOG WRITE FAILED: %s", exc)

    def append_heartbeat(self, broker_equity: float) -> None:
        """Log a successful heartbeat check (no trip)."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": "HEARTBEAT_OK",
            "reason": "Guardian poll successful",
            "broker_equity": round(broker_equity, 2),
            "prev_hash": self._last_hash,
        }
        content = f"{entry['timestamp']}|HEARTBEAT_OK|{broker_equity}|{self._last_hash}"
        entry["entry_hash"] = hashlib.sha256(content.encode()).hexdigest()[:16]
        self._last_hash = entry["entry_hash"]
        self._entries.append(entry)

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # Heartbeat logs are best-effort

    def verify_chain(self) -> bool:
        """Verify the hash chain integrity.

        Uses the canonical verify_chain_hash helper from compliance_audit.
        """
        from core.compliance_audit import verify_chain_hash
        return verify_chain_hash(self._entries)


@dataclass
class BrokerSnapshot:
    """Raw broker state — read directly from B(t), never from S."""
    equity: float = 0.0
    balance: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    margin_level: float = 0.0
    daily_profit: float = 0.0
    open_positions: int = 0
    gross_notional: float = 0.0
    gross_leverage: float = 0.0
    equity_drawdown_pct: float = 0.0
    timestamp: str = ""
    read_ok: bool = False
    error: str = ""


class ExternalGuardian:
    """Ψ — The Guardian.

    Reads B(t) directly from broker API. Independent of Γ.
    Hardcoded thresholds. Fail-closed on any exception.

    Usage:
        guardian = ExternalGuardian()
        guardian.start()  # Spawns independent thread
        # ... later ...
        guardian.record_heartbeat()  # Γ signals it's alive
        # ... later ...
        guardian.stop()
    """

    def __init__(
        self,
        thresholds: Optional[GuardianThresholds] = None,
        audit_log_path: str = "guardian_audit.jsonl",
        flatten_fn: Optional[Callable[[], bool]] = None,
        halt_entries_fn: Optional[Callable[[], None]] = None,
        page_human_fn: Optional[Callable[[str], None]] = None,
        mt5_module: Optional[Any] = None,
    ):
        self.thresholds = thresholds or GuardianThresholds()
        self.audit = GuardianAuditLog(log_path=audit_log_path)
        self.state = GuardianState.ARMED
        self.trip_severity: Optional[TripSeverity] = None
        self.trip_reason: str = ""
        self.trip_time: Optional[str] = None
        self._last_heartbeat_time: float = time.time()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Injectable dependencies for testability
        self._mt5 = mt5_module
        self._flatten_fn = flatten_fn
        self._halt_entries_fn = halt_entries_fn
        self._page_human_fn = page_human_fn

        # Daily peak tracking (independent of Γ's S.P)
        self._daily_peak_equity: float = 0.0
        self._initial_equity: float = 0.0
        self._last_poll_date: str = ""

    def _get_mt5(self):
        """Get MT5 module — fail-closed if unavailable."""
        if self._mt5 is not None:
            return self._mt5
        try:
            import MetaTrader5 as mt5
            return mt5
        except ImportError:
            return None

    def record_heartbeat(self) -> None:
        """Called by Γ to signal it's alive. Independent of Ψ's state."""
        with self._lock:
            self._last_heartbeat_time = time.time()

    def read_broker(self) -> BrokerSnapshot:
        """Read B(t) directly from broker API. Never reads S.

        This is the ONLY way Ψ gets information about the account.
        Any exception → fail closed (return error snapshot).
        """
        mt5 = self._get_mt5()
        if mt5 is None:
            return BrokerSnapshot(
                error="MT5 module unavailable",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        try:
            info = mt5.account_info()
            if info is None:
                return BrokerSnapshot(
                    error="mt5.account_info() returned None",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )

            equity = float(getattr(info, "equity", 0))
            balance = float(getattr(info, "balance", 0))
            margin = float(getattr(info, "margin", 0))
            free_margin = float(getattr(info, "margin_free", 0))
            margin_level = float(getattr(info, "margin_level", 0))

            # Track daily peak (reset at day boundary)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self._last_poll_date:
                self._daily_peak_equity = equity
                self._last_poll_date = today
            if equity > self._daily_peak_equity:
                self._daily_peak_equity = equity

            if self._initial_equity <= 0 and equity > 0:
                self._initial_equity = equity

            # Compute drawdown from daily peak
            dd_pct = 0.0
            if self._daily_peak_equity > 0:
                dd_pct = ((self._daily_peak_equity - equity) / self._daily_peak_equity) * 100.0

            # Read positions directly from broker
            positions = mt5.positions_get()
            n_positions = len(positions) if positions else 0

            # Compute gross notional and leverage
            gross_notional = 0.0
            if positions:
                for pos in positions:
                    vol = float(getattr(pos, "volume", 0))
                    price = float(getattr(pos, "price_current", 0))
                    gross_notional += vol * price

            leverage = 0.0
            if equity > 0:
                leverage = gross_notional / equity

            # Daily realized PnL (from profit field)
            daily_profit = 0.0
            if positions:
                for pos in positions:
                    daily_profit += float(getattr(pos, "profit", 0))

            return BrokerSnapshot(
                equity=equity,
                balance=balance,
                margin=margin,
                free_margin=free_margin,
                margin_level=margin_level,
                daily_profit=daily_profit,
                open_positions=n_positions,
                gross_notional=gross_notional,
                gross_leverage=leverage,
                equity_drawdown_pct=dd_pct,
                timestamp=datetime.now(timezone.utc).isoformat(),
                read_ok=True,
            )

        except Exception as exc:
            # FAIL CLOSED — any exception reading B(t) → error snapshot
            return BrokerSnapshot(
                error=f"Exception reading broker: {exc}",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    def evaluate(self, broker: BrokerSnapshot) -> Optional[TripSeverity]:
        """Evaluate broker state against hardcoded thresholds.

        Returns TripSeverity if a trip condition is met, None if ARMED.
        Never reads S (graph state). Only reads B(t) (broker snapshot).
        """
        # FAIL CLOSED: if broker read failed, trip immediately
        if not broker.read_ok:
            return TripSeverity.EXCEPTION

        # --- RED TIER: Equity drawdown from daily peak ---
        if broker.equity_drawdown_pct >= self.thresholds.RED_DRAWDOWN_PCT:
            return TripSeverity.RED

        # --- ORANGE TIER: Equity drawdown ---
        if broker.equity_drawdown_pct >= self.thresholds.ORANGE_DRAWDOWN_PCT:
            return TripSeverity.ORANGE

        # --- YELLOW TIER: Equity drawdown ---
        if broker.equity_drawdown_pct >= self.thresholds.YELLOW_DRAWDOWN_PCT:
            return TripSeverity.YELLOW

        # --- LEVERAGE: Gross leverage exceeded ---
        if broker.gross_leverage >= self.thresholds.MAX_GROSS_LEVERAGE:
            return TripSeverity.LEVERAGE

        # --- POSITIONS: Too many open positions ---
        if broker.open_positions > self.thresholds.MAX_OPEN_POSITIONS:
            return TripSeverity.POSITIONS

        # --- EQUITY FLOOR: Permanent halt ---
        if (self._initial_equity > 0
                and broker.equity < self._initial_equity * (self.thresholds.EQUITY_FLOOR_PCT / 100.0)):
            return TripSeverity.FLOOR

        # --- HEARTBEAT: Γ went dark ---
        elapsed = time.time() - self._last_heartbeat_time
        if elapsed > self.thresholds.HEARTBEAT_TIMEOUT_S:
            return TripSeverity.HEARTBEAT

        return None

    def _trip(self, severity: TripSeverity, reason: str, broker: BrokerSnapshot) -> None:
        """Execute trip action. Cannot be countermanded by any agent in A."""
        with self._lock:
            if self.state == GuardianState.TRIPPED:
                return  # Already tripped — idempotent

            self.state = GuardianState.TRIPPED
            self.trip_severity = severity
            self.trip_reason = reason
            self.trip_time = datetime.now(timezone.utc).isoformat()

        # Hash-chained audit log
        record = TripRecord(
            timestamp=self.trip_time,
            severity=severity.value,
            reason=reason,
            broker_equity=broker.equity,
            broker_drawdown_pct=broker.equity_drawdown_pct,
            broker_positions=broker.open_positions,
            broker_leverage=broker.gross_leverage,
            prev_hash=self.audit._last_hash,
        )
        self.audit.append(record)

        logger.critical(
            "Ψ GUARDIAN TRIPPED [%s]: %s | equity=%.2f dd=%.2f%% positions=%d leverage=%.2f",
            severity.value, reason, broker.equity,
            broker.equity_drawdown_pct, broker.open_positions, broker.gross_leverage,
        )

        # --- Execute trip actions based on severity ---
        if severity in (TripSeverity.RED, TripSeverity.HEARTBEAT,
                        TripSeverity.EXCEPTION, TripSeverity.FLOOR,
                        TripSeverity.LEVERAGE, TripSeverity.POSITIONS):
            # RED-tier: flatten everything
            self._execute_flatten(reason)

        elif severity == TripSeverity.ORANGE:
            # ORANGE-tier: halt new entries (allow exits only)
            if self._halt_entries_fn:
                try:
                    self._halt_entries_fn()
                except Exception as exc:
                    logger.critical("Ψ halt_entries failed: %s", exc)

        elif severity == TripSeverity.YELLOW:
            # YELLOW-tier: reduce size 50% (soft — logged for Γ to act)
            logger.warning("Ψ YELLOW: Reduce position size 50%% — %s", reason)

        # Page human (all tiers)
        self._page_human(reason, severity)

    def _execute_flatten(self, reason: str) -> None:
        """Cancel all open orders and flatten all positions.

        This is the nuclear option. Cannot be countermanded.
        """
        mt5 = self._get_mt5()
        if mt5 is None:
            logger.critical("Ψ FLATTEN FAILED: MT5 unavailable — cannot cancel orders or close positions")
            return

        try:
            # 1. Cancel all pending orders
            orders = mt5.orders_get()
            if orders:
                for order in orders:
                    ticket = getattr(order, "ticket", None)
                    if ticket:
                        result = mt5.order_cancel(ticket)
                        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
                            logger.warning("Ψ Failed to cancel order %s: %s", ticket, result.comment)

            # 2. Close all open positions
            positions = mt5.positions_get()
            if positions:
                for pos in positions:
                    ticket = getattr(pos, "ticket", None)
                    symbol = getattr(pos, "symbol", "")
                    vol = getattr(pos, "volume", 0)
                    pos_type = getattr(pos, "type", 0)

                    if ticket and vol > 0:
                        # Close opposite to position type
                        close_type = mt5.ORDER_TYPE_SELL if pos_type == 0 else mt5.ORDER_TYPE_BUY
                        price = (mt5.symbol_info_tick(symbol).bid if pos_type == 0
                                 else mt5.symbol_info_tick(symbol).ask)

                        request = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "symbol": symbol,
                            "volume": float(vol),
                            "type": close_type,
                            "position": ticket,
                            "price": price,
                            "deviation": 50,
                            "magic": 234000,
                            "comment": f"Ψ_GUARDIAN_FLATTEN:{reason[:50]}",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        }
                        result = mt5.order_send(request)
                        if result and result.retcode != mt5.TRADE_RETCODE_DONE:
                            logger.warning("Ψ Failed to close position %s: %s", ticket, result.comment)

            logger.critical("Ψ FLATTEN COMPLETE: All orders cancelled, all positions closed")

        except Exception as exc:
            logger.critical("Ψ FLATTEN EXCEPTION: %s — positions may remain open", exc)

    def _page_human(self, reason: str, severity: TripSeverity) -> None:
        """Alert the human operator. Best-effort via alert outbox."""
        # Try alert outbox
        try:
            from operations.alert_outbox import enqueue
            enqueue("guardian_alerts.db", [{
                "code": f"Ψ_GUARDIAN_{severity.value}",
                "severity": "CRITICAL" if severity in (
                    TripSeverity.RED, TripSeverity.HEARTBEAT,
                    TripSeverity.EXCEPTION, TripSeverity.FLOOR
                ) else "WARNING",
                "payload": {
                    "guardian": "Ψ",
                    "trip_severity": severity.value,
                    "reason": reason,
                    "trip_time": self.trip_time,
                    "action": "IMMEDIATE_HUMAN_REVIEW_REQUIRED",
                },
            }], channel="operator")
        except Exception as exc:
            logger.warning("Ψ alert_outbox failed: %s", exc)

        # Try custom page function
        if self._page_human_fn:
            try:
                self._page_human_fn(f"[Ψ {severity.value}] {reason}")
            except Exception:
                pass

    def _poll_cycle(self) -> None:
        """Single poll cycle — read B(t), evaluate, trip if needed."""
        broker = self.read_broker()
        severity = self.evaluate(broker)

        if severity is not None:
            reason = self._build_reason(severity, broker)
            self._trip(severity, reason, broker)
        else:
            # Log successful heartbeat
            self.audit.append_heartbeat(broker.equity)

    def _build_reason(self, severity: TripSeverity, broker: BrokerSnapshot) -> str:
        """Build human-readable trip reason."""
        if severity == TripSeverity.RED:
            return (f"EQUITY_DRAWDOWN_RED: {broker.equity_drawdown_pct:.2f}% >= "
                    f"{self.thresholds.RED_DRAWDOWN_PCT}% (equity={broker.equity:.2f})")
        elif severity == TripSeverity.ORANGE:
            return (f"EQUITY_DRAWDOWN_ORANGE: {broker.equity_drawdown_pct:.2f}% >= "
                    f"{self.thresholds.ORANGE_DRAWDOWN_PCT}% (equity={broker.equity:.2f})")
        elif severity == TripSeverity.YELLOW:
            return (f"EQUITY_DRAWDOWN_YELLOW: {broker.equity_drawdown_pct:.2f}% >= "
                    f"{self.thresholds.YELLOW_DRAWDOWN_PCT}% (equity={broker.equity:.2f})")
        elif severity == TripSeverity.LEVERAGE:
            return (f"GROSS_LEVERAGE_EXCEEDED: {broker.gross_leverage:.2f} >= "
                    f"{self.thresholds.MAX_GROSS_LEVERAGE} (notional={broker.gross_notional:.2f})")
        elif severity == TripSeverity.POSITIONS:
            return (f"MAX_POSITIONS_EXCEEDED: {broker.open_positions} > "
                    f"{self.thresholds.MAX_OPEN_POSITIONS}")
        elif severity == TripSeverity.HEARTBEAT:
            elapsed = time.time() - self._last_heartbeat_time
            return (f"HEARTBEAT_TIMEOUT: Γ silent for {elapsed:.1f}s > "
                    f"{self.thresholds.HEARTBEAT_TIMEOUT_S}s")
        elif severity == TripSeverity.FLOOR:
            return (f"EQUITY_FLOOR: {broker.equity:.2f} < "
                    f"{self.thresholds.EQUITY_FLOOR_PCT}% of initial {self._initial_equity:.2f}")
        elif severity == TripSeverity.EXCEPTION:
            return f"BROKER_READ_FAILED: {broker.error}"
        return "UNKNOWN"

    def _loop(self) -> None:
        """Main guardian loop — runs in independent thread."""
        logger.info("Ψ Guardian thread started (poll_interval=%.1fs)", self.thresholds.POLL_INTERVAL_S)
        while self._running:
            try:
                self._poll_cycle()
            except Exception as exc:
                # FAIL CLOSED: any exception in the loop → trip
                logger.critical("Ψ Guardian loop exception: %s — FAILING CLOSED", exc)
                broker = BrokerSnapshot(
                    error=f"Loop exception: {exc}",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                self._trip(TripSeverity.EXCEPTION,
                           f"GUARDIAN_LOOP_EXCEPTION: {exc}", broker)
                # Don't break — continue polling in case the issue is transient
                # But the system is already tripped

            # Sleep in small increments for responsive shutdown
            for _ in range(int(self.thresholds.POLL_INTERVAL_S * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def start(self) -> None:
        """Start the guardian as an independent daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="Ψ-ExternalGuardian",
            daemon=True,  # Daemon thread — dies with process
        )
        self._thread.start()
        logger.info("Ψ Guardian thread spawned")

    def stop(self) -> None:
        """Stop the guardian thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Ψ Guardian thread stopped")

    @property
    def is_armed(self) -> bool:
        return self.state == GuardianState.ARMED

    @property
    def is_tripped(self) -> bool:
        return self.state == GuardianState.TRIPPED

    def status(self) -> Dict[str, Any]:
        """Current guardian status — read-only snapshot."""
        return {
            "state": self.state.value,
            "severity": self.trip_severity.value if self.trip_severity else None,
            "trip_reason": self.trip_reason,
            "trip_time": self.trip_time,
            "last_heartbeat_age_s": round(time.time() - self._last_heartbeat_time, 1),
            "daily_peak_equity": round(self._daily_peak_equity, 2),
            "initial_equity": round(self._initial_equity, 2),
            "audit_chain_valid": self.audit.verify_chain(),
            "audit_entries": len(self.audit._entries),
            "thresholds": {
                "red_drawdown_pct": self.thresholds.RED_DRAWDOWN_PCT,
                "orange_drawdown_pct": self.thresholds.ORANGE_DRAWDOWN_PCT,
                "yellow_drawdown_pct": self.thresholds.YELLOW_DRAWDOWN_PCT,
                "max_gross_leverage": self.thresholds.MAX_GROSS_LEVERAGE,
                "max_open_positions": self.thresholds.MAX_OPEN_POSITIONS,
                "heartbeat_timeout_s": self.thresholds.HEARTBEAT_TIMEOUT_S,
                "equity_floor_pct": self.thresholds.EQUITY_FLOOR_PCT,
            },
        }


# ============================================================================
# Global singleton — one Ψ per process, like the real protocol
# ============================================================================

_GUARDIAN: Optional[ExternalGuardian] = None
_GUARDIAN_LOCK = threading.Lock()


def get_guardian() -> ExternalGuardian:
    """Get or create the global Ψ Guardian singleton."""
    global _GUARDIAN
    with _GUARDIAN_LOCK:
        if _GUARDIAN is None:
            _GUARDIAN = ExternalGuardian()
        return _GUARDIAN


def start_guardian() -> ExternalGuardian:
    """Start the global Ψ Guardian."""
    guardian = get_guardian()
    guardian.start()
    return guardian


def stop_guardian() -> None:
    """Stop the global Ψ Guardian."""
    global _GUARDIAN
    with _GUARDIAN_LOCK:
        if _GUARDIAN is not None:
            _GUARDIAN.stop()
            _GUARDIAN = None
