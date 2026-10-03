import pytest

from agent_contracts import can, can_write, require, require_order_authority, require_write


RUNTIME_AGENT_EVENTS = (
    ("TechnicalNet", "calculate_features", "data_quality_observation"),
    ("TechnicalNet", "monitor_market_health", "health_report"),
    ("TechnicalNet", "monitor_market_health", "market_snapshot"),
    ("MacroAnalyst", "monitor_event_window", "macro_event_state"),
    ("MacroAnalyst", "review_event", "macro_research_report"),
    ("MarketModel", "propose_trade", "trade_proposal"),
    ("MarketModel", "propose_no_trade", "no_trade_proposal"),
    ("RiskController", "validate_proposal", "validation_decision"),
    ("RiskController", "reconcile_positions", "reconciliation_report"),
    ("RiskController", "review_execution_exceptions", "reconciliation_report"),
    ("QuantitativeCoach", "review_session", "research_report"),
    ("QuantitativeCoach", "run_research", "research_report"),
    ("QuantitativeCoach", "evaluate_strategy_lifecycle", "research_report"),
    ("QuantitativeCoach", "quant_survival_audit", "research_report"),
)


@pytest.mark.parametrize("agent,action,artifact", RUNTIME_AGENT_EVENTS)
def test_runtime_agent_event_pairs_are_contract_permitted(agent, action, artifact):
    assert can(agent, action)
    assert can_write(agent, artifact)
    require(agent, action)
    require_write(agent, artifact)


def test_contracts_still_reject_cross_agent_order_permission():
    with pytest.raises(PermissionError):
        require("MarketModel", "submit_validated_intent")
    with pytest.raises(PermissionError):
        require_write("MarketModel", "order_intent")
    with pytest.raises(PermissionError):
        require_order_authority("MarketModel")
    require_order_authority("RiskController")
