import json
import sqlite3

import pytest

from core.trading_safety import TradingStore


def test_audit_evidence_tables_are_append_only(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    con = store._connect()
    try:
        con.execute(
            "INSERT INTO trade_lifecycle_events(created_at,ticket,setup_key,event_type,actor,payload) "
            "VALUES(?,?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", 1, "setup", "PROPOSED", "test", "{}"),
        )
        con.execute(
            "INSERT INTO position_observations(observed_at,ticket,price,unrealized_pnl,r_multiple,source,mark_quality) "
            "VALUES(?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", 1, 1.1, 0.0, 0.0, "test", "STALE"),
        )
        con.execute(
            "INSERT INTO market_ticks(observed_at,symbol,source,bid,ask,mid,spread,quality,payload) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "EURUSD", "test", 1.1, 1.1002, 1.1001, 0.0002, "FRESH", "{}"),
        )
        con.execute(
            "INSERT INTO broker_execution_reports(reported_at,event_type,payload) VALUES(?,?,?)",
            ("2026-01-01T00:00:00+00:00", "FILLED", json.dumps({})),
        )
        con.commit()
        ids = {
            table: con.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1").fetchone()[0]
            for table in ("trade_lifecycle_events", "position_observations", "market_ticks", "broker_execution_reports")
        }

        for table, row_id in ids.items():
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(f"UPDATE {table} SET id=id WHERE id=?", (row_id,))
            con.rollback()
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
            con.rollback()
    finally:
        con.close()
