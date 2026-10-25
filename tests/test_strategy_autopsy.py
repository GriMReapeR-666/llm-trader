"""Unit tests for research/strategy_autopsy.py: 7 forensic root-cause signatures."""
import pytest
from research.strategy_autopsy import run_strategy_autopsy


def test_autopsy_insufficient_trades():
    trades = [{"r_multiple": 0.5}] * 10
    res = run_strategy_autopsy(trades)
    assert res["status"] == "INSUFFICIENT_DATA"
    assert res["diagnoses"] == []


def test_autopsy_detects_regime_contamination():
    # 15 trades in LOW_VOL_MEAN_REVERT with +0.6R
    good = [{"r_multiple": 0.6, "regime": "LOW_VOL_MEAN_REVERT"} for _ in range(15)]
    # 15 trades in TREND_EXPANSION with -0.5R
    bad = [{"r_multiple": -0.5, "regime": "TREND_EXPANSION"} for _ in range(15)]
    trades = good + bad

    res = run_strategy_autopsy(trades)
    assert res["status"] == "AUTOPSY_COMPLETE"
    codes = [d["code"] for d in res["diagnoses"]]
    assert "REGIME_CONTAMINATION" in codes

    ops = [m["op"] for m in res["recommended_mutations"]]
    assert "RESTRICT_ALLOWED_REGIMES" in ops


def test_autopsy_detects_inverted_signal_polarity():
    # 25 trades with severe negative R and MAE >> MFE
    trades = [
        {"r_multiple": -0.8, "mae_r": 1.5, "mfe_r": 0.2}
        for _ in range(25)
    ]
    res = run_strategy_autopsy(trades)
    codes = [d["code"] for d in res["diagnoses"]]
    assert "INVERTED_SIGNAL_POLARITY" in codes
    ops = [m["op"] for m in res["recommended_mutations"]]
    assert "INVERT_ENTRY_SIGNAL" in ops


def test_autopsy_detects_premature_stop_out():
    # Losers where 50% rallied to +1.5R post-SL
    trades = [
        {"r_multiple": -1.0, "post_sl_reached_1_5r": (i % 2 == 0)}
        for i in range(25)
    ]
    res = run_strategy_autopsy(trades)
    codes = [d["code"] for d in res["diagnoses"]]
    assert "PREMATURE_STOP_OUT" in codes
    ops = [m["op"] for m in res["recommended_mutations"]]
    assert "WIDEN_SL_ATR_MULT" in ops


def test_autopsy_detects_profit_giveback():
    # Losers that first reached >= +1.2R
    trades = [
        {"r_multiple": -0.5, "mfe_r": 1.4}
        for _ in range(25)
    ]
    res = run_strategy_autopsy(trades)
    codes = [d["code"] for d in res["diagnoses"]]
    assert "PROFIT_GIVEBACK_BLEED" in codes
    ops = [m["op"] for m in res["recommended_mutations"]]
    assert "LOWER_BREAKEVEN_TRIGGER_R" in ops
