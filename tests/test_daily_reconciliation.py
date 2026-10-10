from datetime import datetime, timedelta, timezone

from operations.daily_reconciliation import compare_day
from core.trading_safety import SCALP_MAGIC, TradeProposal, TradingStore


def _window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(days=1)


def test_reconciliation_maps_position_ticket_to_order_ticket(tmp_path):
    store = TradingStore(str(tmp_path / "reconcile.db"))
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.1, 1.09, 1.12, 0.8,
        {"setup_id": "reconcile_v1"},
    )
    store.record_open_trade(101, proposal, 1.0, 10.0, {"setup_id": "reconcile_v1"})
    store.record_execution_report(
        event_type="FILLED", order_ticket=101, position_ticket=909,
        filled_price=1.1, filled_volume=1.0,
    )
    store.record_close(101, 1.11, 10.0, "target")

    start, end = _window()
    report = compare_day(
        store,
        [{"magic": SCALP_MAGIC, "position_id": 909, "entry": 1,
          "profit": 10.0, "commission": 0.0, "swap": 0.0}],
        start,
        end,
    )

    assert report["status"] == "CLEAN"
    assert report["discrepancies"]["broker_closed_missing_journal"] == []
    assert report["discrepancies"]["journal_closed_missing_broker"] == []
    assert report["discrepancies"]["position_ticket_mapping"][0] == {
        "position_ticket": 909, "local_ticket": 101, "status": "MATCHED",
    }


def test_reconciliation_keeps_ambiguous_position_mapping_unresolved(tmp_path):
    store = TradingStore(str(tmp_path / "ambiguous.db"))
    start, end = _window()

    report = compare_day(
        store,
        [{"magic": SCALP_MAGIC, "position_id": 909, "entry": 1,
          "profit": 10.0}],
        start,
        end,
    )

    assert report["status"] == "ATTENTION"
    assert report["discrepancies"]["broker_closed_missing_journal"] == [909]
    assert report["discrepancies"]["position_ticket_mapping"][0]["status"] == "AMBIGUOUS_OR_MISSING"


def test_reconciliation_rejects_nonfinite_owned_pnl(tmp_path):
    store = TradingStore(str(tmp_path / "malformed.db"))
    start, end = _window()

    report = compare_day(
        store,
        [{"magic": SCALP_MAGIC, "position_id": 910, "entry": 1,
          "profit": float("nan"), "commission": 0.0}],
        start,
        end,
    )

    assert report["status"] == "ATTENTION"
    assert report["broker"]["malformed_owned_deal_count"] == 1
    assert report["discrepancies"]["malformed_owned_deals"][0]["position_ticket"] == 910


def test_strict_reconciliation_filters_window_and_requires_timestamp(tmp_path):
    store = TradingStore(str(tmp_path / "strict.db"))
    start = datetime(2026, 8, 9, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 10, 0, 0, 0, tzinfo=timezone.utc)
    report = compare_day(
        store,
        [
            {"magic": SCALP_MAGIC, "position_id": 1001, "entry": 1,
             "time": "malformed", "profit": 1.0},
            {"magic": SCALP_MAGIC, "position_id": 1002, "entry": 1,
             "time": datetime(2026, 8, 11, tzinfo=timezone.utc).timestamp(), "profit": 2.0},
        ],
        start,
        end,
        require_timestamps=True,
    )
    assert report["status"] == "ATTENTION"
    assert report["broker"]["outside_window_deal_count"] == 1
    assert report["discrepancies"]["malformed_owned_deals"][0]["reason"] == "missing_or_invalid_deal_timestamp"


def test_strict_reconciliation_rejects_missing_close_marker(tmp_path):
    store = TradingStore(str(tmp_path / "strict-entry.db"))
    start = datetime(2026, 8, 9, tzinfo=timezone.utc)
    end = datetime(2026, 8, 10, tzinfo=timezone.utc)
    report = compare_day(
        store,
        [{"magic": SCALP_MAGIC, "position_id": 1003,
          "time": datetime(2026, 8, 9, 12, tzinfo=timezone.utc).timestamp(),
          "profit": 1.0}],
        start,
        end,
        require_timestamps=True,
    )
    assert report["status"] == "ATTENTION"
    assert report["discrepancies"]["malformed_owned_deals"][0]["reason"] == "missing_deal_entry_marker"
