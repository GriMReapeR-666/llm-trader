import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json

# Add parent dir to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade_manager import TradeManager
from mt5_trader import LLMOrchestrator

class TestTradeManagement(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.mock_risk = MagicMock()
        self.tm = TradeManager(self.mock_client, self.mock_risk)

    @patch("trade_manager.mt5")
    def test_get_position_context(self, mock_mt5):
        # Mock a position
        mock_pos = MagicMock()
        mock_pos.ticket = 12345
        mock_pos.symbol = "EURUSD"
        mock_pos.type = 0 # BUY
        mock_pos.volume = 1.0
        mock_pos.price_open = 1.1000
        mock_pos.sl = 1.0900 # Risk = 100 points
        mock_pos.tp = 1.1200
        mock_pos.profit = 500.0
        mock_pos.time = 1700000000
        
        mock_mt5.positions_get.return_value = [mock_pos]
        mock_mt5.ORDER_TYPE_BUY = 0
        
        # Mock Tick for current price
        mock_tick = MagicMock()
        mock_tick.bid = 1.1050 # Current Price (Profit = 50 points)
        mock_mt5.symbol_info_tick.return_value = mock_tick
        
        ctx = self.tm.get_position_context(12345)
        
        # Risk = 1.1000 - 1.0900 = 0.0100
        # Profit = 1.1050 - 1.1000 = 0.0050
        # R-Multiple = 0.5R
        self.assertEqual(ctx["r_multiple"], 0.5)
        self.assertEqual(ctx["pnl"], 500.0)

    @patch("trade_manager.mt5")
    def test_execute_partial_close(self, mock_mt5):
        # Mock Position
        mock_pos = MagicMock()
        mock_pos.ticket = 12345
        mock_pos.volume = 1.0
        mock_pos.symbol = "EURUSD"
        mock_pos.magic = 460001
        mock_pos.type = 0
        mock_mt5.positions_get.return_value = [mock_pos]
        mock_mt5.TRADE_RETCODE_DONE = 10009
        mock_mt5.symbol_info.return_value = MagicMock(volume_min=0.01, volume_max=500.0, volume_step=0.01, filling_mode=3)
        mock_mt5.ORDER_FILLING_IOC = 1
        mock_mt5.ORDER_FILLING_FOK = 0
        
        # Mock Order Send Success
        mock_res = MagicMock()
        mock_res.retcode = 10009
        mock_mt5.order_send.return_value = mock_res
        
        result = self.tm.execute_partial_close(12345, 0.5, "AI Test")
        
        self.assertTrue(result)
        # Verify order_send called with 0.5 volume
        args, _ = mock_mt5.order_send.call_args
        self.assertEqual(args[0]["volume"], 0.5)
        self.assertIn("AI: AI Test", args[0]["comment"])

    @patch("trade_manager.mt5")
    def test_modify_sl_rejects_broker_stop_distance(self, mock_mt5):
        mock_pos = MagicMock()
        mock_pos.ticket = 12345
        mock_pos.symbol = "EURUSD"
        mock_pos.magic = 460001
        mock_pos.type = 0
        mock_pos.sl = 1.0900
        mock_pos.tp = 1.1200
        mock_mt5.positions_get.return_value = [mock_pos]
        mock_mt5.POSITION_TYPE_BUY = 0
        mock_tick = MagicMock(bid=1.1000, ask=1.1002)
        mock_mt5.symbol_info_tick.return_value = mock_tick
        mock_mt5.symbol_info.return_value = MagicMock(point=0.0001, trade_stops_level=20, trade_freeze_level=10)
        assert self.tm.execute_modify_sl(12345, 1.0995, "too close") is False
        mock_mt5.order_send.assert_not_called()

    @patch("trade_manager.mt5")
    def test_management_volume_uses_broker_step_and_bounds(self, mock_mt5):
        mock_mt5.symbol_info.return_value = MagicMock(
            volume_min=0.1, volume_max=2.0, volume_step=0.1, filling_mode=3
        )
        mock_mt5.ORDER_FILLING_IOC = 1
        mock_mt5.ORDER_FILLING_FOK = 0
        assert self.tm._normalize_volume("XAUUSD", 0.26) == 0.2
        assert self.tm._normalize_volume("XAUUSD", 3.0) == 2.0
        assert self.tm._normalize_volume("XAUUSD", 0.05) == 0.0
        assert self.tm._broker_filling_mode("XAUUSD") == 1
        mock_mt5.symbol_info.return_value.filling_mode = 1
        assert self.tm._broker_filling_mode("XAUUSD") == 0
        mock_mt5.symbol_info.return_value.filling_mode = 0
        assert self.tm._broker_filling_mode("XAUUSD") is None

    @patch("trade_manager.mt5")
    def test_management_refuses_unowned_position(self, mock_mt5):
        position = MagicMock(ticket=12345, symbol="EURUSD", magic=999999, volume=1.0, type=0)
        self.tm._execute_partial_close(position)
        self.tm._execute_pyramid_order(position)
        mock_mt5.order_send.assert_not_called()

    def test_llm_management_decisions_are_advisory_only(self):
        """LLM management_decisions must never call TradeManager execute_*."""
        risk = MagicMock()
        risk.risk_per_trade = 0.01
        orchestrator = LLMOrchestrator(
            self.mock_client, risk, MagicMock(), "api_key", enable_rl=False,
        )
        mock_tm = MagicMock()
        orchestrator.data_provider = MagicMock()
        orchestrator.data_provider.trade_manager = mock_tm
        orchestrator.store = MagicMock()
        orchestrator.operations = MagicMock()
        orchestrator.run_id = "test-run"

        decision = {
            "action": "HOLD",
            "management_decisions": [
                {
                    "ticket": 999,
                    "action": "PARTIAL_CLOSE",
                    "params": {"percentage": 0.5},
                    "reasoning": "Taking profit",
                },
                {
                    "ticket": 1000,
                    "action": "CLOSE_FULL",
                    "params": {},
                    "reasoning": "Exit",
                },
                {
                    "ticket": 1001,
                    "action": "UPDATE_SL",
                    "params": {"new_sl": 1.1},
                    "reasoning": "BE",
                },
            ],
        }

        envelope = orchestrator._record_advisory_management_suggestions(
            decision, symbol="EURUSD", strategy="scalp", cycle_key="test:cycle",
        )

        mock_tm.execute_partial_close.assert_not_called()
        mock_tm.execute_close_full.assert_not_called()
        mock_tm.execute_modify_sl.assert_not_called()
        self.assertFalse(envelope.get("execution_authority"))
        self.assertEqual(envelope.get("count"), 3)
        self.assertIn("deterministic PositionManager", envelope.get("limitation", ""))
        self.assertEqual(decision.get("management_advisory"), envelope)
        for item in decision["management_decisions"]:
            self.assertFalse(item.get("execution_authority"))
            self.assertEqual(item.get("status"), "ADVISORY_ONLY")
        orchestrator.store.set_state.assert_called()
        orchestrator.store.log_lifecycle_event.assert_called()

    def test_llm_conditional_scenarios_are_advisory_only(self):
        """LLM conditional_scenarios must not arm executable TradeManager closes."""
        risk = MagicMock()
        risk.risk_per_trade = 0.01
        orchestrator = LLMOrchestrator(
            self.mock_client, risk, MagicMock(), "api_key", enable_rl=False,
        )
        mock_tm = MagicMock()
        orchestrator.data_provider = MagicMock()
        orchestrator.data_provider.trade_manager = mock_tm
        orchestrator.store = MagicMock()
        orchestrator.operations = MagicMock()
        orchestrator.run_id = "test-run"

        decision = {
            "action": "HOLD",
            "conditional_scenarios": [
                {"condition": "price > 1.2000", "action": "close"},
                {"condition": "price < 1.1000", "action": "BUY BREAKOUT"},
            ],
        }
        envelope = orchestrator._record_advisory_conditional_scenarios(
            decision, symbol="EURUSD", strategy="scalp", cycle_key="test:cond",
        )
        self.assertFalse(envelope.get("execution_authority"))
        self.assertEqual(envelope.get("count"), 2)
        for item in decision["conditional_scenarios"]:
            self.assertFalse(item.get("execution_authority"))
            self.assertEqual(item.get("status"), "ADVISORY_ONLY")
        mock_tm.register_conditions.assert_called_once()
        mock_tm.execute_close_full.assert_not_called()
        args, _ = mock_tm.register_conditions.call_args
        self.assertEqual(args[0], "EURUSD")
        self.assertTrue(all(not row.get("execution_authority") for row in args[1]))

    @patch("trade_manager.mt5")
    def test_programmatic_conditions_do_not_close(self, mock_mt5):
        """Matched LLM smart-exit text must journal only — never order_send."""
        store = MagicMock()
        self.tm.store = store
        self.tm.active_conditions = {
            "EURUSD": [{"condition": "price > 1.1050", "action": "close"}],
        }
        pos = MagicMock(ticket=55, symbol="EURUSD", magic=460001, type=0, volume=0.1)
        self.tm._check_programmatic_conditions("EURUSD", 1.1100, pos)
        mock_mt5.order_send.assert_not_called()
        store.log_lifecycle_event.assert_called()
        self.assertEqual(
            self.tm.active_conditions["EURUSD"][0].get("status"),
            "MATCHED_ADVISORY",
        )

    def test_pulse_maintenance_skips_llm_and_claims(self):
        risk = MagicMock()
        risk.risk_per_trade = 0.01
        orchestrator = LLMOrchestrator(
            self.mock_client, risk, MagicMock(), "api_key", enable_rl=False,
        )
        orchestrator.reconcile_pending_orders = MagicMock()
        orchestrator.reconcile_open_positions = MagicMock()
        orchestrator._monitor_owned_positions_tick = MagicMock()
        orchestrator._process_due_schedules = MagicMock()
        orchestrator._poll_velocity_results = MagicMock()
        orchestrator.velocity = MagicMock()
        orchestrator.velocity.submit_slow.return_value = {"status": "SUBMITTED"}
        orchestrator.operations = MagicMock()
        orchestrator.run_id = "maint-run"

        result = orchestrator.pulse_maintenance("GBPUSD", paper_mode=True)
        self.assertEqual(result["action"], "HOLD")
        self.assertFalse(result.get("execution_authority"))
        orchestrator.operations.claim_task.assert_not_called()
        orchestrator._process_due_schedules.assert_called_once()
        orchestrator._monitor_owned_positions_tick.assert_called_once()


if __name__ == "__main__":
    unittest.main()
