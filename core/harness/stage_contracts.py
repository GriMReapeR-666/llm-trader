"""Stage contracts for the V2 trading pipeline (master-plan P3 / Phase E).

A StageSpec declares how a unit of the fast lane executes and fails. The
runner in pipeline_v2 enforces these semantics uniformly and emits STAGE_*
telemetry so the command deck's TASKS view renders real spans.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


class CycleAbort(Exception):
    """Raised by a stage whose fallback policy aborts the whole cycle."""


FALLBACKS = ("continue", "skip", "degrade", "abort_cycle")


@dataclass(frozen=True)
class StageSpec:
    key: str                      # e.g. "blackout.gate"
    agent: str                    # owning principal (contracts v2 name)
    fn: Callable[[Any, Dict[str, Any]], Any]
    """fn(runner_context, stage_input) -> Any result."""
    timeout_s: float = 10.0       # soft deadline; SLA_MISS emitted past sla_ms
    sla_ms: int = 3000
    retries: int = 0              # only for idempotent stages
    fallback: str = "continue"    # continue | skip | degrade | abort_cycle
    emits_done: bool = True
    notes: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageResult:
    key: str
    status: str                   # done | skipped | degraded | failed | aborted
    duration_ms: int
    value: Any = None
    error: str = ""


@dataclass
class RunnerContext:
    """Everything a stage may touch: orchestrator, shared results, flags."""
    orchestrator: Any
    shared: Dict[str, Any]        # cross-stage results (e.g. size cap)
    trace_id: str
    flags: Dict[str, bool] = field(default_factory=dict)

    def hold_only(self, why: str) -> None:
        """Degrade policy: force HOLD-only decisions for this cycle."""
        self.flags["hold_only"] = True
        self.flags["hold_only_why"] = why


StageFn = Callable[[RunnerContext, Dict[str, Any]], Any]
