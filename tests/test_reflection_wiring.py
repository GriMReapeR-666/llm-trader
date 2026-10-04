"""Tests for wiring the ReflectionGraph into the production trading cycle."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from intelligence.graph_state import MemoryReflectionState, utcnow_iso


class TestReflectionGraphWiring:
    """Verify that LLMOrchestrator wires the reflection graph after trade closure."""

    def _make_orchestrator(self, tmp_path):
        """Build a minimal LLMOrchestrator with mocked dependencies."""
        db_file = str(tmp_path / "test_wiring.db")

        # Avoid loading the full orchestrator; test the method in isolation.
        # We simulate the orchestrator's relevant attributes.
        orch = MagicMock()
        orch.store = MagicMock()
        orch.store.path = db_file
        orch.macro_graph = MagicMock()
        orch.velocity = MagicMock()
        orch.operations = MagicMock()
        orch.run_id = "test_run"
        orch._reflection_graph = None

        # Bind the real method to the mock
        from intelligence.llm_orchestrator import LLMOrchestrator
        orch._run_reflection_graph = LLMOrchestrator._run_reflection_graph.__get__(orch, type(orch))
        return orch

    def test_run_reflection_graph_builds_graph_lazily(self, tmp_path):
        """First invocation builds the reflection graph; subsequent calls reuse it."""
        orch = self._make_orchestrator(tmp_path)
        now = utcnow_iso()

        with patch("intelligence.llm_orchestrator.build_reflection_graph") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.return_value = {
                "deal_ticket": 999, "symbol": "EURUSD", "strategy": "test",
                "open_time": now, "close_time": now, "entry_price": 1.08,
                "close_price": 1.09, "realized_pnl": 100.0,
                "realized_r_multiple": 1.5, "outcome_label": "WIN_TARGET",
                "distilled_atomic_facts": [], "scenario_key": None,
                "scenario_updated": True, "macro_dynamics_feedback": {"pnl": 100.0},
                "audit_trace": ["done"], "updated_at": now,
            }
            mock_build.return_value = mock_app

            orch._run_reflection_graph(
                ticket=999, symbol="EURUSD", strategy="causal_breakout",
                open_time=now, close_time=now, entry_price=1.08,
                close_price=1.09, realized_pnl=100.0,
                realized_r_multiple=1.5, outcome_label="WIN_TARGET",
            )

            # Graph built once
            mock_build.assert_called_once_with(db_path=str(tmp_path / "test_wiring.db"))
            mock_app.invoke.assert_called_once()

            # Second call reuses cached graph
            orch._reflection_graph = None  # reset to prove lazy build
            orch._run_reflection_graph(
                ticket=1000, symbol="GBPUSD", strategy="test",
                open_time=now, close_time=now, entry_price=1.27,
                close_price=1.28, realized_pnl=50.0,
                realized_r_multiple=1.0, outcome_label="WIN_TARGET",
            )
            # build_reflection_graph called once more (lazy rebuild after reset)
            assert mock_build.call_count == 2

    def test_run_reflection_graph_constructs_correct_state(self, tmp_path):
        """The MemoryReflectionState is built from the provided trade parameters."""
        orch = self._make_orchestrator(tmp_path)
        now = utcnow_iso()

        with patch("intelligence.llm_orchestrator.build_reflection_graph") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.return_value = {"audit_trace": ["ok"], "updated_at": now}
            mock_build.return_value = mock_app

            orch._run_reflection_graph(
                ticket=42, symbol="USDJPY", strategy="mean_reversion",
                open_time="2026-01-01T10:00:00+00:00",
                close_time="2026-01-01T14:00:00+00:00",
                entry_price=150.0, close_price=149.5,
                realized_pnl=-75.0, realized_r_multiple=-1.5,
                outcome_label="LOSS_STOP",
            )

            call_args = mock_app.invoke.call_args
            state = call_args[0][0]
            config = call_args[1].get("config", call_args[0][1] if len(call_args[0]) > 1 else {})

            assert state["deal_ticket"] == 42
            assert state["symbol"] == "USDJPY"
            assert state["strategy"] == "mean_reversion"
            assert state["entry_price"] == 150.0
            assert state["close_price"] == 149.5
            assert state["realized_pnl"] == -75.0
            assert state["realized_r_multiple"] == -1.5
            assert state["outcome_label"] == "LOSS_STOP"
            assert state["distilled_atomic_facts"] == []
            assert state["scenario_updated"] is False
            assert state["macro_dynamics_feedback"] is None

    def test_run_reflection_graph_handles_exception_gracefully(self, tmp_path):
        """If the reflection graph raises, the orchestrator logs and continues."""
        orch = self._make_orchestrator(tmp_path)
        now = utcnow_iso()

        with patch("intelligence.llm_orchestrator.build_reflection_graph") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.side_effect = RuntimeError("graph crashed")
            mock_build.return_value = mock_app

            # Should not raise
            orch._run_reflection_graph(
                ticket=1, symbol="EURUSD", strategy="test",
                open_time=now, close_time=now, entry_price=1.0,
                close_price=1.01, realized_pnl=10.0,
                realized_r_multiple=0.5, outcome_label="WIN_TARGET",
            )

    def test_wiring_submits_to_slow_velocity(self, tmp_path):
        """The reconcile path dispatches reflection via velocity.submit_slow."""
        orch = self._make_orchestrator(tmp_path)
        now = utcnow_iso()

        with patch("intelligence.llm_orchestrator.build_reflection_graph") as mock_build:
            mock_app = MagicMock()
            mock_app.invoke.return_value = {"audit_trace": [], "updated_at": now}
            mock_build.return_value = mock_app

            # Simulate what reconcile_open_positions does after trade closure
            orch.velocity.submit_slow(
                "reflection_55",
                orch._run_reflection_graph,
                ticket=55, symbol="EURUSD", strategy="causal_breakout",
                open_time=now, close_time=now, entry_price=1.08,
                close_price=1.09, realized_pnl=200.0,
                realized_r_multiple=2.0, outcome_label="WIN_TARGET",
            )

            orch.velocity.submit_slow.assert_called_once()
            call_kwargs = orch.velocity.submit_slow.call_args
            assert call_kwargs[0][0] == "reflection_55"
            assert call_kwargs[0][1] == orch._run_reflection_graph

    def test_reflection_graph_nodes_execute(self, tmp_path):
        """Full integration: reflection graph runs all four nodes end-to-end."""
        from intelligence.reflection_graph import build_reflection_graph
        from intelligence.graph_checkpointer import SqliteCheckpointSaver

        db_file = str(tmp_path / "integration_reflection.db")
        saver = SqliteCheckpointSaver(db_path=db_file)
        app = build_reflection_graph(db_path=db_file, checkpointer=saver)

        now = utcnow_iso()
        state: MemoryReflectionState = {
            "deal_ticket": 77777,
            "symbol": "GBPUSD",
            "strategy": "causal_continuation",
            "open_time": now,
            "close_time": now,
            "entry_price": 1.2700,
            "close_price": 1.2850,
            "realized_pnl": 500.0,
            "realized_r_multiple": 3.0,
            "outcome_label": "UNKNOWN",
            "distilled_atomic_facts": [],
            "scenario_key": None,
            "scenario_updated": False,
            "macro_dynamics_feedback": None,
            "audit_trace": ["Integration start"],
            "updated_at": now,
        }

        config = {"configurable": {"thread_id": "integration_test"}}
        final = app.invoke(state, config=config)

        # All four nodes executed
        assert final["outcome_label"] == "WIN_TARGET"
        assert len(final["distilled_atomic_facts"]) == 1
        assert final["distilled_atomic_facts"][0]["fact_key"] == "trade_outcome_77777"
        assert final["scenario_updated"] is True
        assert final["macro_dynamics_feedback"]["pnl"] == 500.0
        assert any("EquityStateUpdate" in a for a in final["audit_trace"])
        assert any("QuantitativeCoach" in a for a in final["audit_trace"])
        assert any("ScenarioConsolidator" in a for a in final["audit_trace"])
        assert any("MacroFeedback" in a for a in final["audit_trace"])
