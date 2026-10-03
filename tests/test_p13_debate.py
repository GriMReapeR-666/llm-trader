"""P13 ApexQuant Multi-Agent Debate Protocol (Ding / Sun 2026) Test Suite."""
import pytest
from unittest.mock import MagicMock

from intelligence.debate_protocol import (
    ApexQuantParliament,
    DebateTurn,
    DebateVerdict,
    evaluate_evidence_quality,
)
from core.bounded_autonomy_pipeline import (
    CandidateOrder,
    LLMVetoOperator,
    LLMVetoVerdict,
    PortfolioState,
    BoundedAutonomyPipeline,
)


def test_evidence_quality_scoring():
    """Verify evidence quality density vs rhetorical assertion penalty."""
    # High quantitative evidence
    quant_text = "RSI is 45.2, ADX at 28.5 indicates strong trend with 2.5 R:R at key swing support."
    q_score, q_pen = evaluate_evidence_quality(quant_text)
    assert q_score >= 0.5
    assert q_pen <= 0.05

    # Pure rhetorical assertion with zero numbers
    rhet_text = "This is guaranteed easy money, obviously a no brainer that cannot lose!"
    r_score, r_pen = evaluate_evidence_quality(rhet_text)
    assert r_score <= 0.2
    assert r_pen >= 0.3


def test_parliament_unanimous_bull_validation():
    """Verify clean setup leads to high conviction approval and 1.0x sizing."""
    parliament = ApexQuantParliament()
    candidate = {
        "symbol": "BTCUSD",
        "action": "BUY",
        "entry_price": 60000.0,
        "stop_loss": 58000.0,
        "take_profit": 66000.0,  # 3:1 R:R
        "features": {"rsi": 52.0, "adx": 30.0, "regime": "TRENDING_BULL"},
    }
    context = {
        "regime": "TRENDING_BULL",
        "smc_structure": {"has_choch": False, "in_premium": False, "range_position": 0.40},
    }
    verdict = parliament.conduct_debate(candidate, context)
    assert verdict.approved is True
    assert verdict.decision == "BUY"
    assert verdict.confidence >= 0.70
    assert verdict.sizing_multiplier == 1.0
    assert len(verdict.turns) == 5  # Zealot R1, Reaper R1, Zealot R2, Reaper R2, Fulcrum R3
    assert verdict.consensus_level in ("UNANIMOUS", "MAJORITY")


def test_parliament_reaper_fatal_risk_veto():
    """Verify catastrophic risk (e.g. buying high in premium after CHoCH with bad R:R) triggers CIO VETO."""
    parliament = ApexQuantParliament()
    candidate = {
        "symbol": "ETHUSD",
        "action": "BUY",
        "entry_price": 3500.0,
        "stop_loss": 3400.0,
        "take_profit": 3550.0,  # 0.5:1 R:R (abysmal)
        "features": {"rsi": 78.0, "adx": 15.0, "regime": "TRENDING_BEAR"},
    }
    context = {
        "regime": "TRENDING_BEAR",
        "smc_structure": {"has_choch": True, "in_premium": True, "range_position": 0.85},
    }
    verdict = parliament.conduct_debate(candidate, context)
    assert verdict.approved is False
    assert verdict.decision == "HOLD"
    assert verdict.sizing_multiplier == 0.0
    assert "VETO" in verdict.consensus_level or "Reaper" in verdict.rationale


def test_parliament_divided_verdict_defensive_sizing():
    """Verify partial risk creates defensive position sizing (compromise verdict)."""
    parliament = ApexQuantParliament()
    candidate = {
        "symbol": "SOLUSD",
        "action": "BUY",
        "entry_price": 150.0,
        "stop_loss": 140.0,
        "take_profit": 175.0,  # 2.5:1 R:R
        "features": {"rsi": 55.0, "adx": 28.0, "regime": "TRENDING_BULL"},
    }
    # Mild warning: price is in upper zone but no fatal CHoCH
    context = {
        "regime": "TRENDING_BULL",
        "smc_structure": {"has_choch": False, "in_premium": True, "range_position": 0.65},
    }
    verdict = parliament.conduct_debate(candidate, context)
    assert verdict.approved is True
    assert verdict.decision == "BUY"
    assert 0.20 <= verdict.sizing_multiplier < 1.0  # Sizing reduced due to premium warning
    assert verdict.consensus_level == "DIVIDED"


def test_bounded_autonomy_debate_operator_integration():
    """Verify LLMVetoOperator backed by ApexQuantParliament satisfies Proposition 1."""
    veto_op = LLMVetoOperator.create_apex_debate_veto()
    candidate = CandidateOrder(
        symbol="BTCUSD",
        action="BUY",
        quantity=0.5,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=65000.0,
        score=0.8,
        rationale="Strong signal S(X,P)",
        features={"rsi": 50.0, "adx": 30.0},
    )
    context = {
        "regime": "TRENDING_BULL",
        "smc_structure": {"has_choch": False, "in_premium": False},
    }
    order, verdict = veto_op.evaluate(candidate, context=context)
    assert isinstance(verdict, LLMVetoVerdict)
    assert verdict.approved is True
    assert order.action == "BUY"
    assert order.quantity == 0.5  # Monotone exposure: never upsized

    # Candidate with fatal risk
    bad_cand = CandidateOrder(
        symbol="BTCUSD",
        action="BUY",
        quantity=0.5,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=60500.0,  # 0.5:1 R:R
        features={"rsi": 80.0},
    )
    bad_ctx = {
        "regime": "TRENDING_BEAR",
        "smc_structure": {"has_choch": True, "in_premium": True},
    }
    order2, verdict2 = veto_op.evaluate(bad_cand, context=bad_ctx)
    assert verdict2.approved is False
    assert order2.action == "HOLD"
    assert order2.quantity == 0.0  # Boolean Veto enforced


def test_parliament_custom_llm_caller():
    """Verify custom LLM caller integration."""
    mock_caller = MagicMock(return_value={
        "decision": "BUY",
        "approved": True,
        "confidence": 0.88,
        "sizing_multiplier": 0.75,
        "rationale": "Parliament reached consensus after round 2",
        "consensus_level": "MAJORITY",
    })
    parliament = ApexQuantParliament(llm_caller=mock_caller)
    candidate = {"action": "BUY", "symbol": "BTCUSD"}
    verdict = parliament.conduct_debate(candidate, {})
    assert verdict.approved is True
    assert verdict.decision == "BUY"
    assert verdict.confidence == 0.88
    assert verdict.sizing_multiplier == 0.75
    mock_caller.assert_called_once()
