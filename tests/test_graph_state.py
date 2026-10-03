"""Unit tests for Agentic Graph State definitions and channel reducers."""
import pytest
from graph_state import (
    TradeProposalPayload,
    OrderIntentPayload,
    ExecutionReceiptPayload,
    create_initial_trading_cycle_state,
    merge_audit_logs,
    merge_rejection_reasons,
    merge_hypotheses,
)


def test_trade_proposal_payload_validation():
    # Test valid proposal
    p = TradeProposalPayload(
        symbol="EURUSD",
        direction="buy",
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
        confidence=0.85,
    )
    assert p.direction == "BUY"
    assert p.symbol == "EURUSD"
    assert p.stop_loss == 1.0800

    # Test direction normalization
    p_short = TradeProposalPayload(
        symbol="GBPUSD",
        direction="SHORT",
        entry_price=1.2500,
    )
    assert p_short.direction == "SELL"


def test_merge_reducers():
    # Test merge_audit_logs
    log1 = ["Step 1 started"]
    log2 = ["Step 2 completed", "Step 3 evaluated"]
    merged = merge_audit_logs(log1, log2)
    assert len(merged) == 3
    assert merged == ["Step 1 started", "Step 2 completed", "Step 3 evaluated"]

    # Test merge_rejection_reasons (deduplication)
    r1 = ["HIGH_SPREAD", "MAX_DRAWDOWN"]
    r2 = ["HIGH_SPREAD", "MARGIN_EXHAUSTED"]
    merged_r = merge_rejection_reasons(r1, r2)
    assert len(merged_r) == 3
    assert "HIGH_SPREAD" in merged_r
    assert "MAX_DRAWDOWN" in merged_r
    assert "MARGIN_EXHAUSTED" in merged_r

    # Test merge_hypotheses
    h1 = [{"direction": "BUY", "confidence": 0.7}]
    h2 = {"direction": "HOLD", "confidence": 0.3}
    merged_h = merge_hypotheses(h1, h2)
    assert len(merged_h) == 2


def test_create_initial_trading_cycle_state():
    state = create_initial_trading_cycle_state(
        symbol="USDJPY",
        timeframe="H1",
        strategy="macro_trend",
        paper_mode=True,
    )
    assert state["symbol"] == "USDJPY"
    assert state["timeframe"] == "H1"
    assert state["status"] == "INITIALIZED"
    assert state["validation_passed"] is False
    assert len(state["audit_trace"]) >= 1
