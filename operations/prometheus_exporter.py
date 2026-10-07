"""Prometheus Metrics Exporter

Exposes trading metrics in Prometheus format for Grafana dashboards.
Provides a simple HTTP server that serves /metrics endpoint.

Usage:
    # Start the exporter on port 9090
    python prometheus_exporter.py --port 9090

    # Or import and use programmatically
    from prometheus_exporter import start_exporter
    start_exporter(port=9090)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("PrometheusExporter")

# Global metrics cache
_metrics_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()
_last_update: float = 0
UPDATE_INTERVAL = 10  # seconds


def _collect_metrics() -> Dict[str, Any]:
    """Collect all metrics from the trading system."""
    global _metrics_cache, _last_update

    now = time.time()
    if now - _last_update < UPDATE_INTERVAL and _metrics_cache:
        return _metrics_cache

    base_dir = str(Path(__file__).parent.parent)
    db_path = os.path.join(base_dir, "trading_state.db")
    metrics = {}

    # Collect from SQLite
    if os.path.exists(db_path):
        try:
            con = sqlite3.connect(db_path, timeout=10)
            con.row_factory = sqlite3.Row

            # Trade metrics
            trades = con.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN gross_pnl > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(gross_pnl) as total_pnl, "
                "AVG(gross_pnl) as avg_pnl, "
                "MAX(gross_pnl) as max_pnl, "
                "MIN(gross_pnl) as min_pnl "
                "FROM trades WHERE closed_at IS NOT NULL"
            ).fetchone()

            if trades:
                metrics["trades_total"] = trades["total"] or 0
                metrics["trades_wins"] = trades["wins"] or 0
                metrics["trades_win_rate"] = (trades["wins"] or 0) / max(trades["total"] or 1, 1)
                metrics["trades_total_pnl"] = trades["total_pnl"] or 0
                metrics["trades_avg_pnl"] = trades["avg_pnl"] or 0
                metrics["trades_max_pnl"] = trades["max_pnl"] or 0
                metrics["trades_min_pnl"] = trades["min_pnl"] or 0

            # Open positions
            open_pos = con.execute(
                "SELECT COUNT(*) as count, SUM(profit) as total_profit "
                "FROM trades WHERE closed_at IS NULL"
            ).fetchone()

            if open_pos:
                metrics["positions_open"] = open_pos["count"] or 0
                metrics["positions_unrealized_pnl"] = open_pos["total_profit"] or 0

            # Recent trades (last 24h)
            recent = con.execute(
                "SELECT COUNT(*) as count, SUM(gross_pnl) as pnl "
                "FROM trades WHERE closed_at IS NOT NULL "
                "AND closed_at > datetime('now', '-1 day')"
            ).fetchone()

            if recent:
                metrics["trades_24h_count"] = recent["count"] or 0
                metrics["trades_24h_pnl"] = recent["pnl"] or 0

            # Metrics persistence tables
            try:
                # Latency metrics
                latency = con.execute(
                    "SELECT AVG(latency_ms) as avg_ms, MAX(latency_ms) as max_ms "
                    "FROM metrics_latency "
                    "WHERE recorded_at > datetime('now', '-1 hour')"
                ).fetchone()
                if latency:
                    metrics["latency_avg_ms"] = latency["avg_ms"] or 0
                    metrics["latency_max_ms"] = latency["max_ms"] or 0

                # Cost metrics
                costs = con.execute(
                    "SELECT SUM(cost_usd) as total_cost "
                    "FROM metrics_costs "
                    "WHERE recorded_at > datetime('now', '-1 day')"
                ).fetchone()
                if costs:
                    metrics["llm_cost_daily_usd"] = costs["total_cost"] or 0

                # Equity metrics
                equity = con.execute(
                    "SELECT balance, equity, drawdown_pct "
                    "FROM metrics_equity "
                    "ORDER BY recorded_at DESC LIMIT 1"
                ).fetchone()
                if equity:
                    metrics["account_balance"] = equity["balance"] or 0
                    metrics["account_equity"] = equity["equity"] or 0
                    metrics["account_drawdown_pct"] = equity["drawdown_pct"] or 0
            except Exception:
                pass

            con.close()

        except Exception as exc:
            log.debug("Failed to collect metrics from DB: %s", exc)

    # Collect from risk_state.json
    risk_path = os.path.join(base_dir, "risk_state.json")
    if os.path.exists(risk_path):
        try:
            with open(risk_path, "r") as f:
                risk_state = json.load(f)
            metrics["risk_balance"] = risk_state.get("balance", 0)
            metrics["risk_equity"] = risk_state.get("equity", 0)
            metrics["risk_drawdown_pct"] = risk_state.get("current_drawdown_pct", 0)
            metrics["risk_daily_pnl"] = risk_state.get("daily_pnl", 0)
        except Exception:
            pass

    # Collect from autonomy_state.json
    autonomy_path = os.path.join(base_dir, "autonomy_state.json")
    if os.path.exists(autonomy_path):
        try:
            with open(autonomy_path, "r") as f:
                autonomy = json.load(f)
            metrics["autonomy_level"] = autonomy.get("level", 0)
            metrics["autonomy_frozen"] = 1 if autonomy.get("frozen", False) else 0
        except Exception:
            pass

    with _cache_lock:
        _metrics_cache = metrics
        _last_update = now

    return metrics


def _format_prometheus(metrics: Dict[str, Any]) -> str:
    """Format metrics as Prometheus text exposition format."""
    lines = []
    lines.append("# HELP trading_trades_total Total number of closed trades")
    lines.append("# TYPE trading_trades_total counter")
    lines.append(f"trading_trades_total {metrics.get('trades_total', 0)}")

    lines.append("# HELP trading_trades_win_rate Win rate of closed trades")
    lines.append("# TYPE trading_trades_win_rate gauge")
    lines.append(f"trading_trades_win_rate {metrics.get('trades_win_rate', 0):.4f}")

    lines.append("# HELP trading_trades_total_pnl Total PnL of closed trades")
    lines.append("# TYPE trading_trades_total_pnl gauge")
    lines.append(f"trading_trades_total_pnl {metrics.get('trades_total_pnl', 0):.2f}")

    lines.append("# HELP trading_trades_avg_pnl Average PnL per trade")
    lines.append("# TYPE trading_trades_avg_pnl gauge")
    lines.append(f"trading_trades_avg_pnl {metrics.get('trades_avg_pnl', 0):.2f}")

    lines.append("# HELP trading_positions_open Number of open positions")
    lines.append("# TYPE trading_positions_open gauge")
    lines.append(f"trading_positions_open {metrics.get('positions_open', 0)}")

    lines.append("# HELP trading_positions_unrealized_pnl Unrealized PnL of open positions")
    lines.append("# TYPE trading_positions_unrealized_pnl gauge")
    lines.append(f"trading_positions_unrealized_pnl {metrics.get('positions_unrealized_pnl', 0):.2f}")

    lines.append("# HELP trading_trades_24h_count Trades in last 24 hours")
    lines.append("# TYPE trading_trades_24h_count gauge")
    lines.append(f"trading_trades_24h_count {metrics.get('trades_24h_count', 0)}")

    lines.append("# HELP trading_trades_24h_pnl PnL in last 24 hours")
    lines.append("# TYPE trading_trades_24h_pnl gauge")
    lines.append(f"trading_trades_24h_pnl {metrics.get('trades_24h_pnl', 0):.2f}")

    lines.append("# HELP trading_latency_avg_ms Average latency in milliseconds")
    lines.append("# TYPE trading_latency_avg_ms gauge")
    lines.append(f"trading_latency_avg_ms {metrics.get('latency_avg_ms', 0):.2f}")

    lines.append("# HELP trading_latency_max_ms Maximum latency in milliseconds")
    lines.append("# TYPE trading_latency_max_ms gauge")
    lines.append(f"trading_latency_max_ms {metrics.get('latency_max_ms', 0):.2f}")

    lines.append("# HELP trading_llm_cost_daily_usd Daily LLM cost in USD")
    lines.append("# TYPE trading_llm_cost_daily_usd gauge")
    lines.append(f"trading_llm_cost_daily_usd {metrics.get('llm_cost_daily_usd', 0):.6f}")

    lines.append("# HELP trading_account_balance Account balance")
    lines.append("# TYPE trading_account_balance gauge")
    lines.append(f"trading_account_balance {metrics.get('risk_balance', metrics.get('account_balance', 0)):.2f}")

    lines.append("# HELP trading_account_equity Account equity")
    lines.append("# TYPE trading_account_equity gauge")
    lines.append(f"trading_account_equity {metrics.get('risk_equity', metrics.get('account_equity', 0)):.2f}")

    lines.append("# HELP trading_account_drawdown_pct Current drawdown percentage")
    lines.append("# TYPE trading_account_drawdown_pct gauge")
    lines.append(f"trading_account_drawdown_pct {metrics.get('risk_drawdown_pct', metrics.get('account_drawdown_pct', 0)):.4f}")

    lines.append("# HELP trading_account_daily_pnl Daily PnL")
    lines.append("# TYPE trading_account_daily_pnl gauge")
    lines.append(f"trading_account_daily_pnl {metrics.get('risk_daily_pnl', 0):.2f}")

    lines.append("# HELP trading_autonomy_level Current autonomy level")
    lines.append("# TYPE trading_autonomy_level gauge")
    lines.append(f"trading_autonomy_level {metrics.get('autonomy_level', 0)}")

    lines.append("# HELP trading_autonomy_frozen Whether trading is frozen")
    lines.append("# TYPE trading_autonomy_frozen gauge")
    lines.append(f"trading_autonomy_frozen {metrics.get('autonomy_frozen', 0)}")

    # Timestamp
    lines.append("# HELP trading_metrics_timestamp Unix timestamp of metrics collection")
    lines.append("# TYPE trading_metrics_timestamp gauge")
    lines.append(f"trading_metrics_timestamp {int(time.time())}")

    return "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for /metrics endpoint."""

    def do_GET(self):
        if self.path == "/metrics":
            metrics = _collect_metrics()
            prometheus_text = _format_prometheus(metrics)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.end_headers()
            self.wfile.write(prometheus_text.encode("utf-8"))
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default HTTP logging."""
        pass


def start_exporter(port: int = 9090, host: str = "127.0.0.1") -> HTTPServer:
    """Start the Prometheus metrics exporter server."""
    server = HTTPServer((host, port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Prometheus exporter started on %s:%d", host, port)
    return server


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Prometheus Metrics Exporter")
    parser.add_argument("--port", type=int, default=9090, help="Port to listen on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--once", action="store_true", help="Print metrics once and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.once:
        metrics = _collect_metrics()
        print(_format_prometheus(metrics))
    else:
        server = start_exporter(port=args.port, host=args.host)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            server.shutdown()


if __name__ == "__main__":
    main()
