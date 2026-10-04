"""Deliberative Trading Decision Graph using LangGraph.

Coordinates multi-agent reasoning, causal macro validation, memory retrieval,
deterministic fail-closed risk arbitration, and broker execution.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.base import BaseCheckpointSaver

from intelligence.graph_state import TradingCycleState
from intelligence.graph_checkpointer import SqliteCheckpointSaver
from intelligence.graph_nodes import (
    TechnicalNetNode,
    MacroAnalystNode,
    MemoryRetrieverNode,
    MarketModelNode,
    SignalArbitratorNode,
    DebateProtocolNode,
    RiskControllerNode,
    OrderExecutionNode,
    RejectionSinkNode,
)


def route_after_risk_controller(state: TradingCycleState) -> str:
    """Conditional router determining execution vs rejection sink."""
    if state.get("validation_passed") is True:
        return "order_execution"
    return "rejection_sink"


def build_deliberative_graph(
    db_path: str = "trading_state.db",
    router: Any = None,
    broker: Any = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    require_human_approval: bool = False,
    initial_balance: float = 10000.0,
) -> Any:
    """Construct and compile the Deliberative Trading Decision Graph.
    
    Args:
        db_path: Path to SQLite trading database.
        router: Multi-LLM provider router.
        broker: MT5 client or PaperBroker instance.
        checkpointer: Checkpoint saver for state persistence (defaults to SqliteCheckpointSaver).
        require_human_approval: If True, halts execution at Human-in-the-loop gate before order routing.
    """
    builder = StateGraph(TradingCycleState)

    # Initialize agent node instances
    tech_node = TechnicalNetNode(db_path=db_path)
    macro_node = MacroAnalystNode(db_path=db_path)
    mem_node = MemoryRetrieverNode(db_path=db_path)
    model_node = MarketModelNode(router=router)
    arbit_node = SignalArbitratorNode()
    debate_node = DebateProtocolNode()
    risk_node = RiskControllerNode(db_path=db_path, initial_balance=initial_balance)
    exec_node = OrderExecutionNode(broker=broker)
    rej_node = RejectionSinkNode(db_path=db_path)

    # Add nodes to graph
    builder.add_node("technical_net", tech_node)
    builder.add_node("macro_analyst", macro_node)
    builder.add_node("memory_retriever", mem_node)
    builder.add_node("market_model", model_node)
    builder.add_node("signal_arbitrator", arbit_node)
    builder.add_node("debate_protocol", debate_node)
    builder.add_node("risk_controller", risk_node)
    builder.add_node("order_execution", exec_node)
    builder.add_node("rejection_sink", rej_node)

    # Wire graph edges
    builder.add_edge(START, "technical_net")
    builder.add_edge("technical_net", "macro_analyst")
    builder.add_edge("macro_analyst", "memory_retriever")
    builder.add_edge("memory_retriever", "market_model")
    builder.add_edge("market_model", "signal_arbitrator")
    builder.add_edge("signal_arbitrator", "debate_protocol")
    builder.add_edge("debate_protocol", "risk_controller")

    # Conditional Branching
    builder.add_conditional_edges(
        "risk_controller",
        route_after_risk_controller,
        {
            "order_execution": "order_execution",
            "rejection_sink": "rejection_sink",
        },
    )

    builder.add_edge("order_execution", END)
    builder.add_edge("rejection_sink", END)

    # Checkpoint configuration
    saver = checkpointer if checkpointer is not None else SqliteCheckpointSaver(db_path=db_path)
    
    interrupt_nodes = ["order_execution"] if require_human_approval else []

    compiled_graph = builder.compile(
        checkpointer=saver,
        interrupt_before=interrupt_nodes,
    )
    return compiled_graph
