"""Unified Facade for the Autonomous Agentic Graph Trading Engine.

Provides an institutional entry point for running deliberative trading cycles,
fast-lane pulse monitoring, asynchronous post-trade memory reflection, and
Human-in-the-Loop state resumption.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from intelligence.graph_state import (
    TradingCycleState,
    MemoryReflectionState,
    FastLaneState,
    create_initial_trading_cycle_state,
    utcnow_iso,
)
from intelligence.graph_checkpointer import SqliteCheckpointSaver
from intelligence.deliberative_graph import build_deliberative_graph
from intelligence.reflection_graph import build_reflection_graph
from operations.dual_velocity import DualVelocityExecutor
from operations.agent_operations import AgentOperations

logger = logging.getLogger(__name__)


class TradingGraphEngine:
    """Institutional Agentic Graph Engine coordinating all trading subgraphs."""

    def __init__(
        self,
        db_path: str = "trading_state.db",
        router: Any = None,
        broker: Any = None,
        require_human_approval: bool = False,
        fast_workers: int = 2,
        slow_workers: int = 2,
        initial_balance: float = 0.0,
    ) -> None:
        self.db_path = db_path
        self.router = router
        self.broker = broker
        self.require_human_approval = require_human_approval

        # Initialize SQLite Checkpointer
        self.checkpointer = SqliteCheckpointSaver(db_path=db_path)
        self.ops = AgentOperations(db_path=db_path)
        self.executor = DualVelocityExecutor(
            fast_workers=fast_workers,
            slow_workers=slow_workers,
        )

        # Build and compile subgraphs
        self.deliberative_app = build_deliberative_graph(
            db_path=db_path,
            router=router,
            broker=broker,
            checkpointer=self.checkpointer,
            require_human_approval=require_human_approval,
            initial_balance=initial_balance,
        )
        self.reflection_app = build_reflection_graph(
            db_path=db_path,
            checkpointer=self.checkpointer,
        )

    def run_deliberative_cycle(
        self,
        symbol: str,
        timeframe: str = "M15",
        strategy: str = "causal_continuation",
        market_snapshot: Optional[Dict[str, Any]] = None,
        paper_mode: bool = True,
        thread_id: Optional[str] = None,
    ) -> TradingCycleState:
        """Execute a deliberative bar-close decision cycle through the graph."""
        tid = thread_id or f"trade_{symbol}_{timeframe}_{int(datetime.now(timezone.utc).timestamp())}"
        config = {"configurable": {"thread_id": tid}}

        initial_state = create_initial_trading_cycle_state(
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            paper_mode=paper_mode,
            market_snapshot=market_snapshot,
        )

        # Execute through LangGraph engine
        final_state = self.deliberative_app.invoke(initial_state, config=config)
        return final_state

    def stream_deliberative_cycle(
        self,
        symbol: str,
        timeframe: str = "M15",
        strategy: str = "causal_continuation",
        market_snapshot: Optional[Dict[str, Any]] = None,
        paper_mode: bool = True,
        thread_id: Optional[str] = None,
        sync_ui: bool = True,
    ) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Stream cycle node transitions in real-time for observability dashboards."""
        tid = thread_id or f"stream_{symbol}_{timeframe}_{int(datetime.now(timezone.utc).timestamp())}"
        config = {"configurable": {"thread_id": tid}}

        if sync_ui:
            try:
                from core.shared_state import state as ui_state
                ui_state.reset_graph_pipeline(symbol=symbol, timeframe=timeframe)
            except Exception as exc:
                logger.debug("state snapshot failed: %s", exc)

        initial_state = create_initial_trading_cycle_state(
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            paper_mode=paper_mode,
            market_snapshot=market_snapshot,
        )

        for event in self.deliberative_app.stream(initial_state, config=config):
            for node_name, state_update in event.items():
                if sync_ui:
                    try:
                        from core.shared_state import state as ui_state
                        ui_state.update_graph_step(node_name, state_update)
                    except Exception as exc:
                        logger.debug("event emission failed: %s", exc)
                # DELIBERATION_STEP → command deck deliberation screen (best-effort;
                # silently skipped when the API bridge/deck is not attached).
                try:
                    from ui.gui_api import emit_deliberation_step
                    _output = state_update if isinstance(state_update, str) else json.dumps(
                        state_update, default=str
                    )[:400]
                    emit_deliberation_step(
                        node_id=node_name,
                        status="running",
                        output=_output,
                        node_label=node_name.replace("_", " ").title(),
                    )
                except Exception as exc:
                    logger.debug("deliberation step emit skipped: %s", exc)
                yield (node_name, state_update)

    def run_paper_cycle(
        self,
        symbol: str,
        timeframe: str = "M15",
        strategy: str = "causal_continuation",
        market_snapshot: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
    ) -> TradingCycleState:
        """Run a single paper trading cycle with full streaming and dashboard synchronization."""
        tid = thread_id or f"paper_{symbol}_{timeframe}_{int(datetime.now(timezone.utc).timestamp())}"
        config = {"configurable": {"thread_id": tid}}

        # Stream cycle steps so UI updates live
        for _ in self.stream_deliberative_cycle(
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            market_snapshot=market_snapshot,
            paper_mode=True,
            thread_id=tid,
            sync_ui=True,
        ):
            pass

        state_snapshot = self.deliberative_app.get_state(config)
        if state_snapshot and state_snapshot.values:
            return state_snapshot.values  # type: ignore
        return self.run_deliberative_cycle(
            symbol=symbol,
            timeframe=timeframe,
            strategy=strategy,
            market_snapshot=market_snapshot,
            paper_mode=True,
            thread_id=tid,
        )

    def resume_interrupted_cycle(
        self,
        thread_id: str,
        approve: bool = True,
    ) -> TradingCycleState:
        """Resume a cycle paused at the Human-in-the-Loop approval gate."""
        config = {"configurable": {"thread_id": thread_id}}
        current_state = self.deliberative_app.get_state(config)

        if not current_state or not current_state.next:
            raise ValueError(f"Thread '{thread_id}' is not in an interruptible state.")

        if not approve:
            # Overwrite state to reject order
            self.deliberative_app.update_state(
                config,
                {
                    "validation_passed": False,
                    "rejection_reasons": ["OPERATOR_REJECTED_LIVE_GATE"],
                    "status": "REJECTED",
                    "audit_trace": ["Operator manually rejected live order intent."],
                },
                as_node="risk_controller",
            )

        # Resume execution
        final_state = self.deliberative_app.invoke(None, config=config)
        return final_state

    def run_reflection_cycle(
        self,
        deal_ticket: int,
        symbol: str,
        strategy: str = "causal_continuation",
        realized_pnl: float = 0.0,
        realized_r_multiple: float = 0.0,
        open_time: Optional[str] = None,
        close_time: Optional[str] = None,
        async_lane: bool = True,
    ) -> Optional[MemoryReflectionState]:
        """Run post-trade memory reflection either synchronously or via slow-lane worker."""
        now = utcnow_iso()
        reflection_state: MemoryReflectionState = {
            "deal_ticket": int(deal_ticket),
            "symbol": str(symbol).upper(),
            "strategy": str(strategy),
            "open_time": open_time or now,
            "close_time": close_time or now,
            "entry_price": 1.0,
            "close_price": 1.0,
            "realized_pnl": float(realized_pnl),
            "realized_r_multiple": float(realized_r_multiple),
            "outcome_label": "UNKNOWN",
            "distilled_atomic_facts": [],
            "scenario_key": None,
            "scenario_updated": False,
            "macro_dynamics_feedback": None,
            "audit_trace": [f"Reflection cycle initiated for deal ticket {deal_ticket}"],
            "updated_at": now,
        }

        tid = f"reflection_{deal_ticket}_{int(datetime.now(timezone.utc).timestamp())}"
        config = {"configurable": {"thread_id": tid}}

        if async_lane:
            self.executor.submit_slow(
                key=f"reflection_{deal_ticket}",
                function=self.reflection_app.invoke,
                input=reflection_state,
                config=config,
            )
            return None
        else:
            return self.reflection_app.invoke(reflection_state, config=config)

    def get_thread_history(self, thread_id: str) -> List[Dict[str, Any]]:
        """Retrieve full checkpoint history for time-travel inspection."""
        config = {"configurable": {"thread_id": thread_id}}
        checkpoints = list(self.checkpointer.list(config))
        history = []
        for cp in checkpoints:
            history.append({
                "checkpoint_id": cp.config["configurable"]["checkpoint_id"],
                "node": cp.metadata.get("source", "unknown"),
                "status": cp.checkpoint.get("channel_values", {}).get("status"),
                "timestamp": cp.checkpoint.get("ts"),
            })
        return history
