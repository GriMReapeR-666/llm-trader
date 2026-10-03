"""Tests for S.Q Priority Queue — formal task scheduling.

Covers:
- Priority ordering (CRITICAL > URGENT > HIGH > NORMAL > LOW)
- Dependency resolution (task deps and tool deps)
- Human-review gates (blocks autonomous routing)
- Task lifecycle (PENDING → READY → RUNNING → COMPLETED/FAILED)
- Retry logic with backoff
- Recurring task re-queue
- Migration from DEFAULT_INTERVALS
- AutoQuantTools integration with task queue
- Edge cases: duplicate enqueue, cancel, clear, empty queue
"""
from __future__ import annotations

import pytest

from core.task_queue import (
    TaskQueue,
    Task,
    Priority,
    TaskStatus,
    QueueMetrics,
    migrate_from_intervals,
)
from core.auto_quant_tools import AutoQuantTools, DEFAULT_INTERVALS


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def queue() -> TaskQueue:
    return TaskQueue(human_review_gate=True)


@pytest.fixture
def queue_no_gate() -> TaskQueue:
    return TaskQueue(human_review_gate=False)


def _make_task(task_id: str = "t1", tool_name: str = "cpcv",
               priority: Priority = Priority.NORMAL, interval: int = None,
               depends_on_tools: set = None, dependencies: set = None,
               human_review: bool = False, cycle_due: int = 0) -> Task:
    return Task(
        task_id=task_id,
        name=f"Task {task_id}",
        tool_name=tool_name,
        priority=priority,
        interval=interval,
        depends_on_tools=depends_on_tools or set(),
        dependencies=dependencies or set(),
        human_review=human_review,
        cycle_due=cycle_due,
    )


# ============================================================================
# TEST: ENQUEUE
# ============================================================================

class TestEnqueue:
    def test_enqueue_pending(self, queue):
        t = _make_task("t1")
        queue.enqueue(t)
        assert queue.pending_count() == 1
        assert queue.get_task("t1").status == TaskStatus.PENDING

    def test_enqueue_blocked_by_tool_dep(self, queue):
        t = _make_task("t1", depends_on_tools={"kalman"})
        queue.enqueue(t)
        assert queue.get_task("t1").status == TaskStatus.BLOCKED
        assert queue.pending_count() == 0

    def test_enqueue_blocked_by_task_dep(self, queue):
        t1 = _make_task("t1")
        t2 = _make_task("t2", dependencies={"t1"})
        queue.enqueue(t1)
        queue.enqueue(t2)
        assert queue.get_task("t2").status == TaskStatus.BLOCKED

    def test_enqueue_duplicate_ignored(self, queue):
        queue.enqueue(_make_task("t1"))
        queue.enqueue(_make_task("t1"))
        assert queue.pending_count() == 1

    def test_enqueue_unblocks_when_dep_met(self, queue):
        t1 = _make_task("t1")
        t2 = _make_task("t2", dependencies={"t1"})
        queue.enqueue(t1)
        queue.enqueue(t2)
        assert queue.get_task("t2").status == TaskStatus.BLOCKED
        queue.complete("t1")
        assert queue.get_task("t2").status == TaskStatus.PENDING

    def test_enqueue_unblocks_when_tool_dep_met(self, queue):
        t = _make_task("t1", depends_on_tools={"kalman"})
        queue.enqueue(t)
        assert queue.get_task("t1").status == TaskStatus.BLOCKED
        queue.mark_tool_run("kalman")
        # Unblock check happens at get_ready_tasks time
        ready = queue.get_ready_tasks(cycle_num=0)
        assert len(ready) == 1


# ============================================================================
# TEST: PRIORITY ORDERING
# ============================================================================

class TestPriorityOrdering:
    def test_critical_before_urgent(self, queue):
        queue.enqueue(_make_task("urgent", priority=Priority.URGENT))
        queue.enqueue(_make_task("critical", priority=Priority.CRITICAL))
        queue.enqueue(_make_task("normal", priority=Priority.NORMAL))
        ready = queue.get_ready_tasks(cycle_num=0)
        assert [t.task_id for t in ready] == ["critical", "urgent", "normal"]

    def test_same_priority_sorted_by_cycle_due(self, queue):
        queue.enqueue(_make_task("late", cycle_due=10))
        queue.enqueue(_make_task("early", cycle_due=2))
        queue.enqueue(_make_task("mid", cycle_due=5))
        ready = queue.get_ready_tasks(cycle_num=20)
        assert [t.task_id for t in ready] == ["early", "mid", "late"]

    def test_low_priority_always_last(self, queue):
        queue.enqueue(_make_task("bg", priority=Priority.BACKGROUND))
        queue.enqueue(_make_task("high", priority=Priority.HIGH))
        queue.enqueue(_make_task("low", priority=Priority.LOW))
        ready = queue.get_ready_tasks(cycle_num=0)
        assert ready[0].task_id == "high"
        assert ready[-1].task_id == "bg"


