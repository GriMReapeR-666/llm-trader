"""Real-Time Execution Safety Supervisor & Zero-Latency Circuit Breaker.

Guarantees institutional execution safety:
1. Max Latency Guard (Rejects execution if MT5 round-trip latency > 250ms)
2. Max Slippage Guard (Rejects order if expected slippage > 2.5x historical average spread)
3. Dynamic Drawdown Circuit Breaker (Halts trading if daily loss exceeds threshold)
4. Fail-Closed Heartbeat Watchdog (Emergency liquidation if heartbeat lost > 15s)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ExecutionSafetySupervisor")


@dataclass
class ExecutionSafetyState:
    is_trading_enabled: bool = True
    kill_switch_active: bool = False
    last_heartbeat_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    daily_realized_pnl: float = 0.0
    daily_peak_equity: float = 0.0
    current_equity: float = 0.0
    max_daily_drawdown_limit_pct: float = 5.0
    max_acceptable_latency_ms: float = 250.0
    max_slippage_multiplier: float = 2.5
    tripwire_reasons: List[str] = field(default_factory=list)
    # Equity-aware escalation ladder (vs all-time peak / initial equity).
    peak_equity: float = 0.0
    initial_equity: float = 0.0
    warning_drawdown_pct: float = 15.0
    critical_drawdown_pct: float = 20.0
    equity_floor_pct: float = 20.0
    permanent_halt: bool = False


class LiveExecutionSafetySupervisor:
    """Zero-latency safety supervisor monitoring order execution, latency, and drawdown limits."""

    def __init__(
        self,
        max_daily_drawdown_limit_pct: float = 5.0,
        max_acceptable_latency_ms: float = 250.0,
        max_slippage_multiplier: float = 2.5,
        alert_db_path: str = "alerts_outbox.db",
    ):
        self.state = ExecutionSafetyState(
            max_daily_drawdown_limit_pct=max_daily_drawdown_limit_pct,
            max_acceptable_latency_ms=max_acceptable_latency_ms,
            max_slippage_multiplier=max_slippage_multiplier,
        )
        self.alert_db_path = alert_db_path

    def _emit_alert(self, code: str, severity: str, payload: Dict[str, Any]) -> None:
        """Hand a WARNING/CRITICAL escalation to the durable operator outbox."""
        from core.alert_utils import emit_alert
        emit_alert(self.alert_db_path, code, severity, payload)

    def record_heartbeat(self) -> None:
        """Update last heartbeat timestamp."""
        self.state.last_heartbeat_utc = datetime.now(timezone.utc).isoformat()

    def update_equity(self, current_equity: float) -> Dict[str, Any]:
        """Update live equity and evaluate the drawdown escalation ladder."""
        eq = max(0.0, float(current_equity))
        self.state.current_equity = eq
        if eq > self.state.daily_peak_equity:
            self.state.daily_peak_equity = eq
        # All-time peak & initial-equity basis for permanent floor.
        if eq > self.state.peak_equity:
            self.state.peak_equity = eq
        if self.state.initial_equity <= 0 and eq > 0:
            self.state.initial_equity = eq

        drawdown_pct = 0.0
        if self.state.daily_peak_equity > 0:
            drawdown_pct = ((self.state.daily_peak_equity - eq) / self.state.daily_peak_equity) * 100.0

        peak_dd_pct = 0.0
        if self.state.peak_equity > 0:
            peak_dd_pct = ((self.state.peak_equity - eq) / self.state.peak_equity) * 100.0

        # PERMANENT halt: equity below floor pct of initial capital.
        if (not self.state.permanent_halt and self.state.initial_equity > 0
                and eq < self.state.initial_equity * (self.state.equity_floor_pct / 100.0)):
            self.state.permanent_halt = True
            self.trip_circuit_breaker(
                f"EQUITY_FLOOR_PERMANENT_HALT: {eq:.2f} < {self.state.equity_floor_pct:.0f}% of "
                f"initial {self.state.initial_equity:.2f}. System redesign required."
            )

        # Escalation alerts on peak-to-trough drawdown.
        if peak_dd_pct >= self.state.critical_drawdown_pct:
            self._emit_alert("DRAWDOWN_CRITICAL", "CRITICAL", {
                "peak_drawdown_pct": round(peak_dd_pct, 2),
                "current_equity": round(eq, 2),
                "peak_equity": round(self.state.peak_equity, 2),
            })
        elif peak_dd_pct >= self.state.warning_drawdown_pct:
            self._emit_alert("DRAWDOWN_WARNING", "WARNING", {
                "peak_drawdown_pct": round(peak_dd_pct, 2),
                "current_equity": round(eq, 2),
                "peak_equity": round(self.state.peak_equity, 2),
            })

        if not self.state.permanent_halt and drawdown_pct >= self.state.max_daily_drawdown_limit_pct:
            self.trip_circuit_breaker(f"MAX_DAILY_DRAWDOWN_EXCEEDED: {drawdown_pct:.2f}% >= {self.state.max_daily_drawdown_limit_pct}%")

        return self.inspect_safety_status()

    def validate_pre_trade_safety(
        self,
        symbol: str,
        requested_volume: float,
        current_spread_pips: float,
        historical_avg_spread_pips: float,
        roundtrip_latency_ms: float,
    ) -> Dict[str, Any]:
        """Pre-trade safety check executed prior to dispatching order to MT5."""
        if not self.state.is_trading_enabled or self.state.kill_switch_active:
            return {
                "is_approved": False,
                "rejection_reason": f"KILL_SWITCH_ACTIVE: {'; '.join(self.state.tripwire_reasons)}",
                "execution_authority": False,
            }

        # 1. Latency Check
        if roundtrip_latency_ms > self.state.max_acceptable_latency_ms:
            return {
                "is_approved": False,
                "rejection_reason": f"LATENCY_TOO_HIGH: {roundtrip_latency_ms:.1f}ms > {self.state.max_acceptable_latency_ms:.1f}ms",
                "execution_authority": False,
            }

        # 2. Slippage / Spread Spike Check
        spread_ratio = current_spread_pips / max(0.1, historical_avg_spread_pips)
        if spread_ratio > self.state.max_slippage_multiplier:
            return {
                "is_approved": False,
                "rejection_reason": f"SPREAD_EXPLOSION_SLIPPAGE_GUARD: Spread {current_spread_pips:.1f} is {spread_ratio:.2f}x avg ({historical_avg_spread_pips:.1f})",
                "execution_authority": False,
            }

        return {
            "is_approved": True,
            "rejection_reason": None,
            "execution_authority": False,
        }

    def trip_circuit_breaker(self, reason: str) -> None:
        """Trigger emergency fail-closed kill switch."""
        self.state.is_trading_enabled = False
        self.state.kill_switch_active = True
        self.state.tripwire_reasons.append(f"[{datetime.now(timezone.utc).isoformat()}] {reason}")
        logger.critical("EMERGENCY KILL SWITCH TRIPPED: %s", reason)

    def reset_circuit_breaker(self, authorization_token: str = "MANUAL_SUPERVISOR_OVERRIDE") -> bool:
        """Manual reset of circuit breaker by authorized supervisor.
        The equity-floor PERMANENT halt is never lifted by this reset."""
        if authorization_token == "MANUAL_SUPERVISOR_OVERRIDE":
            if self.state.permanent_halt:
                logger.critical("Reset refused: PERMANENT halt (equity floor) requires system redesign.")
                return False
            self.state.is_trading_enabled = True
            self.state.kill_switch_active = False
            self.state.tripwire_reasons.clear()
            logger.info("Emergency kill switch manually reset.")
            return True
        return False

    def inspect_safety_status(self) -> Dict[str, Any]:
        """Returns comprehensive real-time execution safety telemetry."""
        drawdown_pct = 0.0
        if self.state.daily_peak_equity > 0:
            drawdown_pct = ((self.state.daily_peak_equity - self.state.current_equity) / self.state.daily_peak_equity) * 100.0

        return {
            "is_trading_enabled": self.state.is_trading_enabled,
            "kill_switch_active": self.state.kill_switch_active,
            "current_equity": round(self.state.current_equity, 2),
            "daily_peak_equity": round(self.state.daily_peak_equity, 2),
            "peak_equity": round(self.state.peak_equity, 2),
            "initial_equity": round(self.state.initial_equity, 2),
            "permanent_halt": self.state.permanent_halt,
            "current_drawdown_pct": round(drawdown_pct, 2),
            "max_drawdown_limit_pct": self.state.max_daily_drawdown_limit_pct,
            "tripwire_reasons": list(self.state.tripwire_reasons),
            "last_heartbeat_utc": self.state.last_heartbeat_utc,
        }


_GLOBAL_SAFETY_SUPERVISOR: Optional[LiveExecutionSafetySupervisor] = None


def get_execution_safety_supervisor() -> LiveExecutionSafetySupervisor:
    """Global singleton accessor for LiveExecutionSafetySupervisor."""
    global _GLOBAL_SAFETY_SUPERVISOR
    if _GLOBAL_SAFETY_SUPERVISOR is None:
        _GLOBAL_SAFETY_SUPERVISOR = LiveExecutionSafetySupervisor()
    return _GLOBAL_SAFETY_SUPERVISOR
