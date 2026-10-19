"""Tests for backtest engine isolation: PR 2.4 (1b-R2 = LA-B4).

Verifies that MT5BrokerBacktestEngine:
1. Rejects trading_state.db (by path, basename, case-insensitivity, env var).
2. Rejects databases with LIVE or DEMO journal scopes / TradingStore modes.
3. Accepts dedicated backtest db paths (e.g. backtest_state.db in tmp_path, RESEARCH scope, safe defaults).
4. Does not insert simulated trades, approved memories, playbook rules, or promotions into live stores.
5. Does not auto-promote candidate setups during backtests (promotions require operator action).
"""
import json
import sqlite3
import pytest
import pandas as pd
import numpy as np

from operations.journal_scope import ensure_scope
from core.trading_safety import TradingStore
from research.mt5_backtest_engine import (
    MT5BrokerBacktestEngine,
    BacktestMetrics,
    validate_backtest_journal_path,
    DEFAULT_BACKTEST_DB,
)


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


def test_rejects_trading_state_db(tmp_path):
    """MT5BrokerBacktestEngine must strictly reject trading_state.db."""
    # 1. Plain name
    with pytest.raises(ValueError, match="production database"):
        MT5BrokerBacktestEngine(journal_path="trading_state.db")

    with pytest.raises(ValueError, match="production database"):
        MT5BrokerBacktestEngine(db_path="trading_state.db")

    with pytest.raises(ValueError, match="production database"):
        validate_backtest_journal_path("trading_state.db")

    # 2. Case-insensitivity
    with pytest.raises(ValueError, match="production database"):
        validate_backtest_journal_path("TRADING_STATE.DB")

    # 3. Path variations under directories
    with pytest.raises(ValueError, match="production database"):
        MT5BrokerBacktestEngine(journal_path=str(tmp_path / "trading_state.db"))

    with pytest.raises(ValueError, match="production database"):
        MT5BrokerBacktestEngine(journal_path=tmp_path / "trading_state.db")

    with pytest.raises(ValueError, match="production database"):
        MT5BrokerBacktestEngine(journal_path="data/trading_state.db")

    # 4. Positional argument
    with pytest.raises(ValueError, match="production database"):
        MT5BrokerBacktestEngine(1.5, 0.5, 7.0, "trading_state.db")

    # 5. Empty or None
    with pytest.raises(ValueError, match="cannot be None"):
        validate_backtest_journal_path(None)

    with pytest.raises(ValueError, match="cannot be empty"):
        validate_backtest_journal_path("")


def test_rejects_live_and_demo_journal_scopes(tmp_path, monkeypatch):
    """MT5BrokerBacktestEngine must reject databases with LIVE or DEMO scopes."""
    monkeypatch.delenv("MT5_LOGIN", raising=False)

    # 1. LIVE scope via ensure_scope
    live_db = str(tmp_path / "live_account.db")
    ensure_scope(live_db, "LIVE", "live-fingerprint-1")

    with pytest.raises(ValueError, match="LIVE journal scope"):
        MT5BrokerBacktestEngine(journal_path=live_db)

    with pytest.raises(ValueError, match="LIVE journal scope"):
        validate_backtest_journal_path(live_db)

    # 2. DEMO scope via ensure_scope
    demo_db = str(tmp_path / "demo_account.db")
    ensure_scope(demo_db, "DEMO", "demo-fingerprint-1")

    with pytest.raises(ValueError, match="DEMO journal scope"):
        MT5BrokerBacktestEngine(journal_path=demo_db)

    with pytest.raises(ValueError, match="DEMO journal scope"):
        validate_backtest_journal_path(demo_db)

    # 3. LIVE scope via TradingStore
    live_store_db = str(tmp_path / "live_store.db")
    live_store = TradingStore(live_store_db, mode="LIVE", account_fingerprint="live-fingerprint-2")

    with pytest.raises(ValueError, match="LIVE"):
        MT5BrokerBacktestEngine(journal_path=live_store_db)

    with pytest.raises(ValueError, match="LIVE"):
        MT5BrokerBacktestEngine(journal_path=live_store)

    # 4. DEMO scope via TradingStore
    demo_store_db = str(tmp_path / "demo_store.db")
    demo_store = TradingStore(demo_store_db, mode="DEMO", account_fingerprint="demo-fingerprint-2")

    with pytest.raises(ValueError, match="DEMO"):
        MT5BrokerBacktestEngine(journal_path=demo_store_db)

    with pytest.raises(ValueError, match="DEMO"):
        MT5BrokerBacktestEngine(journal_path=demo_store)

    # 5. Environment variable LLM_TRADER_JOURNAL_PATH matching
    env_journal = str(tmp_path / "env_journal.db")
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", env_journal)
    with pytest.raises(ValueError, match="active environment journal"):
        MT5BrokerBacktestEngine(journal_path=env_journal)


