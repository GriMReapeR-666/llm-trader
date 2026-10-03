"""Executor registry: maps scheduler names to principal-owned handlers.

Phase B of the multi-agent runtime. Replaces the growing if/elif dispatch in
LLMOrchestrator._process_due_schedules with a declarative table validated
against contracts v2 at bootstrap. Legacy schedules stay on the legacy chain;
anything registered here is handled by try_handle() first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutorSpec:
    schedule: str
    principal: str
    handler: str            # "module:function" under operations.agents.*
    interval_hint: int      # informational; cadence stays in agent_scheduler


REGISTRY: Dict[str, ExecutorSpec] = {
    "event_triage": ExecutorSpec("event_triage", "EventAgent",
                                 "operations.agents.event_agent:handle_triage", 60),
    "alloc_rebalance": ExecutorSpec("alloc_rebalance", "PortfolioAgent",
                                    "operations.agents.portfolio_agent:handle_rebalance", 300),
    "incident_sweep": ExecutorSpec("incident_sweep", "IncidentAgent",
                                   "operations.agents.incident_agent:handle_sweep", 60),
    "candidate_sprint": ExecutorSpec("candidate_sprint", "ResearchAgent",
                                     "operations.agents.research_agent:handle_sprint", 604800),
    "alpha_mining_sprint": ExecutorSpec("alpha_mining_sprint", "ResearchAgent",
                                        "operations.agents.research_agent:handle_alpha_mining_sprint", 604800),
    # P5: schedules that previously had NO legacy branch (silently dropped)
    "self_improvement": ExecutorSpec("self_improvement", "QuantitativeCoach",
                                     "operations.learning_loop:run_self_improvement", 86400),
    "daily_review": ExecutorSpec("daily_review", "QuantitativeCoach",
                                 "operations.learning_loop:run_daily_review", 86400),
    "session_review": ExecutorSpec("session_review", "QuantitativeCoach",
                                   "operations.learning_loop:run_session_review", 3600),
    "weekly_research": ExecutorSpec("weekly_research", "QuantitativeCoach",
                                    "operations.learning_loop:run_weekly_research", 604800),
    "monthly_memory_archive": ExecutorSpec("monthly_memory_archive", "QuantitativeCoach",
                                           "operations.agents.learning_agent:handle_memory_archive", 2592000),
    # P6.4: prometheus textfile refresh
    "metrics_export": ExecutorSpec("metrics_export", "IncidentAgent",
                                   "operations.metrics_exporter:handle_metrics_export", 300),
    # Ledger hygiene (MaintenanceAgent): stale locks / orphaned WAL prune
    "ledger_housekeeping": ExecutorSpec("ledger_housekeeping", "MaintenanceAgent",
                                        "operations.agents.ledger_agent:handle_housekeeping", 86400),
    # Operator-alert dispatcher (IncidentAgent): drains the durable alert
    # outbox — recovers expired CLAIMED leases, claims PENDING rows, emits
    # them to the operator event stream, acknowledges delivery.
    "alert_dispatch": ExecutorSpec("alert_dispatch", "IncidentAgent",
                                   "operations.agents.alert_agent:handle_alert_dispatch", 60),
    # Journal maintenance (MaintenanceAgent): WAL checkpoint + integrity on
    # a short cadence, volatile-telemetry prune daily. Evidence tables are
    # never pruned; backup/VACUUM stays operator-only.
    "journal_maintenance": ExecutorSpec("journal_maintenance", "MaintenanceAgent",
                                        "operations.agents.journal_agent:handle_journal_maintenance", 3600),
    # Outbox archiver (MaintenanceAgent): moves terminal (SENT/EXPIRED)
    # alert rows to the cold archive db, keeping the live outbox a hot queue.
    "outbox_archive": ExecutorSpec("outbox_archive", "MaintenanceAgent",
                                   "operations.agents.journal_agent:handle_journal_maintenance", 86400),
    # Sieve scrape poller (ResearchAgent): resumes/polls durable run records to
    # completion. Never starts a run itself; SKIPPED until the key is set.
    "sieve_poll": ExecutorSpec("sieve_poll", "ResearchAgent",
                               "operations.agents.sieve_agent:handle_sieve_poll", 60),
    # Systematic Institutional Quant Workflow sweep (TechnicalNet)
    "quant_workflow_sweep": ExecutorSpec("quant_workflow_sweep", "TechnicalNet",
                                         "operations.agents.workflow_agent:handle_workflow_sweep", 300),
}


def validate_registry() -> None:
    """Bootstrap guard: every entry must name a known principal with scope."""
    try:
        from operations.agent_contracts import CONTRACTS
    except ImportError:
        from agent_contracts import CONTRACTS  # flat import parity with scheduler

    for spec_item in REGISTRY.values():
        if spec_item.principal not in CONTRACTS:
            raise ValueError(f"executor '{spec_item.schedule}' names unknown "
                             f"principal {spec_item.principal}")


def resolve_handler(spec_item: ExecutorSpec) -> Optional[Callable[..., Any]]:
    try:
        module_path, fn_name = spec_item.handler.split(":", 1)
        module = __import__(module_path, fromlist=[fn_name])
        return getattr(module, fn_name)
    except Exception as exc:
        logger.error("executor resolve failed for %s: %s", spec_item.schedule, exc)
        return None


def try_handle(schedule_name: str, payload: Dict[str, Any],
               context: Optional[Any] = None) -> bool:
    """Run a registered executor; returns True when this schedule was handled."""
    spec_item = REGISTRY.get(schedule_name)
    if spec_item is None:
        return False
    handler = resolve_handler(spec_item)
    if handler is None:
        return False
    try:
        handler(payload, context)
        try:
            from operations.supervisor import get_supervisor
            get_supervisor().note_principal(spec_item.principal, ok=True,
                                            detail=schedule_name)
        except Exception as exc:
            logger.debug("schedule executor failed: %s", exc)
        logger.info("executor '%s' handled by %s", schedule_name, spec_item.principal)
        return True
    except Exception as exc:
        logger.error("executor '%s' failed: %s", schedule_name, exc)
        try:
            from operations.supervisor import get_supervisor
            get_supervisor().note_principal(spec_item.principal, ok=False,
                                            detail=f"{schedule_name}: {str(exc)[:120]}")
        except Exception as s_exc:
            logger.debug("schedule executor supervisor notification failed: %s", s_exc)
        try:
            from operations import event_bridge
            event_bridge.emit("EXECUTOR_FAILED", {
                "schedule": schedule_name,
                "principal": spec_item.principal,
                "error": str(exc)[:400],
            }, actor=spec_item.principal)
        except Exception as e_exc:
            logger.debug("schedule executor event emission failed: %s", e_exc)
        return False  # Failed: fall through to legacy chain so work is not silently lost


def bootstrap() -> None:
    validate_registry()
