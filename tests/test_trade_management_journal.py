from types import SimpleNamespace

from unittest.mock import MagicMock, patch

from core.trade_manager import TradeManager
from core.trading_safety import TradingStore


def test_partial_management_writes_lifecycle_and_execution_evidence(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    position = SimpleNamespace(
        ticket=321, volume=1.0, symbol="EURUSD", magic=460001, type=0,
    )
    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(return_value=[position]),
        order_send=MagicMock(return_value=response),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.1, ask=1.1002)),
        symbol_info=MagicMock(return_value=SimpleNamespace(volume_min=0.01, volume_max=500.0, volume_step=0.01, filling_mode=3)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_DEAL=1,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        assert manager.execute_partial_close(321, 0.5, "take partial") is True

    con = store._connect()
    try:
        lifecycle = con.execute(
            "SELECT event_type FROM trade_lifecycle_events WHERE ticket=321"
        ).fetchall()
        reports = con.execute(
            "SELECT event_type FROM broker_execution_reports WHERE position_ticket=321"
        ).fetchall()
    finally:
        con.close()
    assert "PARTIAL_CLOSE" in [row["event_type"] for row in lifecycle]
    assert "PARTIAL_CLOSE" in [row["event_type"] for row in reports]


def test_sl_modification_lifecycle_journaling(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    position = SimpleNamespace(
        ticket=101, symbol="EURUSD", magic=460001, type=0, tp=1.2, sl=1.0,
    )
    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(return_value=[position]),
        order_send=MagicMock(return_value=response),
        symbol_info=MagicMock(return_value=SimpleNamespace(point=0.0001, trade_stops_level=10, trade_freeze_level=10,
                                                           volume_min=0.01, volume_max=500.0, volume_step=0.01, filling_mode=3)),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.1, ask=1.1002)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_SLTP=3,
        POSITION_TYPE_BUY=0,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        assert manager._modify_sl(101, 1.05, "Trailing") is True

    con = store._connect()
    try:
        lifecycle = con.execute("SELECT event_type FROM trade_lifecycle_events WHERE ticket=101").fetchall()
        events = [row["event_type"] for row in lifecycle]
    finally:
        con.close()
    
    assert "SL_MODIFY_REQUESTED" in events
    assert "SL_MODIFY_ACCEPTED" in events


def test_partial_close_lifecycle_journaling(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    position = SimpleNamespace(
        ticket=102, volume=2.0, symbol="EURUSD", magic=460001, type=0, price_open=1.0, sl=0.9
    )
    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(return_value=[position]),
        order_send=MagicMock(return_value=response),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.1, ask=1.1002)),
        symbol_info=MagicMock(return_value=SimpleNamespace(volume_min=0.01, volume_max=500.0, volume_step=0.01, filling_mode=3)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_DEAL=1,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        manager._execute_partial_close(position)

    con = store._connect()
    try:
        lifecycle = con.execute("SELECT event_type FROM trade_lifecycle_events WHERE ticket=102").fetchall()
        events = [row["event_type"] for row in lifecycle]
    finally:
        con.close()
    
    assert "PARTIAL_CLOSE_REQUESTED" in events
    assert "PARTIAL_CLOSE_FILLED" in events


def test_pyramid_lifecycle_journaling(tmp_path, monkeypatch):
    """Pyramiding without DeterministicValidator evidence fails closed at the gateway (1a-F8).

    No live order may leave the manager: the broker mock must stay silent and
    the attempt is journalled as PYRAMID_REJECTED.
    """
    monkeypatch.setenv("PYRAMIDING_ENABLED", "1")
    store = TradingStore(str(tmp_path / "state.db"))
    risk = MagicMock()
    risk.balance = 10000.0
    risk.risk_per_trade = 0.01
    risk.check_daily_drawdown.return_value = True
    risk.check_active_trade_limit.return_value = True
    risk.check_consecutive_loss_breaker.return_value = True
    manager = TradeManager(MagicMock(), risk, store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    manager.attach_safety(store=store)
    position = SimpleNamespace(
        ticket=103, volume=1.0, symbol="EURUSD", magic=460001, type=0, price_open=1.0, sl=0.9, tp=1.2
    )
    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(return_value=[position]),
        order_send=MagicMock(return_value=response),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.1, ask=1.1002)),
        symbol_info=MagicMock(return_value=SimpleNamespace(volume_min=0.01, volume_max=500.0, volume_step=0.01, filling_mode=3)),
        account_info=MagicMock(return_value=SimpleNamespace(equity=10000.0)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_DEAL=1,
        ORDER_TIME_GTC=0,
        ORDER_FILLING_IOC=1,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        manager._execute_pyramid_order(position)

    assert fake_mt5.order_send.call_count == 0
    con = store._connect()
    try:
        lifecycle = con.execute("SELECT event_type FROM trade_lifecycle_events WHERE ticket=103").fetchall()
        events = [row["event_type"] for row in lifecycle]
    finally:
        con.close()

    assert "PYRAMID_AUTHORIZED" in events
    assert "PYRAMID_REQUESTED" in events
    assert "PYRAMID_REJECTED" in events


def test_pyramid_blocked_when_arbiter_rejects(tmp_path, monkeypatch):
    monkeypatch.setenv("PYRAMIDING_ENABLED", "1")
    store = TradingStore(str(tmp_path / "state.db"))
    store.set_kill_switch(True, "test halt")
    risk = MagicMock()
    risk.balance = 10000.0
    risk.risk_per_trade = 0.01
    risk.check_daily_drawdown.return_value = True
    risk.check_active_trade_limit.return_value = True
    risk.check_consecutive_loss_breaker.return_value = True
    manager = TradeManager(MagicMock(), risk, store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    manager.attach_safety(store=store)
    position = SimpleNamespace(
        ticket=203, volume=1.0, symbol="EURUSD", magic=460001, type=0, price_open=1.0, sl=0.9, tp=1.2
    )
    fake_mt5 = MagicMock(
        symbol_info=MagicMock(return_value=SimpleNamespace(volume_min=0.01, volume_max=500.0, volume_step=0.01, filling_mode=3)),
        account_info=MagicMock(return_value=SimpleNamespace(equity=10000.0)),
        order_send=MagicMock(),
        ORDER_TYPE_BUY=0,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        manager._execute_pyramid_order(position)
    assert fake_mt5.order_send.call_count == 0
    con = store._connect()
    try:
        events = [row["event_type"] for row in con.execute(
            "SELECT event_type FROM trade_lifecycle_events WHERE ticket=203"
        ).fetchall()]
    finally:
        con.close()
    assert "PYRAMID_REJECTED" in events


def test_breakeven_and_trailing_stop_trigger_records(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    position = SimpleNamespace(
        ticket=104, volume=1.0, symbol="EURUSD", magic=460001, type=0, price_open=1.0, sl=0.9, tp=1.5
    )
    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(return_value=[position]),
        order_send=MagicMock(return_value=response),
        symbol_info=MagicMock(return_value=SimpleNamespace(point=0.0001, trade_stops_level=10, trade_freeze_level=10)),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.3, ask=1.3002)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_SLTP=3,
        POSITION_TYPE_BUY=0,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        manager._manage_position(position, 1.3, 0.05)

    con = store._connect()
    try:
        lifecycle = con.execute("SELECT event_type FROM trade_lifecycle_events WHERE ticket=104").fetchall()
        events = [row["event_type"] for row in lifecycle]
    finally:
        con.close()

    # 1a-F11: breakeven and trail are computed once and the tighter one wins.
    # At +3R the trail (1.15) is tighter than breakeven (1.005), so only the
    # trailing trigger fires — never both.
    assert "TRAILING_TRIGGERED" in events
    assert "BREAKEVEN_TRIGGERED" not in events
    assert "SL_MODIFY_ACCEPTED" in events


def test_breakeven_wins_when_trail_not_yet_valid(tmp_path):
    """1a-F11 complement: at +1R with trail still below entry, breakeven fires alone."""
    from unittest.mock import MagicMock, patch
    from types import SimpleNamespace
    from core.trade_manager import TradeManager
    from core.trading_safety import TradingStore
    store = TradingStore(str(tmp_path / "state2.db"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm2.json"), conditions_file=str(tmp_path / "tc2.json"))
    position = SimpleNamespace(
        ticket=105, volume=1.0, symbol="EURUSD", magic=460001, type=0, price_open=1.0, sl=0.9, tp=1.5
    )
    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(return_value=[position]),
        order_send=MagicMock(return_value=response),
        symbol_info=MagicMock(return_value=SimpleNamespace(point=0.0001, trade_stops_level=10, trade_freeze_level=10)),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.102, ask=1.1022)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_SLTP=3,
        POSITION_TYPE_BUY=0,
    )
    with patch("core.trade_manager.mt5", fake_mt5):
        # +1.02R: breakeven valid (1.005), trail (0.952) below entry -> invalid.
        manager._manage_position(position, 1.102, 0.05)

    con = store._connect()
    try:
        lifecycle = con.execute("SELECT event_type FROM trade_lifecycle_events WHERE ticket=105").fetchall()
        events = [row["event_type"] for row in lifecycle]
    finally:
        con.close()

    assert "BREAKEVEN_TRIGGERED" in events
    assert "TRAILING_TRIGGERED" not in events
    assert "SL_MODIFY_ACCEPTED" in events


def test_trade_manager_state_persistence_roundtrip(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    
    manager.partial_exits.add(111)
    manager.partial_exits.add(222)
    manager.pyramid_entries.add(333)
    
    manager.save_state()
    
    manager2 = TradeManager(MagicMock(), MagicMock(), store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    assert manager2.partial_exits == {111, 222}
    assert manager2.pyramid_entries == {333}


def test_protective_flatten_closes_owned_positions(tmp_path):
    from core.risk_manager import RiskManager

    store = TradingStore(str(tmp_path / "state.db"))
    risk = RiskManager(balance=10000.0, max_daily_loss=0.03, state_file=str(tmp_path / "risk.json"))
    risk.check_daily_drawdown(current_equity=9600.0)  # trip daily loss
    assert risk.pending_flatten_reason() is not None

    manager = TradeManager(MagicMock(), risk, store=store, state_file=str(tmp_path / "tm.json"), conditions_file=str(tmp_path / "tc.json"))
    manager.attach_safety(store=store)
    owned = SimpleNamespace(
        ticket=501, volume=0.5, symbol="EURUSD", magic=460001, type=0,
        price_current=1.1, profit=-50.0,
    )
    def _positions_get(*args, **kwargs):
        if kwargs.get("ticket") == 501 or (args and args[0] == 501):
            return [owned]
        # First global scan sees the position; later scans see flat book.
        if not hasattr(_positions_get, "calls"):
            _positions_get.calls = 0
        _positions_get.calls += 1
        return [owned] if _positions_get.calls == 1 else []

    response = SimpleNamespace(retcode=10009, comment="done")
    fake_mt5 = MagicMock(
        positions_get=MagicMock(side_effect=_positions_get),
        order_send=MagicMock(return_value=response),
        symbol_info_tick=MagicMock(return_value=SimpleNamespace(bid=1.1, ask=1.1002)),
        account_info=MagicMock(return_value=SimpleNamespace(equity=9600.0)),
        TRADE_RETCODE_DONE=10009,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_DEAL=1,
    )
    with patch("core.trade_manager.mt5", fake_mt5), patch("core.risk_manager.mt5", fake_mt5):
        assert manager.enforce_protective_flatten() is True
    assert fake_mt5.order_send.call_count == 1
    assert risk.pending_flatten_reason() is None
