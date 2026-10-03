"""P06 TwinAlgo (Thophet 2026) - SMC structure + expectancy gates."""
import pytest

from core.smc_structure import (
    detect_structure,
    find_order_blocks,
    is_premium,
    liquidity_pools,
    range_position,
)
from core.twinalgo_gates import (
    TwinGates,
    TwinRegime,
    action_gate,
    breakout_ok,
    breakeven_rr,
    calibrate_confidence,
    clamped_confidence,
    classify_regime,
    expectancy_per_r,
    loss_clamp_active,
    rr_gate,
)


def _bos_setup():
    # Swing high 13.0 at index 4 (pivot over window=3); swing low 9.6 at index 7
    highs = [10, 11, 10.5, 11.5, 13, 12, 11.5, 11, 12.5, 13.5, 14, 14.5]
    lows = [9, 9.5, 9.3, 9.8, 10, 9.9, 9.7, 9.6, 10.2, 10.8, 11, 11.2]
    closes = [10, 10.8, 10.6, 11, 12, 11.8, 11.5, 11.2, 12.8, 13.6, 14, 14.2]
    return highs, lows, closes


def test_bos_needs_two_consecutive_closes():
    h, l, c = _bos_setup()
    evts = detect_structure(h, l, c)
    assert any(e.kind == "BOS_BULL" for e in evts)
    # Only ONE close above the swing high at the final bar -> no event there
    c2 = [10, 10.8, 10.6, 11, 12, 11.8, 11.5, 11.2, 12.8, 12.0, 12.5, 13.5]
    evts2 = detect_structure(h, l, c2)
    assert all(e.index != len(c2) - 1 for e in evts2)


def test_choch_flips_trend():
    h, l, c = _bos_setup()
    # Sharp reversal: two closes below swing low 9.6 after the BOS uptrend
    h = h + [9.2, 8.7]
    l = l + [8.8, 8.3]
    c = c + [9.0, 8.5]
    evts = detect_structure(h, l, c)
    assert any(e.kind == "BOS_BULL" for e in evts)
    assert any(e.kind == "CHOCH_BEAR" for e in evts)


def test_order_block_mitigation():
    o = [100, 100, 101, 99]
    h = [101, 101, 102, 100]
    l = [99, 99.5, 100.5, 97]
    c = [100.5, 99.5, 101.8, 97.0]
    blocks = find_order_blocks(o, h, l, c, disp_atr_mult=1.5, atr=[1.0] * 4)
    assert blocks, "displacement bar should seed an order block"
    assert blocks[0].direction == "bearish"
    assert (blocks[0].top, blocks[0].bottom) == (101.8, 101.0)
    assert blocks[0].mitigated is False
    # Later close through the boundary mitigates it
    blocks2 = find_order_blocks(o + [97.0], h + [103.0], l + [96.0], c + [102.5],
                                disp_atr_mult=1.5, atr=[1.0] * 5)
    assert blocks2 and blocks2[0].mitigated is True


def test_liquidity_pools_and_premium():
    h = [100.0, 100.02, 101.0, 100.01, 102.0]
    l = [99.0, 98.0, 97.5, 98.02, 99.5]
    pools = liquidity_pools(h, l, tol_frac=0.0005, lookback=5)
    assert pools["EQH"], "near-equal highs must pool"
    assert range_position(50.0, 0.0, 100.0) == 0.5
    assert is_premium(70.0, 0.0, 100.0) is True
    assert is_premium(30.0, 0.0, 100.0) is False


def test_expectancy_math_matches_paper():
    assert breakeven_rr(0.40) == 1.5
    assert expectancy_per_r(0.40, 1.5) == 0.0
    assert expectancy_per_r(0.40, 2.0) == 0.2
    assert expectancy_per_r(0.40, 3.0) == 0.6
    assert expectancy_per_r(0.40, 4.0) == 1.0
    assert rr_gate(1.5) and not rr_gate(1.49)


def test_confidence_calibration_caps():
    assert calibrate_confidence(9, choch=True) <= 6
    assert calibrate_confidence(9, premium=True) <= 6
    assert calibrate_confidence(9, mtf_aligned=False) <= 6
    assert calibrate_confidence(9, rr=1.2) <= 6
    assert calibrate_confidence(9) == 9
    assert action_gate(5) == "WAIT"
    assert action_gate(6) == "PROCEED"


def test_loss_clamp_and_breakout():
    assert loss_clamp_active([-1, 1, -1, -1]) is True
    assert loss_clamp_active([1, 1, -1, 1]) is False
    assert clamped_confidence(9, [-1, -1, -1]) == 5
    assert breakout_ok(6.0, 10.0, 1500.0, 1000.0) is True   # body 60%, vol 1.5x
    assert breakout_ok(4.0, 10.0, 1500.0, 1000.0) is False  # body 40%
    assert breakout_ok(6.0, 10.0, 1100.0, 1000.0) is False  # vol 1.1x


def test_regime_table_with_unknown_default():
    assert classify_regime(adx=30, ema_bull=True) is TwinRegime.TRENDING_BULL
    assert classify_regime(adx=30, ema_bear=True) is TwinRegime.TRENDING_BEAR
    assert classify_regime(adx=15, bb_contracting=True) is TwinRegime.RANGING
    assert classify_regime(adx=30, atr=5.0, atr_p85=4.0) is TwinRegime.VOLATILE
    assert classify_regime(adx=30, bb_expanding=True, bos_bull=True) is TwinRegime.BREAKOUT
    # Mid-ADX ambiguity -> UNKNOWN, never RANGING (paper improvement)
    assert classify_regime(adx=22) is TwinRegime.UNKNOWN


