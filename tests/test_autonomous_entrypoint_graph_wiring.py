"""Integration test verifying all graph nodes are wired and functional in the autonomous entry point."""
import os
import json
import pytest

from core.harness import TradingContext
from core.harness.quant_models_plugins import mount_profile
from intelligence.graph_state import create_initial_trading_cycle_state
from core.shared_state import ui_state
from ui.gui_api import _deliberative_graph_snapshot


def test_autonomous_entrypoint_graph_wiring(tmp_path):
    test_db = str(tmp_path / "test_wiring.db")
    
    # 1. Mount paper profile exactly as done in mt5_trader.py
    ctx = TradingContext(name="paper_ctx")
    mount_profile(ctx, "paper")
    
    assert ctx.has("deliberative_graph"), "deliberative_graph plugin must be mounted in paper profile"
    delib_app = ctx.get("deliberative_graph")
    assert delib_app is not None
    
    # 2. Reset UI state for a new cycle
    ui_state.reset_graph_pipeline(symbol="EURUSD", timeframe="M15")
    assert ui_state.node_statuses["technical_net"] == "PENDING"
    assert ui_state.node_statuses["signal_arbitrator"] == "PENDING"
    assert ui_state.node_statuses["debate_protocol"] == "PENDING"
    
    # 3. Simulate autonomous pulse cycle execution with streaming
    initial_state = create_initial_trading_cycle_state(
        symbol="EURUSD",
        timeframe="M15",
        strategy="causal_continuation",
        paper_mode=True,
    )
    
    executed_nodes = []
    final_state = {}
    
    for event in delib_app.stream(
        initial_state,
        config={"configurable": {"thread_id": "test_pulse_thread_1"}},
    ):
        for node_name, state_update in event.items():
            executed_nodes.append(node_name)
            final_state.update(state_update)
            ui_state.update_graph_step(node_name, state_update)
            
    # 4. Verify all sequential nodes were executed
    expected_core_nodes = [
        "technical_net",
        "macro_analyst",
        "memory_retriever",
        "market_model",
        "signal_arbitrator",
        "debate_protocol",
        "risk_controller",
    ]
    for node in expected_core_nodes:
        assert node in executed_nodes, f"Node {node} was not executed in the cycle stream"
        
    # Terminal branch node must be either order_execution or rejection_sink
    assert ("order_execution" in executed_nodes) or ("rejection_sink" in executed_nodes), (
        "Cycle must terminate in order_execution or rejection_sink"
    )
    
    # 5. Check ui_state status transitions
    for node in expected_core_nodes:
        assert ui_state.node_statuses[node] == "COMPLETED", f"UI status for {node} should be COMPLETED, got {ui_state.node_statuses[node]}"
        
    if "rejection_sink" in executed_nodes:
        assert ui_state.node_statuses["rejection_sink"] == "COMPLETED"
        assert ui_state.node_statuses["order_execution"] == "SKIPPED"
        assert ui_state.workflow_state == "REJECTED"
    else:
        assert ui_state.node_statuses["order_execution"] == "COMPLETED"
        assert ui_state.node_statuses["rejection_sink"] == "SKIPPED"
        assert ui_state.workflow_state == "EXECUTED"
        
    # 6. Verify Command Deck snapshot generation
    snapshot = _deliberative_graph_snapshot()
    assert "node_statuses" in snapshot
    assert "graphStates" in snapshot
    assert len(snapshot["graphStates"]) > 0
    deck_nodes = snapshot["graphStates"][0]["nodes"]
    deck_node_ids = [n["id"] for n in deck_nodes]
    
    for node in expected_core_nodes:
        assert node in deck_node_ids, f"Node {node} missing from Command Deck snapshot nodes"
        
    # Check that completed nodes have status == "done"
    tech_node = next(n for n in deck_nodes if n["id"] == "technical_net")
    assert tech_node["status"] == "done", f"Expected done for technical_net in Command Deck, got {tech_node['status']}"
