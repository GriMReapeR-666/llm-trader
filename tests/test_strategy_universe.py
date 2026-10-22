import pytest
from core.strategy_universe import (
    StrategyStyle,
    TradingType,
    MarketRegime,
    CapitalScale,
    StrategySpec,
)

def test_taxonomy_dimensions():
    assert len(StrategyStyle) == 15
    assert len(TradingType) == 12
    assert len(MarketRegime) == 6
    assert len(CapitalScale) == 4

def test_strategy_spec_creation():
    spec = StrategySpec(
        strategy_id="trend_ema_h1_eurusd",
        name="Dual EMA Trend Following",
        style=StrategyStyle.TREND_FOLLOWING,
        trading_type=TradingType.LONG_SHORT,
        regime_suitability=[MarketRegime.BULL_TREND, MarketRegime.BEAR_TREND],
        target_symbols=["EURUSD"],
        timeframes=["H1"],
        capital_scale=CapitalScale.SMALL,
    )
    assert spec.strategy_id == "trend_ema_h1_eurusd"
    assert spec.execution_authority is False
    d = spec.to_dict()
    assert d["style"] == "TREND_FOLLOWING"
    assert d["trading_type"] == "LONG_SHORT"


def test_numba_indicators_and_simulation():
    from core.strategy_universe import (
        compute_ema_numba,
        compute_bollinger_bands_numba,
        simulate_positions_numba,
        calculate_fast_metrics_numba,
    )
    import numpy as np

    prices = np.array([100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 103.5, 105.0], dtype=np.float64)
    ema = compute_ema_numba(prices, 3)
    assert len(ema) == len(prices)
    assert ema[-1] > ema[0]

    upper, mid, lower = compute_bollinger_bands_numba(prices, 3, 2.0)
    assert upper[-1] > mid[-1] > lower[-1]

    # Signal: 1 = Long, -1 = Short, 0 = Flat
    signals = np.array([0, 1, 1, 1, 0, -1, -1, 0], dtype=np.int32)
    equity, trade_pnls, trade_count, total_ret, max_dd = simulate_positions_numba(
        prices, signals, spread_bps=1.0, slippage_bps=0.5, initial_capital=10000.0
    )
    assert len(equity) == len(prices)
    assert trade_count >= 1

    sharpe, sortino, calmar, win_rate, profit_factor, max_dd_pct = calculate_fast_metrics_numba(
        equity, np.diff(equity) / equity[:-1]
    )
    assert isinstance(sharpe, float)
    assert max_dd_pct >= 0.0

    # Edge cases: empty arrays
    empty_prices = np.array([], dtype=np.float64)
    empty_ema = compute_ema_numba(empty_prices, 3)
    assert len(empty_ema) == 0

    empty_eq, empty_pnls, empty_trades, empty_ret, empty_dd = simulate_positions_numba(
        empty_prices, np.array([], dtype=np.int32)
    )
    assert len(empty_eq) == 0
    assert empty_trades == 0

    # Metrics with fewer than 2 return data points
    m_sharpe, m_sortino, m_calmar, m_win, m_pf, m_dd = calculate_fast_metrics_numba(
        np.array([10000.0], dtype=np.float64), np.array([], dtype=np.float64)
    )
    assert m_sharpe == 0.0


def test_polars_data_and_universe_generator():
    from core.strategy_universe import (
        StrategyUniverseGenerator,
        load_market_dataset_polars,
        StrategyStyle,
        TradingType,
    )
    import polars as pl
    import numpy as np

    dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
    prices = [1.1000, 1.1050, 1.1020, 1.1080, 1.1120]
    raw_data = {"date": dates, "close": prices, "volume": [1000, 1200, 950, 1400, 1600]}

    df = load_market_dataset_polars(raw_data)
    assert isinstance(df, pl.DataFrame)
    assert df.height == 5
    assert "close" in df.columns
    assert df["close"].dtype == pl.Float64

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(
        symbols=["EURUSD", "GBPUSD"],
        timeframes=["H1", "D1"],
    )
    # Spans all 15 styles
    assert len(specs) >= 15
    styles_present = {s.style for s in specs}
    for style in StrategyStyle:
        assert style in styles_present

    # Test baseline signals for TREND_FOLLOWING
    trend_spec = next(s for s in specs if s.style == StrategyStyle.TREND_FOLLOWING)
    signals_trend = generator.generate_baseline_signals(trend_spec, df)
    assert len(signals_trend) == df.height
    assert set(np.unique(signals_trend)).issubset({-1, 0, 1})

    # Test baseline signals for MEAN_REVERSION
    mr_spec = next(s for s in specs if s.style == StrategyStyle.MEAN_REVERSION)
    signals_mr = generator.generate_baseline_signals(mr_spec, df)
    assert len(signals_mr) == df.height
    assert set(np.unique(signals_mr)).issubset({-1, 0, 1})

    # Test baseline signals for BREAKOUT (other styles)
    bo_spec = next(s for s in specs if s.style == StrategyStyle.BREAKOUT)
    signals_bo = generator.generate_baseline_signals(bo_spec, df)
    assert len(signals_bo) == df.height
    assert set(np.unique(signals_bo)).issubset({-1, 0, 1})

    # Test fail-closed execution authority
    for s in specs:
        assert s.execution_authority is False

    # Test LONG_ONLY style does not generate short (-1) signals
    carry_spec = next(s for s in specs if s.style == StrategyStyle.CARRY and s.trading_type == TradingType.LONG_ONLY)
    signals_carry = generator.generate_baseline_signals(carry_spec, df)
    assert -1 not in signals_carry

    # Edge cases: empty DataFrame
    empty_df = load_market_dataset_polars({"close": []})
    assert empty_df.height == 0
    signals_empty = generator.generate_baseline_signals(trend_spec, empty_df)
    assert len(signals_empty) == 0

    # Default generate_matrix parameters
    default_specs = generator.generate_matrix()
    assert len(default_specs) >= 15
    assert default_specs[0].target_symbols == ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD"]


