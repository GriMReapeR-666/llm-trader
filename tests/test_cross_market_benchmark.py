"""Tests for Cross-Market Benchmarking Engine — Phase 5.

Covers: CorrelationAnalyzer, RegimeCoverageAnalyzer, AMALeaderboard,
CrossMarketBenchmark full pipeline, and edge cases.
"""

from __future__ import annotations

import json
import math
import os
import random
from typing import Any, Dict, List
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.cross_market_benchmark import (
    BenchmarkResult,
    CrossMarketReport,
    CorrelationAnalyzer,
    RegimeCoverageAnalyzer,
    AMALeaderboard,
    CrossMarketBenchmark,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    DEFAULT_REGIMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    symbol: str = "EURUSD",
    timeframe: str = "H1",
    sharpe: float = 1.0,
    win_rate: float = 0.55,
    max_drawdown: float = -0.05,
    total_return: float = 0.10,
    n_trades: int = 100,
    regime_scores: Dict[str, float] | None = None,
) -> BenchmarkResult:
    if regime_scores is None:
        regime_scores = {
            "LOW_VOL_TREND": 0.02,
            "LOW_VOL_RANGE": 0.015,
            "HIGH_VOL_TREND": -0.01,
            "HIGH_VOL_CRISIS": -0.005,
        }
    pf = max(0.5, (win_rate * sharpe + 0.5)) if sharpe > 0 else 0.3
    calmar = total_return / abs(max_drawdown) if abs(max_drawdown) > 1e-6 else 0
    return BenchmarkResult(
        symbol=symbol,
        timeframe=timeframe,
        sharpe=sharpe,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        total_return=total_return,
        n_trades=n_trades,
        calmar=calmar,
        profit_factor=pf,
        regime_scores=regime_scores,
    )


def _mock_fitness(symbol: str, timeframe: str) -> BenchmarkResult:
    """Deterministic mock fitness function."""
    # Seed based on symbol+timeframe for reproducibility
    seed = hash(symbol + timeframe) % 2**31
    rng = random.Random(seed)
    return _make_result(
        symbol=symbol,
        timeframe=timeframe,
        sharpe=rng.uniform(0.3, 2.0),
        win_rate=rng.uniform(0.40, 0.65),
        max_drawdown=rng.uniform(-0.15, -0.03),
        total_return=rng.uniform(0.02, 0.20),
        n_trades=rng.randint(30, 200),
    )


def _strong_fitness(symbol: str, timeframe: str) -> BenchmarkResult:
    """Always returns strong results."""
    return _make_result(
        symbol=symbol, timeframe=timeframe,
        sharpe=2.0, win_rate=0.65, max_drawdown=-0.03,
        total_return=0.20, n_trades=150,
    )


def _weak_fitness(symbol: str, timeframe: str) -> BenchmarkResult:
    """Always returns weak results."""
    return _make_result(
        symbol=symbol, timeframe=timeframe,
        sharpe=0.2, win_rate=0.42, max_drawdown=-0.20,
        total_return=0.01, n_trades=20,
    )


# ---------------------------------------------------------------------------
# BenchmarkResult
# ---------------------------------------------------------------------------

class TestBenchmarkResult:
    def test_default_values(self):
        r = BenchmarkResult(symbol="EURUSD", timeframe="H1")
        assert r.symbol == "EURUSD"
        assert r.timeframe == "H1"
        assert r.sharpe == 0.0
        assert r.win_rate == 0.0
        assert r.regime_scores == {}

    def test_to_dict(self):
        r = _make_result()
        d = r.to_dict()
        assert d["symbol"] == "EURUSD"
        assert "sharpe" in d
        assert "regime_scores" in d

    def test_calmar_computed(self):
        r = _make_result(max_drawdown=-0.05, total_return=0.10)
        assert r.calmar == pytest.approx(2.0)

    def test_calmar_zero_drawdown(self):
        r = _make_result(max_drawdown=0.0, total_return=0.10)
        assert r.calmar == 0.0


# ---------------------------------------------------------------------------
# CorrelationAnalyzer
# ---------------------------------------------------------------------------

