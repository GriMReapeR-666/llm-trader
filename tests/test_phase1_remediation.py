import pytest
from unittest.mock import MagicMock, patch
from core.trade_manager import TradeManager
from core.order_execution import PaperBroker
from core.risk_manager import RiskManager
from operations.agent_operations import AgentOperations
from intelligence.llm_orchestrator import LLMOrchestrator
import os


class TestPhase1Remediation:
    def test_paper_mode_position_trailing_stop_and_exit(self, tmp_path):
        """Verify TradeManager directly manages, breakevens, and partial-closes PaperBroker positions."""
        # Hermetic risk state: never read ambient root risk_state.json (paper boot may
        # have tripped a LEVEL-3 breaker there, which would flatten instantly).
        risk = RiskManager(balance=10000.0, state_file=str(tmp_path / "risk_state.json"))
        broker = PaperBroker()
        state_file = str(tmp_path / "tm_state.json")
        tm = TradeManager(client=None, risk_manager=risk, paper_broker=broker, managed_magics={460001}, state_file=state_file)

        # Place a paper buy order
        order_res = broker.market_order(
            symbol="EURUSD", side="BUY", volume=0.10, price=1.1000,
            sl=1.0900, tp=1.1300, comment="test_trade", magic=460001
        )
        assert order_res["retcode"] == 10009
        ticket = order_res["order"]
        assert ticket in broker.positions

        # 1. Simulate price moving past +1R (entry 1.1000, risk 100 pts -> price 1.1110)
        # Breakeven should ratchet SL to entry price + 0.1 ATR buffer
        tm.monitor_positions(symbol="EURUSD", current_price=1.1110, atr=0.0010)
        assert broker.positions[ticket]["sl"] == pytest.approx(1.1001)

        # 2. Simulate price moving past +2R (price 1.1210) -> partial close 50%
        tm.monitor_positions(symbol="EURUSD", current_price=1.1210, atr=0.0010)
        assert ticket in tm.partial_exits
        assert broker.positions[ticket]["volume"] == 0.05

        # 3. Simulate protective flatten triggering
        risk.trigger_emergency_flatten("Daily drawdown limit breached")
        assert tm.enforce_protective_flatten() is True
        assert ticket not in broker.positions

    def test_active_trade_limit_scoping_paper_and_live(self, tmp_path):
        """Verify check_active_trade_limit checks paper provider in paper mode and owned magics in live."""
        risk = RiskManager(balance=10000.0, state_file=str(tmp_path / "risk_state2.json"))
        broker = PaperBroker()
        risk.set_active_positions_provider(lambda: len(broker.positions))

        # Empty broker -> within limit
        assert risk.check_active_trade_limit() is True

        # Add 2 positions
        broker.market_order("EURUSD", "BUY", 0.01, 1.10, 1.09, 1.12, "order1", magic=460001)
        broker.market_order("GBPUSD", "BUY", 0.01, 1.25, 1.24, 1.27, "order2", magic=460001)
        # Max active is 2 -> limit reached
        assert risk.check_active_trade_limit() is False

        # Test MT5 owned magics isolation: unowned manual positions do not consume bot capacity
        risk.set_active_positions_provider(None)
        mock_unowned = MagicMock(magic=999999)
        mock_owned = MagicMock(magic=460001)
        with patch("core.risk_manager.mt5") as mock_mt5:
            mock_mt5.positions_get.return_value = [mock_unowned, mock_unowned, mock_owned]
            # Total positions is 3, but owned is only 1 (< max 2) -> Allowed!
            assert risk.check_active_trade_limit() is True

    def test_prompt_token_budget_sanitization(self):
        """Verify prompt budgeting compresses massive news articles and keeps prompt within safe bounds."""
        large_news = [
            {
                "title": f"Major Financial Announcement {i}",
                "description": "A" * 15000,
                "content": "B" * 20000,
                "source": "Reuters",
                "sentiment": 0.5,
                "published_at": "2026-09-03T10:00:00Z",
            }
            for i in range(5)
        ]
        toon_lite = {
            "news_events": large_news,
            "api_news": large_news,
            "hot_topics": [{"topic": f"Topic {i}", "summary": "C" * 2000} for i in range(5)],
            "advanced_auto_tools_summary": {"heavy": "D" * 30000},
            "open_positions": [{"ticket": 1, "symbol": "EURUSD"}],
        }

        sanitized = LLMOrchestrator._sanitize_toon_lite_budget(toon_lite, max_total_chars=60000)
        import json
        dumped = json.dumps(sanitized, default=str)
        assert len(dumped) <= 60000
        # Check that news descriptions were truncated
        assert len(sanitized["news_events"][0]["summary"]) <= 250
        # Core position context is preserved
        assert sanitized["open_positions"][0]["ticket"] == 1

    def test_release_task_on_market_holiday(self):
        """Verify operations.release_task deletes running task lease so candle is not permanently consumed."""
        import tempfile
        tmp_dir = tempfile.mkdtemp()
        db_path = os.path.join(tmp_dir, "test_holiday.db")
        try:
            ops = AgentOperations(db_path)
            cycle_key = "swing:EURUSD:H1:202609031000"
            claimed = ops.claim_task(cycle_key, "swing", "candle_analysis", {"symbol": "EURUSD"})
            assert claimed is True

            # Releasing task
            ops.release_task(cycle_key, reason="market_holiday")

            # Should be able to claim again immediately
            reclaimed = ops.claim_task(cycle_key, "swing", "candle_analysis", {"symbol": "EURUSD"})
            assert reclaimed is True
        finally:
            import shutil
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
