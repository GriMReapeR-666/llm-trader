"""Paper pyramiding must fill the paper book and never reach a real broker.

Regression guard for two stacked defects:
  1. An unconditional `return` in the paper branch (added in 6934b728 on top of
     the paper fill in 2d4800b3) made the paper fill unreachable, so a paper
     pyramid was refused outright instead of being simulated locally.
  2. The test never set PYRAMIDING_ENABLED, so even with the dead guard removed
     it exercised the disable switch rather than the paper path.

The live-broker protection must survive the fix: live/DEMO pyramiding still
routes through the gateway with validation_result=None and therefore fails
closed.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.trade_manager import TradeManager, PaperPosition
from core.order_execution import PaperBroker

INIT = dict(symbol="EURUSD", side="BUY", volume=0.2, price=1.0800,
            sl=1.0750, tp=1.0900, comment="init", magic=123456)


def _manager(tmp_path, paper=True):
    risk = MagicMock()
    risk.balance, risk.risk_per_trade = 10000.0, 0.01
    tm = TradeManager(client=True, risk_manager=risk, managed_magics={123456},
                      state_file=str(tmp_path / "state.json"),
                      conditions_file=str(tmp_path / "cond.json"))
    tm.store = MagicMock()
    tm.store.is_kill_switch_active.return_value = False
    tm.arbiter = MagicMock()
    tm.arbiter.permit.return_value = (True, "approved")
    broker = None
    if paper:
        broker = PaperBroker()
        res = broker.market_order(**INIT)
        tm.attach_paper_broker(broker)
        pos = PaperPosition(broker.positions[res["order"]], res["order"])
    else:
        pos = MagicMock()
        pos.ticket, pos.symbol, pos.volume = 100000, "EURUSD", 0.2
        pos.type, pos.sl, pos.tp, pos.magic = 0, 1.0750, 1.0900, 123456
        pos.price_current, pos.price_open = 1.0820, 1.0800
    return tm, broker, pos


def test_paper_pyramid_does_not_call_real_mt5(tmp_path, monkeypatch):
    # Pyramiding is off by default (PYRAMIDING_ENABLED=0) as a safety default.
    monkeypatch.setenv("PYRAMIDING_ENABLED", "1")
    tm, broker, pos = _manager(tmp_path, paper=True)

    with patch("core.trade_manager.mt5") as mock_mt5:
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.account_info.return_value = MagicMock(equity=10000.0)
        mock_mt5.symbol_info_tick.return_value = MagicMock(ask=1.0820, bid=1.0818)

        tm._execute_pyramid_order(pos)

        mock_mt5.order_send.assert_not_called()
        assert pos.ticket in tm.pyramid_entries
        assert len(broker.positions) == 2


def test_pyramiding_still_disabled_by_default(tmp_path, monkeypatch):
    """The safety default must survive the fix."""
    monkeypatch.delenv("PYRAMIDING_ENABLED", raising=False)
    tm, broker, pos = _manager(tmp_path, paper=True)

    tm._execute_pyramid_order(pos)

    assert len(broker.positions) == 1, "pyramid filled while disabled by default"
    assert tm.pyramid_entries == set()


def test_live_pyramid_never_bypasses_gateway(tmp_path, monkeypatch):
    """Without a paper broker, no direct mt5.order_send may occur."""
    monkeypatch.setenv("PYRAMIDING_ENABLED", "1")
    tm, _, pos = _manager(tmp_path, paper=False)

    with patch("core.trade_manager.mt5") as mock_mt5:
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.ORDER_TIME_GTC = 0
        mock_mt5.account_info.return_value = MagicMock(equity=10000.0)
        mock_mt5.symbol_info_tick.return_value = MagicMock(ask=1.0820, bid=1.0818)

        tm._execute_pyramid_order(pos)

        mock_mt5.order_send.assert_not_called()