def test_accepts_dedicated_backtest_db(tmp_path):
    """MT5BrokerBacktestEngine accepts dedicated backtest db paths and safe defaults."""
    # 1. Default constructor uses safe backtest db, never trading_state.db
    engine_def = MT5BrokerBacktestEngine()
    assert engine_def.journal_path != "trading_state.db"
    assert not engine_def.journal_path.endswith("trading_state.db")
    assert "backtest" in engine_def.journal_path.lower()
    assert engine_def.db_path == engine_def.journal_path

    # 2. Explicit journal_path in tmp_path
    bt_path = str(tmp_path / "backtest_state.db")
    engine = MT5BrokerBacktestEngine(journal_path=bt_path)
    assert engine.journal_path == bt_path
    assert engine.db_path == bt_path

    # 3. Legacy db_path kwarg
    engine_leg = MT5BrokerBacktestEngine(db_path=bt_path)
    assert engine_leg.journal_path == bt_path

    # 4. Dedicated RESEARCH scoped database
    research_path = str(tmp_path / "research_store.db")
    ensure_scope(research_path, "RESEARCH")
    engine_res = MT5BrokerBacktestEngine(journal_path=research_path)
    assert engine_res.journal_path == research_path

    # 5. Dedicated PAPER scoped database
    paper_path = str(tmp_path / "paper_store.db")
    ensure_scope(paper_path, "PAPER")
    engine_pap = MT5BrokerBacktestEngine(journal_path=paper_path)
    assert engine_pap.journal_path == paper_path


