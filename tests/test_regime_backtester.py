import numpy as np
import polars as pl
import pytest

from core.regime_backtester import (
    RegimeClassifier,
    MultiPeriodSplitter,
    compute_15_metrics,
    PnLDistribution,
    StrategyRegimeMetrics,
    RegimeBacktestPipeline,
    RegimeBacktestReport,
)
from core.strategy_universe import StrategyUniverseGenerator, StrategyStyle, TradingType


def test_regime_classifier_synthetic_slices():
    # 1. Synthesize market data with 3 distinct regimes:
    # Bars 0-100: strong bull uptrend (+20%)
    # Bars 100-200: sideways flat range
    # Bars 200-300: steep crash (-15%)
    n = 300
    prices = np.zeros(n, dtype=np.float64)
    prices[:100] = np.linspace(100.0, 125.0, 100)
    prices[100:200] = 125.0 + np.sin(np.linspace(0, 4 * np.pi, 100)) * 0.5
    prices[200:300] = np.linspace(125.0, 95.0, 100)

    df = pl.DataFrame({
        "close": prices,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "volume": np.full(n, 1000.0),
    })

    classifier = RegimeClassifier()
    labels = classifier.classify(df)

    assert len(labels) == n
    assert isinstance(labels, np.ndarray)
    unique_labels = set(labels)
    # Check that bull, bear/stress, and sideways are identified
    assert "BULL" in unique_labels or "LOW_VOL" in unique_labels
    assert "BEAR" in unique_labels or "STRESS" in unique_labels or "HIGH_VOL" in unique_labels


def test_multi_period_splitter():
    splitter = MultiPeriodSplitter(is_ratio=0.50, val_ratio=0.25, oos_ratio=0.25)
    splits = splitter.split(n_bars=1000)

    assert splits.is_range == (0, 500)
    assert splits.val_range == (500, 750)
    assert splits.oos_range == (750, 1000)

    wfo_splits = splitter.get_wfo_splits(n_bars=1000, n_folds=4, purge_bars=10)
    assert len(wfo_splits) == 4
    for fold in wfo_splits:
        train_end = fold["train_range"][1]
        test_start = fold["test_range"][0]
        assert test_start - train_end == 10  # Purge gap verified


def test_compute_15_metrics():
    # Synthetic trade equity and returns
    np.random.seed(42)
    n = 200
    returns = np.random.normal(0.001, 0.01, n)
    equity = np.cumprod(1.0 + returns) * 10000.0
    signals = np.random.choice([-1, 0, 1], size=n)

    metrics = compute_15_metrics(
        equity=equity,
        returns=returns,
        signals=signals,
        spread_bps=2.0,
        slippage_bps=1.0,
    )

    assert isinstance(metrics, StrategyRegimeMetrics)
    assert hasattr(metrics, "total_return_pct")
    assert hasattr(metrics, "annualized_volatility")
    assert hasattr(metrics, "sharpe")
    assert hasattr(metrics, "sortino")
    assert hasattr(metrics, "calmar")
    assert hasattr(metrics, "max_drawdown_pct")
    assert hasattr(metrics, "profit_factor")
    assert hasattr(metrics, "win_rate_pct")
    assert hasattr(metrics, "win_loss_ratio")
    assert hasattr(metrics, "expectancy")
    assert hasattr(metrics, "turnover")
    assert hasattr(metrics, "exposure_pct")
    assert hasattr(metrics, "avg_trade_duration_bars")
    assert hasattr(metrics, "slippage_adjusted_return_pct")
    assert hasattr(metrics, "pnl_distribution")

    pnl = metrics.pnl_distribution
    assert isinstance(pnl, PnLDistribution)
    assert hasattr(pnl, "mean_return")
    assert hasattr(pnl, "std_return")
    assert hasattr(pnl, "skewness")
    assert hasattr(pnl, "kurtosis")
    assert hasattr(pnl, "var_95")
    assert hasattr(pnl, "cvar_95")


def test_regime_backtest_pipeline_end_to_end():
    np.random.seed(42)
    n = 600
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = pl.DataFrame({
        "close": prices,
        "high": prices * 1.002,
        "low": prices * 0.998,
        "volume": np.full(n, 1000.0),
    })

    gen = StrategyUniverseGenerator()
    specs = gen.generate_matrix(symbols=["EURUSD"])
    test_spec = specs[0]

    pipeline = RegimeBacktestPipeline(emit_telemetry=True)
    report = pipeline.run_backtest(test_spec, df)

    assert isinstance(report, RegimeBacktestReport)
    assert report.strategy_id == test_spec.strategy_id
    assert report.total_bars == n
    assert report.execution_authority is False
    assert isinstance(report.overall_metrics, StrategyRegimeMetrics)
    assert isinstance(report.is_metrics, StrategyRegimeMetrics)
    assert isinstance(report.val_metrics, StrategyRegimeMetrics)
    assert isinstance(report.oos_metrics, StrategyRegimeMetrics)
    assert isinstance(report.regime_breakdown, dict)
    assert isinstance(report.walk_forward_efficiency, float)
    assert isinstance(report.wfe_passed, bool)
    assert isinstance(report.stress_survival_passed, bool)
