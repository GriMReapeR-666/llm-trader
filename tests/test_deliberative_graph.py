"""Integration tests for the LangGraph Deliberative Decision Graph."""
import pytest
from graph_state import create_initial_trading_cycle_state
from deliberative_graph import build_deliberative_graph
from graph_checkpointer import SqliteCheckpointSaver


def test_deliberative_graph_execution_with_rejection(tmp_path):
    db_file = str(tmp_path / "test_trading.db")
    saver = SqliteCheckpointSaver(db_path=db_file)
    app = build_deliberative_graph(db_path=db_file, checkpointer=saver)

    # State with neutral RSI -> Expected to generate HOLD / No Actionable Proposal
    initial_state = create_initial_trading_cycle_state(
        symbol="EURUSD",
        timeframe="M15",
        strategy="causal_trend",
        paper_mode=True,
        market_snapshot={
            "features": {
                "close": 1.0850,
                "rsi_14": 50.0,  # Neutral -> HOLD
                "adx_14": 15.0,
                "spread_pips": 1.0,
            }
        },
    )

    config = {"configurable": {"thread_id": "test_delib_thread_1"}}
    final_state = app.invoke(initial_state, config=config)

    assert final_state["status"] == "REJECTED"
    assert final_state["validation_passed"] is False
    assert "NO_ACTIONABLE_PROPOSAL" in final_state["rejection_reasons"]
    assert len(final_state["audit_trace"]) >= 5


def test_deliberative_graph_execution_with_buy_signal(tmp_path):
    db_file = str(tmp_path / "test_trading_buy.db")
    saver = SqliteCheckpointSaver(db_path=db_file)
    app = build_deliberative_graph(db_path=db_file, checkpointer=saver)

    # State with oversold RSI and strong ADX -> Triggers BUY hypothesis
    initial_state = create_initial_trading_cycle_state(
        symbol="EURUSD",
        timeframe="M15",
        strategy="causal_trend",
        paper_mode=True,
        market_snapshot={
            "features": {
                "close": 1.0850,
                "rsi_14": 25.0,  # Oversold -> BUY
                "adx_14": 30.0,
                "spread_pips": 1.0,
            }
        },
    )

    config = {"configurable": {"thread_id": "test_delib_thread_2"}}
    final_state = app.invoke(initial_state, config=config)

    assert final_state["fused_proposal"] is not None
    assert final_state["fused_proposal"]["direction"] == "BUY"
    assert final_state["status"] == "EXECUTED"
    assert final_state["validation_passed"] is True
    assert final_state["execution_receipt"] is not None
    assert final_state["execution_receipt"]["symbol"] == "EURUSD"
