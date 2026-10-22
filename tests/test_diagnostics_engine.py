import numpy as np
import polars as pl
import pytest
from core.diagnostics_engine import (
    DataIntegrityChecker,
    DataIntegrityReport,
    SignalQualityChecker,
    SignalQualityReport,
    ExecutionFeasibilityChecker,
    ExecutionCheckReport,
    PortfolioFitChecker,
    PortfolioFitReport,
)


def test_data_integrity_checker():
    # Valid data
    df_clean = pl.DataFrame({"close": [100.0, 101.0, 102.0, 101.5, 103.0]})
    checker = DataIntegrityChecker()
    rep = checker.check(df_clean)
    assert isinstance(rep, DataIntegrityReport)
    assert rep.is_clean is True
    assert len(rep.issues) == 0
    assert rep.execution_authority is False

    # Stale data (12 identical bars)
    df_stale = pl.DataFrame({"close": [100.0] * 12})
    rep_stale = checker.check(df_stale)
    assert rep_stale.is_clean is False
    assert any("stale" in i.lower() for i in rep_stale.issues)

    # Empty dataframe
    df_empty = pl.DataFrame({"close": []})
    rep_empty = checker.check(df_empty)
    assert rep_empty.is_clean is False
    assert any("empty" in i.lower() for i in rep_empty.issues)

    # Missing close column
    df_noclose = pl.DataFrame({"volume": [100, 200]})
    rep_noclose = checker.check(df_noclose)
    assert rep_noclose.is_clean is False
    assert any("missing 'close'" in i.lower() for i in rep_noclose.issues)

    # Non-positive prices
    df_negative = pl.DataFrame({"close": [10.0, -1.0, 12.0]})
    rep_negative = checker.check(df_negative)
    assert rep_negative.is_clean is False
    assert any("non-positive" in i.lower() for i in rep_negative.issues)


def test_signal_quality_checker():
    checker = SignalQualityChecker()
    # Good steady returns
    returns = np.array([0.01, 0.005, 0.012, 0.008, 0.01, 0.007, 0.009, 0.011], dtype=np.float64)
    equity = np.cumprod(1.0 + returns) * 10000.0
    regime_rets = {
        "BULL": np.array([0.01, 0.02, 0.015]),
        "BEAR": np.array([0.005, 0.002, 0.008]),
    }
    rep = checker.check(equity, returns, regime_rets)
    assert isinstance(rep, SignalQualityReport)
    assert rep.passed is True
    assert rep.regime_consistency_pct >= 50.0
    assert rep.execution_authority is False

    # Small sample size
    rep_small = checker.check(np.array([100.0, 101.0]), np.array([0.01, 0.01]))
    assert rep_small.passed is False
    assert any("sample size" in i.lower() for i in rep_small.issues)

    # Low regime consistency
    bad_regimes = {
        "BULL": np.array([0.01, -0.02]),
        "BEAR": np.array([-0.01, -0.02]),
        "SIDEWAYS": np.array([-0.005, -0.001]),
    }
    rep_bad_regime = checker.check(equity, returns, bad_regimes)
    assert rep_bad_regime.passed is False
    assert rep_bad_regime.regime_consistency_pct < 50.0


def test_execution_feasibility_checker():
    checker = ExecutionFeasibilityChecker()
    # Profitable steady upward prices with long signal
    prices = np.linspace(100.0, 120.0, 100)
    signals = np.ones(100, dtype=np.int32)
    rep = checker.check(prices, signals)
    assert isinstance(rep, ExecutionCheckReport)
    assert rep.is_viable is True
    assert rep.slippage_degradation_pct < 60.0
    assert rep.execution_authority is False

    # Profitable strategy that collapses under 3x slippage
    # Frequent switching (high turnover) where slippage kills returns
    frequent_signals = np.array([1, -1] * 50, dtype=np.int32)
    flat_prices = 100.0 + np.random.RandomState(42).normal(0, 0.01, 100)
    rep_decay = checker.check(flat_prices, frequent_signals)
    assert isinstance(rep_decay, ExecutionCheckReport)
    assert rep_decay.is_viable is False

    # Check with StrategySpec and DataFrame
    from core.strategy_universe import StrategyUniverseGenerator
    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])
    df = pl.DataFrame({"close": prices})
    rep_spec = checker.check(specs[0], df, generator=generator)
    assert isinstance(rep_spec, ExecutionCheckReport)
    assert rep_spec.execution_authority is False


def test_portfolio_fit_checker():
    checker = PortfolioFitChecker()
    strat_rets = np.array([0.01, -0.005, 0.02, 0.01, -0.01])
    # Uncorrelated portfolio
    port_rets = np.array([-0.01, 0.01, -0.01, 0.02, 0.005])
    rep = checker.check(strat_rets, port_rets)
    assert isinstance(rep, PortfolioFitReport)
    assert rep.fit_passed is True
    assert rep.max_correlation < 0.70
    assert rep.execution_authority is False

    # Highly correlated portfolio
    high_corr_port = strat_rets * 1.5 + 0.001
    rep_corr = checker.check(strat_rets, high_corr_port)
    assert rep_corr.fit_passed is False
    assert rep_corr.max_correlation > 0.70
    assert any("high portfolio correlation" in i.lower() for i in rep_corr.issues)

    # None portfolio returns
    rep_none = checker.check(strat_rets, None)
    assert rep_none.fit_passed is True
    assert rep_none.max_correlation == 0.0


def test_diagnostics_pipeline_phase1_and_phase2():
    from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars
    from core.diagnostics_engine import DiagnosticsPipeline

    np.random.seed(42)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])

    pipeline = DiagnosticsPipeline()
    phase1_survivors = pipeline.run_phase1_quick_pass(specs, df)
    assert isinstance(phase1_survivors, list)
    assert len(phase1_survivors) <= len(specs)

    phase2_survivors = pipeline.run_phase2_tier_audit(phase1_survivors, df)
    assert isinstance(phase2_survivors, list)
    assert len(phase2_survivors) <= len(phase1_survivors)

