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
    DiagnosticsPipeline,
)
from core.strategy_universe import StrategyUniverseGenerator, load_market_dataset_polars


def test_data_integrity_duplicate_timestamps_and_spikes():
    checker = DataIntegrityChecker()

    # 1. Duplicate timestamps
    df_dup = pl.DataFrame({
        "time": [1000, 1060, 1060, 1120],
        "close": [100.0, 101.0, 101.5, 102.0],
    })
    rep_dup = checker.check(df_dup)
    assert isinstance(rep_dup, DataIntegrityReport)
    assert rep_dup.is_clean is False
    assert any("duplicate" in i.lower() for i in rep_dup.issues)

    # 2. Price spike / bad fill anomaly (> 6 sigma shock)
    prices = [100.0 + 0.01 * i for i in range(50)]
    prices[25] = 150.0  # Massive 50% single-bar shock
    df_spike = pl.DataFrame({"close": prices})
    rep_spike = checker.check(df_spike)
    assert rep_spike.is_clean is False
    assert any("spike" in i.lower() or "outlier" in i.lower() for i in rep_spike.issues)

    # 3. Missing bars / abnormal gap detection
    times = [1000 + i * 60 for i in range(10)]
    times[5] = times[4] + 600  # 10x interval gap
    df_gap = pl.DataFrame({"time": times, "close": [100.0] * 10})
    rep_gap = checker.check(df_gap)
    assert rep_gap.is_clean is False
    assert any("gap" in i.lower() or "missing" in i.lower() for i in rep_gap.issues)


def test_signal_quality_expanded_metrics():
    checker = SignalQualityChecker()
    returns = np.array([0.005, -0.002, 0.004, 0.006, -0.001, 0.003, 0.005, 0.004], dtype=np.float64)
    equity = np.cumprod(1.0 + returns) * 10000.0
    signals = np.array([1, 1, 1, 0, 0, -1, -1, 1], dtype=np.int32)
    regimes = {
        "BULL": np.array([0.01, 0.02]),
        "BEAR": np.array([0.005, 0.003]),
        "SIDEWAYS": np.array([0.001, 0.002]),
        "HIGH_VOL": np.array([0.012, 0.008]),
        "LOW_VOL": np.array([0.003, 0.004]),
        "STRESS": np.array([-0.001, 0.002]),
    }

    rep = checker.check(
        equity=equity,
        returns=returns,
        regime_returns=regimes,
        signals=signals,
    )
    assert isinstance(rep, SignalQualityReport)
    assert hasattr(rep, "turnover")
    assert hasattr(rep, "decay_half_life")
    assert hasattr(rep, "regime_breakdown")
    assert rep.turnover >= 0.0
    assert rep.execution_authority is False


def test_execution_feasibility_latency_and_impact():
    checker = ExecutionFeasibilityChecker()
    prices = np.linspace(100.0, 110.0, 100)
    signals = np.ones(100, dtype=np.int32)

    rep = checker.check(
        prices=prices,
        signals=signals,
        baseline_spread_bps=1.5,
        simulate_latency_bars=1,
    )
    assert isinstance(rep, ExecutionCheckReport)
    assert hasattr(rep, "latency_degradation_pct")
    assert hasattr(rep, "market_impact_bps")
    assert rep.latency_degradation_pct >= 0.0
    assert rep.market_impact_bps >= 0.0


def test_portfolio_fit_diversification_and_hhi():
    checker = PortfolioFitChecker()
    # 3 strategy return series
    rets1 = np.array([0.01, -0.005, 0.02, 0.01, -0.01])
    rets2 = np.array([-0.01, 0.01, -0.01, 0.02, 0.005])
    rets3 = np.array([0.005, 0.002, -0.003, 0.008, 0.001])
    matrix = np.column_stack([rets1, rets2, rets3])

    rep = checker.check_portfolio_matrix(matrix)
    assert isinstance(rep, PortfolioFitReport)
    assert hasattr(rep, "hhi_concentration")
    assert hasattr(rep, "effective_bets")
    assert rep.hhi_concentration > 0.0
    assert rep.effective_bets > 1.0


def test_parallel_diagnostics_execution():
    np.random.seed(42)
    n = 300
    prices = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, n)))
    df = load_market_dataset_polars({"close": prices})

    generator = StrategyUniverseGenerator()
    specs = generator.generate_matrix(symbols=["EURUSD"])[:6]

    pipeline = DiagnosticsPipeline(emit_telemetry=True)
    parallel_rep = pipeline.run_parallel_diagnostics(specs, df)

    assert isinstance(parallel_rep, dict)
    assert "total_candidates" in parallel_rep
    assert parallel_rep["total_candidates"] == len(specs)
    assert "ranking" in parallel_rep
    assert len(parallel_rep["ranking"]) == len(specs)
    assert "style_summary" in parallel_rep
    assert parallel_rep["execution_authority"] is False
