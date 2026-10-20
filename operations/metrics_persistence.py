"""Real-Time Metrics Persistence Layer

Persists trading metrics to SQLite for historical trending and analysis.
This enables:
- Rolling window metrics (Sharpe, Sortino over last N trades)
- Drawdown duration tracking
- LLM cost tracking
- Latency monitoring
- Feature drift detection

Usage:
    store = MetricsStore()
    store.record_trade({...})
    store.record_latency("llm_call", 250.0)
    store.record_cost("openrouter", 0.0023)
    summary = store.get_rolling_metrics(window=50)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("MetricsPersistence")


class MetricsStore:
    """SQLite-backed metrics persistence store."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or os.path.join(
            str(Path(__file__).parent.parent), "trading_state.db"
        )
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.execute("PRAGMA busy_timeout = 5000")
        return con

    def _init_db(self) -> None:
        """Initialize metrics tables."""
        try:
            con = self._connect()
            con.execute("""
                CREATE TABLE IF NOT EXISTS metrics_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket INTEGER,
                    symbol TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    gross_pnl REAL,
                    risk_amount REAL,
                    r_multiple REAL,
                    opened_at TEXT,
                    closed_at TEXT,
                    latency_ms REAL,
                    slippage_pips REAL,
                    spread_at_entry REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS metrics_latency (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT,
                    operation TEXT,
                    latency_ms REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS metrics_costs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT,
                    model TEXT,
                    tokens_in INTEGER,
                    tokens_out INTEGER,
                    cost_usd REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS metrics_equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    balance REAL,
                    equity REAL,
                    drawdown_pct REAL,
                    open_risk_pct REAL,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS metrics_system (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    component TEXT,
                    metric_name TEXT,
                    metric_value REAL,
                    tags TEXT,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to initialize metrics DB: %s", exc)

    def record_trade(self, trade: Dict[str, Any]) -> None:
        """Record a trade for metrics computation."""
        try:
            con = self._connect()
            r_mult = 0.0
            if trade.get("gross_pnl") and trade.get("risk_amount"):
                r_mult = trade["gross_pnl"] / max(trade["risk_amount"], 0.01)

            con.execute(
                "INSERT INTO metrics_trades "
                "(ticket, symbol, side, entry_price, exit_price, gross_pnl, risk_amount, "
                "r_multiple, opened_at, closed_at, latency_ms, slippage_pips, spread_at_entry) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    trade.get("ticket"),
                    trade.get("symbol"),
                    trade.get("side"),
                    trade.get("entry_price"),
                    trade.get("exit_price"),
                    trade.get("gross_pnl"),
                    trade.get("risk_amount"),
                    r_mult,
                    trade.get("opened_at"),
                    trade.get("closed_at"),
                    trade.get("latency_ms"),
                    trade.get("slippage_pips"),
                    trade.get("spread_at_entry"),
                )
            )
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to record trade: %s", exc)

    def record_latency(self, component: str, operation: str, latency_ms: float) -> None:
        """Record latency measurement."""
        try:
            con = self._connect()
            con.execute(
                "INSERT INTO metrics_latency (component, operation, latency_ms) VALUES (?, ?, ?)",
                (component, operation, latency_ms)
            )
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to record latency: %s", exc)

    def record_cost(
        self,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        """Record LLM cost."""
        try:
            con = self._connect()
            con.execute(
                "INSERT INTO metrics_costs (provider, model, tokens_in, tokens_out, cost_usd) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider, model, tokens_in, tokens_out, cost_usd)
            )
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to record cost: %s", exc)

    def record_equity(
        self,
        balance: float,
        equity: float,
        drawdown_pct: float,
        open_risk_pct: float,
    ) -> None:
        """Record equity snapshot."""
        try:
            con = self._connect()
            con.execute(
                "INSERT INTO metrics_equity (balance, equity, drawdown_pct, open_risk_pct) "
                "VALUES (?, ?, ?, ?)",
                (balance, equity, drawdown_pct, open_risk_pct)
            )
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to record equity: %s", exc)

    def record_system_metric(
        self,
        component: str,
        metric_name: str,
        metric_value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record a system metric."""
        try:
            con = self._connect()
            con.execute(
                "INSERT INTO metrics_system (component, metric_name, metric_value, tags) "
                "VALUES (?, ?, ?, ?)",
                (component, metric_name, metric_value, json.dumps(tags or {}))
            )
            con.commit()
            con.close()
        except Exception as exc:
            log.debug("Failed to record system metric: %s", exc)

    def get_rolling_metrics(self, window: int = 50) -> Dict[str, Any]:
        """Compute rolling window metrics from recent trades."""
        try:
            con = self._connect()
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT r_multiple, gross_pnl, risk_amount FROM metrics_trades "
                "ORDER BY recorded_at DESC LIMIT ?",
                (window,)
            ).fetchall()

            con.close()

            if not rows:
                return {"trade_count": 0}

            r_multiples = [r["r_multiple"] for r in rows if r["r_multiple"] is not None]
            if not r_multiples:
                return {"trade_count": 0}

            # Compute metrics
            wins = [r for r in r_multiples if r > 0]
            losses = [r for r in r_multiples if r <= 0]

            avg_r = sum(r_multiples) / len(r_multiples)
            win_rate = len(wins) / len(r_multiples) if r_multiples else 0

            # Rolling Sharpe (annualized, assuming 252 trades/year)
            if len(r_multiples) > 1:
                mean_r = sum(r_multiples) / len(r_multiples)
                var_r = sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1)
                std_r = math.sqrt(var_r) if var_r > 0 else 1
                rolling_sharpe = (mean_r / std_r) * math.sqrt(252)
            else:
                rolling_sharpe = 0

            # Rolling Sortino (downside deviation)
            if losses:
                downside_var = sum(l ** 2 for l in losses) / len(losses)
                downside_std = math.sqrt(downside_var) if downside_var > 0 else 1
                rolling_sortino = (avg_r / downside_std) * math.sqrt(252)
            else:
                rolling_sortino = float('inf') if avg_r > 0 else 0

            return {
                "trade_count": len(r_multiples),
                "window": window,
                "win_rate": round(win_rate, 4),
                "avg_r": round(avg_r, 4),
                "avg_win_r": round(sum(wins) / len(wins), 4) if wins else 0,
                "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else 0,
                "payoff_ratio": round(
                    (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
                    if wins and losses and sum(losses) != 0 else 0, 4
                ),
                "rolling_sharpe": round(rolling_sharpe, 4),
                "rolling_sortino": round(rolling_sortino, 4) if math.isfinite(rolling_sortino) else None,
                "max_consecutive_losses": self._max_consecutive(r_multiples, negative=True),
                "max_consecutive_wins": self._max_consecutive(r_multiples, negative=False),
            }

        except Exception as exc:
            log.debug("Failed to compute rolling metrics: %s", exc)
            return {"error": str(exc)}

    def _max_consecutive(self, values: List[float], negative: bool = True) -> int:
        """Compute max consecutive wins or losses."""
        max_streak = 0
        current = 0
        for v in values:
            if (negative and v <= 0) or (not negative and v > 0):
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    def get_latency_stats(self, component: str, hours: int = 24) -> Dict[str, Any]:
        """Get latency statistics for a component."""
        try:
            con = self._connect()
            con.row_factory = sqlite3.Row

            cutoff = datetime.now(timezone.utc).isoformat()
            rows = con.execute(
                "SELECT latency_ms FROM metrics_latency "
                "WHERE component=? AND recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC",
                (component, cutoff, f"-{hours} hours")
            ).fetchall()

            con.close()

            if not rows:
                return {"component": component, "count": 0}

            latencies = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
            if not latencies:
                return {"component": component, "count": 0}

            latencies.sort()
            n = len(latencies)

            return {
                "component": component,
                "count": n,
                "mean_ms": round(sum(latencies) / n, 2),
                "median_ms": round(latencies[n // 2], 2),
                "p95_ms": round(latencies[int(n * 0.95)], 2),
                "p99_ms": round(latencies[int(n * 0.99)], 2),
                "min_ms": round(latencies[0], 2),
                "max_ms": round(latencies[-1], 2),
            }

        except Exception as exc:
            return {"component": component, "error": str(exc)}

    def get_cost_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get LLM cost summary."""
        try:
            con = self._connect()
            con.row_factory = sqlite3.Row

            cutoff = datetime.now(timezone.utc).isoformat()
            rows = con.execute(
                "SELECT provider, model, cost_usd, tokens_in, tokens_out "
                "FROM metrics_costs "
                "WHERE recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC",
                (cutoff, f"-{hours} hours")
            ).fetchall()

            con.close()

            if not rows:
                return {"total_cost_usd": 0, "calls": 0}

            total_cost = sum(r["cost_usd"] or 0 for r in rows)
            total_tokens_in = sum(r["tokens_in"] or 0 for r in rows)
            total_tokens_out = sum(r["tokens_out"] or 0 for r in rows)

            # Per-provider breakdown
            by_provider = {}
            for r in rows:
                provider = r["provider"] or "unknown"
                if provider not in by_provider:
                    by_provider[provider] = {"cost_usd": 0, "calls": 0}
                by_provider[provider]["cost_usd"] += r["cost_usd"] or 0
                by_provider[provider]["calls"] += 1

            return {
                "total_cost_usd": round(total_cost, 6),
                "calls": len(rows),
                "total_tokens_in": total_tokens_in,
                "total_tokens_out": total_tokens_out,
                "by_provider": by_provider,
            }

        except Exception as exc:
            return {"error": str(exc)}

    def get_drawdown_duration(self) -> Dict[str, Any]:
        """Get current drawdown duration."""
        try:
            con = self._connect()
            con.row_factory = sqlite3.Row

            rows = con.execute(
                "SELECT equity, drawdown_pct, recorded_at FROM metrics_equity "
                "ORDER BY recorded_at DESC LIMIT 1000"
            ).fetchall()

            con.close()

            if not rows:
                return {"in_drawdown": False, "duration_hours": 0}

            # Find peak equity
            peak = max(r["equity"] or 0 for r in rows)
            current = rows[0]["equity"] or 0

            if current >= peak:
                return {"in_drawdown": False, "duration_hours": 0, "current_dd_pct": 0}

            # Find when drawdown started
            dd_start = None
            for r in reversed(rows):
                if (r["equity"] or 0) >= peak:
                    dd_start = r["recorded_at"]
                    break

            if dd_start:
                from datetime import datetime as dt
                start_dt = dt.fromisoformat(dd_start.replace("Z", "+00:00"))
                now = dt.now(timezone.utc)
                duration_hours = (now - start_dt).total_seconds() / 3600
            else:
                duration_hours = 0

            current_dd = (peak - current) / max(peak, 1)

            return {
                "in_drawdown": True,
                "duration_hours": round(duration_hours, 2),
                "current_dd_pct": round(current_dd, 4),
                "peak_equity": peak,
                "current_equity": current,
            }

        except Exception as exc:
            return {"error": str(exc)}


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Metrics Persistence Store")
    parser.add_argument("--rolling", action="store_true", help="Show rolling metrics")
    parser.add_argument("--latency", type=str, help="Show latency stats for component")
    parser.add_argument("--costs", action="store_true", help="Show cost summary")
    parser.add_argument("--drawdown", action="store_true", help="Show drawdown duration")
    parser.add_argument("--window", type=int, default=50, help="Rolling window size")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    store = MetricsStore()

    if args.rolling:
        result = store.get_rolling_metrics(window=args.window)
        print(json.dumps(result, indent=2, default=str))
    elif args.latency:
        result = store.get_latency_stats(args.latency)
        print(json.dumps(result, indent=2, default=str))
    elif args.costs:
        result = store.get_cost_summary()
        print(json.dumps(result, indent=2, default=str))
    elif args.drawdown:
        result = store.get_drawdown_duration()
        print(json.dumps(result, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
