import sqlite3
from unittest.mock import MagicMock
from research.mt5_backtest_engine import MT5BrokerBacktestEngine, BacktestMetrics

def test_backtest_journaling_does_not_pollute_live_trades(tmp_path):
    # Must NOT be named trading_state.db: the backtest engine hard-refuses any
    # path with that basename (research/mt5_backtest_engine.py:137) precisely so
    # simulated fills cannot reach the production journal, even in tmp_path.
    db_file = str(tmp_path / "backtest_state.db")
    engine = MT5BrokerBacktestEngine(db_path=db_file)
    
    # Create empty live trades table
    con = sqlite3.connect(db_file)
    con.execute("""
        CREATE TABLE trades (
            ticket INTEGER PRIMARY KEY,
            symbol TEXT,
            pnl REAL
        )
    """)
    con.close()
    
    metrics = MagicMock(spec=BacktestMetrics)
    metrics.to_dict.return_value = {"sharpe": 1.5}
    metrics.trades = [{
        "side": "BUY",
        "entry_price": 1.08,
        "exit_price": 1.09,
        "entry_time": "2026-01-01T00:00:00",
        "exit_time": "2026-01-01T01:00:00",
        "pnl_pips": 10.0,
        "exit_reason": "TP",
        "r_multiple": 1.5,
    }]
    metrics.sharpe_ratio = 1.5
    metrics.win_rate = 0.6
    metrics.expectancy_r = 0.5
    metrics.total_trades = 1
    
    res = engine.process_journaling(metrics, symbol="EURUSD", strategy_name="test_strat")
    assert res["persisted_run"] is True
    
    con = sqlite3.connect(db_file)
    cur = con.cursor()
    # Live trades table must remain completely empty
    cur.execute("SELECT COUNT(*) FROM trades")
    live_count = cur.fetchone()[0]
    assert live_count == 0
    
    # Backtest trades must be recorded in research_backtest_trades
    cur.execute("SELECT COUNT(*) FROM research_backtest_trades")
    bt_count = cur.fetchone()[0]
    assert bt_count == 1
    con.close()
