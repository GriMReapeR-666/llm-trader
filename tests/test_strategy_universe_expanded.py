import numpy as np
import polars as pl
import pytest

from core.strategy_universe import (
    CapitalScale,
    MarketRegime,
    StrategySpec,
    StrategyStyle,
    StrategyUniverseGenerator,
    TradingType,
    evaluate_strategy_universe,
    load_market_dataset_polars,
)


@pytest.fixture
def sample_market_data() -> pl.DataFrame:
    np.random.seed(42)
    n = 300
    returns = np.random.normal(0.0002, 0.008, n)
    # Add a trend, then a cycle, then volatility shock
    returns[50:120] += 0.005
    returns[120:180] -= 0.004
    prices = 1.1000 * np.exp(np.cumsum(returns))
    highs = prices * (1.0 + np.abs(np.random.normal(0, 0.002, n)))
    lows = prices * (1.0 - np.abs(np.random.normal(0, 0.002, n)))
    volumes = np.random.randint(500, 5000, n).astype(float)
    return pl.DataFrame({
        "close": prices,
        "high": highs,
        "low": lows,
        "volume": volumes,
    })


def test_all_15_styles_generate_distinct_signals(sample_market_data):
    generator = StrategyUniverseGenerator(seed=42)
    style_signals = {}

    for style in StrategyStyle:
        spec = StrategySpec(
            strategy_id=f"test_{style.value.lower()}",
            name=f"Test {style.value}",
            style=style,
            trading_type=TradingType.LONG_SHORT,
            regime_suitability=[MarketRegime.BULL_TREND],
            target_symbols=["EURUSD"],
            timeframes=["H1"],
            parameters={"fast_period": 10, "slow_period": 30, "threshold": 2.0},
        )
        sig = generator.generate_baseline_signals(spec, sample_market_data)
        assert len(sig) == sample_market_data.height
        assert np.isin(sig, [-1, 0, 1]).all(), f"Signal for {style} contains invalid values: {sig}"
        # Store non-zero signal signatures to test differentiation
        style_signals[style] = sig

    # Verify that different styles produce distinct signals (not all falling back to generic breakout)
    distinct_pairs = 0
    styles_list = list(StrategyStyle)
    for i in range(len(styles_list)):
        for j in range(i + 1, len(styles_list)):
            s1, s2 = styles_list[i], styles_list[j]
            if not np.array_equal(style_signals[s1], style_signals[s2]):
                distinct_pairs += 1

    total_pairs = (len(styles_list) * (len(styles_list) - 1)) // 2
    # At least 85% of style pairs must have distinct signal sequences
    assert distinct_pairs / total_pairs >= 0.85, f"Too many duplicate signals: {distinct_pairs}/{total_pairs} distinct"


def test_trading_type_filters(sample_market_data):
    generator = StrategyUniverseGenerator(seed=42)

    # Test Long-Only vs Short-Only vs Long-Short
    spec_long = StrategySpec(
        strategy_id="test_long_only",
        name="Long Only",
        style=StrategyStyle.TREND_FOLLOWING,
        trading_type=TradingType.LONG_ONLY,
        regime_suitability=[MarketRegime.BULL_TREND],
        target_symbols=["EURUSD"],
        timeframes=["H1"],
    )
    sig_long = generator.generate_baseline_signals(spec_long, sample_market_data)
    assert np.all(sig_long >= 0), "LONG_ONLY produced short signals"

    spec_short = StrategySpec(
        strategy_id="test_short_only",
        name="Short Only",
        style=StrategyStyle.TREND_FOLLOWING,
        trading_type=TradingType.SHORT_ONLY,
        regime_suitability=[MarketRegime.BEAR_TREND],
        target_symbols=["EURUSD"],
        timeframes=["H1"],
    )
    sig_short = generator.generate_baseline_signals(spec_short, sample_market_data)
    assert np.all(sig_short <= 0), "SHORT_ONLY produced long signals"

    spec_ls = StrategySpec(
        strategy_id="test_long_short",
        name="Long Short",
        style=StrategyStyle.TREND_FOLLOWING,
        trading_type=TradingType.LONG_SHORT,
        regime_suitability=[MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND],
        target_symbols=["EURUSD"],
        timeframes=["H1"],
    )
    sig_ls = generator.generate_baseline_signals(spec_ls, sample_market_data)
    assert 1 in sig_ls and -1 in sig_ls, "LONG_SHORT should contain both long and short"


def test_generate_full_matrix():
    generator = StrategyUniverseGenerator(seed=42)
    symbols = ["EURUSD", "GBPUSD", "XAUUSD"]
    timeframes = ["M15", "H1"]
    scales = [CapitalScale.MICRO, CapitalScale.SMALL]

    matrix = generator.generate_full_matrix(symbols=symbols, timeframes=timeframes, scales=scales)
    assert len(matrix) >= 30
    assert all(isinstance(spec, StrategySpec) for spec in matrix)

    styles_present = {s.style for s in matrix}
    assert len(styles_present) == 15, f"Expected 15 styles, got {len(styles_present)}"

    # Check regime suitability tagging
    for spec in matrix:
        assert len(spec.regime_suitability) >= 1
        assert all(isinstance(r, MarketRegime) for r in spec.regime_suitability)


def test_evaluate_full_matrix_performance_and_telemetry(sample_market_data, caplog):
    import logging
    generator = StrategyUniverseGenerator(seed=42)
    matrix = generator.generate_full_matrix(symbols=["EURUSD"], timeframes=["H1"])

    with caplog.at_level(logging.INFO):
        report = evaluate_strategy_universe(
            matrix,
            sample_market_data,
            spread_bps=2.0,
            slippage_bps=1.0,
            initial_capital=10000.0,
            emit_telemetry=True,
            log_diagnostics=True,
        )

    assert report.total_strategies == len(matrix)
    assert len(report.results) == len(matrix)

    # Verify metrics fields exist and are finite
    for res in report.results:
        assert "sharpe" in res
        assert "sortino" in res
        assert "max_drawdown_pct" in res
        assert "win_rate_pct" in res
        assert "profit_factor" in res
        assert "compute_latency_ms" in res
        assert np.isfinite(res["sharpe"])
        assert np.isfinite(res["max_drawdown_pct"])

    # Verify structured logs were output
    log_text = caplog.text.upper()
    assert "STRATEGY UNIVERSE MATRIX EVALUATION" in log_text or "UNIVERSE SWEEP" in log_text
