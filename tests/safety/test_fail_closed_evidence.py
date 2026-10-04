import math
import os
from unittest.mock import MagicMock, patch
import pytest

from core.trading_safety import DeterministicValidator, MarketEvidence, TradeProposal, TradingStore
from data.market_data_provider import MarketDataProvider


def _valid_market_dict():
    return {
        "market_open": True,
        "tick_age_ms": 10.0,
        "max_tick_age_ms": 5000.0,
        "symbol_tradable": True,
        "spread": 0.0001,
        "max_spread": 0.001,
        "clock_ok": True,
        "session_allowed": True,
        "liquidity_ok": True,
        "microstructure_ok": True,
        "required_data_ok": True,
        "completed_candle": True,
        "breakout_event": False,
        "stop_distance": 0.01,
        "min_stop_distance": 0.0001,
        "high_impact_news": False,
        "setup_cooldown_seconds": 900.0,
    }


def _proposal():
    return TradeProposal(
        strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1000,
        stop_loss=1.0900, take_profit=1.1200, confidence=0.8,
        features={"ema_alignment": 1, "mtf_alignment": 1, "adx": 25,
                  "near_key_level": True, "confirmed_candle": True,
                  "volume_confirmed": True},
    )


def test_market_evidence_parse_valid():
    """MarketEvidence.parse succeeds on a fully specified market dictionary."""
    data = _valid_market_dict()
    evidence, problems = MarketEvidence.parse(data)
    assert not problems
    assert evidence is not None
    assert evidence.market_open is True
    assert evidence.tick_age_ms == 10.0
    assert evidence.symbol_tradable is True


def test_market_evidence_parse_missing_and_none_fields():
    """MarketEvidence.parse rejects missing or None required fields."""
    for field in MarketEvidence.REQUIRED_FIELDS:
        # 1. Missing field
        bad_missing = _valid_market_dict()
        bad_missing.pop(field)
        ev, problems = MarketEvidence.parse(bad_missing)
        assert ev is None
        assert any(f"missing evidence: {field}" in p for p in problems)

        # 2. None field
        bad_none = _valid_market_dict()
        bad_none[field] = None
        ev, problems = MarketEvidence.parse(bad_none)
        assert ev is None
        assert any(f"missing evidence: {field}" in p for p in problems)


def test_market_evidence_parse_non_finite_and_negative_tick_age():
    """MarketEvidence.parse rejects non-finite floats and negative tick age."""
    bad_nan = _valid_market_dict()
    bad_nan["spread"] = float("nan")
    ev, problems = MarketEvidence.parse(bad_nan)
    assert ev is None
    assert any("missing evidence: spread" in p for p in problems)

    bad_neg = _valid_market_dict()
    bad_neg["tick_age_ms"] = -500.0
    # In validate, negative tick_age is rejected by tick_fresh 0 <= tick_age <= max
    store = TradingStore(path=":memory:")
    validator = DeterministicValidator(store)
    res = validator.validate(_proposal(), bad_neg)
    assert not res.approved
    assert "stale tick" in res.reasons


def test_producer_validate_proposal_supplies_all_required_evidence():
    """Assert that _validate_proposal supplies every field in MarketEvidence.REQUIRED_FIELDS."""
    import inspect
    from intelligence.llm_orchestrator import LLMOrchestrator

    src = inspect.getsource(LLMOrchestrator._validate_proposal)
    # The market dictionary definition inside _validate_proposal must mention all required fields
    for field in MarketEvidence.REQUIRED_FIELDS:
        assert f'"{field}"' in src or f"'{field}'" in src, (
            f"_validate_proposal producer drops required evidence field: {field}"
        )


import threading


def _make_orchestrator():
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = object.__new__(LLMOrchestrator)
    orch._cycle_local = threading.local()
    orch.store = MagicMock()
    orch.operations = MagicMock()
    orch.data_quality = MagicMock()
    orch.ablation_modes = set()
    orch.run_id = "test_run"
    orch.model_name = "mock"
    orch._run_maintenance_prefix = MagicMock()
    orch._v2_stage_start = MagicMock()
    orch._v2_seam = MagicMock()
    orch._bus_emit = MagicMock()
    orch._guardian_progress = MagicMock()
    orch._run_schedules_bounded = MagicMock()
    orch._completed_candle_candidate = MagicMock(return_value="2026-10-05T00:00:00")
    orch._apply_ablation_context_strips = MagicMock()
    orch.operations.claim_task.return_value = True
    orch.data_provider = MagicMock()
    orch.data_provider.api_manager = None
    orch.data_provider.fetch_data.return_value = {"ohlc": [1, 2, 3]}
    return orch


def test_fail_closed_data_quality_exception_returns_hold_and_releases_task():
    """1a-F15: DataQualityGate exception in execute_cycle fails closed to HOLD and releases task."""
    orch = _make_orchestrator()
    orch.data_quality.validate.side_effect = RuntimeError("transient db error in data quality")

    decision, confidence = orch.execute_cycle(
        symbol="EURUSD", timeframe="M15", cycle_num=1, total_cycles=1,
    )
    assert decision["action"] == "HOLD"
    assert "Data quality gate exception" in decision["reasoning"]
    assert confidence == 0.0
    orch.operations.release_task.assert_called_once()


def test_fail_closed_news_gate_exception_returns_hold_and_releases_task():
    """1a-F15: NewsTradingGate exception in execute_cycle fails closed to HOLD and releases task."""
    orch = _make_orchestrator()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])

    with patch("core.news_trading.NewsTradingGate", side_effect=ValueError("corrupt calendar json")):
        decision, confidence = orch.execute_cycle(
            symbol="EURUSD", timeframe="M15", cycle_num=1, total_cycles=1,
        )
        assert decision["action"] == "HOLD"
        assert "News trading gate exception" in decision["reasoning"]
        assert confidence == 0.0
        orch.operations.release_task.assert_called_once()


def test_fail_closed_guardian_check_exception_rejects_pending_order():
    """1a-F15: Exception during Ψ Guardian check in market_data_provider fails closed and rejects."""
    prov = MarketDataProvider(client=MagicMock(), risk_manager=MagicMock())
    with patch("core.external_guardian.get_guardian", side_effect=RuntimeError("guardian socket broken")):
        res = prov.place_pending_order(
            symbol="EURUSD", otype="BUY_LIMIT", price=1.1000, volume=0.1,
            sl=1.0900, tp=1.1200, comment="test", paper=True,
        )
        assert res["rejected"] is True
        assert "Ψ_GUARDIAN_CHECK_FAILED" in res["rejection_reason"]