def test_does_not_insert_simulated_trades_or_promotions_into_live_store(tmp_path, sample_bars):
    """Backtesting must never insert simulated trades, memories, playbooks, or promotions into live stores."""
    # Create production live store
    live_db_path = str(tmp_path / "live_production.db")
    live_store = TradingStore(live_db_path, mode="LIVE", account_fingerprint="live_operator_1")

    # Verify backtest engine cannot bind to live store
    with pytest.raises(ValueError):
        MT5BrokerBacktestEngine(journal_path=live_db_path)
    with pytest.raises(ValueError):
        MT5BrokerBacktestEngine(journal_path=live_store)

    # Initialize engine with dedicated backtest database
    bt_db_path = str(tmp_path / "backtest_state.db")
    engine = MT5BrokerBacktestEngine(journal_path=bt_db_path)

    # Run complete crossover strategy and lifecycle pipeline
    metrics = engine.run_crossover_strategy(sample_bars, fast_period=8, slow_period=20)
    journal_res = engine.process_journaling(metrics, symbol="EURUSD", strategy_name="test_strat")
    strat_res = engine.process_strategy_improvement(metrics, symbol="EURUSD", strategy_name="test_strat")
    refl_res = engine.process_reflection(metrics, symbol="EURUSD", strategy_name="test_strat")

    assert journal_res["persisted_run"] is True
    assert journal_res["trades_logged"] > 0
    assert refl_res["playbook_updated"] is False

    # Check LIVE store: remains completely untouched
    with sqlite3.connect(live_db_path) as con_live:
        live_trades = con_live.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert live_trades == 0, "Live store trades table must have 0 trades from backtest"

        tables = {row[0] for row in con_live.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "research_validation_runs" in tables:
            runs = con_live.execute("SELECT COUNT(*) FROM research_validation_runs").fetchone()[0]
            assert runs == 0
        if "case_study_memories" in tables:
            memories = con_live.execute("SELECT COUNT(*) FROM case_study_memories").fetchone()[0]
            assert memories == 0
        if "playbooks" in tables:
            playbooks = con_live.execute("SELECT COUNT(*) FROM playbooks").fetchone()[0]
            assert playbooks == 0
        if "strategy_lifecycle" in tables:
            strats = con_live.execute("SELECT COUNT(*) FROM strategy_lifecycle").fetchone()[0]
            assert strats == 0

    # Check backtest DB: trades are logged, but NO lifecycle promotions or approved memories
    with sqlite3.connect(bt_db_path) as con_bt:
        # Backtest fills land in research_backtest_trades, deliberately NOT in
        # `trades` - the table that TradingStore.record_open_trade writes to.
        # A dedicated table name is part of the isolation guarantee.
        bt_trades = con_bt.execute("SELECT COUNT(*) FROM research_backtest_trades").fetchone()[0]
        assert bt_trades > 0, "Dedicated backtest store should contain logged backtest trades"

        bt_tables_live = {row[0] for row in con_bt.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "trades" not in bt_tables_live, (
            "Backtest store must not create a `trades` table; simulated fills "
            "belong in research_backtest_trades only."
        )

        bt_runs = con_bt.execute("SELECT COUNT(*) FROM research_validation_runs").fetchone()[0]
        assert bt_runs > 0, "Dedicated backtest store should contain validation run"

        bt_tables = {row[0] for row in con_bt.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "case_study_memories" not in bt_tables, "Backtest engine must not write case_study_memories"
        assert "playbooks" not in bt_tables, "Backtest engine must not write playbooks"

        if "strategy_lifecycle_history" in bt_tables:
            promotions = con_bt.execute(
                "SELECT * FROM strategy_lifecycle_history WHERE to_state IN ('VALIDATING', 'PAPER_PROVEN')"
            ).fetchall()
            assert len(promotions) == 0, "Backtest engine must not write lifecycle promotions"


def test_high_performance_metrics_do_not_trigger_auto_promotion(tmp_path):
    """Even stellar backtest metrics must not auto-promote setups; operator action required."""
    bt_db = str(tmp_path / "backtest_candidate.db")
    engine = MT5BrokerBacktestEngine(journal_path=bt_db)

    # Fabricate high-performing metrics exceeding all promotion thresholds
    metrics = BacktestMetrics(
        sharpe_ratio=3.5,
        win_rate=75.0,
        expectancy_r=0.85,
        profit_factor=2.8,
        max_drawdown_pct=5.0,
        total_trades=100,
        r_multiples=[0.85] * 100,
    )

    strat_imp = engine.process_strategy_improvement(
        metrics=metrics,
        symbol="EURUSD",
        strategy_name="alpha_crossover",
    )

    # Setup is eligible, but transition is NOT applied by engine
    assert strat_imp["lifecycle"]["eligible_for_promotion"] is True
    assert strat_imp["lifecycle"]["transition_applied"] is None
    assert strat_imp["lifecycle"]["current_state"] == "CANDIDATE"

    with sqlite3.connect(bt_db) as con:
        # Check current state in database
        row = con.execute("SELECT state FROM strategy_lifecycle WHERE setup_id='alpha_crossover'").fetchone()
        assert row is not None
        assert row[0] == "CANDIDATE", "Setup must remain CANDIDATE; no auto-promotion to VALIDATING or PAPER_PROVEN"

        # Check history table
        promotions = con.execute(
            "SELECT to_state FROM strategy_lifecycle_history WHERE setup_id='alpha_crossover' AND to_state != 'CANDIDATE'"
        ).fetchall()
        assert len(promotions) == 0, "No promotion history records should be written by backtest engine"


def test_override_paths_in_methods_rejected(tmp_path, sample_bars):
    """Passing a live database override to methods must be refused."""
    bt_db = str(tmp_path / "valid_backtest.db")
    engine = MT5BrokerBacktestEngine(journal_path=bt_db)

    live_db = str(tmp_path / "override_live.db")
    ensure_scope(live_db, "LIVE", "override_fp")

    metrics = engine.run_crossover_strategy(sample_bars)

    with pytest.raises(ValueError, match="LIVE"):
        engine.process_journaling(metrics, journal_path=live_db)

    with pytest.raises(ValueError, match="LIVE"):
        engine.process_strategy_improvement(metrics, journal_path=live_db)

    with pytest.raises(ValueError, match="LIVE"):
        engine.process_reflection(metrics, journal_path=live_db)

    with pytest.raises(ValueError, match="LIVE"):
        engine.run_full_autonomous_backtest(journal_path=live_db)

    with pytest.raises(ValueError, match="production database"):
        engine.process_journaling(metrics, journal_path="trading_state.db")
