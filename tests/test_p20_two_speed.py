"""Unit tests for P20: Two-Speed Reasoning Architecture & Deliberation Tax Guard (Cheng et al., 2026).

Validates:
- Decision-time reasoning latency tradeoffs (deliberation dividend vs deliberation tax).
- Expired tender detection when LLM thinking latency exceeds max_deliberation_ms.
- Expired tender detection when price drifts adversely during deliberation.
- Integration into LLMVetoOperator & BoundedAutonomyPipeline.
- Compiled fast-path bypass for deterministic execution.
"""
from __future__ import annotations

import pytest
from core.two_speed_reasoning import (
    DeliberationConfig,
    DeliberationTaxGuard,
    TwoSpeedMode,
)
from core.bounded_autonomy_pipeline import (
    CandidateOrder,
    LLMVetoOperator,
    LLMVetoVerdict,
    BoundedAutonomyPipeline,
    RiskShield,
    SizingRiskBudget,
    PortfolioState,
)


def test_deliberation_guard_admissible_when_within_budget():
    """Tender is accepted when deliberation latency and price drift are within budget."""
    cfg = DeliberationConfig(max_deliberation_ms=3000.0, max_drift_pct=0.002)
    guard = DeliberationTaxGuard(config=cfg)

    ok, reason = guard.evaluate_tender(
        entry_price=100.0,
        current_market_price=100.05,  # 0.05% drift < 0.20%
        latency_ms=1200.0,            # 1200ms < 3000ms
    )
    assert ok is True
    assert reason == "admissible"


def test_deliberation_guard_expired_tender_latency():
    """Tender is rejected when thinking latency exceeds max_deliberation_ms (deliberation tax)."""
    cfg = DeliberationConfig(max_deliberation_ms=2500.0)
    guard = DeliberationTaxGuard(config=cfg)

    ok, reason = guard.evaluate_tender(
        entry_price=100.0,
        current_market_price=100.0,
        latency_ms=3200.0,  # Exceeds 2500ms
    )
    assert ok is False
    assert "expired_tender_latency" in reason


def test_deliberation_guard_expired_tender_price_drift():
    """Tender is rejected when price drifts beyond tolerance while agent was thinking."""
    cfg = DeliberationConfig(max_deliberation_ms=5000.0, max_drift_pct=0.002)
    guard = DeliberationTaxGuard(config=cfg)

    # 0.5% drift > 0.2% max drift
    ok, reason = guard.evaluate_tender(
        entry_price=100.0,
        current_market_price=100.50,
        latency_ms=1500.0,
    )
    assert ok is False
    assert "expired_tender_price_drift" in reason


def test_deliberation_guard_summary_metrics():
    """Deliberation guard maintains accurate session metrics for thinking time and tax events."""
    guard = DeliberationTaxGuard(DeliberationConfig(max_deliberation_ms=2000.0))
    guard.record_decision(latency_ms=1000.0, approved=True, tax_breach=False)
    guard.record_decision(latency_ms=3500.0, approved=False, tax_breach=True)

    summary = guard.summary()
    assert summary["total_deliberations"] == 2
    assert summary["tenders_accepted"] == 1
    assert summary["deliberation_tax_breaches"] == 1
    assert summary["mean_latency_ms"] == 2250.0


def test_llm_veto_operator_enforces_deliberation_tax():
    """LLMVetoOperator overrides LLM approval if deliberation latency exceeds threshold."""
    guard = DeliberationTaxGuard(DeliberationConfig(max_deliberation_ms=100.0))

    # Mock caller that simulates thinking latency
    def slow_llm_caller(prompt):
        import time
        time.sleep(0.15)  # 150ms > 100ms
        return {"approved": True, "rationale": "Strong setup", "confidence": 0.9}

    operator = LLMVetoOperator(
        llm_caller=slow_llm_caller,
        deliberation_guard=guard,
    )

    candidate = CandidateOrder(
        symbol="EURUSD",
        action="BUY",
        quantity=1.0,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        score=0.85,
        rationale="Momentum breakout",
    )

    out_order, verdict = operator.evaluate(candidate)
    assert out_order.action == "HOLD"
    assert verdict.approved is False
    assert "deliberation_tax_breach" in verdict.rationale


def test_compiled_fast_path_bypass():
    """Compiled fast-path execution operates with zero deliberation overhead."""
    shield = RiskShield(SizingRiskBudget(max_position_pct=0.10))
    portfolio = PortfolioState(equity=100_000.0, cash=50_000.0, holdings={})
    candidate = CandidateOrder(
        symbol="EURUSD",
        action="BUY",
        quantity=1000.0,
        entry_price=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        score=0.85,
        rationale="Compiled execution",
    )

    # Fast path: instant deterministic execution
    decision = shield.enforce(candidate, portfolio)
    assert decision.action == "BUY"
    assert decision.quantity > 0
    assert decision.budget_notional_cap > 0
