"""Unit tests for bar_synchronized_auction executor, scheduler registration, and task queue dispatch."""
import pytest
from unittest.mock import MagicMock

from operations.executors import REGISTRY, validate_registry
from operations.agent_scheduler import SCHEDULES
from operations.agent_contracts import validate_schedule_agents
from operations.agents.workflow_agent import handle_bar_synchronized_auction, get_bar_opportunity_bus
from operations.autonomous_pipeline import run_pipeline
from core.task_queue import TaskQueue, Priority, Task


def test_registry_and_contracts_contain_bar_auction():
    """Verify bar_synchronized_auction is registered with TechnicalNet principal."""
    validate_registry()
    assert "bar_synchronized_auction" in REGISTRY
    spec = REGISTRY["bar_synchronized_auction"]
    assert spec.principal == "TechnicalNet"
    assert spec.handler == "operations.agents.workflow_agent:handle_bar_synchronized_auction"
    assert spec.interval_hint == 60


def test_scheduler_contains_bar_auction():
    """Verify scheduler registers bar_synchronized_auction at 60s cadence."""
    validate_schedule_agents(item["agent"] for item in SCHEDULES)
    names = [row["name"] for row in SCHEDULES]
    assert "bar_synchronized_auction" in names
    row = next(r for r in SCHEDULES if r["name"] == "bar_synchronized_auction")
    assert row["interval"] == 60
    assert row["agent"] == "TechnicalNet"


def test_handle_bar_synchronized_auction_lifecycle():
    """Verify execution, dual velocity routing, and deduplication of bar_synchronized_auction."""
    dummy_context = MagicMock()
    dummy_context.market_data = MagicMock()
    dummy_context.market_data.get_latest_price.return_value = 1.0850

    payload = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "bar_time": "2026-10-21T09:00:00Z",
        "quote_bid": 1.0850,
        "quote_ask": 1.0852,
        "context": {
            "symbol": "EURUSD",
            "timeframe": "M15",
            "price": 1.0850,
            "bid": 1.0850,
            "ask": 1.0852,
            "digits": 5,
            "point": 0.0001,
            "inventory_units": 0.0,
            "features": {"volatility": 0.004},
        },
    }

    # First call: new bar processed
    res1 = handle_bar_synchronized_auction(payload, context=dummy_context)
    assert res1["status"] == "AUCTION_COMPLETE"
    assert res1["execution_authority"] is False
    assert "scalp_winners" in res1
    assert "swing_winners" in res1

    # Second call with same bar_time: skip deduplication
    res2 = handle_bar_synchronized_auction(payload, context=dummy_context)
    assert res2["status"] == "SKIPPED_SAME_BAR"
    assert res2["execution_authority"] is False
    assert res2["symbol"] == "EURUSD"
    assert res2["timeframe"] == "M15"

    # Third call with new bar_time: processes again
    payload_new_bar = dict(payload, bar_time="2026-10-21T09:15:00Z")
    res3 = handle_bar_synchronized_auction(payload_new_bar, context=dummy_context)
    assert res3["status"] == "AUCTION_COMPLETE"


def test_autonomous_pipeline_task_queue_integration():
    """Verify run_pipeline correctly drains bounded tasks from S.Q TaskQueue."""
    tq = TaskQueue()

    # Add 3 ready tasks at cycle 1
    tq.enqueue(Task(task_id="t1", name="Task 1", tool_name="tool_1", priority=Priority.NORMAL, cycle_due=1))
    tq.enqueue(Task(task_id="t2", name="Task 2", tool_name="tool_2", priority=Priority.HIGH, cycle_due=1))
    tq.enqueue(Task(task_id="t3", name="Task 3", tool_name="tool_3", priority=Priority.LOW, cycle_due=1))

    result = run_pipeline(queue=tq, cycle_num=1, run_quant_pipeline=False)

    # Bounded to max 2 tasks per tick
    assert result.get("queue_dispatched") == 2
    assert "queue_tasks" in result
    assert len(result["queue_tasks"]) == 2
