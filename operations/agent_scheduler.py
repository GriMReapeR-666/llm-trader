"""Default restart-safe cadence for the local continuous trading agents."""
from __future__ import annotations

from typing import Any, Dict, List

try:
    from operations.agent_contracts import validate_schedule_agents
except ImportError:
    from operations.agent_contracts import validate_schedule_agents


SCHEDULES: List[Dict[str, Any]] = [
    {"name": "market_health", "agent": "TechnicalNet", "interval": 30, "payload": {"kind": "health"}},
    {"name": "event_monitor", "agent": "MacroAnalyst", "interval": 60, "payload": {"kind": "event_window"}},
    {"name": "macro_post_event_review", "agent": "MacroAnalyst", "interval": 3600, "payload": {"kind": "post_event_review", "hours": 48}},
    {"name": "causal_edge_validate", "agent": "MacroAnalyst", "interval": 900, "payload": {"kind": "causal_validate"}},
    # Macro event evidence: turn calendar observations collected by
    # event_monitor into macro_events rows (completed releases, deduped via
    # atomic-fact keys) and schedule/capture reaction trackers into
    # event_reactions — evidence the causal engine otherwise only got when a
    # full trading cycle happened to run.
    {"name": "macro_event_evidence", "agent": "MacroAnalyst", "interval": 900, "payload": {"kind": "macro_event_evidence"}},
    {"name": "economic_dynamics_seed", "agent": "MacroAnalyst", "interval": 86400, "payload": {"kind": "economic_dynamics_seed"}},
    {"name": "catalog_measurement_pass", "agent": "MacroAnalyst", "interval": 1800, "payload": {"kind": "catalog_measurement"}},
    {"name": "historical_regime_research", "agent": "QuantitativeCoach", "interval": 86400, "payload": {"kind": "historical_regime_research"}},
    {"name": "position_reconcile", "agent": "RiskController", "interval": 60, "payload": {"kind": "reconcile"}},
    # Strategy lifecycle evaluation: consumes labelled evidence and governance
    # gates; may only demote (DECAYING/FROZEN) and enqueue operator alerts.
    {"name": "lifecycle_evaluation", "agent": "QuantitativeCoach", "interval": 3600, "payload": {"kind": "lifecycle_evaluation"}},
    {"name": "session_review", "agent": "QuantitativeCoach", "interval": 3600, "payload": {"kind": "session_review"}},
    {"name": "daily_review", "agent": "QuantitativeCoach", "interval": 86400, "payload": {"kind": "daily_review"}},
    # Self-improvement: memory quality + optional offline Kronos RL research.
    # Never grants order permission; cadence is independent of candle analysis.
    {"name": "self_improvement", "agent": "QuantitativeCoach", "interval": 86400, "payload": {"kind": "self_improvement"}},
    {"name": "weekly_research", "agent": "QuantitativeCoach", "interval": 604800, "payload": {"kind": "research"}},
    {"name": "monthly_memory_archive", "agent": "QuantitativeCoach", "interval": 2592000, "payload": {"kind": "memory_archive"}},
    # ---- v2 principals: deterministic executors via operations/executors.py ----
    {"name": "event_triage", "agent": "EventAgent", "interval": 60, "payload": {"kind": "event_window"}},
    {"name": "alloc_rebalance", "agent": "PortfolioAgent", "interval": 300, "payload": {"kind": "allocation_audit"}},
    {"name": "incident_sweep", "agent": "IncidentAgent", "interval": 60, "payload": {"kind": "circuit_sweep"}},
    # Operator-alert dispatch: drains the journal alert_outbox every minute
    # (recover expired leases -> claim -> emit -> acknowledge).
    {"name": "alert_dispatch", "agent": "IncidentAgent", "interval": 60,
     "payload": {"kind": "alert_dispatch"}},
    # Journal maintenance: WAL checkpoint + integrity verify hourly; the
    # executor alternates with a daily full volatile-telemetry prune.
    {"name": "journal_maintenance", "agent": "MaintenanceAgent", "interval": 3600,
     "payload": {"kind": "checkpoint"}},
    # Outbox archive: move SENT/EXPIRED alert rows to the cold archive db so
    # the live journal's alert_outbox stays a small hot queue. Idempotent and
    # evidence-preserving (rows are copied, never dropped).
    {"name": "outbox_archive", "agent": "MaintenanceAgent", "interval": 86400,
     "payload": {"kind": "archive_outbox", "limit": 2000}},
    {"name": "candidate_sprint", "agent": "ResearchAgent", "interval": 604800, "payload": {"kind": "candidate_sprint"}},
    {"name": "alpha_mining_sprint", "agent": "ResearchAgent", "interval": 604800, "payload": {"kind": "alpha_mining_sprint"}},
    # Ledger hygiene: prune probe-verified stale lockfiles / orphaned WAL
    # siblings daily. Dry-run by default; the executor applies deletions.
    {"name": "ledger_housekeeping", "agent": "MaintenanceAgent", "interval": 86400,
     "payload": {"kind": "ledger_housekeeping", "apply": True, "root": "artifacts"}},
    # Sieve scrape polling: resumes/polls already-started runs to completion
    # every minute. No-op (SKIPPED) until SIEVE_API_KEY is configured, so the
    # cadence is inert on deployments without Sieve.
    {"name": "sieve_poll", "agent": "ResearchAgent", "interval": 60,
     "payload": {"kind": "sieve_poll", "limit": 5}},
    # Systematic Institutional Quant Workflow sweep (TechnicalNet): sweeps all 7
    # quant workflows (statarb, mean_reversion, macro_momentum, microstructure,
    # neural_kronos, macro_event_alpha, retail_quant) every 5 minutes.
    {"name": "quant_workflow_sweep", "agent": "TechnicalNet", "interval": 300,
     "payload": {"kind": "workflow_sweep"}},
]


def bootstrap(operations: Any) -> None:
    validate_schedule_agents(item["agent"] for item in SCHEDULES)
    for item in SCHEDULES:
        operations.register_schedule(item["name"], item["agent"], item["interval"], item["payload"])
