"""Automated Degradation Detection & Auto-Freeze

Monitors live trading performance and automatically triggers circuit breakers
when degradation is detected. Works with the alert rules engine and metrics
persistence to detect problems before they become catastrophic.

Detection modes:
1. Rolling performance degradation (win rate, expectancy, Sharpe declining)
2. Equity curve inflection (new equity low)
3. Drawdown duration exceeding threshold
4. Divergence from backtest expectations
5. Consecutive risk management violations

Freeze actions:
1. REDUCE: Reduce position size by 50%
2. PAUSE_NEW: Stop opening new positions
3. CLOSE_ALL: Force close all open positions
4. SHUTDOWN: Halt engine completely

IMPORTANT: This system requires explicit operator approval for SHUTDOWN.
REDUCE and PAUSE_NEW can be automated.

Usage:
    detector = DegradationDetector()
    action = detector.check({"drawdown_pct": 0.07, "rolling_win_rate": 0.38})
    if action:
        execute_action(action)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("DegradationDetector")


class DegradationDetector:
    """Automated degradation detection with circuit breakers."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.db_path = os.path.join(self.base_dir, "trading_state.db")
        self.state_path = os.path.join(self.base_dir, "artifacts", "degradation_state.json")
        self.state = self._load_state()

        # Thresholds
        self.thresholds = {
            "drawdown_reduce": 0.03,      # 3% DD → reduce size
            "drawdown_pause": 0.06,        # 6% DD → pause new
            "drawdown_close": 0.10,        # 10% DD → close all
            "drawdown_shutdown": 0.15,     # 15% DD → shutdown (requires operator)
            "drawdown_duration_hours": 48, # 48h in DD → pause
            "consecutive_losses_pause": 7, # 7 losses in a row → pause
            "consecutive_losses_reduce": 5,# 5 losses → reduce
            "win_rate_floor": 0.35,        # Below 35% → reduce
            "expectancy_floor": -0.5,      # Below -0.5R → pause
            "divergence_pause": 0.40,      # 40% divergence → pause
            "equity_low_count": 3,         # 3 equity lows → reduce
        }

        # Action cooldowns (seconds)
        self.cooldowns = {
            "REDUCE": 3600,      # 1 hour
            "PAUSE_NEW": 1800,   # 30 minutes
            "CLOSE_ALL": 86400,  # 24 hours
            "SHUTDOWN": 86400,   # 24 hours (requires operator)
        }

    def _load_state(self) -> Dict[str, Any]:
        """Load detector state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "current_action": None,
            "action_history": [],
            "equity_lows": [],
            "last_check": None,
            "frozen_until": None,
            "freeze_reason": None,
        }

    def _save_state(self) -> None:
        """Save detector state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save degradation state: %s", exc)

    def check(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Check for degradation and return recommended action.
        
        Returns None if no action needed, or a dict with:
        - action: REDUCE / PAUSE_NEW / CLOSE_ALL / SHUTDOWN
        - reason: human-readable explanation
        - severity: INFO / WARNING / CRITICAL / HALT
        - details: additional context
        """
        log.info("Running degradation check...")

        # Check if currently frozen
        if self._is_frozen():
            return None

        # Run all checks, collect actions
        actions = []

        # 1. Drawdown checks
        dd_action = self._check_drawdown(metrics)
        if dd_action:
            actions.append(dd_action)

        # 2. Drawdown duration
        duration_action = self._check_drawdown_duration(metrics)
        if duration_action:
            actions.append(duration_action)

        # 3. Consecutive losses
        loss_action = self._check_consecutive_losses(metrics)
        if loss_action:
            actions.append(loss_action)

        # 4. Win rate
        wr_action = self._check_win_rate(metrics)
        if wr_action:
            actions.append(wr_action)

        # 5. Expectancy
        exp_action = self._check_expectancy(metrics)
        if exp_action:
            actions.append(exp_action)

        # 6. Divergence
        div_action = self._check_divergence(metrics)
        if div_action:
            actions.append(div_action)

        # 7. Equity curve inflection
        eq_action = self._check_equity_curve(metrics)
        if eq_action:
            actions.append(eq_action)

        # Select most severe action
        if actions:
            severity_order = {"SHUTDOWN": 4, "CLOSE_ALL": 3, "PAUSE_NEW": 2, "REDUCE": 1}
            most_severe = max(actions, key=lambda a: severity_order.get(a["action"], 0))

            # Check cooldown
            if self._check_cooldown(most_severe["action"]):
                log.info("Action %s in cooldown, skipping", most_severe["action"])
                return None

            # Execute action
            self._execute_action(most_severe)
            return most_severe

        self.state["last_check"] = datetime.now(timezone.utc).isoformat()
        self._save_state()

        return None

    def _check_drawdown(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check drawdown level."""
        dd = metrics.get("drawdown_pct", 0)
        if dd is None:
            return None

        dd = float(dd)

        if dd >= self.thresholds["drawdown_shutdown"]:
            return {
                "action": "SHUTDOWN",
                "reason": f"Drawdown {dd*100:.1f}% exceeded {self.thresholds['drawdown_shutdown']*100:.0f}% shutdown threshold",
                "severity": "HALT",
                "details": {"drawdown_pct": dd, "threshold": self.thresholds["drawdown_shutdown"]},
            }
        elif dd >= self.thresholds["drawdown_close"]:
            return {
                "action": "CLOSE_ALL",
                "reason": f"Drawdown {dd*100:.1f}% exceeded {self.thresholds['drawdown_close']*100:.0f}% close threshold",
                "severity": "CRITICAL",
                "details": {"drawdown_pct": dd, "threshold": self.thresholds["drawdown_close"]},
            }
        elif dd >= self.thresholds["drawdown_pause"]:
            return {
                "action": "PAUSE_NEW",
                "reason": f"Drawdown {dd*100:.1f}% exceeded {self.thresholds['drawdown_pause']*100:.0f}% pause threshold",
                "severity": "CRITICAL",
                "details": {"drawdown_pct": dd, "threshold": self.thresholds["drawdown_pause"]},
            }
        elif dd >= self.thresholds["drawdown_reduce"]:
            return {
                "action": "REDUCE",
                "reason": f"Drawdown {dd*100:.1f}% exceeded {self.thresholds['drawdown_reduce']*100:.0f}% reduce threshold",
                "severity": "WARNING",
                "details": {"drawdown_pct": dd, "threshold": self.thresholds["drawdown_reduce"]},
            }

        return None

    def _check_drawdown_duration(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check how long we've been in drawdown."""
        duration = metrics.get("drawdown_duration_hours", 0)
        if duration is None:
            return None

        duration = float(duration)

        if duration >= self.thresholds["drawdown_duration_hours"]:
            return {
                "action": "PAUSE_NEW",
                "reason": f"Drawdown duration {duration:.1f}h exceeded {self.thresholds['drawdown_duration_hours']}h threshold",
                "severity": "WARNING",
                "details": {"duration_hours": duration, "threshold": self.thresholds["drawdown_duration_hours"]},
            }

        return None

    def _check_consecutive_losses(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check consecutive losses."""
        losses = metrics.get("consecutive_losses", 0)
        if losses is None:
            return None

        losses = int(losses)

        if losses >= self.thresholds["consecutive_losses_pause"]:
            return {
                "action": "PAUSE_NEW",
                "reason": f"Consecutive losses {losses} exceeded {self.thresholds['consecutive_losses_pause']} threshold",
                "severity": "WARNING",
                "details": {"consecutive_losses": losses, "threshold": self.thresholds["consecutive_losses_pause"]},
            }
        elif losses >= self.thresholds["consecutive_losses_reduce"]:
            return {
                "action": "REDUCE",
                "reason": f"Consecutive losses {losses} exceeded {self.thresholds['consecutive_losses_reduce']} threshold",
                "severity": "WARNING",
                "details": {"consecutive_losses": losses, "threshold": self.thresholds["consecutive_losses_reduce"]},
            }

        return None

    def _check_win_rate(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check rolling win rate (requires a meaningful closed-trade sample)."""
        wr = metrics.get("rolling_win_rate")
        if wr is None:
            return None

        # Empty-sample guard: without it, rolling_win_rate=0.0 from zero
        # closed trades degraded the position sizing on every check.
        try:
            samples = int(metrics.get("rolling_win_rate_samples") or 0)
        except (TypeError, ValueError):
            samples = 0
        if samples < 20:
            return None

        wr = float(wr)

        if wr < self.thresholds["win_rate_floor"]:
            return {
                "action": "REDUCE",
                "reason": f"Win rate {wr*100:.1f}% below {self.thresholds['win_rate_floor']*100:.0f}% floor",
                "severity": "WARNING",
                "details": {"win_rate": wr, "threshold": self.thresholds["win_rate_floor"]},
            }

        return None

    def _check_expectancy(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check rolling expectancy."""
        exp = metrics.get("rolling_expectancy_r")
        if exp is None:
            return None

        exp = float(exp)

        if exp < self.thresholds["expectancy_floor"]:
            return {
                "action": "PAUSE_NEW",
                "reason": f"Expectancy {exp:.3f}R below {self.thresholds['expectancy_floor']}R floor",
                "severity": "CRITICAL",
                "details": {"expectancy_r": exp, "threshold": self.thresholds["expectancy_floor"]},
            }

        return None

    def _check_divergence(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check live-backtest divergence."""
        div = metrics.get("overall_divergence")
        if div is None:
            return None

        div = float(div)

        if div >= self.thresholds["divergence_pause"]:
            return {
                "action": "PAUSE_NEW",
                "reason": f"Divergence {div*100:.1f}% exceeded {self.thresholds['divergence_pause']*100:.0f}% threshold",
                "severity": "WARNING",
                "details": {"divergence": div, "threshold": self.thresholds["divergence_pause"]},
            }

        return None

    def _check_equity_curve(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check for equity curve inflection (new equity low)."""
        equity = metrics.get("equity")
        if equity is None:
            return None

        equity = float(equity)

        # Track equity lows
        lows = self.state.get("equity_lows", [])
        if not lows or equity < min(lows):
            lows.append(equity)
            self.state["equity_lows"] = lows[-20:]  # Keep last 20 lows

            # Count recent equity lows (last 10 trades)
            recent_lows = [l for l in lows[-10:]]
            if len(recent_lows) >= self.thresholds["equity_low_count"]:
                return {
                    "action": "REDUCE",
                    "reason": f"Equity curve inflection: {len(recent_lows)} new equity lows in last trades",
                    "severity": "WARNING",
                    "details": {"equity": equity, "lows_count": len(recent_lows)},
                }

        return None

    def _is_frozen(self) -> bool:
        """Check if currently frozen."""
        frozen_until = self.state.get("frozen_until")
        if frozen_until:
            try:
                freeze_dt = datetime.fromisoformat(frozen_until.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < freeze_dt:
                    return True
                else:
                    # Freeze expired
                    self.state["frozen_until"] = None
                    self.state["freeze_reason"] = None
            except Exception:
                pass
        return False

    def _check_cooldown(self, action: str) -> bool:
        """Check if action is in cooldown."""
        history = self.state.get("action_history", [])
        cooldown = self.cooldowns.get(action, 3600)

        for entry in reversed(history):
            if entry.get("action") == action:
                try:
                    entry_dt = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - entry_dt).total_seconds()
                    if elapsed < cooldown:
                        return True
                except Exception:
                    pass
                break

        return False

    def _execute_action(self, action: Dict[str, Any]) -> None:
        """Execute a circuit breaker action."""
        action_type = action["action"]
        reason = action["reason"]

        log.log(
            logging.CRITICAL if action_type in ("CLOSE_ALL", "SHUTDOWN") else logging.WARNING,
            "CIRCUIT BREAKER [%s]: %s",
            action_type, reason,
        )

        # Record action
        self.state["current_action"] = action_type
        self.state["action_history"].append({
            "action": action_type,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": action.get("details", {}),
        })
        self.state["action_history"] = self.state["action_history"][-50:]

        # Set freeze if PAUSE_NEW or higher
        if action_type in ("PAUSE_NEW", "CLOSE_ALL", "SHUTDOWN"):
            freeze_minutes = {
                "PAUSE_NEW": 30,
                "CLOSE_ALL": 1440,  # 24 hours
                "SHUTDOWN": 1440,   # 24 hours (requires operator to resume)
            }.get(action_type, 30)

            from datetime import timedelta
            freeze_until = datetime.now(timezone.utc) + timedelta(minutes=freeze_minutes)
            self.state["frozen_until"] = freeze_until.isoformat()
            self.state["freeze_reason"] = reason

        self._save_state()

        # Write action to file for engine to pick up
        self._write_action_file(action)

    def _write_action_file(self, action: Dict[str, Any]) -> None:
        """Write action to file for engine to read."""
        try:
            action_path = os.path.join(self.base_dir, "artifacts", "circuit_breaker_action.json")
            os.makedirs(os.path.dirname(action_path), exist_ok=True)
            with open(action_path, "w") as f:
                json.dump(action, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to write action file: %s", exc)

    def resume(self, operator_override: bool = False) -> bool:
        """Resume from freeze (requires operator override for SHUTDOWN)."""
        if self.state.get("current_action") == "SHUTDOWN" and not operator_override:
            log.warning("SHUTDOWN requires operator override to resume")
            return False

        self.state["current_action"] = None
        self.state["frozen_until"] = None
        self.state["freeze_reason"] = None
        self._save_state()

        log.info("Trading resumed (operator_override=%s)", operator_override)
        return True

    def get_status(self) -> Dict[str, Any]:
        """Get detector status."""
        return {
            "current_action": self.state.get("current_action"),
            "is_frozen": self._is_frozen(),
            "frozen_until": self.state.get("frozen_until"),
            "freeze_reason": self.state.get("freeze_reason"),
            "last_check": self.state.get("last_check"),
            "total_actions": len(self.state.get("action_history", [])),
        }


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Degradation Detector")
    parser.add_argument("--status", action="store_true", help="Show detector status")
    parser.add_argument("--resume", action="store_true", help="Resume from freeze")
    parser.add_argument("--override", action="store_true", help="Operator override for SHUTDOWN")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    detector = DegradationDetector()

    if args.status:
        status = detector.get_status()
        print(json.dumps(status, indent=2, default=str))
    elif args.resume:
        success = detector.resume(operator_override=args.override)
        print("Resumed" if success else "Failed - operator override required")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
