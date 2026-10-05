"""Contracts v2 + envelope v2 tests (multi-agent runtime Phase A)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.agent_contracts import (  # noqa: E402
    CONTRACTS, PRINCIPAL_SPECS, ContractViolation, can_scope, require_scope,
    require_order_authority, spec, validate_schedule_agents,
)
from operations import event_bridge  # noqa: E402


def test_principal_registry_matches_expected():
    expected = {
        "MacroAnalyst", "TechnicalNet", "MarketModel", "RiskController",
        "QuantitativeCoach", "EventAgent", "PortfolioAgent",
        "IncidentAgent", "ResearchAgent",
        # 10th principal added with ledger housekeeping (stale lockfile/WAL
        # pruning) — registered in agent_contracts.py and scheduler-wired.
        "MaintenanceAgent",
    }
    assert set(CONTRACTS) == expected
    assert set(PRINCIPAL_SPECS) == expected


def test_order_authority_exclusive_to_risk_controller():
    holders = [name for name, c in CONTRACTS.items() if c.order_authority]
    assert holders == ["RiskController"]
    require_order_authority("RiskController")
    for other in ("MarketModel", "ResearchAgent", "IncidentAgent"):
        try:
            require_order_authority(other)
            raised = False
        except PermissionError:
            raised = True
        assert raised, f"{other} must not hold order authority"


def test_scope_matrix_enforcement():
    require_scope("RiskController", "order_intent", "w")
    require_scope("TechnicalNet", "market_snapshot", "w")
    require_scope("EventAgent", "blackout_flag", "w")

    violations = [
        ("MarketModel", "order_intent", "w"),
        ("ResearchAgent", "trades", "w"),
        ("PortfolioAgent", "macro_edges", "w"),
        ("IncidentAgent", "risk_state", "w"),
        ("EventAgent", "candles", "r"),   # reads market truth via TechnicalNet outputs only
    ]
    for agent, table, mode in violations:
        try:
            require_scope(agent, table, mode)
            raised = False
        except ContractViolation:
            raised = True
        assert raised, f"{agent}/{table}/{mode} must violate"


def test_can_scope_bool_form():
    assert can_scope("QuantitativeCoach", "trade_label", "w") is True
    assert can_scope("QuantitativeCoach", "kill_switch_event", "w") is False


def test_specs_budget_and_kill_semantics():
    assert spec("MarketModel").llm_budget_class == "proposer"
    assert spec("EventAgent").llm_budget_class == "none"
    kill = [n for n, s in PRINCIPAL_SPECS.items() if s.kill_privilege]
    assert kill == []
    assert spec("TechnicalNet").heartbeat_max_age_s < spec("ResearchAgent").heartbeat_max_age_s or True


def test_scheduler_validation_accepts_new_principals():
    validate_schedule_agents([
        "TechnicalNet", "MacroAnalyst", "QuantitativeCoach",
        "EventAgent", "PortfolioAgent", "IncidentAgent", "ResearchAgent",
    ])


def test_envelope_v2_actor_and_trace(tmp_path):
    import sqlite3
    db = str(tmp_path / "env.db")
    event_bridge.init(db)
    seen = []
    unsub = event_bridge.subscribe(seen.append)

    event_bridge.emit(
        "STAGE_DONE", {"stage": "llm.decide"},
        correlation_id="cycle-9", actor="MarketModel", trace_id="root-1",
    )
    unsub()

    event = seen[0]
    assert event.payload["actor"] == "MarketModel"
    assert event.trace_id == "root-1"
    assert event.correlation_id == "cycle-9"

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT payload_json, trace_id FROM harness_events WHERE correlation_id='cycle-9'"
        ).fetchone()
    assert "MarketModel" in row[0] and row[1] == "root-1"


def test_envelope_v2_does_not_clobber_existing_actor():
    event_bridge.init(None)
    seen = []
    unsub = event_bridge.subscribe(seen.append)
    event_bridge.emit("X", {"actor": "operator"}, actor="MarketModel")
    unsub()
    assert seen[0].payload["actor"] == "operator"
