import pytest
from datetime import datetime, timezone

from trading_safety import TradingStore
from daily_reconciliation import compare_day


def test_manual_activity_logging_and_query(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    
    store.record_manual_activity(
        "SL_MODIFY_REFUSED", ticket=998877, magic=1234, symbol="EURUSD",
        payload={"reason": "unowned magic number", "operator": "system"}
    )
    
    with store._connect() as con:
        rows = con.execute("SELECT * FROM manual_activity WHERE ticket=998877").fetchall()
    assert len(rows) == 1
    assert rows[0]["ticket"] == 998877
    assert rows[0]["symbol"] == "EURUSD"
    assert rows[0]["action"] == "SL_MODIFY_REFUSED"


def test_execution_reviews_workflow_lifecycle(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    
    # Record review
    store.record_execution_review("review_101", ticket=554433, severity="HIGH", reason="Order timeout without broker confirmation")
    
    unresolved = store.get_unresolved_reviews()
    assert len(unresolved) == 1
    assert unresolved[0]["intent_key"] == "review_101"
    assert unresolved[0]["status"] == "OPEN"
    
    # Update status to ACKNOWLEDGED
    store.update_review_status("review_101", "RESOLVED", resolved_by="admin", note="Investigated and resolved")
    unresolved_after_res = store.get_unresolved_reviews()
    assert len(unresolved_after_res) == 0
    
    # Verify updated row
    with store._connect() as con:
        row = con.execute("SELECT * FROM execution_reviews WHERE intent_key='review_101'").fetchone()
    assert row["status"] == "RESOLVED"
    assert row["operator"] == "admin"


def test_daily_reconciliation_creates_execution_reviews(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    
    # Broker deal for position 776655 (magic 460001 = BOT_MAGIC)
    broker_deals = [{
        "magic": 460001,
        "position_id": 776655,
        "profit": 150.0,
        "commission": -2.5,
        "swap": 0.0,
        "fee": 0.0,
        "entry": 1
    }]
    
    start = datetime(2026, 8, 9, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 9, 23, 59, 59, tzinfo=timezone.utc)
    
    # Compare day — position 776655 is missing in local journal
    report = compare_day(store, broker_deals, start, end)
    assert report["status"] == "ATTENTION"
    assert 776655 in report["discrepancies"]["broker_closed_missing_journal"]
    
    # Verify execution_reviews record was created
    unresolved = store.get_unresolved_reviews()
    assert len(unresolved) >= 1
    assert any("recon_missing_journal_776655" in r["intent_key"] for r in unresolved)