class TestCorrelationAnalyzer:
    def test_perfect_correlation(self):
        ca = CorrelationAnalyzer()
        returns = {
            "A": [1.0, 2.0, 3.0, 4.0, 5.0],
            "B": [2.0, 4.0, 6.0, 8.0, 10.0],
        }
        corr, symbols = ca.compute_correlation_matrix(returns)
        assert symbols == ["A", "B"]
        assert corr[0, 1] == pytest.approx(1.0)
        assert corr[0, 0] == pytest.approx(1.0)

    def test_negative_correlation(self):
        ca = CorrelationAnalyzer()
        returns = {
            "A": [1.0, 2.0, 3.0, 4.0, 5.0],
            "B": [5.0, 4.0, 3.0, 2.0, 1.0],
        }
        corr, symbols = ca.compute_correlation_matrix(returns)
        assert corr[0, 1] == pytest.approx(-1.0)

    def test_uncorrelated(self):
        ca = CorrelationAnalyzer()
        rng = np.random.RandomState(42)
        a = rng.randn(100).tolist()
        b = rng.randn(100).tolist()
        returns = {"A": a, "B": b}
        corr, _ = ca.compute_correlation_matrix(returns)
        # Should be close to 0
        assert abs(corr[0, 1]) < 0.3

    def test_diversification_score(self):
        ca = CorrelationAnalyzer()
        # Perfectly correlated -> low diversification
        returns = {"A": [1, 2, 3, 4, 5], "B": [1, 2, 3, 4, 5]}
        score = ca.diversification_score(returns)
        assert score < 0.01  # Near zero for perfect positive correlation

    def test_diversification_score_negative_corr(self):
        ca = CorrelationAnalyzer()
        # Negatively correlated -> high diversification
        returns = {"A": [1, 2, 3, 4, 5], "B": [5, 4, 3, 2, 1]}
        score = ca.diversification_score(returns)
        assert score > 0.8  # High diversification from negative correlation

    def test_single_instrument(self):
        ca = CorrelationAnalyzer()
        returns = {"A": [1, 2, 3]}
        corr, symbols = ca.compute_correlation_matrix(returns)
        assert corr.shape == (1, 1)
        assert corr[0, 0] == 1.0

    def test_empty_returns(self):
        ca = CorrelationAnalyzer()
        corr, symbols = ca.compute_correlation_matrix({})
        assert symbols == []

    def test_two_instruments(self):
        ca = CorrelationAnalyzer()
        returns = {
            "EURUSD": list(range(50)),
            "GBPUSD": list(range(50)),
        }
        corr, symbols = ca.compute_correlation_matrix(returns)
        assert len(symbols) == 2
        assert corr.shape == (2, 2)

    def test_avg_pairwise_correlation(self):
        ca = CorrelationAnalyzer()
        returns = {
            "A": [1, 2, 3, 4, 5],
            "B": [1, 2, 3, 4, 5],
            "C": [5, 4, 3, 2, 1],
        }
        avg = ca.avg_pairwise_correlation(returns)
        # A-B = 1.0, A-C = |-1.0| = 1.0, B-C = |-1.0| = 1.0 -> avg(|corr|) = 1.0
        assert avg == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# RegimeCoverageAnalyzer
# ---------------------------------------------------------------------------

