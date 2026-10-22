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


def test_diagnostics_pipeline_phase3_walk_forward():
    from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars
    from core.diagnostics_engine import (
        DiagnosticsPipeline,
        DiagnosticsPipelineReport,
        run_purged_walk_forward,
    )

    np.random.seed(42)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])

    # Test standalone run_purged_walk_forward
    wfo_res = run_purged_walk_forward(specs[0], df, generator, n_splits=4, purge_bars=5)
    assert isinstance(wfo_res, dict)
    assert "strategy_id" in wfo_res
    assert wfo_res["strategy_id"] == specs[0].strategy_id
    assert "mean_is_sharpe" in wfo_res
    assert "mean_oos_sharpe" in wfo_res
    assert "wfe" in wfo_res
    assert "splits" in wfo_res
    assert len(wfo_res["splits"]) == 4
    assert wfo_res["execution_authority"] is False

    # Check splits have purge gap
    for s in wfo_res["splits"]:
        train_end = s["train_range"][1]
        test_start = s["test_range"][0]
        assert test_start - train_end == 5  # purge buffer of 5 bars

    # Test DiagnosticsPipeline Phase 3
    pipeline = DiagnosticsPipeline()
    phase1_survivors = pipeline.run_phase1_quick_pass(specs, df)
    phase2_survivors = pipeline.run_phase2_tier_audit(phase1_survivors, df)

    report = pipeline.run_phase3_walk_forward(phase2_survivors, df, n_splits=4, purge_bars=5)
    assert isinstance(report, DiagnosticsPipelineReport)
    assert report.total_input == len(specs)
    assert report.phase1_survivors == len(phase1_survivors)
    assert report.phase2_survivors == len(phase2_survivors)
    assert len(report.finalists) == len(phase2_survivors)
    assert report.execution_authority is False
    assert report.evaluated_at > 0
    d = report.to_dict()
    assert isinstance(d, dict)
    assert d["total_input"] == len(specs)


def test_full_diagnostics_pipeline_end_to_end():
    from core.agent_event_bus import get_bus
    from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars
    from core.diagnostics_engine import DiagnosticsPipeline, DiagnosticsPipelineReport

    # 1. Generate full 1,000 bar synthetic market data
    np.random.seed(42)
    n = 1000
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = load_market_dataset_polars({"close": prices})
    assert df.height == 1000

    # 2. Generate multi-strategy matrix
    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])
    assert len(specs) > 0

    # 3. Track event bus before running pipeline
    bus = get_bus()
    since_id = bus.latest_id()

    # 4. Run end-to-end 3-phase diagnostics pipeline (1,000 bars simulation)
    pipeline = DiagnosticsPipeline(emit_telemetry=True)
    report = pipeline.run_pipeline(
        specs=specs,
        dataset=df,
        min_sharpe=0.0,
        max_dd=40.0,
        n_splits=4,
        purge_bars=5,
    )

    # 5. Verify report structure and safety invariants
    assert isinstance(report, DiagnosticsPipelineReport)
    assert report.total_input == len(specs)
    assert report.phase1_survivors <= len(specs)
    assert report.phase2_survivors <= report.phase1_survivors
    assert len(report.finalists) == report.phase2_survivors
    assert report.execution_authority is False
    assert report.evaluated_at > 0

    d = report.to_dict()
    assert d["execution_authority"] is False
    assert "finalists" in d

    for finalist in report.finalists:
        assert finalist["execution_authority"] is False
        assert "mean_is_sharpe" in finalist
        assert "mean_oos_sharpe" in finalist
        assert "wfe" in finalist
        assert len(finalist["splits"]) == 4
        for s in finalist["splits"]:
            assert s["test_range"][0] - s["train_range"][1] == 5

    # 6. Verify event bus telemetry emissions for Phase 1, Phase 2, and Phase 3
    events = bus.poll(since_id=since_id, limit=200)
    diag_events = [e for e in events if e.agent == "DiagnosticsPipeline"]
    actions = [e.action for e in diag_events]

    assert "DIAGNOSTIC_PHASE1_COMPLETED" in actions
    assert "DIAGNOSTIC_PHASE2_COMPLETED" in actions
    assert "DIAGNOSTIC_PHASE3_COMPLETED" in actions

    # Verify event payloads match pipeline report metrics
    p1_evt = next(e for e in diag_events if e.action == "DIAGNOSTIC_PHASE1_COMPLETED")
    assert p1_evt.payload["total_tested"] == len(specs)
    assert p1_evt.payload["survivors_count"] == report.phase1_survivors

    p2_evt = next(e for e in diag_events if e.action == "DIAGNOSTIC_PHASE2_COMPLETED")
    assert p2_evt.payload["input_count"] == report.phase1_survivors
    assert p2_evt.payload["passed_count"] == report.phase2_survivors

    p3_evt = next(e for e in diag_events if e.action == "DIAGNOSTIC_PHASE3_COMPLETED")
    assert p3_evt.payload["finalists_count"] == len(report.finalists)
    assert "mean_wfe" in p3_evt.payload