def test_evaluate_strategy_universe():
    import numpy as np
    import polars as pl
    from unittest.mock import MagicMock
    import sys
    from core.strategy_universe import (
        StrategyUniverseGenerator,
        load_market_dataset_polars,
        UniverseEvaluationReport,
        evaluate_strategy_universe,
    )

    # Generate synthetic price series
    np.random.seed(42)
    n = 200
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"], timeframes=["H1"])[:5]

    # Test baseline evaluation without telemetry
    report = evaluate_strategy_universe(specs, df, emit_telemetry=False)
    assert isinstance(report, UniverseEvaluationReport)
    assert report.total_strategies == 5
    assert len(report.results) == 5
    assert report.execution_authority is False
    assert report.evaluated_at > 0.0

    d = report.to_dict()
    assert d["total_strategies"] == 5
    assert d["execution_authority"] is False
    assert len(d["results"]) == 5

    first_result = report.results[0]
    expected_keys = {
        "strategy_id",
        "style",
        "trading_type",
        "total_trades",
        "total_return_pct",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown_pct",
        "win_rate_pct",
        "profit_factor",
        "compute_latency_ms",
    }
    assert expected_keys.issubset(first_result.keys())
    assert isinstance(first_result["sharpe"], float)
    assert isinstance(first_result["max_drawdown_pct"], float)
    assert isinstance(first_result["compute_latency_ms"], float)
    assert first_result["compute_latency_ms"] >= 0.0

    # Test telemetry emission when emit_telemetry=True and emit_agent_event exists
    mock_emit = MagicMock()
    import core.agent_event_bus as aeb
    orig_emit = getattr(aeb, "emit_agent_event", None)
    try:
        aeb.emit_agent_event = mock_emit
        report_telemetry = evaluate_strategy_universe(specs[:2], df, emit_telemetry=True)
        assert mock_emit.call_count == 2
        call_args = mock_emit.call_args_list[0]
        kwargs = call_args.kwargs
        assert kwargs.get("sender") == "StrategyUniverseEngine"
        assert kwargs.get("event_type") == "STRATEGY_UNIVERSE_EVALUATED"
        assert "payload" in kwargs
    finally:
        if orig_emit is not None:
            aeb.emit_agent_event = orig_emit
        elif hasattr(aeb, "emit_agent_event"):
            delattr(aeb, "emit_agent_event")

    # Test telemetry not emitted when emit_telemetry=False even if emit_agent_event exists
    try:
        aeb.emit_agent_event = mock_emit
        mock_emit.reset_mock()
        evaluate_strategy_universe(specs[:2], df, emit_telemetry=False)
        assert mock_emit.call_count == 0
    finally:
        if orig_emit is not None:
            aeb.emit_agent_event = orig_emit
        elif hasattr(aeb, "emit_agent_event"):
            delattr(aeb, "emit_agent_event")

    # Test edge case: empty specs
    empty_report = evaluate_strategy_universe([], df, emit_telemetry=False)
    assert empty_report.total_strategies == 0
    assert len(empty_report.results) == 0

    # Test edge case: empty dataset
    empty_df = load_market_dataset_polars({"close": []})
    empty_data_report = evaluate_strategy_universe(specs[:1], empty_df, emit_telemetry=False)
    assert empty_data_report.total_strategies == 1
    assert len(empty_data_report.results) == 1
    assert empty_data_report.results[0]["total_trades"] == 0