class TestRegimeCoverageAnalyzer:
    def test_full_coverage(self):
        rca = RegimeCoverageAnalyzer()
        results = [
            _make_result("EURUSD", "H1", regime_scores={
                "LOW_VOL_TREND": 0.01,
                "LOW_VOL_RANGE": 0.02,
                "HIGH_VOL_TREND": -0.01,
                "HIGH_VOL_CRISIS": -0.02,
            }),
            _make_result("GBPUSD", "H1", regime_scores={
                "LOW_VOL_TREND": 0.02,
                "LOW_VOL_RANGE": 0.01,
                "HIGH_VOL_TREND": -0.02,
                "HIGH_VOL_CRISIS": -0.01,
            }),
        ]
        coverage = rca.compute_regime_coverage(results)
        for regime in DEFAULT_REGIMES:
            assert coverage[regime] == 1.0

    def test_partial_coverage(self):
        rca = RegimeCoverageAnalyzer()
        results = [
            _make_result("EURUSD", "H1", regime_scores={
                "LOW_VOL_TREND": 0.01,
                "LOW_VOL_RANGE": 0.02,
            }),
            _make_result("GBPUSD", "H1", regime_scores={
                "LOW_VOL_TREND": 0.02,
                "HIGH_VOL_TREND": -0.01,
            }),
        ]
        coverage = rca.compute_regime_coverage(results)
        assert coverage["LOW_VOL_TREND"] == 1.0
        assert coverage["HIGH_VOL_CRISIS"] == 0.0

    def test_regime_robustness_uniform(self):
        rca = RegimeCoverageAnalyzer()
        # Same performance across all regimes
        results = [
            _make_result("EURUSD", "H1", regime_scores={
                r: 0.05 for r in DEFAULT_REGIMES
            }),
        ]
        robustness = rca.compute_regime_robustness(results)
        assert robustness > 0.9  # Very uniform

    def test_regime_robustness_concentrated(self):
        rca = RegimeCoverageAnalyzer()
        # All performance in one regime
        results = [
            _make_result("EURUSD", "H1", regime_scores={
                "LOW_VOL_TREND": 0.10,
                "LOW_VOL_RANGE": 0.0,
                "HIGH_VOL_TREND": 0.0,
                "HIGH_VOL_CRISIS": 0.0,
            }),
        ]
        robustness = rca.compute_regime_robustness(results)
        assert robustness < 0.5

    def test_check_regime_coverage_pass(self):
        rca = RegimeCoverageAnalyzer()
        results = [
            _make_result("EURUSD", "H1", regime_scores={
                r: 0.01 for r in DEFAULT_REGIMES
            }),
            _make_result("GBPUSD", "H1", regime_scores={
                r: 0.01 for r in DEFAULT_REGIMES
            }),
        ]
        ok, coverage = rca.check_regime_coverage(results, min_coverage=0.5)
        assert ok is True

    def test_check_regime_coverage_fail(self):
        rca = RegimeCoverageAnalyzer()
        results = [
            _make_result("EURUSD", "H1", regime_scores={
                "LOW_VOL_TREND": 0.01,
            }),
        ]
        ok, coverage = rca.check_regime_coverage(results, min_coverage=0.5)
        assert ok is False

    def test_empty_results(self):
        rca = RegimeCoverageAnalyzer()
        coverage = rca.compute_regime_coverage([])
        for r in DEFAULT_REGIMES:
            assert coverage[r] == 0.0


# ---------------------------------------------------------------------------
# AMALeaderboard
# ---------------------------------------------------------------------------

class TestAMALeaderboard:
    def test_ranking(self):
        lb = AMALeaderboard()
        results = [
            _make_result("EURUSD", "H1", sharpe=2.0, win_rate=0.65),
            _make_result("GBPUSD", "H1", sharpe=0.5, win_rate=0.45),
            _make_result("USDJPY", "H1", sharpe=1.0, win_rate=0.55),
        ]
        ranked = lb.rank(results)
        assert len(ranked) == 3
        assert ranked[0]["rank"] == 1
        assert ranked[0]["composite_score"] >= ranked[1]["composite_score"]
        assert ranked[1]["composite_score"] >= ranked[2]["composite_score"]

    def test_best_symbol_at_top(self):
        lb = AMALeaderboard()
        results = [
            _make_result("EURUSD", "H1", sharpe=0.3, win_rate=0.42),
            _make_result("GBPUSD", "H1", sharpe=2.5, win_rate=0.70),
        ]
        ranked = lb.rank(results)
        assert ranked[0]["symbol"] == "GBPUSD"

    def test_composite_score_range(self):
        lb = AMALeaderboard()
        results = [_make_result("EURUSD", "H1") for _ in range(5)]
        ranked = lb.rank(results)
        for entry in ranked:
            assert 0 <= entry["composite_score"] <= 1

    def test_portfolio_score_high(self):
        lb = AMALeaderboard()
        results = [
            _make_result("EURUSD", "H1", sharpe=2.0, win_rate=0.65),
            _make_result("GBPUSD", "H1", sharpe=1.8, win_rate=0.60),
        ]
        ranked = lb.rank(results)
        score = lb.compute_portfolio_score(ranked)
        assert score > 0.5

    def test_portfolio_score_empty(self):
        lb = AMALeaderboard()
        score = lb.compute_portfolio_score([])
        assert score == 0.0

    def test_portfolio_score_low(self):
        lb = AMALeaderboard()
        ranked = [{"composite_score": 0.1}, {"composite_score": 0.2}]
        score = lb.compute_portfolio_score(ranked)
        assert score < 0.5

    def test_composite_score_consistency_bonus(self):
        """Two strategies with same average but different spreads should differ."""
        lb = AMALeaderboard()
        # Consistent: all scores ~0.6
        ranked_consistent = [
            {"composite_score": 0.6},
            {"composite_score": 0.6},
            {"composite_score": 0.6},
        ]
        # Spread: 0.3, 0.6, 0.9
        ranked_spread = [
            {"composite_score": 0.9},
            {"composite_score": 0.6},
            {"composite_score": 0.3},
        ]
        score_c = lb.compute_portfolio_score(ranked_consistent)
        score_s = lb.compute_portfolio_score(ranked_spread)
        # Consistent should be better (same avg, lower spread)
        assert score_c >= score_s

    def test_rank_includes_all_fields(self):
        lb = AMALeaderboard()
        results = [_make_result("EURUSD", "H1")]
        ranked = lb.rank(results)
        entry = ranked[0]
        for key in ["symbol", "timeframe", "composite_score", "sharpe",
                     "win_rate", "max_drawdown", "calmar", "profit_factor",
                     "n_trades", "rank"]:
            assert key in entry


