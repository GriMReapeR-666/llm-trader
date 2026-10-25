"""Populate real strategy-specific vector simulation backtest trades.

Generates real strategy-specific backtest trades from multi-thousand-bar
H1 history for each registered strategy, populating research_backtest_trades
in the dedicated backtest database (backtest_state.db).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_data_provider import fetch_ohlc
from research.mt5_backtest_engine import MT5BrokerBacktestEngine, DEFAULT_BACKTEST_DB
from research.strategy_signal_registry import _REGISTRY, build_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SimPopulate")


def populate_simulations():
    engine = MT5BrokerBacktestEngine(journal_path=DEFAULT_BACKTEST_DB)
    db_path = engine.journal_path

    logger.info("Target Backtest DB: %s", db_path)
    logger.info("Strategies in registry: %d", len(_REGISTRY))

    symbol_map = {
        "statarb_eur_chf": ("EURCHF", "H1", 3000),
        "gold_momentum_h4": ("XAUUSD", "H4", 2000),
        "spx_volatility_skew": ("EURUSD", "H1", 3000),  # Fallback to liquid FX if index unmapped
    }

    total_inserted = 0

    for strat_name in sorted(_REGISTRY.keys()):
        symbol, tf, bar_count = symbol_map.get(strat_name, ("EURUSD", "H1", 3000))
        logger.info("Simulating %s on %s %s (%d bars)...", strat_name, symbol, tf, bar_count)

        bars_df = fetch_ohlc(symbol, tf, count=bar_count)
        if bars_df is None or len(bars_df) < 200:
            logger.warning("Insufficient bars for %s on %s (%s)", strat_name, symbol, len(bars_df) if bars_df is not None else 0)
            continue

        def sig_builder(bdf, s_name=strat_name):
            return build_signal(
                s_name,
                bdf["close"].to_numpy(),
                bdf["high"].to_numpy(),
                bdf["low"].to_numpy(),
            )

        res = engine.run_full_autonomous_backtest(
            symbol=symbol,
            strategy_name=strat_name,
            timeframe=tf,
            count=len(bars_df),
            signal_builder=sig_builder,
            bars_df=bars_df,
            data_source="mt5_history",
        )

        trades_count = res.get("metrics", {}).get("total_trades", 0)
        sharpe = res.get("metrics", {}).get("sharpe_ratio", 0.0)
        logger.info("  -> %s completed: %d trades, Sharpe=%.3f", strat_name, trades_count, sharpe)
        total_inserted += trades_count

    logger.info("Total strategy-specific backtest trades generated: %d", total_inserted)

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "research_backtest_trades" in tables:
            count = con.execute("SELECT COUNT(*) FROM research_backtest_trades").fetchone()[0]
            distinct_strats = con.execute("SELECT strategy, COUNT(*) FROM research_backtest_trades GROUP BY strategy").fetchall()
            logger.info("Total research_backtest_trades rows in DB: %d", count)
            for s, n in distinct_strats:
                logger.info("  - %s: %d trades", s, n)


if __name__ == "__main__":
    populate_simulations()
