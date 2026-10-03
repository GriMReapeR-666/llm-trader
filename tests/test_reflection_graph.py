"""Integration tests for the LangGraph Slow-Lane Reflection Graph."""
import pytest
from reflection_graph import build_reflection_graph
from graph_checkpointer import SqliteCheckpointSaver
from graph_state import MemoryReflectionState, utcnow_iso


def test_reflection_graph_execution(tmp_path):
    db_file = str(tmp_path / "test_reflection.db")
    saver = SqliteCheckpointSaver(db_path=db_file)
    app = build_reflection_graph(db_path=db_file, checkpointer=saver)

    now = utcnow_iso()
    state: MemoryReflectionState = {
        "deal_ticket": 123456,
        "symbol": "EURUSD",
        "strategy": "causal_breakout",
        "open_time": now,
        "close_time": now,
        "entry_price": 1.0800,
        "close_price": 1.0850,
        "realized_pnl": 250.0,
        "realized_r_multiple": 2.5,
        "outcome_label": "UNKNOWN",
        "distilled_atomic_facts": [],
        "scenario_key": None,
        "scenario_updated": False,
        "macro_dynamics_feedback": None,
        "audit_trace": ["Start test reflection"],
        "updated_at": now,
    }

    config = {"configurable": {"thread_id": "test_reflect_thread"}}
    final_state = app.invoke(state, config=config)

    assert final_state["outcome_label"] == "WIN_TARGET"
    assert len(final_state["distilled_atomic_facts"]) == 1
    assert "trade_outcome_123456" == final_state["distilled_atomic_facts"][0]["fact_key"]
    assert final_state["scenario_updated"] is True
    assert final_state["macro_dynamics_feedback"]["pnl"] == 250.0
