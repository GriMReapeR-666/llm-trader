import logging
from unittest.mock import MagicMock
from core.trade_manager import TradeManager


def test_normalize_volume_logs_warning_on_micro_lot_zero_drop(caplog):
    client = MagicMock()
    risk = MagicMock()
    store = MagicMock()
    tm = TradeManager(client=client, risk_manager=risk, store=store, paper_broker=MagicMock())

    # Requested volume 0.001 is below minimum 0.01
    with caplog.at_level(logging.WARNING, logger="TradeManager"):
        vol = tm._normalize_volume("EURUSD", 0.001)

    assert vol == 0.0
    assert any("below minimum" in record.message for record in caplog.records)


def test_normalize_volume_succeeds_without_warning_when_above_min(caplog):
    client = MagicMock()
    risk = MagicMock()
    store = MagicMock()
    tm = TradeManager(client=client, risk_manager=risk, store=store, paper_broker=MagicMock())

    with caplog.at_level(logging.WARNING, logger="TradeManager"):
        vol = tm._normalize_volume("EURUSD", 0.05)

    assert vol == 0.05
    assert not any("below minimum" in record.message for record in caplog.records)
