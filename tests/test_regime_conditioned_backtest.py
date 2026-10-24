"""Unit tests for Stage 1 Backtest Depth & Regime-Conditioned Backtesting (Flag 3)."""
import tempfile
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest

from research.continuous_backtest_pipeline import ContinuousBacktestPipeline


def _generate_synthetic_ohlc(n: int = 3000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # First 1500 bars: mean-reverting sideways chop
    p0 = 1.0850
    prices_chop = [p0]
    for _ in range(1499):
        # Mean reversion pull towards p0
        drift = -0.05 * (prices_chop[-1] - p0)
        ret = drift + rng.normal(0, 0.0003)
        prices_chop.append(prices_chop[-1] * (1.0 + ret))
    
    # Next 1500 bars: strong trending bull market
    prices_trend = [prices_chop[-1]]
    for _ in range(1499):
        ret = 0.0005 + rng.normal(0, 0.0002)
        prices_trend.append(prices_trend[-1] * (1.0 + ret))
        
    prices = np.array(prices_chop + prices_trend, dtype=np.float64)
    highs = prices * (1.0 + np.abs(rng.normal(0, 0.0002, len(prices))))
    lows = prices * (1.0 - np.abs(rng.normal(0, 0.0002, len(prices))))
    opens = np.roll(prices, 1)
    opens[0] = prices[0]
    
    times = pd.date_range("2026-01-01", periods=len(prices), freq="1h")
    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": prices,
        "tick_volume": np.full(len(prices), 1000),
    }, index=times)
    return df


def test_continuous_backtest_depth_defaults():
    """Verify ContinuousBacktestPipeline defaults to >= 3000 bars for statistical depth."""
    pipe = ContinuousBacktestPipeline(base_dir=tempfile.mkdtemp())
    assert pipe._last_bars >= 3000 or pipe.run_validation_cycle.__defaults__[3] >= 3000


def test_classify_bars_regimes():
    """Bar classification identifies mean-reverting vs trending regimes."""
    df = _generate_synthetic_ohlc(3000)
    regimes = ContinuousBacktestPipeline._classify_bars_regimes(df)
    assert len(regimes) == len(df)
    
    # First half should have substantial MEAN_REVERTING
    first_half_chop = sum(1 for r in regimes[:1500] if r in ("MEAN_REVERTING", "LOW_VOL_RANGE"))
    assert first_half_chop > 500
    
    # Second half should have substantial BULL_TREND
    second_half_trend = sum(1 for r in regimes[1500:] if r in ("BULL_TREND", "HIGH_VOL_TREND", "LOW_VOL_TREND"))
    assert second_half_trend > 500


def test_statarb_caller_bars_symbol_mismatch():
    """When caller passes EURUSD bars but strategy is statarb_eur_chf, pipeline re-fetches for EURCHF."""
    pipe = ContinuousBacktestPipeline(base_dir=tempfile.mkdtemp())
    eurusd_bars = _generate_synthetic_ohlc(500)
    
    fetched_symbols = []
    def fake_fetch_ohlc(sym, tf, count):
        fetched_symbols.append((sym, count))
        return _generate_synthetic_ohlc(count)
        
    with patch("data.market_data_provider.fetch_ohlc", side_effect=fake_fetch_ohlc):
        with patch.object(pipe, "_run_pipeline_backtest", return_value={"status": "OK", "sharpe": 1.2, "total_trades": 80, "data_source": "mt5_history"}):
            with patch.object(pipe, "_run_dsr_gate", return_value={"status": "OK", "passed": True}):
                with patch.object(pipe, "_run_walkforward", return_value={"status": "OK"}):
                    with patch.object(pipe, "_stage_cpcv", return_value={"status": "CPCV_VALIDATED", "probability_of_backtest_overfitting_pbo": 0.1, "is_statistically_robust": True}):
                        with patch.object(pipe, "_compare_live", return_value={"status": "OK"}):
                            pipe.run_full_validation(
                                symbol="EURUSD",
                                strategy_name="statarb_eur_chf",
                                bars_df=eurusd_bars,
                                data_source="mt5_history",
                            )
                            
    # Must have refetched EURCHF with count >= 3000 because caller bars were for EURUSD
    assert any(s == "EURCHF" and c >= 3000 for s, c in fetched_symbols)


def test_regime_conditioned_signal_filtering():
    """Regime-conditioned signal filtering mutes mean-reversion entries during adverse trend regimes."""
    pipe = ContinuousBacktestPipeline(base_dir=tempfile.mkdtemp())
    df = _generate_synthetic_ohlc(3000)
    
    raw_scores = np.ones(len(df), dtype=np.float64)
    filtered = pipe._apply_regime_filter_to_scores(
        strategy_name="statarb_eur_chf",
        scores=raw_scores,
        bars_df=df,
    )
    assert len(filtered) == len(raw_scores)
    
    # In the strong bull trend section (second half), scores should be filtered (0.0)
    filtered_trend_zeros = sum(1 for s in filtered[1500:] if s == 0.0)
    assert filtered_trend_zeros > 500