# ============================================================================
# TEST: CYCLE DUE
# ============================================================================

class TestCycleDue:
    def test_not_ready_before_due(self, queue):
        queue.enqueue(_make_task("t1", cycle_due=100))
        ready = queue.get_ready_tasks(cycle_num=50)
        assert len(ready) == 0

    def test_ready_at_due(self, queue):
        queue.enqueue(_make_task("t1", cycle_due=100))
        ready = queue.get_ready_tasks(cycle_num=100)
        assert len(ready) == 1

    def test_ready_after_due(self, queue):
        queue.enqueue(_make_task("t1", cycle_due=100))
        ready = queue.get_ready_tasks(cycle_num=200)
        assert len(ready) == 1


# ============================================================================
# TEST: TASK LIFECYCLE
# ============================================================================

class TestLifecycle:
    def test_complete(self, queue):
        queue.enqueue(_make_task("t1"))
        queue.start_task("t1")
        assert queue.get_task("t1").status == TaskStatus.RUNNING
        queue.complete("t1", result={"status": "OK"})
        assert queue.get_task("t1").status == TaskStatus.COMPLETED
        assert queue.get_task("t1").result == {"status": "OK"}
        assert queue.get_task("t1").completed_at is not None

    def test_fail_with_retry(self, queue):
        t = _make_task("t1")
        t.max_retries = 2
        queue.enqueue(t)
        queue.start_task("t1")
        queue.fail("t1", "timeout")
        assert queue.get_task("t1").status == TaskStatus.PENDING  # Retried
        assert queue.get_task("t1").retry_count == 1

    def test_fail_exhausted(self, queue):
        t = _make_task("t1")
        t.max_retries = 0
        queue.enqueue(t)
        queue.start_task("t1")
        queue.fail("t1", "error")
        assert queue.get_task("t1").status == TaskStatus.FAILED

    def test_cancel(self, queue):
        queue.enqueue(_make_task("t1"))
        queue.cancel("t1")
        assert queue.get_task("t1").status == TaskStatus.CANCELLED
        assert queue.pending_count() == 0

    def test_start_nonexistent_fails(self, queue):
        assert queue.start_task("nonexistent") is False

    def test_execution_time(self, queue):
        t = _make_task("t1")
        queue.enqueue(t)
        queue.start_task("t1")
        queue.complete("t1")
        assert t.execution_time_s is not None
        assert t.execution_time_s >= 0


# ============================================================================
# TEST: HUMAN REVIEW GATES
# ============================================================================

class TestHumanReview:
    def test_flag_blocks_task(self, queue):
        t = _make_task("t1", human_review=True)
        queue.enqueue(t)
        ready = queue.get_ready_tasks(cycle_num=0)
        assert len(ready) == 0
        assert queue.has_human_review_tasks()

    def test_flag_blocks_autonomous_routing(self, queue):
        queue.enqueue(_make_task("t1", human_review=True))
        queue.enqueue(_make_task("t2"))  # Not flagged
        ready = queue.get_ready_tasks(cycle_num=0)
        # Gate blocks ALL autonomous routing when any task has human review
        assert len(ready) == 0
        assert queue.is_blocked()

    def test_clear_unblocks(self, queue):
        t = _make_task("t1", human_review=True)
        queue.enqueue(t)
        queue.clear_human_review("t1")
        ready = queue.get_ready_tasks(cycle_num=0)
        assert len(ready) == 1
        assert not queue.has_human_review_tasks()

    def test_no_gate_allows_through(self, queue_no_gate):
        queue_no_gate.enqueue(_make_task("t1", human_review=True))
        ready = queue_no_gate.get_ready_tasks(cycle_num=0)
        assert len(ready) == 1

    def test_get_human_review_tasks(self, queue):
        queue.enqueue(_make_task("t1", human_review=True))
        queue.enqueue(_make_task("t2", human_review=True))
        queue.enqueue(_make_task("t3"))
        tasks = queue.get_human_review_tasks()
        assert len(tasks) == 2


# ============================================================================
# TEST: RECURRING TASKS
# ============================================================================

class TestRecurring:
    def test_requeue_after_completion(self, queue):
        t = _make_task("t1", interval=10, cycle_due=0)
        queue.enqueue(t)
        queue.start_task("t1")
        queue.complete("t1")
        # Should have re-queued for cycle 10
        ready = queue.get_ready_tasks(cycle_num=10)
        assert len(ready) == 1
        assert ready[0].cycle_due == 10

    def test_one_shot_not_requeued(self, queue):
        t = _make_task("t1", interval=None, cycle_due=0)
        queue.enqueue(t)
        queue.start_task("t1")
        queue.complete("t1")
        ready = queue.get_ready_tasks(cycle_num=100)
        assert len(ready) == 0

    def test_recurring_preserves_deps(self, queue):
        t = _make_task("t1", interval=10, depends_on_tools={"kalman"})
        queue.enqueue(t)
        queue.start_task("t1")
        queue.complete("t1")
        # New recurring task should still depend on kalman
        ready = queue.get_ready_tasks(cycle_num=10)
        assert len(ready) == 0  # kalman hasn't run yet


