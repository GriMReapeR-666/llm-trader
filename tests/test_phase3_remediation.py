import os
import time
import pytest
from unittest.mock import MagicMock, patch

from core.order_execution import PaperBroker
from data.market_data_provider import PerformanceMonitor
from core.urgency_sor import (
    calculate_alpha_decay_urgency_score,
    UrgencySmartOrderRouter,
    SyntheticStealthExecutionDaemon,
)


class TestPhase3Remediation:
    def test_paper_broker_closed_deal_history(self):
        """Verify PaperBroker records closed deals in self.history with accurate fields."""
        broker = PaperBroker()
        assert hasattr(broker, "history")
        assert len(broker.history) == 0

        # Place market buy order
        order_res = broker.market_order(
            symbol="EURUSD", side="BUY", volume=0.10, price=1.1000,
            sl=1.0900, tp=1.1200, comment="test_deal", magic=460001
        )
        assert order_res["retcode"] == 10009
        ticket = order_res["order"]

        # Close position at 1.1050 (profit = +50 pips * 0.10 = 0.0050 * 0.10 = 0.0005)
        close_res = broker.close_position(ticket=ticket, price=1.1050, volume=0.10, magic=460001)
        assert close_res["retcode"] == 10009
        assert close_res["status"] == "CLOSED"

        assert len(broker.history) == 1
        deal = broker.history[0]
        assert deal["ticket"] == ticket
        assert deal["symbol"] == "EURUSD"
        assert deal["side"] == "BUY"
        assert deal["volume"] == 0.10
        assert deal["price_open"] == 1.1000
        assert deal["price_close"] == 1.1050
        assert deal["profit"] == pytest.approx((1.1050 - 1.1000) * 0.10)
        assert deal["magic"] == 460001
        assert deal["status"] == "CLOSED"
        assert (time.time() - deal["close_time"]) < 5.0

    def test_performance_monitor_store_hydration(self, tmp_path):
        """Verify PerformanceMonitor hydrates open trades from SQLite store when JSON history is empty."""
        history_file = str(tmp_path / "non_existent_history.json")
        store_mock = MagicMock()
        store_mock.open_trade_rows.return_value = [
            {
                "ticket": 200001,
                "symbol": "GBPUSD",
                "side": "BUY",
                "volume": 0.20,
                "price_open": 1.3000,
                "opened_at": time.time(),
                "risk_amount": 100.0,
            }
        ]

        pm = PerformanceMonitor(history_file=history_file, store=store_mock)
        assert len(pm.history) == 1
        trade = pm.history[0]
        assert trade["ticket"] == 200001
        assert trade["symbol"] == "GBPUSD"
        assert trade["volume"] == 0.20
        assert trade["status"] == "OPEN"

    def test_urgency_sor_scoring_and_routing(self):
        """Verify institutional alpha-decay urgency calculation and SOR execution routing."""
        # 1. High urgency: high instantaneous volatility & order-flow imbalance
        u_high = calculate_alpha_decay_urgency_score(
            instantaneous_volatility=0.03,
            order_flow_imbalance=100.0,
            order_book_depth=20.0,
            elapsed_time_seconds=0.0,
            decay_lambda=0.10,
        )
        assert u_high >= 0.70

        # 2. Decayed urgency over time
        u_decayed = calculate_alpha_decay_urgency_score(
            instantaneous_volatility=0.015,
            order_flow_imbalance=85.0,
            order_book_depth=20.0,
            elapsed_time_seconds=60.0,
            decay_lambda=0.10,
        )
        assert u_decayed < u_high

        # 3. Degenerate / invalid inputs fail safely to 0.0
        assert calculate_alpha_decay_urgency_score(-0.01, 10.0, 10.0) == 0.0
        assert calculate_alpha_decay_urgency_score(0.0, 10.0, 10.0) == 0.0
        assert calculate_alpha_decay_urgency_score(float("nan"), 10.0, 10.0) == 0.0

        # 4. Router execution
        router = UrgencySmartOrderRouter(high_urgency_threshold=0.70, low_urgency_threshold=0.30)

        # High urgency -> IOC Taker
        order_high = router.route_execution_order(
            symbol="EURUSD", side="BUY", volume=0.5, price=1.1000,
            urgency_score=0.85, best_bid=1.0999, best_ask=1.1001,
        )
        assert order_high["order_type"] == "MARKET_IOC"
        assert order_high["is_taker"] is True
        assert order_high["target_price"] == 1.1001  # Taker hits Ask for BUY

        # Low urgency -> Passive Maker
        order_low = router.route_execution_order(
            symbol="EURUSD", side="BUY", volume=0.5, price=1.1000,
            urgency_score=0.15, best_bid=1.0999, best_ask=1.1001,
            bid_depth=100.0, ask_depth=50.0,
        )
        assert order_low["order_type"] == "LIMIT_MAKER"
        assert order_low["is_taker"] is False
        assert order_low["target_price"] <= 1.1001

    def test_synthetic_stealth_execution_daemon(self):
        """Verify client-side stealth daemon trigger detection for hidden stops/targets."""
        # BUY trade triggers
        # Current price <= SL -> stop fired
        res1 = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
            current_price=1.0945, stop_loss_price=1.0950, take_profit_price=1.1100, side="BUY"
        )
        assert res1["synthetic_stop_fired"] is True
        assert res1["synthetic_tp_fired"] is False

        # Current price >= TP -> TP fired
        res2 = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
            current_price=1.1105, stop_loss_price=1.0950, take_profit_price=1.1100, side="BUY"
        )
        assert res2["synthetic_stop_fired"] is False
        assert res2["synthetic_tp_fired"] is True

        # Normal price within bounds -> neither fired
        res3 = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
            current_price=1.1020, stop_loss_price=1.0950, take_profit_price=1.1100, side="BUY"
        )
        assert res3["synthetic_stop_fired"] is False
        assert res3["synthetic_tp_fired"] is False

        # SELL trade triggers
        # Price >= SL -> stop fired
        res4 = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
            current_price=1.1055, stop_loss_price=1.1050, take_profit_price=1.0900, side="SELL"
        )
        assert res4["synthetic_stop_fired"] is True
        assert res4["synthetic_tp_fired"] is False

        # Price <= TP -> TP fired
        res5 = SyntheticStealthExecutionDaemon.evaluate_synthetic_trigger(
            current_price=1.0895, stop_loss_price=1.1050, take_profit_price=1.0900, side="SELL"
        )
        assert res5["synthetic_stop_fired"] is False
        assert res5["synthetic_tp_fired"] is True
