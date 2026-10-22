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


