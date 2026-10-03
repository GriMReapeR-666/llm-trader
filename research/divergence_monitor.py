"""Live-vs-Backtest Divergence Monitor

Continuously monitors live/demo trading performance against backtest
expectations and alerts when significant divergence is detected.

This is critical for detecting:
- Regime shifts (live performance degrades vs backtest)
- Overfitting (backtest was too optimistic)
- Execution quality issues (slippage/spread worse than modeled)
- Model degradation (RL/PPO models losing edge)

Usage:
    monitor = DivergenceMonitor()
    report = monitor.check_divergence("EURUSD")
    if report["alert"]:
        print(f"ALERT: {report['reason']}")
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("DivergenceMonitor")


class DivergenceMonitor:
    """Monitors live vs backtest performance divergence."""

    def __init__(self, base_dir: str = "", lookback_trades: int = 50):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.db_path = os.path.join(self.base_dir, "trading_state.db")
        self.state_path = os.path.join(self.base_dir, "artifacts", "divergence_state.json")
        self.lookback_trades = lookback_trades
        self.state = self._load_state()

        # Divergence thresholds
        self.win_rate_threshold = 0.15  # 15% divergence triggers warning
        self.expectancy_threshold = 0.25  # 25% divergence triggers warning
        self.sharpe_threshold = 0.3  # 30% divergence triggers warning
        self.consecutive_loss_threshold = 5  # Consecutive losses triggers alert

    def _load_state(self) -> Dict[str, Any]:
        """Load monitor state."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_check": None,
            "alerts": [],
            "history": [],
            "consecutive_failures": 0,
        }

    def _save_state(self) -> None:
        """Save monitor state."""
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save divergence state: %s", exc)

    def check_divergence(self, symbol: str) -> Dict[str, Any]:
        """Check for divergence between live and backtest performance."""
        result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alert": False,
            "severity": "OK",
            "reason": "",
            "metrics": {},
        }

        try:
            if not os.path.exists(self.db_path):
                result["reason"] = "no_trading_db"
                return result

            try:
                from research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            tickets = sorted(broker_reconciled_tickets(self.db_path))
            if not tickets:
                result["reason"] = "no_broker_reconciled_trades"
                return result

            con = sqlite3.connect(self.db_path, timeout=2)
            con.row_factory = sqlite3.Row

            # Get recent broker-reconciled trades
            marks = ",".join("?" for _ in tickets)
            trades = con.execute(
                "SELECT gross_pnl, risk_amount, side, symbol, opened_at, closed_at "
                "FROM trades WHERE symbol=? AND closed_at IS NOT NULL "
                "AND ticket IN (%s) "
                "ORDER BY closed_at DESC LIMIT ?" % marks,
                (symbol, *tickets, self.lookback_trades)
            ).fetchall()

            if len(trades) < 10:
                result["reason"] = "insufficient_trades"
                result["metrics"]["trade_count"] = len(trades)
                return result

            # Compute live metrics
            live_metrics = self._compute_live_metrics(trades)

            # Get backtest expectations (from last backtest results)
            bt_metrics = self._get_backtest_expectations(symbol)

            if not bt_metrics:
                result["reason"] = "no_backtest_data"
                result["metrics"] = live_metrics
                return result

            # Compare
            divergence = self._compute_divergence(live_metrics, bt_metrics)
            result["metrics"] = {
                "live": live_metrics,
                "backtest": bt_metrics,
                "divergence": divergence,
            }

            # Check for alerts
            alert_check = self._check_alerts(divergence, live_metrics)
            result["alert"] = alert_check["alert"]
            result["severity"] = alert_check["severity"]
            result["reason"] = alert_check["reason"]

            con.close()

        except Exception as exc:
            result["reason"] = f"error: {exc}"
            log.error("Divergence check failed: %s", exc)

        # Update state
        self.state["last_check"] = result["timestamp"]
        if result["alert"]:
            self.state["alerts"].append({
                "timestamp": result["timestamp"],
                "symbol": symbol,
                "severity": result["severity"],
                "reason": result["reason"],
            })
            # Keep last 100 alerts
            self.state["alerts"] = self.state["alerts"][-100:]
        self._save_state()

        return result

    def _compute_live_metrics(self, trades: List) -> Dict[str, Any]:
        """Compute live trading metrics."""
        r_multiples = []
        for t in trades:
            pnl = t["gross_pnl"] or 0
            risk = t["risk_amount"] or 1
            r_multiples.append(pnl / max(risk, 0.01))

        wins = [r for r in r_multiples if r > 0]
        losses = [r for r in r_multiples if r <= 0]

        return {
            "trade_count": len(trades),
            "win_rate": len(wins) / len(r_multiples) if r_multiples else 0,
            "avg_r": sum(r_multiples) / len(r_multiples) if r_multiples else 0,
            "avg_win_r": sum(wins) / len(wins) if wins else 0,
            "avg_loss_r": sum(losses) / len(losses) if losses else 0,
            "payoff_ratio": (
                (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
                if wins and losses and sum(losses) != 0 else 0
            ),
            "expectancy_r": sum(r_multiples) / len(r_multiples) if r_multiples else 0,
            "consecutive_losses": self._max_consecutive_losses(r_multiples),
            "total_pnl": sum(t["gross_pnl"] or 0 for t in trades),
        }

    def _max_consecutive_losses(self, r_multiples: List[float]) -> int:
        """Compute max consecutive losses."""
        max_streak = 0
        current_streak = 0
        for r in r_multiples:
            if r <= 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        return max_streak

    def _get_backtest_expectations(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get backtest expectations from last backtest results."""
        results_path = os.path.join(
            self.base_dir, "artifacts", "backtest_results", "pipeline_results.json"
        )
        if not os.path.exists(results_path):
            return None

        try:
            with open(results_path, "r") as f:
                history = json.load(f)

            # Find most recent backtest for this symbol
            for result in reversed(history):
                if result.get("symbol") == symbol:
                    bt = result.get("stages", {}).get("backtest", {})
                    if bt.get("status") == "completed":
                        return {
                            "win_rate": bt.get("win_rate", 0.5),
                            "expectancy_r": bt.get("expectancy_r", 0),
                            "sharpe": bt.get("sharpe", 0),
                            "profit_factor": bt.get("profit_factor", 1.0),
                        }
        except Exception:
            pass

        return None

    def _compute_divergence(
        self,
        live: Dict[str, Any],
        backtest: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compute divergence scores."""
        metrics = {}

        # Win rate divergence
        bt_wr = backtest.get("win_rate", 0.5)
        live_wr = live.get("win_rate", 0.5)
        metrics["win_rate_divergence"] = abs(live_wr - bt_wr) / max(bt_wr, 0.01)

        # Expectancy divergence
        bt_exp = backtest.get("expectancy_r", 0)
        live_exp = live.get("expectancy_r", 0)
        metrics["expectancy_divergence"] = (
            abs(live_exp - bt_exp) / max(abs(bt_exp), 0.01) if bt_exp != 0 else 0
        )

        # Sharpe divergence (if available)
        bt_sharpe = backtest.get("sharpe", 0)
        if bt_sharpe > 0:
            # Approximate live Sharpe from R-multiples
            metrics["sharpe_approx"] = live.get("avg_r", 0) / max(live.get("avg_loss_r", -1), 0.01)
            metrics["sharpe_divergence"] = abs(
                metrics["sharpe_approx"] - bt_sharpe
            ) / max(bt_sharpe, 0.01)
        else:
            metrics["sharpe_divergence"] = 0

        # Overall divergence score (weighted average)
        metrics["overall_divergence"] = (
            0.4 * metrics["win_rate_divergence"] +
            0.4 * metrics["expectancy_divergence"] +
            0.2 * metrics["sharpe_divergence"]
        )

        return metrics

    def _check_alerts(
        self,
        divergence: Dict[str, Any],
        live_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check if alerts should be triggered."""
        # Check consecutive losses
        if live_metrics.get("consecutive_losses", 0) >= self.consecutive_loss_threshold:
            return {
                "alert": True,
                "severity": "CRITICAL",
                "reason": f"consecutive_losses={live_metrics['consecutive_losses']}",
            }

        # Check win rate divergence
        if divergence.get("win_rate_divergence", 0) > self.win_rate_threshold:
            return {
                "alert": True,
                "severity": "WARNING",
                "reason": f"win_rate_divergence={divergence['win_rate_divergence']:.2%}",
            }

        # Check expectancy divergence
        if divergence.get("expectancy_divergence", 0) > self.expectancy_threshold:
            return {
                "alert": True,
                "severity": "WARNING",
                "reason": f"expectancy_divergence={divergence['expectancy_divergence']:.2%}",
            }

        # Check overall divergence
        if divergence.get("overall_divergence", 0) > 0.3:
            return {
                "alert": True,
                "severity": "WARNING",
                "reason": f"overall_divergence={divergence['overall_divergence']:.2%}",
            }

        return {
            "alert": False,
            "severity": "OK",
            "reason": "",
        }

    def get_alert_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent alert history."""
        return self.state.get("alerts", [])[-limit:]

    def get_status(self) -> Dict[str, Any]:
        """Get monitor status."""
        return {
            "last_check": self.state.get("last_check"),
            "total_alerts": len(self.state.get("alerts", [])),
            "lookback_trades": self.lookback_trades,
            "thresholds": {
                "win_rate": self.win_rate_threshold,
                "expectancy": self.expectancy_threshold,
                "sharpe": self.sharpe_threshold,
                "consecutive_losses": self.consecutive_loss_threshold,
            },
        }


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Live-vs-Backtest Divergence Monitor")
    parser.add_argument("--symbol", type=str, default="EURUSD", help="Symbol to monitor")
    parser.add_argument("--continuous", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=3600, help="Check interval (seconds)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    monitor = DivergenceMonitor()

    if args.continuous:
        while True:
            result = monitor.check_divergence(args.symbol)
            if result["alert"]:
                print(f"ALERT [{result['severity']}]: {result['reason']}")
            else:
                print(f"OK: {args.symbol} - no divergence")
            time.sleep(args.interval)
    else:
        result = monitor.check_divergence(args.symbol)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
