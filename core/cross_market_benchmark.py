"""Cross-Market Benchmarking Engine — AI-Trader / AMA Leaderboard Approach.

Validates that strategy performance is robust across instruments, timeframes,
and regimes — not just overfit to a single symbol.

References:
  - AI-Trader (2025): Minimal-information paradigm, cross-market benchmarking
  - AMA (Adaptive Market Analysis): Performance leaderboard across instruments
  - FinPFN: Regime-based cross-sectional performance evaluation
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from core.state_persistence import StoreBackedState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    """Single instrument/timeframe benchmark result."""
    symbol: str
    timeframe: str
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    n_trades: int = 0
    calmar: float = 0.0
    profit_factor: float = 0.0
    regime_scores: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CrossMarketReport:
    """Aggregated cross-market benchmark report."""
    benchmark_results: List[BenchmarkResult] = field(default_factory=list)
    correlation_matrix: Optional[List[List[float]]] = None
    regime_coverage: Dict[str, float] = field(default_factory=dict)
    leaderboard: List[Dict[str, Any]] = field(default_factory=list)
    composite_robustness_score: float = 0.0
    recommendation: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "benchmark_results": [b.to_dict() for b in self.benchmark_results],
            "regime_coverage": self.regime_coverage,
            "leaderboard": self.leaderboard,
            "composite_robustness_score": round(self.composite_robustness_score, 4),
            "recommendation": self.recommendation,
            "timestamp": self.timestamp,
        }
        if self.correlation_matrix is not None:
            d["correlation_matrix"] = self.correlation_matrix
        return d


# ---------------------------------------------------------------------------
# Default Instruments
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USDCAD"]
DEFAULT_TIMEFRAMES = ["M15", "H1", "H4"]
DEFAULT_REGIMES = ["LOW_VOL_TREND", "LOW_VOL_RANGE", "HIGH_VOL_TREND", "HIGH_VOL_CRISIS"]


# ---------------------------------------------------------------------------
# Correlation Analysis
# ---------------------------------------------------------------------------

class CorrelationAnalyzer:
    """Cross-asset return correlation analysis.

    Measures diversification benefit: if all instruments move together,
    the portfolio has no diversification.
    """

    def __init__(self, lookback_bars: int = 100):
        self.lookback_bars = lookback_bars

    def compute_return_series(
        self, returns_by_symbol: Dict[str, List[float]]
    ) -> Dict[str, np.ndarray]:
        """Trim all return series to the same length."""
        min_len = min(
            len(r) for r in returns_by_symbol.values()
        ) if returns_by_symbol else 0
        min_len = min(min_len, self.lookback_bars)
        return {
            sym: np.array(r[-min_len:], dtype=float)
            for sym, r in returns_by_symbol.items()
        }

    def compute_correlation_matrix(
        self, returns_by_symbol: Dict[str, List[float]]
    ) -> Tuple[np.ndarray, List[str]]:
        """Compute pairwise Pearson correlation matrix."""
        trimmed = self.compute_return_series(returns_by_symbol)
        symbols = sorted(trimmed.keys())
        n = len(symbols)
        if n < 2:
            return np.eye(max(n, 1)), symbols

        matrix = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i, j] = 1.0
                else:
                    a, b = trimmed[symbols[i]], trimmed[symbols[j]]
                    if len(a) > 1 and np.std(a) > 1e-10 and np.std(b) > 1e-10:
                        matrix[i, j] = float(np.corrcoef(a, b)[0, 1])
                    else:
                        matrix[i, j] = 0.0
        return matrix, symbols

    def avg_pairwise_correlation(
        self, returns_by_symbol: Dict[str, List[float]]
    ) -> float:
        """Average absolute pairwise correlation (lower = better diversification)."""
        matrix, symbols = self.compute_correlation_matrix(returns_by_symbol)
        n = len(symbols)
        if n < 2:
            return 0.0
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append(abs(matrix[i, j]))
        return float(np.mean(pairs)) if pairs else 0.0

    def avg_raw_pairwise_correlation(
        self, returns_by_symbol: Dict[str, List[float]]
    ) -> float:
        """Average signed pairwise correlation. Negative = better diversification."""
        matrix, symbols = self.compute_correlation_matrix(returns_by_symbol)
        n = len(symbols)
        if n < 2:
            return 0.0
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairs.append(matrix[i, j])
        return float(np.mean(pairs)) if pairs else 0.0

    def diversification_score(
        self, returns_by_symbol: Dict[str, List[float]]
    ) -> float:
        """Score 0-1: 1 = perfectly diversified (negative corr), 0 = fully correlated."""
        # Use raw (signed) correlation: negative = high diversification
        avg_corr = self.avg_raw_pairwise_correlation(returns_by_symbol)
        # Map: avg_corr=-1 -> score=1, avg_corr=0 -> score=0.5, avg_corr=1 -> score=0
        return float(np.clip(0.5 - avg_corr / 2, 0, 1))


# ---------------------------------------------------------------------------
# Regime Coverage Analysis
# ---------------------------------------------------------------------------

class RegimeCoverageAnalyzer:
    """Measure whether strategy performs across all market regimes, not just one."""

    def __init__(self, regimes: Optional[List[str]] = None):
        self.regimes = regimes or DEFAULT_REGIMES

    def compute_regime_coverage(
        self, benchmark_results: List[BenchmarkResult]
    ) -> Dict[str, float]:
        """For each regime, compute the fraction of instruments that have data."""
        regime_counts = {r: 0 for r in self.regimes}
        total_instruments = len(benchmark_results)

        for br in benchmark_results:
            for regime in self.regimes:
                if regime in br.regime_scores and br.regime_scores[regime] != 0:
                    regime_counts[regime] += 1

        if total_instruments == 0:
            return {r: 0.0 for r in self.regimes}

        return {
            r: round(count / total_instruments, 4)
            for r, count in regime_counts.items()
        }

    def compute_regime_robustness(
        self, benchmark_results: List[BenchmarkResult]
    ) -> float:
        """Score 0-1: How robust is performance across regimes.

        Penalizes if performance is concentrated in a single regime.
        """
        if not benchmark_results:
            return 0.0

        # Collect all regime scores across all instruments
        regime_perf: Dict[str, List[float]] = {}
        for br in benchmark_results:
            for regime, score in br.regime_scores.items():
                if regime not in regime_perf:
                    regime_perf[regime] = []
                regime_perf[regime].append(score)

        if not regime_perf:
            return 0.0

        # Average performance per regime
        avg_per_regime = {
            r: float(np.mean(vals)) for r, vals in regime_perf.items()
        }

        vals = list(avg_per_regime.values())
        if not vals:
            return 0.0

        # Compute coefficient of variation (lower = more uniform = more robust)
        mean_val = np.mean(vals)
        std_val = np.std(vals)

        if abs(mean_val) < 1e-10:
            return 0.0

        cv = std_val / abs(mean_val)
        # Map CV to score: CV=0 -> score=1, CV>1 -> score~0
        return float(max(0.0, min(1.0, 1.0 - cv)))

    def check_regime_coverage(
        self, benchmark_results: List[BenchmarkResult],
        min_coverage: float = 0.5,
    ) -> Tuple[bool, Dict[str, float]]:
        """Check if at least min_coverage fraction of instruments cover each regime."""
        coverage = self.compute_regime_coverage(benchmark_results)
        undercovered = [r for r, c in coverage.items() if c < min_coverage]
        return len(undercovered) == 0, coverage


# ---------------------------------------------------------------------------
# AMA Leaderboard
# ---------------------------------------------------------------------------

class AMALeaderboard:
    """Adaptive Market Analysis leaderboard — ranks strategies across instruments.

    Composite score = w_sharpe * Sharpe + w_win * WinRate + w_dd * (1 - DD)
        + w_calmar * Calmar + w_div * DiversificationBonus
    """

    def __init__(
        self,
        w_sharpe: float = 0.25,
        w_win_rate: float = 0.20,
        w_drawdown: float = 0.25,
        w_calmar: float = 0.15,
        w_profit_factor: float = 0.15,
    ):
        self.w_sharpe = w_sharpe
        self.w_win_rate = w_win_rate
        self.w_drawdown = w_drawdown
        self.w_calmar = w_calmar
        self.w_profit_factor = w_profit_factor

    def compute_composite_score(self, result: BenchmarkResult) -> float:
        """Compute AMA composite score for a single benchmark result."""
        # Normalize each metric to 0-1 range
        sharpe_norm = np.clip((result.sharpe + 1) / 4, 0, 1)  # [-1,3] -> [0,1]
        win_norm = np.clip(result.win_rate, 0, 1)
        dd_norm = np.clip(1 - abs(result.max_drawdown), 0, 1)
        calmar_norm = np.clip((result.calmar + 1) / 4, 0, 1)
        pf_norm = np.clip(result.profit_factor / 3, 0, 1)

        score = (
            self.w_sharpe * sharpe_norm
            + self.w_win_rate * win_norm
            + self.w_drawdown * dd_norm
            + self.w_calmar * calmar_norm
            + self.w_profit_factor * pf_norm
        )
        return float(score)

    def rank(
        self, benchmark_results: List[BenchmarkResult]
    ) -> List[Dict[str, Any]]:
        """Rank instruments by composite score, return leaderboard."""
        entries = []
        for br in benchmark_results:
            score = self.compute_composite_score(br)
            entries.append({
                "symbol": br.symbol,
                "timeframe": br.timeframe,
                "composite_score": round(score, 4),
                "sharpe": round(br.sharpe, 4),
                "win_rate": round(br.win_rate, 4),
                "max_drawdown": round(br.max_drawdown, 4),
                "calmar": round(br.calmar, 4),
                "profit_factor": round(br.profit_factor, 4),
                "n_trades": br.n_trades,
            })

        entries.sort(key=lambda e: e["composite_score"], reverse=True)

        # Add rank
        for i, entry in enumerate(entries):
            entry["rank"] = i + 1

        return entries

    def compute_portfolio_score(
        self, leaderboard: List[Dict[str, Any]]
    ) -> float:
        """Compute overall portfolio robustness score from leaderboard."""
        if not leaderboard:
            return 0.0

        scores = [e["composite_score"] for e in leaderboard]
        avg_score = float(np.mean(scores))
        min_score = float(np.min(scores))
        spread = max(scores) - min(scores)

        # Bonus for consistency (low spread) + high average
        consistency_bonus = max(0.0, 1.0 - spread)
        return float(np.clip(avg_score * 0.7 + consistency_bonus * 0.3, 0, 1))


# ---------------------------------------------------------------------------
# Cross-Market Benchmark Engine
# ---------------------------------------------------------------------------

class CrossMarketBenchmark(StoreBackedState):
    """Full cross-market benchmarking pipeline.

    Workflow:
      1. Run fitness_fn on each (symbol, timeframe) pair
      2. Compute cross-asset correlations
      3. Analyze regime coverage
      4. Rank on AMA leaderboard
      5. Generate recommendation
    """

    store_key = "cross_market_benchmark"

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        regimes: Optional[List[str]] = None,
        state_file: str = "cross_market_benchmark.json",
        store: Optional[Any] = None,
    ):
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.timeframes = timeframes or DEFAULT_TIMEFRAMES
        self.regimes = regimes or DEFAULT_REGIMES
        self.state_file = state_file
        self.store = store
        self.correlation_analyzer = CorrelationAnalyzer()
        self.regime_analyzer = RegimeCoverageAnalyzer(self.regimes)
        self.leaderboard = AMALeaderboard()
        if store is not None:
            self.attach_store(store)

    def run_benchmark(
        self,
        fitness_fn: Callable[[str, str], BenchmarkResult],
    ) -> CrossMarketReport:
        """Run cross-market benchmark.

        Args:
            fitness_fn: Function(symbol, timeframe) -> BenchmarkResult

        Returns:
            CrossMarketReport with full analysis
        """
        logger.info(
            "Starting cross-market benchmark: %d symbols × %d timeframes",
            len(self.symbols), len(self.timeframes),
        )

        # Step 1: Run benchmark on each pair
        results: List[BenchmarkResult] = []
        for symbol in self.symbols:
            for tf in self.timeframes:
                try:
                    result = fitness_fn(symbol, tf)
                    results.append(result)
                    logger.info(
                        "  %s/%s: Sharpe=%.2f WR=%.1f%% DD=%.1f%%",
                        symbol, tf, result.sharpe,
                        result.win_rate * 100,
                        result.max_drawdown * 100,
                    )
                except Exception as e:
                    logger.warning("  %s/%s failed: %s", symbol, tf, e)

        if not results:
            return CrossMarketReport(
                recommendation="NO_DATA: All benchmark runs failed",
                timestamp=time.time(),
            )

        # Step 2: Correlation analysis (using synthetic returns from results)
        returns_by_symbol = self._build_returns_from_results(results)
        corr_matrix, corr_symbols = self.correlation_analyzer.compute_correlation_matrix(
            returns_by_symbol
        )
        div_score = self.correlation_analyzer.diversification_score(returns_by_symbol)
        avg_corr = self.correlation_analyzer.avg_pairwise_correlation(returns_by_symbol)

        # Step 3: Regime coverage
        regime_coverage = self.regime_analyzer.compute_regime_coverage(results)
        regime_robustness = self.regime_analyzer.compute_regime_robustness(results)
        coverage_ok, _ = self.regime_analyzer.check_regime_coverage(results)

        # Step 4: AMA leaderboard
        ranked = self.leaderboard.rank(results)
        portfolio_score = self.leaderboard.compute_portfolio_score(ranked)

        # Step 5: Composite robustness score
        composite = self._compute_composite_robustness(
            portfolio_score, div_score, regime_robustness, avg_corr
        )

        # Step 6: Recommendation
        recommendation = self._generate_recommendation(
            ranked, composite, avg_corr, regime_coverage, coverage_ok
        )

        report = CrossMarketReport(
            benchmark_results=results,
            correlation_matrix=corr_matrix.tolist() if hasattr(corr_matrix, 'tolist') else None,
            regime_coverage=regime_coverage,
            leaderboard=ranked,
            composite_robustness_score=composite,
            recommendation=recommendation,
            timestamp=time.time(),
        )

        self._save_state(report)
        return report

    def _build_returns_from_results(
        self, results: List[BenchmarkResult]
    ) -> Dict[str, List[float]]:
        """Build synthetic return series from benchmark results.

        Since we don't have actual returns, we approximate from metrics.
        """
        returns: Dict[str, List[float]] = {}
        for br in results:
            sym = br.symbol
            # Approximate daily returns from win_rate and Sharpe
            n = max(br.n_trades, 20)
            daily_vol = 0.005  # Approximate for forex
            daily_ret = br.sharpe * daily_vol / np.sqrt(252)

            # Generate synthetic returns with given win_rate and vol
            rng = np.random.RandomState(hash(sym + br.timeframe) % 2**31)
            wins = rng.random(n) < br.win_rate
            magnitudes = rng.lognormal(0, daily_vol * 10, n)
            synthetic = np.where(wins, magnitudes, -magnitudes * 0.7)

            if sym not in returns:
                returns[sym] = []
            returns[sym].extend(synthetic.tolist())

        return returns

    def _compute_composite_robustness(
        self,
        portfolio_score: float,
        diversification: float,
        regime_robustness: float,
        avg_correlation: float,
    ) -> float:
        """Composite robustness: blend of leaderboard, diversification, regime coverage."""
        return float(np.clip(
            0.35 * portfolio_score
            + 0.25 * diversification
            + 0.25 * regime_robustness
            + 0.15 * (1.0 - avg_correlation),
            0, 1,
        ))

    def _generate_recommendation(
        self,
        ranked: List[Dict[str, Any]],
        composite: float,
        avg_corr: float,
        regime_coverage: Dict[str, float],
        coverage_ok: bool,
    ) -> str:
        """Generate human-readable recommendation."""
        parts = []

        if composite >= 0.7:
            parts.append("STRONG: Strategy shows robust cross-market performance.")
        elif composite >= 0.5:
            parts.append("MODERATE: Strategy has acceptable cross-market performance.")
        else:
            parts.append("WEAK: Strategy performance is not robust across markets.")

        if ranked:
            best = ranked[0]
            worst = ranked[-1]
            parts.append(
                f"Best: {best['symbol']}/{best['timeframe']} "
                f"(score={best['composite_score']:.3f})"
            )
            parts.append(
                f"Worst: {worst['symbol']}/{worst['timeframe']} "
                f"(score={worst['composite_score']:.3f})"
            )

        if avg_corr > 0.7:
            parts.append(
                f"WARNING: High cross-asset correlation ({avg_corr:.2f}) — "
                "limited diversification benefit."
            )

        if not coverage_ok:
            undercovered = [r for r, c in regime_coverage.items() if c < 0.5]
            parts.append(
                f"WARNING: Low regime coverage for: {', '.join(undercovered)}"
            )
        else:
            parts.append("Regime coverage is adequate across all instruments.")

        return " | ".join(parts)

    def _save_state(self, report: CrossMarketReport) -> None:
        """Persist benchmark report to the store + file mirror."""
        self._save_state_dict(report.to_dict())

    def load_state(self) -> Optional[CrossMarketReport]:
        """Load last benchmark report."""
        data = self._load_state_dict()
        if not data:
            return None
        try:
            report = CrossMarketReport(
                composite_robustness_score=data.get("composite_robustness_score", 0),
                recommendation=data.get("recommendation", ""),
                timestamp=data.get("timestamp", 0),
                regime_coverage=data.get("regime_coverage", {}),
                leaderboard=data.get("leaderboard", []),
                correlation_matrix=data.get("correlation_matrix"),
            )
            return report
        except Exception:
            return None

    def get_status(self) -> Dict[str, Any]:
        """Return current benchmark status."""
        report = self.load_state()
        if report is None:
            return {"status": "NO_BENCHMARKS_RUN", "symbols": self.symbols}
        return {
            "status": "BENCHMARKED",
            "composite_robustness_score": report.composite_robustness_score,
            "recommendation": report.recommendation,
            "n_instruments": len(report.benchmark_results),
            "leaderboard_top3": report.leaderboard[:3],
            "timestamp": report.timestamp,
        }
