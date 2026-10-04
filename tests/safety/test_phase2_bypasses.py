"""Tests for Phase 2 PR 2.1: Closing bypasses that don't need the gateway.

Pins:
- P1-C3: OpenTUI deck palette has no order_send and callTool has read-only allowlist
- 1a-F4 / 1b-R1: RL bridge merge never alters action (HOLD stays HOLD)
- 1a-F6 / PI-P12: Provider outage never promotes institutional workflow to live trade
- PI-P1 / TL-S1: Read-only decision toolkit (no exec, no memory writing, no skills)
- PI-P10: ζ review fails closed on error or non-approval
- PI-P6: Alpha DSL AST check uses strict allowlist (no attributes, no dunders, no unlisted calls)
- TL-S2: Prometheus exporter binds 127.0.0.1 by default
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.alpha_discovery import SafeAlphaEvaluator
from operations.prometheus_exporter import start_exporter
from research.rl_action_bridge import BridgedExecutionParams, merge_bridge_into_decision


def test_deck_palette_has_no_order_send():
    """P1-C3: The deck palette must not expose mt5_order_send."""
    screen_path = Path("tui_opentui/src/screens/MT5Screen.tsx")
    assert screen_path.exists(), "MT5Screen.tsx missing"
    content = screen_path.read_text(encoding="utf-8")
    assert '"mt5_order_send"' not in content, "mt5_order_send must be removed from MT5Screen.tsx palette"

    mcp_path = Path("tui_opentui/src/transport/mcp.ts")
    assert mcp_path.exists(), "mcp.ts missing"
    mcp_content = mcp_path.read_text(encoding="utf-8")
    assert "ALLOWED_MCP_TOOLS" in mcp_content or "READONLY" in mcp_content, (
        "mcp.ts must enforce a read-only allowlist"
    )


def test_rl_bridge_merge_never_alters_action():
    """1a-F4 / 1b-R1: RL bridge merge may fill numeric fields only and NEVER changes action."""
    bridged = BridgedExecutionParams(
        action="BUY",
        risk_fraction=0.01,
        entry=1.1050,
        stop_loss=1.1000,
        take_profit=1.1200,
        strategy="scalp",
    )

    # 1. HOLD must remain HOLD regardless of fill_missing_only flag
    decision_hold = {"action": "HOLD", "confidence": 0.90}
    merged_fill = merge_bridge_into_decision(decision_hold, bridged, fill_missing_only=True)
    assert merged_fill["action"] == "HOLD", "HOLD was mutated to BUY with fill_missing_only=True"

    merged_nofill = merge_bridge_into_decision(decision_hold, bridged, fill_missing_only=False)
    assert merged_nofill["action"] == "HOLD", "HOLD was mutated to BUY with fill_missing_only=False"

    # 2. NONE or empty action must also not be promoted to BUY/SELL
    for empty_act in ["", "NONE", "NULL"]:
        dec_empty = {"action": empty_act}
        res = merge_bridge_into_decision(dec_empty, bridged, fill_missing_only=True)
        assert res["action"] in {"", "NONE", "NULL", "HOLD"}, f"Empty action '{empty_act}' was promoted to BUY"

    # 3. Numeric fields can still be populated when already a BUY/SELL decision
    decision_buy = {"action": "BUY", "entry": None, "stop_loss": None, "take_profit": None}
    merged_buy = merge_bridge_into_decision(decision_buy, bridged, fill_missing_only=True)
    assert merged_buy["action"] == "BUY"
    assert merged_buy["entry"] == 1.1050
    assert merged_buy["stop_loss"] == 1.1000
    assert merged_buy["take_profit"] == 1.1200


def test_outage_does_not_promote_institutional_workflow():
    """1a-F6 / PI-P12: During LLM provider outage, institutional workflow is NOT promoted to a trade."""
    orch_path = Path("intelligence/llm_orchestrator.py")
    content = orch_path.read_text(encoding="utf-8")
    # Verify the promotion block is deleted
    assert "Autonomous institutional workflow proposal promoted during LLM outage" not in content, (
        "Outage promotion block must be deleted from llm_orchestrator.py"
    )


def test_decision_toolkit_is_read_only_and_safe():
    """PI-P1 / TL-S1: Read-only decision toolkit."""
    from core.trading_tools import (
        DECISION_READONLY_TOOLS,
        _build_tool_registry,
        execute_tool,
        get_tool_schemas,
    )

    # 1. execute_dynamic_metric_analysis is completely absent from registry
    registry = _build_tool_registry()
    assert "execute_dynamic_metric_analysis" not in registry, (
        "execute_dynamic_metric_analysis must not be in tool registry"
    )

    # 2. Decision schemas only contain the approved read-only tools
    schemas = get_tool_schemas(toolkits=DECISION_READONLY_TOOLS)
    schema_names = {s["function"]["name"] for s in schemas}

    forbidden = {
        "execute_dynamic_metric_analysis",
        "invoke_agent_skill",
        "ingest_quant_and_market_memory",
        "sync_working_memory_state",
    }
    assert schema_names.isdisjoint(forbidden), f"Decision schemas contain forbidden tools: {schema_names & forbidden}"

    # 3. execute_tool refuses forbidden or out-of-toolkit tools
    err_res = execute_tool("execute_dynamic_metric_analysis", {"code": "print('pwn')"})
    assert "Unknown tool" in err_res or "not allowed" in err_res

    # 4. Contextual restriction when allowed_tools is provided
    err_context = execute_tool("invoke_agent_skill", {"skill_name": "foo"}, allowed_tools=set(DECISION_READONLY_TOOLS))
    assert "not allowed" in err_context or "Unknown tool" in err_context


def test_zeta_review_fails_closed():
    """PI-P10: ζ dynamic code review fails closed on any exception or non-approval."""
    from core.code_safety_agent import SafetyVerdict
    from core.skill_registry import skill_dynamic_code_runner

    # 1. Non-approved verdicts fail closed
    with patch("core.code_safety_agent.get_zeta_agent") as mock_zeta:
        mock_instance = MagicMock()
        mock_instance.review.return_value = MagicMock(
            verdict=SafetyVerdict.REJECTED,
            human_review_reason="Dangerous AST construct",
            static=MagicMock(safety_score=0.1),
        )
        mock_zeta.return_value = mock_instance

        res = skill_dynamic_code_runner("import os; os.system('ls')")
        assert res["success"] is False
        assert "rejected" in res["error"]

    # 2. Exception in ζ review MUST fail closed, not pass through
    with patch("core.code_safety_agent.get_zeta_agent") as mock_zeta:
        mock_zeta.side_effect = RuntimeError("Zeta engine crash")
        res = skill_dynamic_code_runner("print('test')")
        assert res["success"] is False
        assert "fail" in res["error"].lower() or "error" in res["error"].lower()


def test_alpha_dsl_ast_allowlist_rejects_escapes():
    """PI-P6: Alpha DSL AST check uses strict allowlist (no attributes, no dunders, no unlisted calls)."""
    alpha = SafeAlphaEvaluator()

    # Valid expressions must pass
    valid_expressions = [
        "(close - open) / (high - low)",
        "ts_rank(close, 10)",
        "delta(close, 5) * 1.5",
        "-abs(close - vwap)",
        "sqrt(high) + log(volume)",
        "close > open",
    ]
    for expr in valid_expressions:
        safe, msg = alpha._check_ast_safety(expr)
        assert safe is True, f"Valid expression rejected: {expr} ({msg})"

    # Dangerous expressions must be rejected
    dangerous_expressions = [
        "(1).__class__.__bases__[0]",
        "df.__class__",
        "open('/etc/passwd')",
        "__import__('os').system('ls')",
        "eval('1+1')",
        "[x for x in close]",
        "lambda x: x",
        "close.values",
        "df.close",
        "pd.read_csv('foo')",
        "np.random.normal(0, 1)",
    ]
    for expr in dangerous_expressions:
        safe, msg = alpha._check_ast_safety(expr)
        assert safe is False, f"Dangerous expression was NOT rejected: {expr} ({msg})"


def test_prometheus_exporter_binds_localhost_by_default():
    """TL-S2: Prometheus exporter binds 127.0.0.1 by default."""
    sig = inspect.signature(start_exporter)
    assert sig.parameters["host"].default == "127.0.0.1", (
        f"start_exporter default host is {sig.parameters['host'].default}, expected 127.0.0.1"
    )
