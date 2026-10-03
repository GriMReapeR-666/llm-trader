import math
import time
import pytest
from unittest.mock import MagicMock, patch

from core.order_execution import (
    PaperBroker,
    calculate_dynamic_slippage_deviation,
    place_urgency_routed_order,
)
from mt5_trader import Config, MT5Client


class TestExecutionEngineHardening:
    def test_calculate_dynamic_slippage_deviation(self):
        # 1. High urgency expands deviation (1.5x)
        dev_high = calculate_dynamic_slippage_deviation("EURUSD", urgency_score=0.85, spread_points=20.0)
        assert dev_high == 30

        # 2. Low urgency tightens deviation (0.8x)
        dev_low = calculate_dynamic_slippage_deviation("EURUSD", urgency_score=0.20, spread_points=20.0)
        assert dev_low == 16

        # 3. Neutral urgency preserves spread points (1.0x)
        dev_neutral = calculate_dynamic_slippage_deviation("EURUSD", urgency_score=0.50, spread_points=20.0)
        assert dev_neutral == 20

        # 4. Clamping bounds [5, 150]
        dev_tight = calculate_dynamic_slippage_deviation("EURUSD", urgency_score=0.10, spread_points=2.0)
        assert dev_tight >= 5

        dev_wide = calculate_dynamic_slippage_deviation("EURUSD", urgency_score=0.95, spread_points=500.0)
        assert dev_wide <= 150

        # 5. Non-finite / degenerate inputs fail safely to base points
        assert calculate_dynamic_slippage_deviation("EURUSD", urgency_score=float("nan"), spread_points=None, base_points=20) == 20
        assert calculate_dynamic_slippage_deviation("EURUSD", urgency_score=0.5, spread_points=float("inf"), base_points=25) == 25

    def test_paper_maker_limit_routing(self):
        paper = PaperBroker()
        assert len(paper.orders) == 0
        assert len(paper.positions) == 0

        # Mock tick and urgency score to simulate low urgency (maker limit)
        mock_tick = MagicMock()
        mock_tick.bid = 1.1000
        mock_tick.ask = 1.1002
        mock_tick.volume = 1

        with patch("core.order_execution._symbol_tick", return_value=mock_tick):
            with patch("core.urgency_sor.calculate_alpha_decay_urgency_score", return_value=0.15):
                res = place_urgency_routed_order(
                    symbol="EURUSD",
                    side="BUY",
                    volume=0.10,
                    sl=1.0950,
                    tp=1.1100,
                    comment="maker_test",
                    paper=paper,
                    magic=460001,
                )

        assert res["retcode"] == 10009
        # Low urgency routes to pending order queue in PaperBroker, not immediate positions
        assert len(paper.orders) == 1
        assert len(paper.positions) == 0
        order = list(paper.orders.values())[0]
        assert order["symbol"] == "EURUSD"
        assert order["type"] == "buy_limit"
        assert order["magic"] == 460001

    def test_reconnect_if_needed_backoff(self):
        cfg = MagicMock()
        client = MT5Client(cfg)
        assert client._reconnect_backoff_sec == 5.0
        assert client._last_reconnect_attempt == 0.0

        mock_ti_disconnected = MagicMock(connected=False)
        mock_ti_connected = MagicMock(connected=True)

        with patch("mt5_trader.mt5.terminal_info", return_value=mock_ti_disconnected):
            with patch.object(client, "login", return_value=False) as mock_login:
                # First attempt: should execute login and start backoff
                res1 = client.reconnect_if_needed()
                assert res1 is False
                assert mock_login.call_count == 1
                assert client._reconnect_backoff_sec == 10.0  # doubled from 5.0 to 10.0

                # Second immediate attempt within 10s: should be throttled (login not called)
                res2 = client.reconnect_if_needed()
                assert res2 is False
                assert mock_login.call_count == 1  # count did not increase

                # Advance simulated clock past backoff window
                client._last_reconnect_attempt -= 15.0
                res3 = client.reconnect_if_needed()
                assert res3 is False
                assert mock_login.call_count == 2
                assert client._reconnect_backoff_sec == 20.0  # doubled to 20.0

        # Now simulate reconnection success
        with patch("mt5_trader.mt5.terminal_info", return_value=mock_ti_connected):
            res_success = client.reconnect_if_needed()
            assert res_success is True
            assert client._reconnect_backoff_sec == 5.0  # reset back to 5.0s
