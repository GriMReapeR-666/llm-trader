import pytest
import time
from core.trade_manager import TradeManager, TradingStyle, StyleManagementConfig, PaperPosition
from core.order_execution import PaperBroker


class MockRisk:
    def check_daily_drawdown(self):
        return True

    def check_active_trade_limit(self):
        return True


def test_trade_manager_style_registration_and_config():
    broker = PaperBroker()
    tm = TradeManager(client=broker, risk_manager=MockRisk(), paper_broker=broker)

    # Register different styles for different tickets
    tm.register_position_style(1001, TradingStyle.SCALP)
    tm.register_position_style(1002, TradingStyle.SWING)
    tm.register_position_style(1003, TradingStyle.LONG_TERM)

    assert tm.get_position_style(PaperPosition({"ticket": 1001}, 1001)) == "SCALP"
    assert tm.get_position_style(PaperPosition({"ticket": 1002}, 1002)) == "SWING"
    assert tm.get_position_style(PaperPosition({"ticket": 1003}, 1003)) == "LONG_TERM"

    scalp_cfg = tm.get_style_config(PaperPosition({"ticket": 1001}, 1001))
    assert scalp_cfg.trading_style == TradingStyle.SCALP
    assert scalp_cfg.time_stop_seconds == 900
    assert scalp_cfg.use_spread_trailing is True

    swing_cfg = tm.get_style_config(PaperPosition({"ticket": 1002}, 1002))
    assert swing_cfg.trading_style == TradingStyle.SWING
    assert swing_cfg.time_stop_seconds == 14400

    long_cfg = tm.get_style_config(PaperPosition({"ticket": 1003}, 1003))
    assert long_cfg.trading_style == TradingStyle.LONG_TERM
    assert long_cfg.time_stop_seconds == 7 * 86400
    assert long_cfg.weekly_rebalance is True


def test_trade_manager_style_inference_from_comment():
    broker = PaperBroker()
    tm = TradeManager(client=broker, risk_manager=MockRisk(), paper_broker=broker)

    scalp_pos = PaperPosition({"ticket": 2001, "comment": "avellaneda_scalp"}, 2001)
    macro_pos = PaperPosition({"ticket": 2002, "comment": "macro_event_long_term"}, 2002)
    swing_pos = PaperPosition({"ticket": 2003, "comment": "kalman_swing_pair"}, 2003)

    assert tm.get_position_style(scalp_pos) == "SCALP"
    assert tm.get_position_style(macro_pos) == "LONG_TERM"
    assert tm.get_position_style(swing_pos) == "SWING"


def test_scalp_time_stop_triggers_after_15_minutes():
    broker = PaperBroker()
    tm = TradeManager(client=broker, risk_manager=MockRisk(), paper_broker=broker)

    # Position opened 20 minutes ago (1200s ago)
    t0 = time.time() - 1200
    open_res = broker.market_order("EURUSD", "BUY", 0.1, 1.0800, 1.0750, 1.0900, "scalp_trade", magic=99999)
    ticket = open_res["order"]
    broker.positions[ticket]["time"] = t0
    tm.managed_magics.add(99999)
    tm.register_position_style(ticket, TradingStyle.SCALP)

    pos = PaperPosition(broker.positions[ticket], ticket)
    # Stagnant price (only +0.0001 profit, well below 0.5R)
    tm._check_time_stop(pos, current_price=1.0801, atr=0.0010)

    # Position should be closed due to SCALP 15-minute time stop!
    assert ticket not in broker.positions


def test_swing_position_not_closed_by_15_minute_time_stop():
    broker = PaperBroker()
    tm = TradeManager(client=broker, risk_manager=MockRisk(), paper_broker=broker)

    # Position opened 20 minutes ago (1200s ago)
    t0 = time.time() - 1200
    open_res = broker.market_order("EURUSD", "BUY", 0.1, 1.0800, 1.0750, 1.0900, "swing_trade", magic=99999)
    ticket = open_res["order"]
    broker.positions[ticket]["time"] = t0
    tm.managed_magics.add(99999)
    tm.register_position_style(ticket, TradingStyle.SWING)

    pos = PaperPosition(broker.positions[ticket], ticket)
    # 20 minutes is far below SWING time stop of 4 hours (14400s)
    tm._check_time_stop(pos, current_price=1.0801, atr=0.0010)

    # SWING position remains OPEN!
    assert ticket in broker.positions
