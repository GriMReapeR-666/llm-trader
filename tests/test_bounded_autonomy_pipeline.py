"""Tests for Bounded-Autonomy LLM Agent Pipeline (NZE, 2026)."""
import json
import os
import tempfile
import pytest
from core.bounded_autonomy_pipeline import (
    BoundedAutonomyPipeline,
    CandidateOrder,
    ExecutedDecision,
    LLMVetoOperator,
    LLMVetoVerdict,
    PortfolioState,
    RiskShield,
    RuleProposer,
    SizingRiskBudget,
)


def test_monotone_exposure_approval_and_veto():
    """Verify Proposition 1: Monotone Exposure Property under approval and veto."""
    proposer = RuleProposer(default_quantity=0.5)
    
    # 1. Test LLM Approval
    veto_approve = LLMVetoOperator(
        llm_caller=lambda p: {"approved": True, "rationale": "Strong trend alignment"},
        model_name="test-llm",
    )
    shield = RiskShield(budget=SizingRiskBudget(max_position_pct=0.5))
    pipe_approve = BoundedAutonomyPipeline(proposer, veto_approve, shield)

    portfolio = PortfolioState(cash=50000.0, equity=50000.0, holdings={})
    # Simulated uptrend prices
    prices = [100.0 + i * 0.5 for i in range(40)]

    decision = pipe_approve.step("BTC/USD", prices, portfolio)
    assert decision.action == "BUY"
    assert decision.quantity <= 0.5  # Never exceeds proposed 0.5
    assert decision.veto_verdict is not None
    assert decision.veto_verdict.approved is True

    # 2. Test LLM Veto
    veto_reject = LLMVetoOperator(
        llm_caller=lambda p: {"approved": False, "rationale": "Resistance overhead, vetoing"},
        model_name="test-llm",
    )
    pipe_veto = BoundedAutonomyPipeline(proposer, veto_reject, shield)
    decision_vetoed = pipe_veto.step("BTC/USD", prices, portfolio)
    assert decision_vetoed.action == "HOLD"
    assert decision_vetoed.quantity == 0.0
    assert decision_vetoed.veto_verdict.approved is False
    assert pipe_veto.veto_rate == 1.0
    assert pipe_veto.decision_consistency == 0.0


def test_adversarial_mutation_immunity():
    """Verify that LLM cannot mutate symbol, flip direction, or inflate quantity."""
    # LLM returns malicious/adversarial response attempting to inflate size and flip direction
    adversarial_caller = lambda p: {
        "approved": True,
        "action": "SELL",  # Attempting to invert BUY to SELL
        "quantity": 100.0,  # Attempting 200x size
        "symbol": "MALICIOUS/TICKER",
        "rationale": "Adversarial injection",
    }
    veto_op = LLMVetoOperator(llm_caller=adversarial_caller)
    shield = RiskShield()

    candidate = CandidateOrder(
        symbol="EURUSD",
        action="BUY",
        quantity=0.1,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        score=0.7,
    )
    filtered_cand, verdict = veto_op.evaluate(candidate)

    # Invariant checks:
    assert filtered_cand.symbol == "EURUSD"
    assert filtered_cand.action == "BUY"
    assert filtered_cand.quantity == 0.1
    assert verdict.approved is True


def test_fail_closed_on_llm_exception():
    """Verify that an LLM timeout/exception safely triggers fail-closed veto to HOLD."""
    def crashing_caller(p):
        raise TimeoutError("Provider gateway timed out after 5000ms")

    veto_op = LLMVetoOperator(llm_caller=crashing_caller, fail_closed=True)
    candidate = CandidateOrder(
        symbol="BTC/USD",
        action="BUY",
        quantity=0.5,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=62000.0,
    )
    filtered_cand, verdict = veto_op.evaluate(candidate)
    assert filtered_cand.action == "HOLD"
    assert filtered_cand.quantity == 0.0
    assert verdict.approved is False
    assert "Provider gateway timed out" in str(verdict.rationale)