# ---------------------------------------------------------------------------
# CrossMarketBenchmark — Full Pipeline
# ---------------------------------------------------------------------------

class TestCrossMarketBenchmark:
    def test_run_benchmark(self):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD", "GBPUSD"],
            timeframes=["H1"],
            state_file="test_benchmark.json",
        )
        report = bench.run_benchmark(_mock_fitness)
        assert isinstance(report, CrossMarketReport)
        assert len(report.benchmark_results) == 2
        assert report.composite_robustness_score > 0
        assert len(report.leaderboard) == 2
        assert report.recommendation != ""

    def test_benchmark_multiple_timeframes(self):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD"],
            timeframes=["M15", "H1", "H4"],
            state_file="test_benchmark.json",
        )
        report = bench.run_benchmark(_mock_fitness)
        assert len(report.benchmark_results) == 3

    def test_benchmark_strong_strategy(self):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD", "GBPUSD", "USDJPY"],
            timeframes=["H1"],
            state_file="test_benchmark.json",
        )
        report = bench.run_benchmark(_strong_fitness)
        assert report.composite_robustness_score > 0.4
        assert "STRONG" in report.recommendation or "MODERATE" in report.recommendation

    def test_benchmark_weak_strategy(self):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD", "GBPUSD", "USDJPY"],
            timeframes=["H1"],
            state_file="test_benchmark.json",
        )
        report = bench.run_benchmark(_weak_fitness)
        # Weak strategy should have lower composite than strong strategy
        # (absolute threshold depends on diversification bonus)
        assert report.composite_robustness_score < 1.0
        assert len(report.leaderboard) == 3

    def test_benchmark_saves_state(self, tmp_path):
        # tmp_path, not a bare relative name: tests/conftest.py redirects any
        # state path whose parent directory IS the repo root into a per-test
        # sandbox, so a bare "test_benchmark_save.json" is written somewhere the
        # test cannot see. tmp_path lives outside the repo root, so the engine
        # writes exactly where we assert — and pytest cleans it up.
        state_file = str(tmp_path / "benchmark_save.json")
        bench = CrossMarketBenchmark(
            symbols=["EURUSD"],
            timeframes=["H1"],
            state_file=state_file,
        )
        bench.run_benchmark(_mock_fitness)
        assert os.path.exists(state_file)

    def test_benchmark_loads_state(self, tmp_path):
        state_file = str(tmp_path / "benchmark_load.json")
        bench = CrossMarketBenchmark(
            symbols=["EURUSD"],
            timeframes=["H1"],
            state_file=state_file,
        )
        bench.run_benchmark(_mock_fitness)
        loaded = bench.load_state()
        assert loaded is not None
        assert loaded.composite_robustness_score > 0

    def test_benchmark_no_state(self, tmp_path):
        bench = CrossMarketBenchmark(state_file=str(tmp_path / "nonexistent_state.json"))
        loaded = bench.load_state()
        assert loaded is None

    def test_benchmark_error_handling(self, tmp_path):
        """Fitness function that fails for one symbol should not crash."""
        call_count = {"n": 0}
        def failing_fitness(symbol: str, timeframe: str) -> BenchmarkResult:
            call_count["n"] += 1
            if symbol == "GBPUSD":
                raise ConnectionError("broker disconnected")
            return _make_result(symbol, timeframe)

        bench = CrossMarketBenchmark(
            symbols=["EURUSD", "GBPUSD"],
            timeframes=["H1"],
            state_file=str(tmp_path / "benchmark_err.json"),
        )
        report = bench.run_benchmark(failing_fitness)
        assert len(report.benchmark_results) == 1  # Only EURUSD succeeded
        assert call_count["n"] == 2

    def test_benchmark_all_fail(self, tmp_path):
        def all_fail(symbol: str, timeframe: str) -> BenchmarkResult:
            raise RuntimeError("all broken")

        bench = CrossMarketBenchmark(
            symbols=["EURUSD"],
            timeframes=["H1"],
            state_file=str(tmp_path / "benchmark_allfail.json"),
        )
        report = bench.run_benchmark(all_fail)
        assert "NO_DATA" in report.recommendation

    def test_get_status_no_benchmarks(self, tmp_path):
        bench = CrossMarketBenchmark(state_file=str(tmp_path / "nonexistent.json"))
        status = bench.get_status()
        assert status["status"] == "NO_BENCHMARKS_RUN"

    def test_get_status_after_benchmark(self, tmp_path):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD"],
            timeframes=["H1"],
            state_file=str(tmp_path / "benchmark_status.json"),
        )
        bench.run_benchmark(_mock_fitness)
        status = bench.get_status()
        assert status["status"] == "BENCHMARKED"
        assert status["composite_robustness_score"] > 0

    def test_report_to_dict(self, tmp_path):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD", "GBPUSD"],
            timeframes=["H1"],
            state_file=str(tmp_path / "benchmark_dict.json"),
        )
        report = bench.run_benchmark(_mock_fitness)
        d = report.to_dict()
        assert "benchmark_results" in d
        assert "leaderboard" in d
        assert "regime_coverage" in d
        assert "composite_robustness_score" in d
        assert "recommendation" in d

    def test_correlation_matrix_in_report(self, tmp_path):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD", "GBPUSD"],
            timeframes=["H1"],
            state_file=str(tmp_path / "benchmark_corr.json"),
        )
        report = bench.run_benchmark(_mock_fitness)
        assert report.correlation_matrix is not None
        assert len(report.correlation_matrix) == 2  # 2x2 matrix


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_instrument_single_timeframe(self):
        bench = CrossMarketBenchmark(
            symbols=["EURUSD"],
            timeframes=["H1"],
            state_file="test_edge_single.json",
        )
        report = bench.run_benchmark(_mock_fitness)
        assert len(report.benchmark_results) == 1
        assert report.composite_robustness_score >= 0

    def test_many_instruments(self):
        symbols = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]
        bench = CrossMarketBenchmark(
            symbols=symbols,
            timeframes=["H1"],
            state_file="test_edge_many.json",
        )
        report = bench.run_benchmark(_mock_fitness)
        assert len(report.benchmark_results) == 7

    def test_default_constants(self):
        assert len(DEFAULT_SYMBOLS) > 0
        assert len(DEFAULT_TIMEFRAMES) > 0
        assert len(DEFAULT_REGIMES) > 0

    def test_empty_correlation_series(self):
        ca = CorrelationAnalyzer()
        trimmed = ca.compute_return_series({})
        assert len(trimmed) == 0

    def test_equal_length_series(self):
        ca = CorrelationAnalyzer(lookback_bars=5)
        returns = {
            "A": list(range(20)),
            "B": list(range(20)),
        }
        trimmed = ca.compute_return_series(returns)
        assert len(trimmed["A"]) == 5
        assert len(trimmed["B"]) == 5

    def test_regime_coverage_all_empty(self):
        rca = RegimeCoverageAnalyzer()
        results = [
            _make_result("EURUSD", "H1", regime_scores={}),
        ]
        coverage = rca.compute_regime_coverage(results)
        for r in DEFAULT_REGIMES:
            assert coverage[r] == 0.0
