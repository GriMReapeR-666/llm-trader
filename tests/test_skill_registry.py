"""Unit tests for the agentic skills registry and repetitive task skills."""
import pytest
from core.skill_registry import (
    SKILL_REGISTRY,
    skill_macro_digest,
    skill_backtest_sweep,
    skill_journal_integrity_audit,
    skill_distill_performance_scorecard,
    skill_dynamic_code_runner,
)
from core.trading_tools import (
    execute_dynamic_metric_analysis,
    invoke_agent_skill,
    list_available_agent_skills,
)


def test_skill_registry_listing():
    skills = SKILL_REGISTRY.list_skills()
    assert len(skills) >= 5
    names = [s["name"] for s in skills]
    assert "macro_digest" in names
    assert "backtest_sweep" in names
    assert "journal_integrity_audit" in names
    assert "distill_performance_scorecard" in names
    assert "dynamic_code_runner" in names


def test_invoke_macro_digest_skill():
    res = SKILL_REGISTRY.invoke_skill("macro_digest", symbol="EURUSD")
    assert res["status"] == "SUCCESS"
    assert res["output"]["symbol"] == "EURUSD"
    assert "cot_institutional_bias" in res["output"]


def test_invoke_backtest_sweep_skill():
    res = SKILL_REGISTRY.invoke_skill(
        "backtest_sweep",
        symbols=["EURUSD"],
        fast_periods=[5, 10],
        slow_periods=[20],
    )
    assert res["status"] == "SUCCESS"
    assert res["output"]["total_combinations_tested"] >= 1
    assert "best_configuration" in res["output"]


def test_invoke_journal_audit_skill():
    res = SKILL_REGISTRY.invoke_skill("journal_integrity_audit")
    assert res["status"] == "SUCCESS"
    assert "audit_passed" in res["output"]


def test_invoke_performance_scorecard_skill():
    res = SKILL_REGISTRY.invoke_skill("distill_performance_scorecard")
    assert res["status"] == "SUCCESS"
    assert "win_rate_pct" in res["output"]


def test_tool_execute_dynamic_metric_analysis():
    tool_res = execute_dynamic_metric_analysis.invoke({
        "code": "OUTPUT = {'rolling_omega_ratio': 1.84, 'max_consecutive_losses': 2}",
        "description": "Calculate Omega ratio and consecutive loss metric",
    })
    assert tool_res["success"] is False
    assert "disabled" in tool_res["error"].lower()


def test_tool_invoke_agent_skill():
    tool_res = invoke_agent_skill.invoke({
        "skill_name": "macro_digest",
        "arguments_json": '{"symbol": "GBPUSD"}',
    })
    assert tool_res["status"] == "SUCCESS"
    assert tool_res["output"]["symbol"] == "GBPUSD"


def test_tool_list_available_agent_skills():
    tool_res = list_available_agent_skills.invoke({"category": "RESEARCH"})
    assert tool_res["total_skills"] >= 1
