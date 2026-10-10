"""Integration tests for live paper trading simulation with the compiled LangGraph engine."""
import pytest
from intelligence.graph_engine_facade import TradingGraphEngine
from core.shared_state import state as ui_state


def test_graph_paper_trading_simulation(tmp_path):
    db_file = str(tmp_path / "test_paper_sim.db")
    engine = TradingGraphEngine(db_path=db_file)

    # 1. Run paper trading cycle with oversold signal
    result = engine.run_paper_cycle(
        symbol="EURUSD",
        timeframe="M15",
        strategy="causal_trend",
        market_snapshot={
            "features": {
                "close": 1.0850,
                "rsi_14": 25.0,  # Oversold -> BUY
                "adx_14": 30.0,
                "spread_pips": 1.0,
            }
        },
    )

    assert result["symbol"] == "EURUSD"
    assert result["status"] == "EXECUTED"
    assert result["execution_receipt"] is not None
    assert result["execution_receipt"]["is_paper"] is True

    # 2. Verify that shared_state was updated during the cycle
    assert ui_state.active_graph_node == "order_execution"
    assert ui_state.node_statuses["order_execution"] == "COMPLETED"
    assert ui_state.last_fused_proposal is not None
    assert ui_state.last_fused_proposal["direction"] == "BUY"
    assert ui_state.last_validation_result["passed"] is True