def test_risk_shield_notional_cap():
    """Verify deterministic per-position notional cap B_{i,t} = tau_pos * e_t - |h_{i,t}| * p_{i,t}."""
    # Equity = $100,000, max_position_pct = 0.10 -> max notional = $10,000
    # Current holding = 1.0 BTC at $8,000 = $8,000 -> remaining budget B_{i,t} = $2,000
    portfolio = PortfolioState(
        cash=50000.0,
        equity=100000.0,
        holdings={"BTC/USD": 1.0},
    )
    budget = SizingRiskBudget(max_position_pct=0.10, min_trade_notional=10.0)
    shield = RiskShield(budget=budget)

    # Proposer wants to buy 1.0 BTC at $8,000 ($8,000 desired notional)
    candidate = CandidateOrder(
        symbol="BTC/USD",
        action="BUY",
        quantity=1.0,
        entry_price=8000.0,
        stop_loss=7800.0,
        take_profit=8400.0,
    )

    decision = shield.enforce(candidate, portfolio)
    # Remaining budget is $2,000. Price is $8,000. Allowed qty = $2000 / $8000 = 0.25 BTC!
    assert decision.action == "BUY"
    assert pytest.approx(decision.quantity, abs=1e-5) == 0.25
    assert pytest.approx(decision.executed_notional, abs=1e-2) == 2000.0

    # If remaining budget is exhausted (B_{i,t} <= $10):
    portfolio_full = PortfolioState(
        cash=50000.0,
        equity=100000.0,
        holdings={"BTC/USD": 1.25},  # 1.25 * 8000 = $10,000 (100% of cap)
    )
    decision_blocked = shield.enforce(candidate, portfolio_full)
    assert decision_blocked.action == "HOLD"
    assert decision_blocked.quantity == 0.0
    assert not decision_blocked.risk_approved
    assert any("budget exhausted" in r for r in decision_blocked.rejection_reasons)


def test_structured_audit_logging():
    """Verify per-cycle structured audit logging to JSONL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = os.path.join(tmpdir, "orders.jsonl")
        proposer = RuleProposer(default_quantity=0.2)
        veto_op = LLMVetoOperator(llm_caller=lambda p: {"approved": True, "rationale": "Pass"})
        shield = RiskShield()
        pipe = BoundedAutonomyPipeline(proposer, veto_op, shield, audit_log_path=audit_path)

        portfolio = PortfolioState(cash=10000.0, equity=10000.0)
        prices = [50.0 + i * 0.2 for i in range(40)]

        pipe.step("ETH/USD", prices, portfolio)

        assert os.path.exists(audit_path)
        with open(audit_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["symbol"] == "ETH/USD"
        assert record["cycle"] == 1
        assert "proposer" in record
        assert "llm_veto" in record
        assert "shield_execution" in record

def test_forex_lot_contract_size_notional_scaling():
    """Verify that Forex lots are properly scaled by contract size (100,000) for notional checks."""
    shield = RiskShield(budget=SizingRiskBudget(
        max_position_pct=0.10,      # 10% of equity max position
        min_trade_notional=100.0,   # $100 min trade
        cash_buffer_pct=0.95
    ))

    # Account with $50,000 equity -> Max position notional = $5,000
    portfolio = PortfolioState(cash=50000.0, equity=50000.0, holdings={})

    # EURUSD entry at 1.0850.
    # 0.01 lots = 1,000 EUR = $1,085 notional.
    cand_small = CandidateOrder(
        action="BUY",
        quantity=0.01,
        symbol="EURUSD",
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
        score=0.8,
        features={},
    )
    dec_small = shield.enforce(cand_small, portfolio)
    assert dec_small.action == "BUY"
    assert dec_small.risk_approved is True
    # Notional must be ~$1,085, NOT $0.01085
    assert 1080.0 <= dec_small.executed_notional <= 1090.0
    assert abs(dec_small.quantity - 0.01) < 1e-4

    # Large order: 0.10 lots = 10,000 EUR = $10,850 notional.
    # Must be capped to max_position ($5,000), resulting in ~0.046 lots.
    cand_large = CandidateOrder(
        action="BUY",
        quantity=0.10,
        symbol="EURUSD",
        entry_price=1.0850,
        stop_loss=1.0800,
        take_profit=1.0950,
        score=0.8,
        features={},
    )
    dec_large = shield.enforce(cand_large, portfolio)
    assert dec_large.action == "BUY"
    assert dec_large.risk_approved is True
    assert dec_large.executed_notional <= 5000.01
    assert dec_large.quantity < 0.10
    # ~0.046 lots
    assert 0.045 <= dec_large.quantity <= 0.047

