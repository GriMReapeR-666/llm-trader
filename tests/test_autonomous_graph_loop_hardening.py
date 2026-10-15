"""Tests for autonomous graph loop hardening and wiring completions."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch
import pytest

from intelligence.llm_orchestrator import LLMOrchestrator
from core.trade_manager import TradeManager
from core.order_execution import PaperBroker


class TestAutonomousGraphLoopHardening:
    """Verify autonomous graph loop completions and paper-to-reflection wiring."""

    def test_journal_paper_fill_dispatches_reflection(self, tmp_path):
        """PaperBroker closed positions must trigger the slow-lane reflection graph."""
        db_file = str(tmp_path / "test_paper_reflect.db")
        orch = MagicMock()
        orch.store = MagicMock()
        orch.store.path = db_file
        orch.store.trade_risk.return_value = 100.0
        orch.velocity = MagicMock()
        orch.operations = MagicMock()
        orch.run_id = "test_run_paper"
        orch._run_reflection_graph = MagicMock()

        # Bind the real _journal_paper_fill
        orch._journal_paper_fill = LLMOrchestrator._journal_paper_fill.__get__(orch, type(orch))

        closed_deal = {
            "ticket": 554433,
            "symbol": "EURUSD",
            "side": "BUY",
            "volume": 0.1,
            "price_open": 1.0800,
            "price_close": 1.0850,
            "profit": 50.0,
            "status": "CLOSED",
            "close_time": time.time(),
        }

        orch._journal_paper_fill(closed_deal)

        # Execution report is recorded
        orch.store.record_execution_report.assert_called_once()
        call_kwargs = orch.store.record_execution_report.call_args[1]
        assert call_kwargs["event_type"] == "PAPER_FILL"
        assert call_kwargs["position_ticket"] == 554433

        # Reflection graph must be dispatched via slow lane
        orch.velocity.submit_slow.assert_called_once()
        submit_args, submit_kwargs = orch.velocity.submit_slow.call_args
        assert submit_args[0] == "reflection_554433"
        assert submit_args[1] == orch._run_reflection_graph
        assert submit_kwargs["ticket"] == 554433
        assert submit_kwargs["realized_pnl"] == 50.0
        assert submit_kwargs["outcome_label"] == "WIN_TARGET"

    def test_paper_position_monitoring_moves_breakeven(self, tmp_path):
        """TradeManager must actively monitor and trail paper broker positions without live client."""
        paper = PaperBroker()
        # Open a buy position at 1.1000 with SL at 1.0950 (risk_dist = 50 pips = 0.0050)
        res = paper.market_order(
            symbol="EURUSD",
            side="BUY",
            volume=0.1,
            price=1.1000,
            sl=1.0950,
            tp=1.1200,
            comment="TestPaper",
            magic=1001,
        )
        ticket = res["order"]
        assert ticket in paper.positions

        risk_mgr = MagicMock()
        risk_mgr.pending_flatten_reason.return_value = None
        risk_mgr.check_daily_drawdown.return_value = True

        store = MagicMock()
        tm = TradeManager(
            client=None,
            risk_manager=risk_mgr,
            managed_magics={1001},
            store=store,
            paper_broker=paper,
        )

        # Price moves up by +1.2R: 1.1000 + 0.0050 * 1.2 = 1.1060
        # Breakeven threshold is +1.0R (0.0050 -> 1.1050)
        current_price = 1.1065
        atr = 0.0020

        tm.monitor_positions(symbol="EURUSD", current_price=current_price, atr=atr)

        # Verify paper position SL was updated to entry + buffer (1.1000 + 0.0002 = 1.1002)
        pos = paper.positions[ticket]
        assert pos["sl"] > 1.0950
        assert pos["sl"] >= 1.1000

    def test_execution_engine_arg_parsing(self):
        """CLI parser must accept --execution-engine [orchestrator|graph]."""
        import subprocess
        import sys

        res = subprocess.run(
            [sys.executable, "mt5_trader.py", "llm-trade", "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "--execution-engine {orchestrator,graph}" in res.stdout

    def test_self_improvement_tick_runs_safely(self, tmp_path):
        """RecursiveSelfImprovementEngine run_improvement_cycle runs cleanly."""
        from core.self_improvement_loop import RecursiveSelfImprovementEngine
        state_file = str(tmp_path / "self_improvement_state.json")
        engine = RecursiveSelfImprovementEngine(state_file=state_file)
        res = engine.run_improvement_cycle(store=None)
        assert isinstance(res, list)
        for proposal in res:
            assert hasattr(proposal, "status")

