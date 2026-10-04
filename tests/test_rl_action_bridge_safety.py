import pytest
from rl_action_bridge import BridgedExecutionParams, merge_bridge_into_decision
from trading_safety import SCALP_MAGIC

def test_rl_bridge_never_mutates_hold_to_trade():
    """An LLM HOLD decision must NEVER be escalated to BUY or SELL by RL exploration (01a F4)."""
    bridged = BridgedExecutionParams(
        action="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy="scalp",
        magic=SCALP_MAGIC,
    )
    decision = {"action": "HOLD", "reasoning": "LLM decided no trade", "confidence": 0.0}
    merged = merge_bridge_into_decision(decision, bridged, fill_missing_only=True)
    assert merged["action"] == "HOLD", "RL bridge must not mutate HOLD to BUY"
    assert merged.get("entry") is None or merged.get("entry") == 0
    assert merged["execution_authority"] is False

def test_rl_bridge_never_mutates_empty_action_to_trade():
    """An empty or missing action defaults to HOLD and must not become a trade."""
    bridged = BridgedExecutionParams(
        action="SELL",
        entry=1.1000,
        stop_loss=1.1050,
        take_profit=1.0900,
        strategy="scalp",
        magic=SCALP_MAGIC,
    )
    decision = {"reasoning": "No action specified"}
    merged = merge_bridge_into_decision(decision, bridged, fill_missing_only=True)
    assert merged.get("action", "HOLD") == "HOLD"
    assert merged["execution_authority"] is False

def test_rl_bridge_fills_params_for_existing_buy():
    """RL bridge may fill missing entry/SL/TP when the decision is ALREADY BUY."""
    bridged = BridgedExecutionParams(
        action="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        strategy="scalp",
        magic=SCALP_MAGIC,
    )
    decision = {"action": "BUY", "entry": None, "stop_loss": None, "take_profit": None, "confidence": 0.75}
    merged = merge_bridge_into_decision(decision, bridged, fill_missing_only=True)
    assert merged["action"] == "BUY"
    assert merged["entry"] == 1.1000
    assert merged["stop_loss"] == 1.0950
    assert merged["take_profit"] == 1.1100
    assert merged["execution_authority"] is False
