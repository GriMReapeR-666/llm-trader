import math
from datetime import datetime, timedelta, timezone
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from mt5_trader import (
    PaperBroker,
    cancel_pending_order,
    emergency_market_exit,
    place_market_order,
    place_pending_order,
    validate_pending_entry_geometry,
    _broker_market_filling_mode,
)


def test_paper_broker_refuses_non_finite_quote():
    broker = PaperBroker()
    result = broker.market_order("XAUUSD", "buy", 0.1, math.nan, None, None, "test")
    assert result["order"] is None
    assert result["retcode"] == 10013
    assert broker.positions == {}


def test_direct_live_market_entry_is_disabled():
    with pytest.raises(RuntimeError, match="direct market entries are disabled"):
        place_market_order("EURUSD", "buy", 0.1, None, None, "test", paper=None, magic=9001)


def test_broker_market_filling_mode_maps_symbol_flags():
    fake_mt5 = MagicMock(
        symbol_info=MagicMock(return_value=SimpleNamespace(filling_mode=3)),
        ORDER_FILLING_IOC=1,
        ORDER_FILLING_FOK=0,
    )
    with patch("mt5_trader.mt5", fake_mt5):
        assert _broker_market_filling_mode("EURUSD") == 1
        fake_mt5.symbol_info.return_value.filling_mode = 1
        assert _broker_market_filling_mode("EURUSD") == 0
        fake_mt5.symbol_info.return_value.filling_mode = 0
        with pytest.raises(RuntimeError, match="supported market filling mode"):
            _broker_market_filling_mode("EURUSD")


def test_paper_broker_refuses_invalid_pending_order():
    broker = PaperBroker()
    result = broker.pending_order("XAUUSD", "buy_limit", 0.0, 0.1, None, None, "test")
    assert result["order"] is None
    assert result["retcode"] == 10013
    assert broker.orders == {}


def test_pending_entry_boundary_requires_owned_expiring_protective_plan():
    broker = PaperBroker()
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    result = place_pending_order(
        "EURUSD", "buy_limit", 1.1000, 0.1, 1.0990, 1.1021,
        "planned", broker, magic=9001, expires_at=expiry,
    )
    assert result["status"] == "PENDING"
    assert broker.orders[result["order"]]["magic"] == 9001


def test_pending_cancellation_is_magic_bound():
    broker = PaperBroker()
    expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
    placed = place_pending_order(
        "EURUSD", "buy_limit", 1.1000, 0.1, 1.0990, 1.1021,
        "planned", broker, magic=9001, expires_at=expiry,
    )
    ticket = placed["order"]
    assert cancel_pending_order(ticket, broker, magic=9002) is False
    assert ticket in broker.orders
    assert cancel_pending_order(ticket, broker, magic=9001) is True
    assert ticket not in broker.orders


def test_pending_entry_geometry_preserves_limit_or_stop_side():
    validate_pending_entry_geometry("buy_limit", 1.0990, 1.0998, 1.1000)
    validate_pending_entry_geometry("buy_stop", 1.1010, 1.0998, 1.1000)
    validate_pending_entry_geometry("sell_limit", 1.1010, 1.0998, 1.1000)
    validate_pending_entry_geometry("sell_stop", 1.0990, 1.0998, 1.1000)


@pytest.mark.parametrize("otype, entry", [("buy_limit", 1.1000), ("buy_stop", 1.0999),
                                            ("sell_limit", 1.0998), ("sell_stop", 1.0999)])
def test_pending_entry_geometry_rejects_marketable_or_crossed_plan(otype, entry):
    with pytest.raises(ValueError, match="marketable|crossed"):
        validate_pending_entry_geometry(otype, entry, 1.0998, 1.1000)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"magic": 0}, "magic"),
        ({"sl": None}, "stop"),
        ({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}, "expiry"),
    ],
)
def test_pending_entry_boundary_rejects_unsafe_plan(kwargs, message):
    broker = PaperBroker()
    params = {
        "symbol": "EURUSD", "otype": "buy_limit", "price": 1.1000,
        "volume": 0.1, "sl": 1.0990, "tp": 1.1021, "comment": "planned",
        "paper": broker, "magic": 9001,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    params.update(kwargs)
    with pytest.raises(ValueError, match=message):
        place_pending_order(**params)
    assert broker.orders == {}


def test_paper_emergency_exit_is_ticket_and_magic_bound():
    broker = PaperBroker()
    opened = broker.market_order("EURUSD", "buy", 0.1, 1.1000, None, None, "test", magic=9001)
    ticket = opened["order"]
    result = emergency_market_exit(
        symbol="EURUSD", position_ticket=ticket, side="BUY", volume=0.1,
        magic=9001, reason="kill switch", paper=broker, price=1.0990,
    )
    assert result["status"] == "CLOSED"
    assert ticket not in broker.positions


def test_paper_emergency_exit_rejects_unowned_position():
    broker = PaperBroker()
    opened = broker.market_order("EURUSD", "buy", 0.1, 1.1000, None, None, "test", magic=9001)
    with pytest.raises(RuntimeError, match="identity"):
        emergency_market_exit(
            symbol="EURUSD", position_ticket=opened["order"], side="BUY", volume=0.1,
            magic=9002, reason="manual", paper=broker, price=1.0990,
        )
