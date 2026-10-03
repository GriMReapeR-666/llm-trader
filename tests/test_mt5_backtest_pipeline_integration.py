"""Integration and unit tests for MT5BrokerBacktestEngine:
Verifies live MT5 broker specs extraction, full metrics computation,
data improvement, strategy improvement, journaling, and reflection.
"""
import os
import sqlite3
import pytest
import pandas as pd
import numpy as np

from research.mt5_backtest_engine import MT5BrokerBacktestEngine, BacktestMetrics


@pytest.fixture
def temp_db(tmp_path):
    db_file = str(tmp_path / "test_trading_state.db")
    return db_file


@pytest.fixture
def sample_bars():
    np.random.seed(42)
    n = 200
    dt_index = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    returns = np.random.normal(0.0002, 0.001, n)
    price = 1.1000 * np.exp(np.cumsum(returns))
    highs = price * (1.0 + np.abs(np.random.normal(0, 0.0005, n)))
    lows = price * (1.0 - np.abs(np.random.normal(0, 0.0005, n)))
    opens = price * (1.0 + np.random.normal(0, 0.0002, n))
    vols = np.random.randint(100, 1000, n)
    spreads = np.random.randint(10, 20, n)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": price,
        "tick_volume": vols,
        "spread": spreads,
        "time": dt_index,
    })
    df.set_index("time", inplace=True)
    return df


def test_mt5_broker_specs_fetch():
    engine = MT5BrokerBacktestEngine()
    specs = engine.fetch_broker_specs("EURUSD")
    assert isinstance(specs, dict)
    assert "point" in specs
    assert "digits" in specs
    assert "contract_size" in specs
    assert "spread_pips" in specs
    assert "commission_per_lot" in specs


def test_mt5_backtest_metrics_calculation(sample_bars):
    engine = MT5BrokerBacktestEngine(spread_pips=1.5, slippage_pips=0.5, commission_per_lot=7.0)
    metrics = engine.run_crossover_strategy(sample_bars, fast_period=8, slow_period=20)

    assert isinstance(metrics, BacktestMetrics)
    assert metrics.total_trades > 0
    assert hasattr(metrics, "sharpe_ratio")
    assert hasattr(metrics, "sortino_ratio")
    assert hasattr(metrics, "calmar_ratio")
    assert hasattr(metrics, "max_drawdown_pct")
    assert hasattr(metrics, "expectancy_r")
    assert hasattr(metrics, "payoff_ratio")
    assert hasattr(metrics, "var_95")
    assert hasattr(metrics, "cvar_95")
    assert len(metrics.trades) == metrics.total_trades
    assert len(metrics.r_multiples) == metrics.total_trades

    m_dict = metrics.to_dict()
    assert "total_return_pct" in m_dict
    assert "total_friction_drag_usd" in m_dict


def test_data_improvement(sample_bars):
    engine = MT5BrokerBacktestEngine()
    data_imp = engine.process_data_improvement(sample_bars, symbol="EURUSD", timeframe="M15")

    assert isinstance(data_imp, dict)
    assert data_imp["status"] in ("VALIDATED", "CLEANED_WITH_WARNINGS")
    assert data_imp["bars_inspected"] == len(sample_bars)
    assert "regime_distribution" in data_imp
    assert "volatility_profiles" in data_imp
    assert "garman_klass_annualized" in data_imp["volatility_profiles"]


def test_strategy_improvement_and_lifecycle(sample_bars, temp_db):
    engine = MT5BrokerBacktestEngine(db_path=temp_db)
    metrics = engine.run_crossover_strategy(sample_bars, fast_period=8, slow_period=20)
    strat_imp = engine.process_strategy_improvement(
        metrics=metrics,
        symbol="EURUSD",
        strategy_name="test_crossover",
        timeframe="M15",
    )

    assert isinstance(strat_imp, dict)
    assert "dsr_gate" in strat_imp
    assert "cpcv" in strat_imp
    assert "lifecycle" in strat_imp
    assert strat_imp["lifecycle"]["current_state"] in ("CANDIDATE", "VALIDATING", "PAPER_PROVEN")


def test_journaling_and_reflection(sample_bars, temp_db):
    engine = MT5BrokerBacktestEngine(db_path=temp_db)
    metrics = engine.run_crossover_strategy(sample_bars, fast_period=8, slow_period=20)

    # Journaling
    journal_res = engine.process_journaling(
        metrics=metrics,
        symbol="EURUSD",
        strategy_name="test_crossover",
        config={"timeframe": "M15"},
    )
    assert journal_res["persisted_run"] is True
    assert journal_res["trades_logged"] > 0

    # Verify SQLite database rows
    con = sqlite3.connect(temp_db)
    runs = con.execute("SELECT run_id, run_kind, status FROM research_validation_runs").fetchall()
    assert len(runs) >= 1
    assert runs[0][1] == "MT5_BROKER_BACKTEST"

    trades = con.execute("SELECT ticket, strategy, symbol FROM trades").fetchall()
    assert len(trades) >= 1
    con.close()

    # Reflection
    refl_res = engine.process_reflection(
        metrics=metrics,
        symbol="EURUSD",
        strategy_name="test_crossover",
        timeframe="M15",
    )
    assert isinstance(refl_res, dict)
    assert "health_audit" in refl_res
    assert "drawdown_analysis" in refl_res
    assert refl_res["playbook_updated"] is True


def test_run_full_autonomous_backtest(temp_db):
    engine = MT5BrokerBacktestEngine(db_path=temp_db)
    full_res = engine.run_full_autonomous_backtest(
        symbol="EURUSD",
        timeframe="M15",
        count=150,
        strategy_name="auto_test_strat",
        fast_period=8,
        slow_period=20,
    )

    assert full_res["status"] == "COMPLETED"
    assert "metrics" in full_res
    assert "broker_specs" in full_res
    assert "data_improvement" in full_res
    assert "strategy_improvement" in full_res
    assert "journaling" in full_res
    assert "reflection" in full_res
    assert full_res["journaling"]["persisted_run"] is True
