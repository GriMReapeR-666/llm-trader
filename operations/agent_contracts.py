"""Least-privilege contracts for the persistent local trading agents.

These contracts are deliberately small and deterministic.  They are not an
LLM prompt convention: callers can ask this module to reject an unauthorised
write before it reaches the journal or execution gateway.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable


@dataclass(frozen=True)
class AgentContract:
    name: str
    reads: FrozenSet[str]
    writes: FrozenSet[str]
    actions: FrozenSet[str]
    order_authority: bool = False
    notes: str = ""


def _items(*values: str) -> FrozenSet[str]:
    return frozenset(values)


CONTRACTS: Dict[str, AgentContract] = {
    "MacroAnalyst": AgentContract(
        "MacroAnalyst", _items("calendar", "macro_sources", "market_snapshots", "playbooks"),
        _items("event_observation", "macro_candidate", "playbook_candidate", "macro_event_state",
               "macro_research_report", "opportunity_candidate"),
        _items("monitor_event_window", "propose_macro_context", "review_event",
               "emit_opportunity", "validate_causal_edges", "seed_economic_dynamics"),
        notes="May create source-linked macro candidates and advisory opportunities; never creates orders or risk decisions.",
    ),
    "TechnicalNet": AgentContract(
        "TechnicalNet", _items("ticks", "candles", "dom", "symbol_specs"),
        _items("market_snapshot", "feature_observation", "data_quality_observation",
               "health_report", "opportunity_candidate"),
        _items("monitor_market_health", "calculate_features", "emit_opportunity"),
        notes="Produces point-in-time observations and opportunity candidates only; never interprets them as permission.",
    ),
    "MarketModel": AgentContract(
        "MarketModel", _items("market_snapshots", "features", "playbooks", "memory_context"),
        _items("trade_proposal", "no_trade_proposal", "llm_trace"),
        _items("propose_trade", "propose_no_trade"),
        notes="The LLM-facing proposer cannot validate, size, submit, or alter a position.",
    ),
    "RiskController": AgentContract(
        "RiskController", _items("trade_proposals", "account", "positions", "risk_policy", "symbol_specs"),
        _items("validation_decision", "risk_state", "order_intent", "position_reconciliation", "reconciliation_report", "kill_switch_event"),
        _items("validate_proposal", "arbitrate_risk", "submit_validated_intent", "reconcile_positions", "review_execution_exceptions"),
        order_authority=True,
        notes="Only deterministic gateway allowed to submit a previously validated order intent.",
    ),
    "QuantitativeCoach": AgentContract(
        "QuantitativeCoach", _items("closed_trades", "labels", "metrics", "research_snapshots", "macro_events"),
        _items("trade_label", "scenario_candidate", "core_memory_candidate", "research_report"),
        _items("label_trade", "review_session", "run_research",
               "evaluate_strategy_lifecycle", "quant_survival_audit"),
        notes="Writes retrospective/review/audit artefacts only; cannot change live permission or policy.",
    ),
    # ---- deterministic principals added by the multi-agent runtime (v2) ----
    "EventAgent": AgentContract(
        "EventAgent", _items("calendar", "news_feed", "macro_events", "event_history"),
        _items("event_window", "blackout_flag", "news_triage_row"),
        _items("triage_event_window", "classify_news", "set_blackout", "expire_blackout"),
        notes="Owns trading blackouts around high-impact events; deterministic classification only.",
    ),
    "PortfolioAgent": AgentContract(
        "PortfolioAgent", _items("positions", "account", "correlation_matrix", "allocation_policy",
                                 "strategy_registry", "risk_state"),
        _items("allocation_weights", "size_cap", "exposure_ledger"),
        _items("compute_allocation", "audit_exposure", "cap_size"),
        notes="Advisory sizing caps consulted by RiskController; never submits orders itself.",
    ),
    "IncidentAgent": AgentContract(
        "IncidentAgent", _items("operational_events", "health_alerts", "circuit_state", "supervisor_state",
                                "queue_health", "dead_letter_vault"),
        _items("incident_record", "alert_route", "runbook_reference"),
        _items("record_incident", "route_alert", "match_runbook", "feed_circuit_metrics"),
        notes="Detection and routing only; halt/pause authority stays with doctrine engine and operator.",
    ),
    "ResearchAgent": AgentContract(
        "ResearchAgent", _items("strategy_registry", "replay_reports", "closed_trades", "labels",
                                "research_validation_runs", "scrape_runs"),
        _items("research_artifact", "replay_report", "registry_proposal", "scrape_artifact"),
        _items("run_candidate_sprint", "audit_paper_shadow", "propose_promotion", "run_scrape"),
        notes="Executes factory sprints and read-only scrape polls; stage transitions require signed approvals, never direct writes.",
    ),
    "MaintenanceAgent": AgentContract(
        "MaintenanceAgent", _items("ledger_lockfiles", "ledger_wal_siblings", "remediation_reports"),
        _items("housekeeping_deletion_record"),
        _items("prune_stale_lockfiles", "prune_orphaned_wal_siblings", "prune_stale_reports",
               "archive_terminal_alerts"),
        notes="Prunes only probe-verified inert files (stale locks, orphaned WAL/SHM, aged reports) and archives terminal outbox rows to the cold archive db; never touches DB evidence, encrypted backups, live databases, or held locks.",
    ),
}

MacroAnalyst = CONTRACTS["MacroAnalyst"]
TechnicalNet = CONTRACTS["TechnicalNet"]
MarketModel = CONTRACTS["MarketModel"]
RiskController = CONTRACTS["RiskController"]
QuantitativeCoach = CONTRACTS["QuantitativeCoach"]
EventAgent = CONTRACTS["EventAgent"]
PortfolioAgent = CONTRACTS["PortfolioAgent"]
IncidentAgent = CONTRACTS["IncidentAgent"]
ResearchAgent = CONTRACTS["ResearchAgent"]


# ---- PrincipalSpec v2: budget / concurrency / kill semantics ---------------
from dataclasses import dataclass as _dataclass  # noqa: E402


@_dataclass(frozen=True)
class PrincipalSpec:
    """Runtime semantics layered on top of the artifact-level contract."""
    name: str
    llm_budget_class: str = "none"       # none | light | proposer | coach
    max_parallel_leases: int = 1
    kill_privilege: bool = False
    heartbeat_max_age_s: int = 120


PRINCIPAL_SPECS: Dict[str, PrincipalSpec] = {
    "MacroAnalyst": PrincipalSpec("MacroAnalyst", llm_budget_class="light", max_parallel_leases=2),
    "TechnicalNet": PrincipalSpec("TechnicalNet", max_parallel_leases=3, heartbeat_max_age_s=60),
    "MarketModel": PrincipalSpec("MarketModel", llm_budget_class="proposer"),
    "RiskController": PrincipalSpec("RiskController", max_parallel_leases=2),
    "QuantitativeCoach": PrincipalSpec("QuantitativeCoach", llm_budget_class="coach", max_parallel_leases=2),
    "EventAgent": PrincipalSpec("EventAgent", max_parallel_leases=2, heartbeat_max_age_s=90),
    "PortfolioAgent": PrincipalSpec("PortfolioAgent"),
    "IncidentAgent": PrincipalSpec("IncidentAgent", max_parallel_leases=2),
    "ResearchAgent": PrincipalSpec("ResearchAgent", max_parallel_leases=1),
    # Principal spec added with the ledger-housekeeping principal: the
    # scheduler's ledger_housekeeping task references this agent, and a
    # missing spec would raise "unknown principal spec" on lease validation.
    "MaintenanceAgent": PrincipalSpec("MaintenanceAgent", max_parallel_leases=1),
}


class ContractViolation(PermissionError):
    """Raised when a principal touches a table outside its granted scope."""


def spec(agent: str) -> PrincipalSpec:
    try:
        return PRINCIPAL_SPECS[agent]
    except KeyError as error:
        raise ValueError(f"unknown principal spec: {agent}") from error


def require_scope(agent: str, table: str, mode: str) -> None:
    """Enforce read/write table scope for a principal ('r' or 'w')."""
    item = contract(agent)
    pool = item.reads if mode == "r" else item.writes
    if table not in pool:
        raise ContractViolation(
            f"{agent} lacks {mode.upper()} scope on '{table}' "
            f"(has {mode.upper()}: {sorted(pool)})"
        )


def can_scope(agent: str, table: str, mode: str) -> bool:
    try:
        require_scope(agent, table, mode)
        return True
    except ContractViolation:
        return False


def contract(agent: str) -> AgentContract:
    try:
        return CONTRACTS[agent]
    except KeyError as error:
        raise ValueError(f"unknown trading agent: {agent}") from error


def can(agent: str, action: str) -> bool:
    return action in contract(agent).actions


def require(agent: str, action: str) -> None:
    if not can(agent, action):
        raise PermissionError(f"{agent} is not permitted to perform {action}")


def can_write(agent: str, artifact: str) -> bool:
    return artifact in contract(agent).writes


def require_write(agent: str, artifact: str) -> None:
    if not can_write(agent, artifact):
        raise PermissionError(f"{agent} is not permitted to write {artifact}")


def require_order_authority(agent: str) -> None:
    """Require the explicit execution-principal bit for order intents.

    Action and artifact permissions describe what an agent may request/write;
    this separate bit prevents a future contract edit from accidentally making
    a proposer an execution principal.
    """
    if not contract(agent).order_authority:
        raise PermissionError(f"{agent} does not have order authority")


def validate_schedule_agents(agents: Iterable[str]) -> None:
    """Fail startup if the persistent scheduler names an unknown principal."""
    for agent in agents:
        contract(agent)


def summaries() -> list[dict[str, object]]:
    return [
        {"agent": item.name, "reads": sorted(item.reads), "writes": sorted(item.writes),
         "actions": sorted(item.actions), "order_authority": item.order_authority, "notes": item.notes}
        for item in CONTRACTS.values()
    ]