def test_validate_proposal_twinalgo_calibration(monkeypatch):
    """Integration test: _validate_proposal calibrates confidence and clamps on CHoCH / losses."""
    from unittest.mock import MagicMock
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.model_name = "test_model"
    orch.run_id = "test_run"
    orch.store = MagicMock()
    orch.operations = MagicMock()
    orch.risk = MagicMock()
    orch.risk.consecutive_losses = 0
    orch.risk.open_positions = []
    orch.risk.risk_per_trade = 0.01
    orch.risk.last_trade_time = None
    orch.perf = MagicMock()
    orch.perf.recent_outcomes = []
    orch.perf.calc_daily_pnl_pct.return_value = 0.0
    orch.validator = MagicMock()
    orch.validator.confluence.return_value = 5
    orch.validator.min_rr = 1.5
    orch.validator.validate.return_value = MagicMock(approved=True, reasons=[], confluence_score=5)
    orch.overfit_gate = None
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])

    monkeypatch.setattr("intelligence.llm_orchestrator._resolve_symbol",
                        lambda name, default: (lambda f, s, st: {"eligible": True, "setup_id": "test_setup", "reason": "ok"}) if name == "classify_setup" else default)
    monkeypatch.setattr("intelligence.llm_orchestrator.validate_feature_schema", lambda s, require_ready: {"valid": True, "reasons": []})

    decision = {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 98.0,
        "take_profit": 106.0,
        "confidence": 0.9,  # 9/10
    }
    # Case 1: Normal proposal -> 9/10 (MTF aligned)
    toon = {
        "ohlc": [
            {"open": 98, "high": 101, "low": 97, "close": 99, "tick_volume": 1000},
            {"open": 99, "high": 102, "low": 98, "close": 100, "tick_volume": 1200},
        ],
        "multi_timeframe": {"H1": {"trend": "BULLISH"}},
        "feature_snapshot": {"ready": False},
        "smc_structure": {"has_choch": False, "in_premium": False},
    }
    proposal, result = orch._validate_proposal(decision, toon, "EURUSD", "test_strat", paper_mode=True)
    assert proposal is not None
    assert proposal.features["twinalgo_calibrated_conf"] == 9
    assert proposal.confidence == 0.9

    # Case 2: CHoCH detected -> confidence capped at 6 (0.6)
    toon_choch = {
        "ohlc": toon["ohlc"],
        "multi_timeframe": {"H1": {"trend": "BULLISH"}},
        "feature_snapshot": {"ready": False},
        "smc_structure": {"has_choch": True, "in_premium": False},
    }
    prop_choch, _ = orch._validate_proposal(decision, toon_choch, "EURUSD", "test_strat", paper_mode=True)
    assert prop_choch is not None
    assert prop_choch.features["twinalgo_calibrated_conf"] == 6
    assert prop_choch.confidence == 0.6

    # Case 3: Consecutive losses >= 3 -> confidence clamped to 5 (0.5)
    orch.risk.consecutive_losses = 3
    prop_loss, _ = orch._validate_proposal(decision, toon_choch, "EURUSD", "test_strat", paper_mode=True)
    assert prop_loss is not None
    assert prop_loss.features["twinalgo_calibrated_conf"] == 5
    assert prop_loss.confidence == 0.5


def test_validate_proposal_twinalgo_breakout_filter(monkeypatch):
    """Integration test: false breakout rejected by TwinAlgo breakout filter."""
    from unittest.mock import MagicMock
    from intelligence.llm_orchestrator import LLMOrchestrator

    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.model_name = "test_model"
    orch.run_id = "test_run"
    orch.store = MagicMock()
    orch.operations = MagicMock()
    orch.risk = MagicMock()
    orch.risk.consecutive_losses = 0
    orch.risk.open_positions = []
    orch.risk.risk_per_trade = 0.01
    orch.risk.last_trade_time = None
    orch.perf = MagicMock()
    orch.perf.recent_outcomes = []
    orch.perf.calc_daily_pnl_pct.return_value = 0.0
    orch.validator = MagicMock()
    orch.validator.confluence.return_value = 5
    orch.validator.min_rr = 1.5
    orch.overfit_gate = None
    orch.data_quality = MagicMock()
    orch.data_quality.validate.return_value = MagicMock(passed=True, reasons=[])

    decision = {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 98.0,
        "take_profit": 106.0,
        "confidence": 0.8,
    }
    # Breakout setup with small body (30% range) and low volume (<1.3x)
    toon = {
        "ohlc": [
            {"open": 98, "high": 108, "low": 98, "close": 101, "tick_volume": 1000},
            {"open": 99, "high": 100, "low": 98, "close": 100, "tick_volume": 1000},
        ],
        "multi_timeframe": {"H1": {"trend": "BULLISH"}},
        "feature_snapshot": {
            "ready": True,
            "version": "1.0",
            "values": {"avg_volume_20": 1000.0, "asian_range_breakout_up": True},
        },
        "technical_analysis": {"primary_trend": "TRENDING_BULL"},
    }
    monkeypatch.setattr("intelligence.llm_orchestrator.validate_feature_schema", lambda s, require_ready: {"valid": True, "reasons": []})
    monkeypatch.setattr("intelligence.llm_orchestrator._resolve_symbol",
                        lambda name, default: (lambda f, s, st: {"eligible": True, "setup_id": "breakout_v1", "reason": "ok"}) if name == "classify_setup" else default)

    proposal, result = orch._validate_proposal(decision, toon, "EURUSD", "breakout_strat", paper_mode=True)
    assert proposal is not None
    assert not result.approved
    assert proposal.features.get("twinalgo_breakout_rejected") is True



