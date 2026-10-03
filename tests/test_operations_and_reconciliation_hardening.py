import gc
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import pytest

from core.trading_safety import TradingStore, SCALP_MAGIC, SWING_MAGIC
from operations.agent_operations import AgentOperations
from operations.daily_reconciliation import adapt_paper_deals, compare_day, format_discrepancy_alert


class TestOperationsAndReconciliationHardening:
    @pytest.fixture
    def temp_env(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test_trading_state.db")
        backup_dir = os.path.join(tmpdir, "test_backups")
        yield {"tmpdir": tmpdir, "db_path": db_path, "backup_dir": backup_dir}
        gc.collect()
        try:
            for root, dirs, files in os.walk(tmpdir, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except (PermissionError, OSError):
                        pass
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except (PermissionError, OSError):
                        pass
            os.rmdir(tmpdir)
        except (PermissionError, OSError):
            pass

    def test_adapt_paper_deals_and_clean_reconciliation(self, temp_env):
        store = TradingStore(path=temp_env["db_path"])
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=2)
        end = now + timedelta(hours=1)

        # 1. Seed closed trade in journal
        ticket = 5001
        with store._connect() as con:
            con.execute("""
                INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, take_profit,
                                    risk_amount, opened_at, closed_at, exit_price, pnl, exit_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticket, SWING_MAGIC, "trend_pullback", "EURUSD", "BUY", 0.10, 1.1000, 1.0950, 1.1100,
                  50.0, (now - timedelta(hours=1)).isoformat(), now.isoformat(), 1.1050, 50.0, "TP", "{}"))

        # 2. Adapt simulated paper broker position
        paper_history = [{
            "ticket": ticket,
            "magic": SWING_MAGIC,
            "symbol": "EURUSD",
            "volume": 0.10,
            "price_open": 1.1000,
            "price_close": 1.1050,
            "profit": 50.0,
            "close_time": now.timestamp(),
        }]
        adapted_deals = adapt_paper_deals(paper_history)
        assert len(adapted_deals) == 1
        assert adapted_deals[0]["ticket"] == ticket
        assert adapted_deals[0]["magic"] == SWING_MAGIC
        assert adapted_deals[0]["profit"] == 50.0
        assert adapted_deals[0]["entry"] == 1

        # 3. Run reconciliation
        report = compare_day(store, adapted_deals, start, end, require_timestamps=False)
        assert report["status"] == "CLEAN"
        assert report["journal"]["closed_trade_count"] == 1
        assert report["broker"]["owned_closed_positions"] == 1
        assert report["broker"]["owned_net_pnl"] == 50.0
        assert report["alert_payload"] is None
        assert report["execution_authority"] is False

    def test_reconciliation_discrepancies_and_alert_formatting(self, temp_env):
        store = TradingStore(path=temp_env["db_path"])
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=3)
        end = now + timedelta(hours=1)

        # Seed trade in journal with PnL = 100.0
        ticket_matched = 6001
        ticket_absent_broker = 6002
        with store._connect() as con:
            con.execute("""
                INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, take_profit,
                                    risk_amount, opened_at, closed_at, exit_price, pnl, exit_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticket_matched, SWING_MAGIC, "trend", "EURUSD", "BUY", 0.10, 1.1000, 1.0950, 1.1100,
                  50.0, (now - timedelta(hours=2)).isoformat(), now.isoformat(), 1.1100, 100.0, "TP", "{}"))
            con.execute("""
                INSERT INTO trades (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, take_profit,
                                    risk_amount, opened_at, closed_at, exit_price, pnl, exit_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ticket_absent_broker, SWING_MAGIC, "trend", "EURUSD", "BUY", 0.10, 1.1000, 1.0950, 1.1100,
                  50.0, (now - timedelta(hours=2)).isoformat(), now.isoformat(), 1.1050, 50.0, "TP", "{}"))

        # Broker reports ticket_matched with different PnL (80.0 instead of 100.0), plus an untracked ticket 6003
        broker_deals = [
            {
                "position_id": ticket_matched,
                "ticket": ticket_matched,
                "magic": SWING_MAGIC,
                "profit": 80.0,
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
                "entry": 1,
                "time": now.timestamp(),
            },
            {
                "position_id": 6003,
                "ticket": 6003,
                "magic": SWING_MAGIC,
                "profit": 25.0,
                "commission": 0.0,
                "swap": 0.0,
                "fee": 0.0,
                "entry": 1,
                "time": now.timestamp(),
            },
        ]

        report = compare_day(store, broker_deals, start, end, require_timestamps=False)
        assert report["status"] == "ATTENTION"
        assert ticket_absent_broker in report["discrepancies"]["journal_closed_missing_broker"]
        assert 6003 in report["discrepancies"]["broker_closed_missing_journal"]
        assert len(report["discrepancies"]["matched_position_net_pnl_differences"]) == 1

        alert = format_discrepancy_alert(report)
        assert alert is not None
        assert alert["event_type"] == "RECONCILIATION_DISCREPANCY"
        assert alert["severity"] == "CRITICAL"
        assert "ATTENTION" in alert["summary"]

    def test_database_maintenance_wal_checkpoint_and_backup(self, temp_env):
        # 1. Test AgentOperations maintenance
        ops = AgentOperations(db_path=temp_env["db_path"])
        ops.event("operator", "test_event", {"data": 123})
        res_ops = ops.perform_database_maintenance(backup_dir=temp_env["backup_dir"])

        assert res_ops["wal_checkpoint"]["status"] in ("SUCCESS", "BUSY")
        assert res_ops["integrity_check"]["passed"] is True
        assert res_ops["backup"]["created"] is True
        assert os.path.exists(res_ops["backup"]["path"])
        assert res_ops["backup"]["size_bytes"] > 0

        # Verify SHA-256 matches actual backup file
        with open(res_ops["backup"]["path"], "rb") as f:
            computed_sha = hashlib.sha256(f.read()).hexdigest()
        assert res_ops["backup"]["sha256"] == computed_sha

        # 2. Test TradingStore maintenance
        store = TradingStore(path=temp_env["db_path"])
        res_store = store.perform_maintenance(backup_dir=temp_env["backup_dir"])
        assert res_store["integrity_check"]["passed"] is True
        assert res_store["backup"]["created"] is True
        assert os.path.exists(res_store["backup"]["path"])

    def test_cli_reconciliation_run_and_db_maintenance(self, temp_env):
        # Initialize store
        store = TradingStore(path=temp_env["db_path"])
        store.log_lifecycle_event(None, "setup_1", "TEST_INIT", "operator", {})
        del store
        gc.collect()

        # Run reconciliation-run --paper via CLI
        cmd_recon = [
            sys.executable,
            "mt5_trader.py",
            "journal",
            "reconciliation-run",
            "--paper",
            "--journal-path",
            temp_env["db_path"],
        ]
        res_recon = subprocess.run(cmd_recon, capture_output=True, text=True)
        assert res_recon.returncode == 0, f"Error: {res_recon.stderr}"
        data_recon = json.loads(res_recon.stdout.strip())
        assert "status" in data_recon
        assert data_recon["execution_authority"] is False

        # Run db-maintenance via CLI
        cmd_maint = [
            sys.executable,
            "mt5_trader.py",
            "journal",
            "db-maintenance",
            "--journal-path",
            temp_env["db_path"],
            "--backup-dir",
            temp_env["backup_dir"],
        ]
        res_maint = subprocess.run(cmd_maint, capture_output=True, text=True)
        assert res_maint.returncode == 0, f"Error: {res_maint.stderr}"
        data_maint = json.loads(res_maint.stdout.strip())
        assert data_maint["integrity_check"]["passed"] is True
        assert data_maint["backup"]["created"] is True
        assert data_maint["execution_authority"] is False
