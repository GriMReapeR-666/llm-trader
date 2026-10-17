import pytest
from core.trading_tools import execute_dynamic_metric_analysis, _build_tool_registry

def test_dynamic_metric_analysis_is_disabled():
    # The @tool wrapper was deliberately removed when this was disarmed
    # (commit f1957992, I1), so the inert stub is now a plain function.
    # Calling it must still refuse and must never run caller-supplied code.
    res = execute_dynamic_metric_analysis(
        code="import os; os.system('echo pwned')",
        description="Attempt OS command injection",
    )
    assert res["success"] is False
    assert "disabled" in res["error"].lower()

def test_dynamic_metric_analysis_not_in_decision_tool_registry():
    registry = _build_tool_registry()
    assert "execute_dynamic_metric_analysis" not in registry
