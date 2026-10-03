# tests/test_quantitative_metrics_service.py
"""Unit and integration tests for Modular QuantitativeMetricsService."""

import os
import pytest
from core.quantitative_metrics_service import QuantitativeMetricsService
from core.trading_safety import TradingStore


def test_service_initialization_and_defaults():
    """Verify default engine state on service initialization."""
    service = QuantitativeMetricsService(store=None, initial_balance=10000.0, auto_load=False)
    summary = service.get_metrics_summary()

    assert summary["total_trades"] == 0
    assert summary["win_rate"] == 0.0
    assert summary["synced_trades_count"] == 0
    assert summary["current_consecutive_losses"] == 0
    assert service.engine.peak_equity == 10000.0


def test_record_closed_trade_and_deduplication():
    """Verify trade recording and ticket deduplication."""
    service = QuantitativeMetricsService(store=None, initial_balance=10000.0, auto_load=False)

    # 1. Record winning trade (+2.0R, $200)
    ok1 = service.record_closed_trade(ticket=101, pnl=200.0, r_multiple=2.0, risk_amount=100.0, save=False)
    assert ok1 is True
    assert len(service.synced_tickets) == 1
    assert service.engine.r_multiples == [2.0]

    # 2. Record losing trade (-1.0R, -$100)
    ok2 = service.record_closed_trade(ticket=102, pnl=-100.0, r_multiple=-1.0, risk_amount=100.0, save=False)
    assert ok2 is True
    assert len(service.synced_tickets) == 2
    assert service.engine.consecutive_losses == 1

    # 3. Duplicate ticket 101 must be rejected
    ok_dup = service.record_closed_trade(ticket=101, pnl=200.0, r_multiple=2.0, risk_amount=100.0, save=False)
    assert ok_dup is False
    assert len(service.synced_tickets) == 2
    assert len(service.engine.r_multiples) == 2


def test_incremental_journal_sync(tmp_path):
    """Verify incremental sync from SQLite TradingStore."""
    db_path = str(tmp_path / "test_journal.db")
    store = TradingStore(db_path)
    service = QuantitativeMetricsService(store=store, initial_balance=10000.0)

    # 1. Insert 3 closed trades
    with store._connect() as con:
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, risk_amount, opened_at, closed_at, pnl, payload) "
            "VALUES (101, 1001, 'swing', 'EURUSD', 'BUY', 1.0, 1.1000, 100.0, '2026-09-01T00:00:00Z', '2026-09-01T01:00:00Z', 150.0, '{}')"
        )
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, risk_amount, opened_at, closed_at, pnl, payload) "
            "VALUES (102, 1001, 'swing', 'GBPUSD', 'BUY', 1.0, 1.3000, 100.0, '2026-09-01T02:00:00Z', '2026-09-01T03:00:00Z', -100.0, '{}')"
        )
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, risk_amount, opened_at, closed_at, pnl, payload) "
            "VALUES (103, 1001, 'swing', 'AUDUSD', 'BUY', 1.0, 0.6500, 100.0, '2026-09-01T04:00:00Z', '2026-09-01T05:00:00Z', 250.0, '{}')"
        )
        # Open trade (closed_at is NULL) must NOT be synced
        con.execute(
            "INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, risk_amount, opened_at, closed_at, pnl, payload) "
            "VALUES (104, 1001, 'swing', 'NZDUSD', 'BUY', 1.0, 0.6000, 100.0, '2026-09-01T06:00:00Z', NULL, 0.0, '{}')"
        )
        # Independent submission evidence: the sync gate admits only tickets
        # recorded in order_intents; a self-declared trades row proves nothing.
        con.execute("CREATE TABLE IF NOT EXISTS order_intents (mt5_ticket INTEGER)")
        con.executemany(
            "INSERT INTO order_intents (mt5_ticket) VALUES (?)",
            [(101,), (102,), (103,)],
        )

    # Sync
    synced_count = service.sync_from_journal()
    assert synced_count == 3
    assert len(service.synced_tickets) == 3
    assert service.engine.trade_pnls == [150.0, -100.0, 250.0]
    assert service.engine.r_multiples == [1.5, -1.0, 2.5]

    # Re-syncing immediately finds 0 new trades
    assert service.sync_from_journal() == 0


def test_state_persistence_and_restart_recovery(tmp_path):
    """Verify that state persists across service re-instantiation via TradingStore."""
    db_path = str(tmp_path / "test_persist.db")
    store = TradingStore(db_path)

    # Session 1: Create service, record trades and equity, save
    s1 = QuantitativeMetricsService(store=store, initial_balance=10000.0)
    s1.update_equity(10500.0)
    s1.record_closed_trade(ticket=201, pnl=-100.0, r_multiple=-1.0, risk_amount=100.0)
    s1.record_closed_trade(ticket=202, pnl=-100.0, r_multiple=-1.0, risk_amount=100.0)
    s1.update_equity(10300.0)
    summary_s1 = s1.get_metrics_summary()

    assert summary_s1["current_consecutive_losses"] == 2
    assert s1.engine.peak_equity == 10500.0
    assert summary_s1["max_drawdown_pct"] > 0

    # Session 2: Instantiate brand new service from same store (simulating restart)
    s2 = QuantitativeMetricsService(store=store, initial_balance=10000.0, auto_load=True)
    summary_s2 = s2.get_metrics_summary()

    # Verify complete historical continuity
    assert summary_s2["current_consecutive_losses"] == 2
    assert s2.engine.peak_equity == 10500.0
    assert summary_s2["max_drawdown_pct"] == summary_s1["max_drawdown_pct"]
    assert s2.synced_tickets == {201, 202}
    assert s2.engine.trade_pnls == [-100.0, -100.0]


def test_file_fallback_persistence(tmp_path):
    """Verify file-based persistence fallback when store is absent."""
    json_path = str(tmp_path / "metrics_state.json")
    s1 = QuantitativeMetricsService(store=None, state_file=json_path, auto_load=False)
    s1.record_closed_trade(ticket=301, pnl=300.0, r_multiple=3.0, risk_amount=100.0)
    s1.update_equity(10300.0)
    assert os.path.exists(json_path)

    s2 = QuantitativeMetricsService(store=None, state_file=json_path, auto_load=True)
    assert s2.synced_tickets == {301}
    assert s2.engine.trade_pnls == [300.0]
    assert s2.engine.peak_equity == 10300.0


def test_institutional_metrics_summary():
    """Verify calculation of institutional summary metrics."""
    service = QuantitativeMetricsService(store=None, initial_balance=10000.0, auto_load=False)
    # Record 12 trades: 8 wins (2R each), 4 losses (-1R each)
    for i in range(1, 9):
        service.record_closed_trade(i, pnl=200, r_multiple=2.0, save=False)
    for i in range(9, 13):
        service.record_closed_trade(i, pnl=-100, r_multiple=-1.0, save=False)

    summary = service.get_metrics_summary()
    assert summary["total_trades"] == 12
    assert summary["win_rate"] == pytest.approx(8 / 12, 0.01)
    assert summary["avg_win_r"] == 2.0
    assert summary["avg_loss_r"] == 1.0
    assert summary["payoff_ratio"] == 2.0
    # EV = 0.6667 * 2 - 0.3333 * 1 = 1.0R
    assert summary["expected_value_r"] == pytest.approx(1.0, 0.05)
    assert summary["sqn"] > 0
    assert "deflated_sharpe_pvalue" in summary
