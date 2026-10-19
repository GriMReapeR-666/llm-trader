import pytest
from unittest.mock import MagicMock, patch
from operations.autonomy import apply_operator_control, AutonomyManager

def test_apply_killswitch_engages_all_layers():
    """Killswitch must engage store, autonomy halt, latch, and intent cancellations (C4)."""
    store = MagicMock()
    autonomy = MagicMock()
    latch = MagicMock()
    operations = MagicMock()
    operations.get_active_order_intents.return_value = []

    control = {"action": "killswitch", "actor": "operator@test", "source": "command-deck"}
    outcome = apply_operator_control(
        control,
        store=store,
        autonomy=autonomy,
        latch=latch,
        operations=operations,
    )

    assert outcome["status"] == "applied"
    assert outcome["store_kill_switch"] is True
    assert outcome["autonomy_halted"] is True
    assert outcome["latch_engaged"] is True
    store.set_kill_switch.assert_called_once_with(True, reason="operator:operator@test")
    autonomy.halt.assert_called_once_with("killswitch:operator:operator@test")
    latch.apply_market_data.assert_called_once_with(halt_flag=True)

def test_apply_flatten_with_trade_manager():
    """Flatten must flag risk manager and trigger TradeManager.enforce_protective_flatten (C4)."""
    risk = MagicMock()
    risk.pending_flatten_reason.return_value = None
    trade_manager = MagicMock()
    store = MagicMock()
    autonomy = MagicMock()

    control = {"action": "flatten", "actor": "operator@test"}
    outcome = apply_operator_control(
        control,
        store=store,
        autonomy=autonomy,
        risk=risk,
        trade_manager=trade_manager,
    )

    assert outcome["status"] == "applied"
    risk.trigger_emergency_flatten.assert_called_once_with("operator:operator@test")
    trade_manager.enforce_protective_flatten.assert_called_once()

def test_apply_flatten_with_remaining_pending_is_partial():
    """If positions remain open or flatten is still pending, outcome is partial."""
    risk = MagicMock()
    risk.pending_flatten_reason.return_value = "1 position pending closure"
    trade_manager = MagicMock()
    store = MagicMock()
    autonomy = MagicMock()

    control = {"action": "flatten", "actor": "operator@test"}
    outcome = apply_operator_control(
        control,
        store=store,
        autonomy=autonomy,
        risk=risk,
        trade_manager=trade_manager,
    )

    assert outcome["status"] == "partial"
    assert outcome["reason"] == "1 position pending closure"

def test_apply_flatten_without_trade_manager_is_honestly_queued():
    """When TradeManager is not yet attached, flatten is queued, never claimed applied (D1)."""
    risk = MagicMock()
    store = MagicMock()
    autonomy = MagicMock()

    control = {"action": "flatten", "actor": "operator@test"}
    outcome = apply_operator_control(
        control,
        store=store,
        autonomy=autonomy,
        risk=risk,
        trade_manager=None,
    )

    assert outcome["status"] == "queued"
    risk.trigger_emergency_flatten.assert_called_once_with("operator:operator@test")

def test_apply_killswitch_fails_closed_on_store_error():
    """If the primary store kill switch fails, status must be 'failed', never falsely reporting execution."""
    store = MagicMock()
    store.set_kill_switch.side_effect = RuntimeError("database disk I/O error")
    autonomy = MagicMock()

    control = {"action": "killswitch", "actor": "operator@test"}
    outcome = apply_operator_control(
        control,
        store=store,
        autonomy=autonomy,
    )

    assert outcome["status"] == "failed"
    assert any("database disk I/O error" in err for err in outcome["errors"])

def test_orchestrator_autonomy_gate_fails_closed_when_trading_not_allowed():
    """When autonomy forbids trading (e.g. halted), _autonomy_gate must return HOLD tuple (C4)."""
    from intelligence.llm_orchestrator import LLMOrchestrator

    with patch("intelligence.llm_orchestrator.TradingStore"), \
         patch("intelligence.llm_orchestrator.RiskManager"):
        orch = LLMOrchestrator.__new__(LLMOrchestrator)
        orch.store = MagicMock()
        orch.data_provider = MagicMock()

        # Mock autonomy returning False
        mock_autonomy = MagicMock()
        mock_autonomy.drain_controls.return_value = []
        mock_autonomy.trading_allowed.return_value = (False, "operator:killswitch")

        with patch("operations.autonomy.get_autonomy", return_value=mock_autonomy):
            res = orch._autonomy_gate("EURUSD", "scalp", paper_mode=True)
            assert res is not None, "Gate must return a HOLD tuple when trading is not allowed"
            decision, latency = res
            assert decision["action"] == "HOLD"
            assert "operator:killswitch" in decision["reasoning"]