# ============================================================================
# TEST: MIGRATION FROM INTERVALS
# ============================================================================

class TestMigration:
    def test_migrate_creates_tasks(self):
        q = migrate_from_intervals({"cpcv": 100, "kalman": 10})
        assert q.pending_count() == 2

    def test_migrate_preserves_intervals(self):
        q = migrate_from_intervals({"cpcv": 2016, "bocpd": 1})
        cpcv_task = q.get_task("cpcv_scheduled")
        bocpd_task = q.get_task("bocpd_scheduled")
        assert cpcv_task.interval == 2016
        assert bocpd_task.interval == 1

    def test_migrate_priority_map(self):
        q = migrate_from_intervals(
            {"cpcv": 100},
            priority_map={"cpcv": Priority.CRITICAL},
        )
        cpcv_task = q.get_task("cpcv_scheduled")
        assert cpcv_task.priority == Priority.CRITICAL

    def test_migrate_default_priorities(self):
        q = migrate_from_intervals(DEFAULT_INTERVALS)
        bocpd = q.get_task("bocpd_scheduled")
        assert bocpd.priority == Priority.URGENT
        cpcv = q.get_task("cpcv_scheduled")
        assert cpcv.priority == Priority.HIGH


# ============================================================================
# TEST: METRICS
# ============================================================================

class TestMetrics:
    def test_empty_metrics(self, queue):
        m = queue.metrics()
        assert m.total_enqueued == 0
        assert m.completion_rate == 0.0

    def test_completion_rate(self, queue):
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        t2.max_retries = 0  # No retries — fail immediately
        queue.enqueue(t1)
        queue.enqueue(t2)
        queue.start_task("t1")
        queue.complete("t1")
        queue.start_task("t2")
        queue.fail("t2", "error")
        m = queue.metrics()
        assert m.total_completed == 1
        assert m.total_failed == 1
        assert m.failure_rate == 0.5
        assert m.completion_rate == 0.5

    def test_status_summary(self, queue):
        queue.enqueue(_make_task("t1"))
        queue.enqueue(_make_task("t2", human_review=True))
        summary = queue.status_summary()
        assert "pending" in summary
        assert "blocked" in summary
        assert summary["human_review_waiting"] == 1


# ============================================================================
# TEST: EDGE CASES
# ============================================================================

class TestEdgeCases:
    def test_empty_queue(self, queue):
        ready = queue.get_ready_tasks(cycle_num=100)
        assert len(ready) == 0

    def test_get_next_task_empty(self, queue):
        assert queue.get_next_task(100) is None

    def test_clear(self, queue):
        queue.enqueue(_make_task("t1"))
        queue.enqueue(_make_task("t2"))
        queue.clear()
        assert queue.pending_count() == 0
        assert queue.get_task("t1") is None

    def test_concurrent_running(self, queue):
        queue.enqueue(_make_task("t1"))
        queue.enqueue(_make_task("t2"))
        queue.start_task("t1")
        queue.start_task("t2")
        assert queue.running_count() == 2
        queue.complete("t1")
        assert queue.running_count() == 1
        queue.complete("t2")
        assert queue.running_count() == 0

    def test_tool_has_run(self, queue):
        assert not queue.tool_has_run("cpcv")
        queue.mark_tool_run("cpcv")
        assert queue.tool_has_run("cpcv")


# ============================================================================
# TEST: AUTOQUANTTOOLS INTEGRATION
# ============================================================================

class TestAutoQuantIntegration:
    def test_update_with_no_queue_falls_back(self):
        aqt = AutoQuantTools()
        result = aqt.update_with_task_queue(1, {"close": 100.0, "volume": 1000})
        assert "auto_quant_tools" in result
        # Falls back to interval-based update() which doesn't have 'method' key
        assert "results" in result["auto_quant_tools"]

    def test_update_with_queue(self):
        from core.task_queue import TaskQueue, Task, Priority
        q = TaskQueue(human_review_gate=False)
        q.enqueue_scheduled(
            task_id="bocpd_sched",
            name="BOCPD",
            tool_name="bocpd",
            interval=1,
            priority=Priority.URGENT,
        )
        aqt = AutoQuantTools(task_queue=q)
        result = aqt.update_with_task_queue(1, {"close": 100.0, "volume": 1000})
        assert "auto_quant_tools" in result
        assert result["auto_quant_tools"]["method"] == "priority_queue"
        assert "task_queue" in result["auto_quant_tools"]
        assert result["auto_quant_tools"]["task_queue"]["completed"] >= 1
