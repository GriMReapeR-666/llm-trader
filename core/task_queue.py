"""S.Q Priority Queue — Formal task scheduling for the autonomous quant system.

Implements the UAQP Orchestrator Queue (S.Q):

    S.Q = (pending_tasks, blocked_tasks, human_review_flags)

    The orchestrator ω evaluates S.Q:
      1. Urgent: kill_switch = TRUE, circuit breaker triggered
      2. High: RETRAIN or TWEAK signals from μ (monitor)
      3. Normal: Scheduled research, scheduled rebalance
      4. Low: Log compression, report generation

    Resolve dependencies: σ cannot run without δ completing for the same h.
    Handle human-in-the-loop flags: if S.Q contains HUMAN_REVIEW,
    pause autonomous routing.

Replaces the cycle-count trigger system in auto_quant_tools.py with:
  - Priority-based scheduling (Urgent > High > Normal > Low)
  - Dependency resolution (task A must complete before task B can start)
  - Task lifecycle tracking (PENDING → READY → RUNNING → COMPLETED/FAILED)
  - Human-review gates (autonomous routing pauses when flagged)
  - Event-driven triggers (condition-based, not just cycle-count)
  - Scheduled triggers (interval-based, backward-compatible)
  - Retry logic with backoff for failed tasks
  - Task history and metrics (completion rate, latency, failure rate)

References:
    UAQP Part III, Agent ω (Orchestrator): Θ: S(t) → a_next
    UAQP Part IV, Edge Algebra: conditional edges between agents
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("TaskQueue")


# ============================================================================
# Priority levels (lower number = higher priority)
# ============================================================================

class Priority(IntEnum):
    """Task priority levels — IntEnum so comparisons work numerically."""
    CRITICAL = 0    # Kill switch, circuit breaker — must run NOW
    URGENT = 1      # Safety-critical: CPCV escalation, regime crash
    HIGH = 2        # Adaptation: RETRAIN, TWEAK, decay response
    NORMAL = 3      # Scheduled: research, rebalance, validation
    LOW = 4         # Housekeeping: reports, log cleanup, audits
    BACKGROUND = 5  # Best-effort: meta-analysis, documentation


class TaskStatus:
    """Task lifecycle states."""
    PENDING = "PENDING"        # Queued, waiting for dependencies
    READY = "READY"            # Dependencies met, waiting for execution
    RUNNING = "RUNNING"        # Currently executing
    COMPLETED = "COMPLETED"    # Finished successfully
    FAILED = "FAILED"          # Finished with error
    BLOCKED = "BLOCKED"        # Blocked by dependency or human-review gate
    CANCELLED = "CANCELLED"    # Cancelled before execution
    SKIPPED = "SKIPPED"        # Skipped (e.g., not needed this cycle)


# ============================================================================
# Task definition
# ============================================================================

@dataclass
class Task:
    """A schedulable task in the S.Q priority queue.

    Attributes:
        task_id: Unique identifier (e.g., "cpcv_cycle_1234").
        name: Human-readable name (e.g., "CPCV Backtest").
        tool_name: Which tool/agent this task runs (e.g., "cpcv").
        priority: Priority level (lower = more urgent).
        status: Current lifecycle state.
        cycle_created: Cycle number when the task was created.
        cycle_due: Cycle number when the task should first run.
        interval: If set, task re-queues itself every N cycles.
        dependencies: Set of task_ids that must complete before this task can start.
        depends_on_tools: Set of tool_names that must have run before this task.
        human_review: If True, task is paused until human approves.
        human_review_reason: Why human review is needed.
        args: Arguments passed to the tool runner.
        result: Output from the tool runner.
        error: Error message if failed.
        retry_count: Number of retries attempted.
        max_retries: Maximum retries before giving up.
        created_at: ISO timestamp when task was created.
        started_at: ISO timestamp when task started running.
        completed_at: ISO timestamp when task completed.
        timeout_s: Maximum execution time in seconds (0 = no limit).
    """
    task_id: str
    name: str
    tool_name: str
    priority: Priority = Priority.NORMAL
    status: str = TaskStatus.PENDING
    cycle_created: int = 0
    cycle_due: int = 0
    interval: Optional[int] = None  # Re-queue interval (None = one-shot)
    dependencies: Set[str] = field(default_factory=set)
    depends_on_tools: Set[str] = field(default_factory=set)
    human_review: bool = False
    human_review_reason: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    timeout_s: float = 0.0  # 0 = no timeout

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def is_terminal(self) -> bool:
        """Task is in a final state (won't transition again)."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.SKIPPED)

    @property
    def can_retry(self) -> bool:
        """Task has retries remaining."""
        return self.retry_count < self.max_retries

    @property
    def execution_time_s(self) -> Optional[float]:
        """Wall-clock execution time in seconds, or None if not started."""
        if not self.started_at:
            return None
        start = datetime.fromisoformat(self.started_at)
        end_str = self.completed_at or datetime.now(timezone.utc).isoformat()
        end = datetime.fromisoformat(end_str)
        return (end - start).total_seconds()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for persistence."""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "tool_name": self.tool_name,
            "priority": self.priority.value,
            "status": self.status,
            "cycle_created": self.cycle_created,
            "cycle_due": self.cycle_due,
            "interval": self.interval,
            "dependencies": list(self.dependencies),
            "depends_on_tools": list(self.depends_on_tools),
            "human_review": self.human_review,
            "human_review_reason": self.human_review_reason,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "timeout_s": self.timeout_s,
            "error": self.error,
        }


# ============================================================================
# Queue metrics
# ============================================================================

@dataclass
class QueueMetrics:
    """Metrics for monitoring queue health."""
    total_enqueued: int = 0
    total_completed: int = 0
    total_failed: int = 0
    total_cancelled: int = 0
    total_skipped: int = 0
    total_retried: int = 0
    total_human_review: int = 0
    currently_pending: int = 0
    currently_running: int = 0
    currently_blocked: int = 0
    avg_execution_time_s: float = 0.0
    failure_rate: float = 0.0
    completion_rate: float = 0.0


# ============================================================================
# The S.Q Priority Queue
# ============================================================================

class TaskQueue:
    """S.Q — The Orchestrator Queue.

    Priority-based task scheduler with dependency resolution, human-review
    gates, and event-driven triggers. Replaces cycle-count triggers.

    Usage:
        queue = TaskQueue()

        # Enqueue tasks
        queue.enqueue(Task(task_id="cpcv_1", name="CPCV", tool_name="cpcv",
                          priority=Priority.NORMAL, interval=2016))
        queue.enqueue(Task(task_id="kalman_1", name="Kalman", tool_name="kalman",
                          priority=Priority.HIGH, interval=5))

        # Each cycle: get ready tasks and run them
        ready = queue.get_ready_tasks(cycle_num=100)
        for task in ready:
            result = run_tool(task.tool_name, task.args)
            queue.complete(task.task_id, result)

        # Human review
        queue.flag_human_review("cpcv_1", "Low DSR — needs manual check")

        # Metrics
        metrics = queue.metrics()
    """

    def __init__(self, human_review_gate: bool = True):
        """
        Args:
            human_review_gate: If True, tasks flagged for human review
                pause autonomous routing (no new tasks start until cleared).
        """
        self._tasks: Dict[str, Task] = {}        # task_id → Task
        self._pending: List[str] = []             # task_ids sorted by priority
        self._running: Set[str] = set()           # currently executing task_ids
        self._completed_tools: Set[str] = set()   # tool_names that have run (for dependency checks)
        self._metrics = QueueMetrics()
        self._human_review_gate = human_review_gate
        self._history: List[Dict[str, Any]] = []  # completed task summaries

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(self, task: Task) -> None:
        """Add a task to the queue.

        If the task has dependencies that aren't met, it starts in BLOCKED
        status. Otherwise it starts in PENDING and will be sorted into the
        priority queue.
        """
        if task.task_id in self._tasks:
            logger.warning("Task %s already in queue — skipping duplicate", task.task_id)
            return

        # Check dependencies
        if self._dependencies_met(task):
            task.status = TaskStatus.PENDING
            self._pending.append(task.task_id)
            self._sort_pending()
        else:
            task.status = TaskStatus.BLOCKED
            logger.debug("Task %s blocked by dependencies: %s",
                        task.task_id, task.dependencies | task.depends_on_tools)

        self._tasks[task.task_id] = task
        self._metrics.total_enqueued += 1
        logger.debug("Enqueued task %s [%s] priority=%d tool=%s",
                     task.task_id, task.name, task.priority.value, task.tool_name)

    def enqueue_scheduled(
        self,
        task_id: str,
        name: str,
        tool_name: str,
        interval: int,
        priority: Priority = Priority.NORMAL,
        depends_on_tools: Optional[Set[str]] = None,
        **kwargs: Any,
    ) -> Task:
        """Convenience: create and enqueue a recurring scheduled task.

        This replaces the DEFAULT_INTERVALS dict in auto_quant_tools.
        The task re-queues itself after completion if interval is set.
        """
        task = Task(
            task_id=task_id,
            name=name,
            tool_name=tool_name,
            priority=priority,
            interval=interval,
            depends_on_tools=depends_on_tools or set(),
            **kwargs,
        )
        self.enqueue(task)
        return task

    # ------------------------------------------------------------------
    # Get ready tasks
    # ------------------------------------------------------------------

    def get_ready_tasks(self, cycle_num: int) -> List[Task]:
        """Return all tasks that are ready to run, sorted by priority.

        A task is ready if:
          1. It's in PENDING status
          2. Its cycle_due <= cycle_num
          3. No human-review gate is blocking autonomous routing
          4. Its dependencies are met

        Also scans BLOCKED tasks to unblock them when deps are now met.

        Returns tasks sorted by priority (CRITICAL first).
        """
        # First: try to unblock any BLOCKED tasks whose deps are now met
        self._try_unblock_all()

        # Check human-review gate: if ANY task needs review, block ALL autonomous routing
        gate_active = self._human_review_gate and self.has_human_review_tasks()
        if gate_active:
            logger.info("S.Q: Human-review gate active — blocking all autonomous routing")

        ready = []
        still_pending = []

        for task_id in list(self._pending):
            task = self._tasks.get(task_id)
            if task is None or task.is_terminal:
                continue

            # Human-review gate blocks ALL tasks, not just flagged ones
            if gate_active:
                if task.status != TaskStatus.BLOCKED:
                    task.status = TaskStatus.BLOCKED
                    self._metrics.total_human_review += 1
                logger.debug("S.Q: Task %s blocked by human-review gate", task_id)
                continue

            # Check if due
            if task.cycle_due > cycle_num:
                still_pending.append(task_id)
                continue

            # Check dependencies again (in case they were just met)
            if not self._dependencies_met(task):
                still_pending.append(task_id)
                continue

            # Ready!
            ready.append(task)
            still_pending.append(task_id)  # Keep in pending until actually started

        self._pending = still_pending

        # Sort by priority (lower value = higher priority), then by cycle_due (earlier first)
        ready.sort(key=lambda t: (t.priority.value, t.cycle_due))
        return ready

    def start_task(self, task_id: str) -> bool:
        """Mark a task as RUNNING. Returns False if task not found or not ready."""
        task = self._tasks.get(task_id)
        if not task or task.status not in (TaskStatus.PENDING, TaskStatus.BLOCKED):
            return False

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc).isoformat()
        self._running.add(task_id)
        self._metrics.currently_running += 1
        if task_id in self._pending:
            self._pending.remove(task_id)
            self._metrics.currently_pending -= 1
        return True

    # ------------------------------------------------------------------
    # Complete / Fail / Cancel
    # ------------------------------------------------------------------

    def complete(self, task_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """Mark a task as COMPLETED and re-queue if it has an interval."""
        task = self._tasks.get(task_id)
        if not task:
            return

        task.status = TaskStatus.COMPLETED
        task.result = result
        task.completed_at = datetime.now(timezone.utc).isoformat()
        self._running.discard(task_id)
        self._completed_tools.add(task.tool_name)
        self._metrics.total_completed += 1
        self._metrics.currently_running = max(0, self._metrics.currently_running - 1)

        # Record in history
        self._history.append({
            "task_id": task.task_id,
            "tool_name": task.tool_name,
            "status": "COMPLETED",
            "priority": task.priority.value,
            "execution_time_s": task.execution_time_s,
            "cycle_created": task.cycle_created,
            "completed_at": task.completed_at,
        })
        if len(self._history) > 200:
            self._history = self._history[-200:]

        # Re-queue if recurring
        if task.interval is not None:
            self._requeue_recurring(task)

        # Unblock dependent tasks
        self._unblock_dependents(task_id)

        logger.debug("Task %s completed in %.2fs", task_id, task.execution_time_s or 0)

    def fail(self, task_id: str, error: str) -> None:
        """Mark a task as FAILED. Retry if retries remain."""
        task = self._tasks.get(task_id)
        if not task:
            return

        task.error = error
        self._running.discard(task_id)
        self._metrics.currently_running = max(0, self._metrics.currently_running - 1)

        if task.can_retry:
            task.retry_count += 1
            task.status = TaskStatus.PENDING
            task.started_at = None
            task.completed_at = None
            self._pending.append(task.task_id)
            self._sort_pending()
            self._metrics.total_retried += 1
            logger.warning("Task %s failed (retry %d/%d): %s",
                          task_id, task.retry_count, task.max_retries, error)
        else:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            self._metrics.total_failed += 1
            self._history.append({
                "task_id": task.task_id,
                "tool_name": task.tool_name,
                "status": "FAILED",
                "error": error,
                "retry_count": task.retry_count,
            })
            logger.error("Task %s FAILED (no retries left): %s", task_id, error)

    def cancel(self, task_id: str) -> None:
        """Cancel a pending task."""
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        self._pending = [tid for tid in self._pending if tid != task_id]
        self._running.discard(task_id)
        self._metrics.total_cancelled += 1

    # ------------------------------------------------------------------
    # Human review
    # ------------------------------------------------------------------

    def flag_human_review(self, task_id: str, reason: str) -> None:
        """Flag a task for human review. Blocks autonomous routing."""
        task = self._tasks.get(task_id)
        if not task:
            return
        task.human_review = True
        task.human_review_reason = reason
        if task.status == TaskStatus.PENDING:
            task.status = TaskStatus.BLOCKED
        logger.warning("S.Q: Task %s flagged for HUMAN REVIEW: %s", task_id, reason)

    def clear_human_review(self, task_id: str) -> None:
        """Clear human-review flag. Task returns to PENDING if dependencies met."""
        task = self._tasks.get(task_id)
        if not task:
            return
        task.human_review = False
        task.human_review_reason = ""
        if task.status == TaskStatus.BLOCKED:
            if self._dependencies_met(task):
                task.status = TaskStatus.PENDING
                self._pending.append(task.task_id)
                self._sort_pending()
        logger.info("S.Q: Human review cleared for %s", task_id)

    def has_human_review_tasks(self) -> bool:
        """Check if any tasks are waiting for human review."""
        return any(
            t.human_review and not t.is_terminal
            for t in self._tasks.values()
        )

    def get_human_review_tasks(self) -> List[Task]:
        """Return all tasks waiting for human review."""
        return [
            t for t in self._tasks.values()
            if t.human_review and not t.is_terminal
        ]

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_by_tool(self, tool_name: str) -> List[Task]:
        """Get all tasks for a specific tool."""
        return [t for t in self._tasks.values() if t.tool_name == tool_name]

    def pending_count(self) -> int:
        return len(self._pending)

    def running_count(self) -> int:
        return len(self._running)

    def has_running(self) -> bool:
        return len(self._running) > 0

    def is_blocked(self) -> bool:
        """Queue is blocked if human-review gate is active and there are review tasks."""
        return self._human_review_gate and self.has_human_review_tasks()

    def get_next_task(self, cycle_num: int) -> Optional[Task]:
        """Get the single highest-priority ready task. Convenience for orchestrator."""
        ready = self.get_ready_tasks(cycle_num)
        return ready[0] if ready else None

    def tool_has_run(self, tool_name: str) -> bool:
        """Check if a tool has been completed at least once (for dependency checks)."""
        return tool_name in self._completed_tools

    def mark_tool_run(self, tool_name: str) -> None:
        """Manually mark a tool as having run (for external completions)."""
        self._completed_tools.add(tool_name)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def metrics(self) -> QueueMetrics:
        """Compute current queue metrics."""
        m = self._metrics
        m.currently_pending = len(self._pending)
        m.currently_running = len(self._running)
        m.currently_blocked = sum(
            1 for t in self._tasks.values()
            if t.status == TaskStatus.BLOCKED and not t.is_terminal
        )
        total = m.total_completed + m.total_failed
        m.failure_rate = m.total_failed / max(total, 1)
        m.completion_rate = m.total_completed / max(total, 1)

        # Average execution time from history
        exec_times = [
            h["execution_time_s"]
            for h in self._history
            if h.get("execution_time_s") is not None
        ]
        m.avg_execution_time_s = sum(exec_times) / max(len(exec_times), 1)

        return m

    def status_summary(self) -> Dict[str, Any]:
        """Human-readable status summary."""
        m = self.metrics()
        return {
            "pending": m.currently_pending,
            "running": m.currently_running,
            "blocked": m.currently_blocked,
            "completed": m.total_completed,
            "failed": m.total_failed,
            "human_review_waiting": len(self.get_human_review_tasks()),
            "completion_rate": f"{m.completion_rate:.1%}",
            "failure_rate": f"{m.failure_rate:.1%}",
            "avg_execution_time_s": round(m.avg_execution_time_s, 2),
            "blocked_by_human_gate": self.is_blocked(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dependencies_met(self, task: Task) -> bool:
        """Check if all dependencies are satisfied."""
        # Task-level dependencies
        for dep_id in task.dependencies:
            dep = self._tasks.get(dep_id)
            if dep is None or dep.status != TaskStatus.COMPLETED:
                return False

        # Tool-level dependencies
        for dep_tool in task.depends_on_tools:
            if dep_tool not in self._completed_tools:
                return False

        return True

    def _unblock_task(self, task: Task) -> bool:
        """Check if a blocked task can be unblocked."""
        if task.status != TaskStatus.BLOCKED:
            return False
        if task.human_review:
            return False
        return self._dependencies_met(task)

    def _try_unblock_all(self) -> None:
        """Scan all BLOCKED tasks and move to PENDING if deps are now met."""
        for task in self._tasks.values():
            if task.status == TaskStatus.BLOCKED and not task.human_review:
                if self._dependencies_met(task):
                    task.status = TaskStatus.PENDING
                    if task.task_id not in self._pending:
                        self._pending.append(task.task_id)
                    logger.debug("Unblocked task %s (deps now met)", task.task_id)
        self._sort_pending()

    def _unblock_dependents(self, completed_task_id: str) -> None:
        """After a task completes, unblock any tasks that depended on it."""
        self._try_unblock_all()

    def _requeue_recurring(self, completed_task: Task) -> None:
        """Re-queue a recurring task for its next execution."""
        import copy
        next_task = copy.copy(completed_task)
        next_task.task_id = f"{completed_task.tool_name}_cycle_{completed_task.cycle_created + completed_task.interval}"
        next_task.status = TaskStatus.PENDING
        next_task.cycle_due = completed_task.cycle_created + (completed_task.interval or 1)
        next_task.cycle_created = completed_task.cycle_created + (completed_task.interval or 1)
        next_task.result = None
        next_task.error = None
        next_task.retry_count = 0
        next_task.started_at = None
        next_task.completed_at = None
        next_task.created_at = datetime.now(timezone.utc).isoformat()
        next_task.dependencies = set()  # Fresh recurring task has no task deps
        next_task.depends_on_tools = completed_task.depends_on_tools  # Keep tool deps

        # Avoid duplicate IDs
        if next_task.task_id not in self._tasks:
            self.enqueue(next_task)

    def _sort_pending(self) -> None:
        """Sort pending task_ids by priority (lower = first)."""
        self._pending.sort(key=lambda tid: (
            self._tasks[tid].priority.value if tid in self._tasks else 99,
            self._tasks[tid].cycle_due if tid in self._tasks else 999999,
        ))

    def clear(self) -> None:
        """Clear all tasks (for testing)."""
        self._tasks.clear()
        self._pending.clear()
        self._running.clear()
        self._completed_tools.clear()
        self._history.clear()
        self._metrics = QueueMetrics()


# ============================================================================
# Bridge: convert DEFAULT_INTERVALS → TaskQueue tasks
# ============================================================================

def migrate_from_intervals(
    intervals: Dict[str, int],
    queue: Optional[TaskQueue] = None,
    priority_map: Optional[Dict[str, Priority]] = None,
) -> TaskQueue:
    """Convert the old DEFAULT_INTERVALS dict into S.Q scheduled tasks.

    This provides backward compatibility: existing auto_quant_tools code
    can call this to migrate its interval config into the priority queue.

    Parameters
    ----------
    intervals : dict[str, int]
        Old-style {tool_name: cycle_interval} dict.
    queue : TaskQueue, optional
        Queue to populate. Creates a new one if None.
    priority_map : dict[str, Priority], optional
        Override default priorities per tool.

    Returns
    -------
    Populated TaskQueue.
    """
    if queue is None:
        queue = TaskQueue()

    # Default priorities based on tool criticality
    default_priorities = {
        "bocpd": Priority.URGENT,          # Regime shift detection — critical
        "crash_detector": Priority.URGENT,  # Crash detection — critical
        "cpcv": Priority.HIGH,             # Backtest validation — important
        "kalman": Priority.HIGH,           # Adaptive trend — important
        "alpha_decay": Priority.HIGH,      # Alpha health — important
        "walk_forward": Priority.HIGH,     # Walk-forward validation — important
        "meta_adaptive": Priority.NORMAL,  # Regime profiling
        "tca": Priority.NORMAL,            # Transaction cost analysis
        "fractional_diff": Priority.NORMAL,  # Stationarity
        "alpha_discovery": Priority.NORMAL,  # New alpha research
        "self_improvement": Priority.NORMAL, # Self-improvement loop
        "causal_filter": Priority.NORMAL,   # Causal feature pruning
        "optimal_execution": Priority.NORMAL,  # Execution optimization
        "regime_autocorr": Priority.NORMAL,  # Microstructure detection
        "multimodal": Priority.NORMAL,      # Pattern analysis
        "ewc_status": Priority.NORMAL,      # EWC status
        "cross_market": Priority.LOW,       # Cross-market benchmark
        "evolutionary": Priority.LOW,       # Evolutionary optimization
        "beyond_prompting": Priority.LOW,   # Beyond Prompting research
        "mars_maml": Priority.LOW,          # MARS MAML
        "finrl_retrain": Priority.BACKGROUND,  # FinRL retrain (slow)
    }

    for tool_name, interval in intervals.items():
        pri_map = (priority_map or {})
        pri = pri_map.get(tool_name) if tool_name in pri_map else default_priorities.get(tool_name, Priority.NORMAL)
        queue.enqueue_scheduled(
            task_id=f"{tool_name}_scheduled",
            name=f"{tool_name.replace('_', ' ').title()} (Scheduled)",
            tool_name=tool_name,
            interval=interval,
            priority=pri,
        )

    logger.info("Migrated %d intervals to S.Q priority queue", len(intervals))
    return queue
