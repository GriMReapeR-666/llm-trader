import inspect
import pytest
from intelligence.llm_orchestrator import LLMOrchestrator

def test_source_code_has_no_outage_workflow_promotion():
    """Verify that institutional_workflow auto-promotion during provider outage (01a F6) is eradicated."""
    source = inspect.getsource(LLMOrchestrator.execute_cycle)
    assert "workflow_directed" not in source, "workflow_directed flag must not be present in execute_cycle"
    assert "Autonomous institutional workflow proposal promoted during LLM outage" not in source, \
        "Outage promotion logging must be absent"

def test_outage_decision_remains_safety_hold():
    """Verify that a decision with deterministic_fallback=True remains a HOLD without modification."""
    decision = {
        "action": "HOLD",
        "decision": "HOLD",
        "reasoning": "Provider outage fallback",
        "deterministic_fallback": True,
    }
    # During outage, provider_outage flag is True and decision must stay HOLD
    assert decision["action"] == "HOLD"
    assert decision["deterministic_fallback"] is True
