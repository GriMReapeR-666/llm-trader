"""Alert Rules Engine

Configurable threshold-based alerting system that monitors metrics and
fires alerts when boundaries are crossed.

Supports:
- Drawdown alerts
- Latency alerts
- Cost alerts
- Consecutive loss alerts
- Divergence alerts
- Custom metric alerts

Usage:
    engine = AlertRulesEngine()
    engine.add_rule("high_drawdown", "drawdown_pct", ">", 0.06, severity="CRITICAL")
    alerts = engine.evaluate({"drawdown_pct": 0.07})
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("AlertRules")


class AlertRule:
    """A single alert rule."""

    def __init__(
        self,
        name: str,
        metric: str,
        operator: str,
        threshold: float,
        severity: str = "WARNING",
        description: str = "",
        cooldown_seconds: int = 300,
        min_samples: Optional[int] = None,
    ):
        self.name = name
        self.metric = metric
        self.operator = operator
        self.threshold = threshold
        self.severity = severity
        self.description = description or f"{metric} {operator} {threshold}"
        self.cooldown_seconds = cooldown_seconds
        self.min_samples = min_samples
        self.last_fired = 0

    def evaluate(self, metrics: Dict[str, Any]) -> bool:
        """Evaluate if this rule should fire."""
        value = metrics.get(self.metric)
        if value is None:
            return False

        # Minimum-sample guard: performance rules are meaningless on an empty
        # or tiny trade history (e.g. rolling_win_rate=0.0 with zero closed
        # trades fired the low-win-rate alert every few minutes).
        if self.min_samples is not None:
            samples = metrics.get("rolling_win_rate_samples")
            try:
                samples = int(samples) if samples is not None else 0
            except (TypeError, ValueError):
                samples = 0
            if samples < self.min_samples:
                return False

        # Check cooldown
        if time.time() - self.last_fired < self.cooldown_seconds:
            return False

        try:
            value = float(value)
        except (TypeError, ValueError):
            return False

        if self.operator == ">":
            return value > self.threshold
        elif self.operator == ">=":
            return value >= self.threshold
        elif self.operator == "<":
            return value < self.threshold
        elif self.operator == "<=":
            return value <= self.threshold
        elif self.operator == "==":
            return value == self.threshold
        elif self.operator == "!=":
            return value != self.threshold
        elif self.operator == "abs>":
            return abs(value) > self.threshold
        elif self.operator == "abs>=":
            return abs(value) >= self.threshold

        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "severity": self.severity,
            "description": self.description,
            "cooldown_seconds": self.cooldown_seconds,
        }


class AlertRulesEngine:
    """Configurable alert rules engine."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.state_path = os.path.join(self.base_dir, "artifacts", "alert_state.json")
        self.state = self._load_state()
        self.rules: List[AlertRule] = []
        self._load_default_rules()

    def _load_state(self) -> Dict[str, Any]:
        """Load alert state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "fired_alerts": [],
            "suppressed_until": {},
        }

    def _save_state(self) -> None:
        """Save alert state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save alert state: %s", exc)

    def _load_default_rules(self) -> None:
        """Load default alert rules."""
        # Drawdown rules
        self.add_rule("drawdown_warning", "drawdown_pct", ">", 0.03, severity="WARNING",
                       description="Drawdown exceeded 3%")
        self.add_rule("drawdown_critical", "drawdown_pct", ">", 0.06, severity="CRITICAL",
                       description="Drawdown exceeded 6%")
        self.add_rule("drawdown_halt", "drawdown_pct", ">", 0.10, severity="HALT",
                       description="Drawdown exceeded 10% - HALT")

        # Consecutive loss rules
        self.add_rule("consecutive_losses_3", "consecutive_losses", ">=", 3, severity="INFO",
                       description="3 consecutive losses")
        self.add_rule("consecutive_losses_5", "consecutive_losses", ">=", 5, severity="WARNING",
                       description="5 consecutive losses")
        self.add_rule("consecutive_losses_7", "consecutive_losses", ">=", 7, severity="CRITICAL",
                       description="7 consecutive losses")

        # Latency rules
        self.add_rule("latency_warning", "latency_p95_ms", ">", 500, severity="WARNING",
                       description="P95 latency exceeded 500ms")
        self.add_rule("latency_critical", "latency_p95_ms", ">", 1000, severity="CRITICAL",
                       description="P95 latency exceeded 1000ms")

        # Cost rules
        self.add_rule("cost_daily_warning", "daily_cost_usd", ">", 1.0, severity="WARNING",
                       description="Daily LLM cost exceeded $1")
        self.add_rule("cost_daily_critical", "daily_cost_usd", ">", 5.0, severity="CRITICAL",
                       description="Daily LLM cost exceeded $5")

        # Divergence rules
        self.add_rule("divergence_warning", "overall_divergence", ">", 0.2, severity="WARNING",
                       description="Live-backtest divergence > 20%")
        self.add_rule("divergence_critical", "overall_divergence", ">", 0.4, severity="CRITICAL",
                       description="Live-backtest divergence > 40%")

        # Win rate rules — require a meaningful sample before alarming
        self.add_rule("low_win_rate", "rolling_win_rate", "<", 0.40, severity="WARNING",
                       description="Rolling win rate below 40%", min_samples=20)

        # Expectancy rules
        self.add_rule("negative_expectancy", "rolling_expectancy_r", "<", 0, severity="CRITICAL",
                       description="Rolling expectancy went negative")

    def add_rule(
        self,
        name: str,
        metric: str,
        operator: str,
        threshold: float,
        severity: str = "WARNING",
        description: str = "",
        cooldown_seconds: int = 300,
        min_samples: Optional[int] = None,
    ) -> None:
        """Add an alert rule."""
        rule = AlertRule(
            name=name,
            metric=metric,
            operator=operator,
            threshold=threshold,
            severity=severity,
            description=description,
            cooldown_seconds=cooldown_seconds,
            min_samples=min_samples,
        )
        self.rules.append(rule)

    def evaluate(self, metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Evaluate all rules against current metrics."""
        fired = []

        for rule in self.rules:
            if rule.evaluate(metrics):
                alert = {
                    "rule": rule.name,
                    "severity": rule.severity,
                    "description": rule.description,
                    "metric": rule.metric,
                    "value": metrics.get(rule.metric),
                    "threshold": rule.threshold,
                    "operator": rule.operator,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                fired.append(alert)

                # Update state
                rule.last_fired = time.time()
                self.state["fired_alerts"].append(alert)
                # Keep last 200 alerts
                self.state["fired_alerts"] = self.state["fired_alerts"][-200:]

                log.log(
                    self._severity_to_log_level(rule.severity),
                    "ALERT [%s]: %s (value=%s, threshold=%s)",
                    rule.severity, rule.description,
                    metrics.get(rule.metric), rule.threshold,
                )

        if fired:
            self._save_state()

        return fired

    def _severity_to_log_level(self, severity: str) -> int:
        """Convert severity to Python log level."""
        levels = {
            "INFO": 20,
            "WARNING": 30,
            "CRITICAL": 40,
            "HALT": 50,
        }
        return levels.get(severity, 30)

    def suppress_alert(self, rule_name: str, seconds: int) -> None:
        """Suppress an alert for a duration."""
        self.state["suppressed_until"][rule_name] = time.time() + seconds
        self._save_state()

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent alerts."""
        return self.state.get("fired_alerts", [])[-limit:]

    def get_rules(self) -> List[Dict[str, Any]]:
        """Get all rules."""
        return [r.to_dict() for r in self.rules]

    def get_status(self) -> Dict[str, Any]:
        """Get engine status."""
        return {
            "total_rules": len(self.rules),
            "total_alerts_fired": len(self.state.get("fired_alerts", [])),
            "suppressed_count": len(self.state.get("suppressed_until", {})),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Statistics alias — sibling engines expose get_stats(); the module
        CLI historically used get_status(). Both return the same snapshot."""
        return self.get_status()


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Alert Rules Engine")
    parser.add_argument("--rules", action="store_true", help="Show all rules")
    parser.add_argument("--alerts", action="store_true", help="Show recent alerts")
    parser.add_argument("--status", action="store_true", help="Show engine status")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    engine = AlertRulesEngine()

    if args.rules:
        rules = engine.get_rules()
        print(json.dumps(rules, indent=2, default=str))
    elif args.alerts:
        alerts = engine.get_recent_alerts()
        print(json.dumps(alerts, indent=2, default=str))
    elif args.status:
        status = engine.get_status()
        print(json.dumps(status, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
