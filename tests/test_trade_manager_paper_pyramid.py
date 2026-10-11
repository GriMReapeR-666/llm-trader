from unittest.mock import MagicMock, patch
import pytest
from core.trade_manager import TradeManager, PaperPosition
from core.order_execution import PaperBroker

def test_paper_pyramid_does_not_call_real_mt5(tmp_path, monkeypatch):
    # Pyramiding is off by default (PYRAMIDING_ENABLED=0) as a safety
    # default. Opt in explicitly so this test exercises the paper path rather
    # than the disable switch.
    monkeypatch.setenv("PYRAMIDING_ENABLED", "1")
    state_f = str(tmp_path / "state.json")
    cond_f = str(tmp_path / "cond.json")
    mock_risk = MagicMock()
    mock_risk.check_daily_drawdown.return_value = True
    mock_risk.check_active_trade_limit.return_value = True
    mock_risk.balance = 10000.0
    mock_risk.risk_per_trade = 0.01
    tm = TradeManager(client=True, risk_manager=mock_risk, managed_magics={123456}, state_file=state_f, conditions_file=cond_f)
    paper_broker = PaperBroker()
    
    # Place initial position into paper broker
    init_res = paper_broker.market_order(
        symbol="EURUSD",
        side="BUY",
        volume=0.2,
        price=1.0800,
        sl=1.0750,
        tp=1.0900,
        comment="init",
        magic=123456,
    )
    init_ticket = init_res["order"]
    tm.attach_paper_broker(paper_broker)
    
    # Mock store and arbiter to allow authorization
    tm.store = MagicMock()
    tm.store.is_kill_switch_active.return_value = False
    tm.arbiter = MagicMock()
    tm.arbiter.permit.return_value = (True, "approved")
    
    pos = PaperPosition(paper_broker.positions[init_ticket], init_ticket)
    
    with patch("core.trade_manager.mt5") as mock_mt5:
        mock_mt5.ORDER_TYPE_BUY = 0
        mock_mt5.ORDER_TYPE_SELL = 1
        mock_mt5.TRADE_ACTION_DEAL = 1
        mock_mt5.account_info.return_value = MagicMock(equity=10000.0)
        mock_mt5.symbol_info_tick.return_value = MagicMock(ask=1.0820, bid=1.0818)
        
        tm._execute_pyramid_order(pos)
        
        # Real mt5.order_send MUST NOT be called in paper mode
        mock_mt5.order_send.assert_not_called()
        assert init_ticket in tm.pyramid_entries
        # And paper broker should now have the pyramid position
        assert len(paper_broker.positions) == 2
