"""Integration tests for the TradingGraphEngine Facade and Human-in-the-Loop gates."""
import pytest
from graph_engine_facade import TradingGraphEngine


def test_trading_graph_engine_deliberative_and_reflection(tmp_path):
    db_file = str(tmp_path / "test_facade.db")
    engine = TradingGraphEngine(db_path=db_file, initial_balance=10000.0)

    # 1. Run deliberative cycle
    result = engine.run_deliberative_cycle(
        symbol="GBPUSD",
        timeframe="M15",
        strategy="causal_continuation",
        market_snapshot={
            "features": {
                "close": 1.2500,
                "rsi_14": 20.0,  # Oversold -> BUY
                "adx_14": 30.0,
                "spread_pips": 1.0,
            }
        },
        paper_mode=True,
    )

    assert result["symbol"] == "GBPUSD"
    assert result["status"] == "EXECUTED"
    assert result["execution_receipt"] is not None

    # 2. Run post-trade reflection
    reflection = engine.run_reflection_cycle(
        deal_ticket=998877,
        symbol="GBPUSD",
        strategy="causal_continuation",
        realized_pnl=150.0,
        realized_r_multiple=1.5,
        async_lane=False,
    )

    assert reflection is not None
    assert reflection["outcome_label"] == "WIN_TARGET"
    assert len(reflection["distilled_atomic_facts"]) >= 1


def test_trading_graph_engine_human_in_the_loop(tmp_path):
    db_file = str(tmp_path / "test_hitl.db")
    engine = TradingGraphEngine(db_path=db_file, require_human_approval=True, initial_balance=10000.0)

    thread_id = "hitl_thread_001"
    # Initiate cycle that triggers a BUY proposal
    # (standard-quote pair so paper-mode sizing clears the micro-lot floor)
    result = engine.run_deliberative_cycle(
        symbol="EURUSD",
        timeframe="H1",
        strategy="macro_trend",
        market_snapshot={
            "features": {
                "close": 1.1000,
                "rsi_14": 20.0,  # Oversold -> BUY
                "adx_14": 35.0,
                "spread_pips": 0.8,
            }
        },
        paper_mode=True,
        thread_id=thread_id,
    )

    # Should be paused before 'order_execution'
    state_history = engine.get_thread_history(thread_id)
    assert len(state_history) > 0

    # Resume with approval
    resumed = engine.resume_interrupted_cycle(thread_id=thread_id, approve=True)
    assert resumed["status"] == "EXECUTED"
    assert resumed["execution_receipt"] is not None
