"""Pairs Trading Module.

Implements the pairs trading methodology from Temlyakov (2012):
  - Cointegration-based pair selection (ADF test on spread)
  - Stochastic beam search for parameter optimization
  - 3-parameter model: entry threshold, exit threshold, loss control
  - Normalized price spread calculation
  - Market-neutral signal generation

References:
  - Temlyakov, K. (2012). Optimal Parameters to Pairs Trading.
  - Gatev, Goetzmann, Rouwenhorst (2006): Pairs trading performance.
  - Vidyamurphy (2004): Cointegration approach to pair selection.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from statsmodels.tsa.stattools import adfuller, coint
    STATS_MODELS_AVAILABLE = True
except ImportError:
    STATS_MODELS_AVAILABLE = False
    logger.info("statsmodels not available - using simplified cointegration test")


@dataclass
class TradingParams:
    """Optimized trading parameters for a pair."""
    entry_threshold: float
    exit_threshold: float
    loss_control: float
    sharpe: float
    total_return: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_threshold": round(self.entry_threshold, 4),
            "exit_threshold": round(self.exit_threshold, 4),
            "loss_control": round(self.loss_control, 4),
            "sharpe": round(self.sharpe, 4),
            "total_return": round(self.total_return, 4),
        }


@dataclass
class PairSignal:
    """Trade signal for a pair."""
    timestamp: Any
    asset_long: str
    asset_short: str
    spread: float
    z_score: float
    action: str  # "enter_long", "enter_short", "exit", "loss_exit"
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": str(self.timestamp),
            "asset_long": self.asset_long,
            "asset_short": self.asset_short,
            "spread": round(self.spread, 6),
            "z_score": round(self.z_score, 4),
            "action": self.action,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class PairsResult:
    """Full pairs trading analysis result."""
    pairs_found: int
    signals: List[PairSignal]
    optimized_params: Optional[TradingParams]
    cointegrated_pairs: List[Tuple[str, str, float]]
    spread_stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pairs_found": self.pairs_found,
            "n_signals": len(self.signals),
            "optimized_params": self.optimized_params.to_dict() if self.optimized_params else None,
            "cointegrated_pairs": [
                {"asset1": p[0], "asset2": p[1], "adf_stat": round(p[2], 4)}
                for p in self.cointegrated_pairs
            ],
            "spread_stats": self.spread_stats,
            "signals": [s.to_dict() for s in self.signals[-10:]],
        }


class PairsTrader:
    """Pairs trading engine with stochastic beam search optimization.

    Finds cointegrated pairs and optimizes entry/exit/loss parameters.
    """

    def __init__(
        self,
        lookback: int = 250,
        n_population: int = 40,
        n_iterations: int = 10,
        n_remove: int = 5,
        adf_significance: float = 0.05,
        max_pairs: int = 10,
    ):
        self.lookback = lookback
        self.n_population = n_population
        self.n_iterations = n_iterations
        self.n_remove = n_remove
        self.adf_significance = adf_significance
        self.max_pairs = max_pairs

    def find_cointegrated_pairs(
        self, price_data: pd.DataFrame
    ) -> List[Tuple[str, str, float]]:
        """Find cointegrated pairs using ADF test on spread.

        Args:
            price_data: DataFrame with asset prices as columns

        Returns:
            List of (asset1, asset2, adf_statistic) tuples
        """
        symbols = price_data.columns.tolist()
        pairs = []

        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                s1 = price_data[symbols[i]].dropna()
                s2 = price_data[symbols[j]].dropna()
                common_idx = s1.index.intersection(s2.index)
                if len(common_idx) < self.lookback:
                    continue

                s1 = s1.loc[common_idx].values
                s2 = s2.loc[common_idx].values

                if STATS_MODELS_AVAILABLE:
                    _, adf_stat, _ = coint(s1, s2)
                else:
                    spread = s1 - s2
                    adf_stat = self._simple_adf(spread)

                if adf_stat < -2.86:  # Approx 5% critical value
                    pairs.append((symbols[i], symbols[j], adf_stat))

        pairs.sort(key=lambda x: x[2])
        return pairs[:self.max_pairs]

    def optimize_parameters(
        self,
        price_data: pd.DataFrame,
        pair: Tuple[str, str],
        period_start: int = 0,
        period_end: Optional[int] = None,
    ) -> TradingParams:
        """Optimize trading parameters using stochastic beam search.

        Args:
            price_data: DataFrame with asset prices
            pair: Tuple of (asset1, asset2)
            period_start: Start index for optimization
            period_end: End index for optimization

        Returns:
            Optimized TradingParams
        """
        a1, a2 = pair
        s1 = price_data[a1].dropna().values
        s2 = price_data[a2].dropna().values

        if period_end is None:
            period_end = len(s1)

        s1 = s1[period_start:period_end]
        s2 = s2[period_start:period_end]

        norm1 = s1 / s1[0]
        norm2 = s2 / s2[0]
        spread = norm1 - norm2

        best_sharpe = -np.inf
        best_params = TradingParams(2.0, 0.0, 5.0, 0.0, 0.0)

        population = self._init_population()

        for iteration in range(self.n_iterations):
            scored = []
            for params in population:
                ret = self._simulate_trades(spread, params)
                sharpe = self._compute_sharpe(ret)
                scored.append((params, sharpe, ret))

            scored.sort(key=lambda x: x[1], reverse=True)

            if scored[0][1] > best_sharpe:
                best_sharpe = scored[0][1]
                best_params = scored[0][0]
                best_params.sharpe = best_sharpe
                best_params.total_return = float(np.sum(scored[0][2]))

            survivors = [s[0] for s in scored[:self.n_population - self.n_remove]]

            new_params = []
            for _ in range(self.n_remove):
                parent = survivors[np.random.randint(len(survivors))]
                mutated = self._mutate(parent)
                new_params.append(mutated)

            population = survivors + new_params

        return best_params

    def generate_signals(
        self,
        price_data: pd.DataFrame,
        pair: Tuple[str, str],
        params: TradingParams,
    ) -> List[PairSignal]:
        """Generate trading signals for a pair.

        Args:
            price_data: DataFrame with asset prices
            pair: Tuple of (asset1, asset2)
            params: Optimized trading parameters

        Returns:
            List of PairSignal
        """
        a1, a2 = pair
        s1 = price_data[a1].dropna()
        s2 = price_data[a2].dropna()
        common = s1.index.intersection(s2.index)
        s1 = s1.loc[common].values
        s2 = s2.loc[common].values

        norm1 = s1 / s1[0]
        norm2 = s2 / s2[0]
        spread = norm1 - norm2

        mean_spread = np.mean(spread)
        std_spread = np.std(spread) if np.std(spread) > 0 else 1.0
        z_scores = (spread - mean_spread) / std_spread

        signals = []
        in_position = False
        position_dir = 0

        for i in range(1, len(spread)):
            z = z_scores[i]
            action = None

            if not in_position:
                if z > params.entry_threshold:
                    action = "enter_short"
                    in_position = True
                    position_dir = -1
                elif z < -params.entry_threshold:
                    action = "enter_long"
                    in_position = True
                    position_dir = 1
            else:
                if position_dir == 1 and z >= params.exit_threshold:
                    action = "exit"
                    in_position = False
                elif position_dir == -1 and z <= -params.exit_threshold:
                    action = "exit"
                    in_position = False
                elif abs(z) > params.loss_control:
                    action = "loss_exit"
                    in_position = False

            if action:
                confidence = min(abs(z) / params.entry_threshold, 1.0)
                signals.append(PairSignal(
                    timestamp=common[i],
                    asset_long=a1 if position_dir == 1 else a2,
                    asset_short=a2 if position_dir == 1 else a1,
                    spread=float(spread[i]),
                    z_score=float(z),
                    action=action,
                    confidence=confidence,
                ))

        return signals

    def analyze(
        self, price_data: pd.DataFrame
    ) -> PairsResult:
        """Full pairs trading analysis.

        Args:
            price_data: DataFrame with asset prices as columns

        Returns:
            PairsResult with signals and diagnostics
        """
        pairs = self.find_cointegrated_pairs(price_data)

        if not pairs:
            return PairsResult(
                pairs_found=0, signals=[], optimized_params=None,
                cointegrated_pairs=[], spread_stats={},
            )

        best_pair = pairs[0]
        params = self.optimize_parameters(price_data, best_pair)
        signals = self.generate_signals(price_data, best_pair, params)

        spread = (
            price_data[best_pair[0]].values / price_data[best_pair[0]].values[0]
            - price_data[best_pair[1]].values / price_data[best_pair[1]].values[0]
        )
        spread_stats = {
            "mean": float(np.mean(spread)),
            "std": float(np.std(spread)),
            "min": float(np.min(spread)),
            "max": float(np.max(spread)),
            "half_life": self._estimate_half_life(spread),
        }

        return PairsResult(
            pairs_found=len(pairs),
            signals=signals,
            optimized_params=params,
            cointegrated_pairs=pairs,
            spread_stats=spread_stats,
        )

    def _init_population(self) -> List[TradingParams]:
        """Initialize random population of parameters."""
        population = []
        for _ in range(self.n_population):
            entry = abs(np.random.normal(0, 1))
            exit_ = abs(np.random.normal(0, 2))
            loss = abs(np.random.normal(0, 5))
            population.append(TradingParams(entry, exit_, loss, 0.0, 0.0))
        return population

    def _mutate(self, params: TradingParams) -> TradingParams:
        """Mutate one parameter of a candidate."""
        p = [params.entry_threshold, params.exit_threshold, params.loss_control]
        idx = np.random.randint(3)
        p[idx] += np.random.normal(0, 2)
        p = [abs(v) for v in p]
        return TradingParams(p[0], p[1], p[2], 0.0, 0.0)

    def _simulate_trades(
        self, spread: np.ndarray, params: TradingParams
    ) -> np.ndarray:
        """Simulate trades and return returns."""
        mean = np.mean(spread)
        std = np.std(spread) if np.std(spread) > 0 else 1.0
        z = (spread - mean) / std

        returns = []
        in_pos = False
        entry_spread = 0.0

        for i in range(1, len(z)):
            if not in_pos:
                if abs(z[i]) > params.entry_threshold:
                    in_pos = True
                    entry_spread = spread[i]
            else:
                ret = (spread[i] - entry_spread) / abs(entry_spread) if abs(entry_spread) > 0 else 0
                if abs(z[i]) < params.exit_threshold or abs(z[i]) > params.loss_control:
                    returns.append(ret)
                    in_pos = False
                else:
                    returns.append(0.0)

        return np.array(returns) if returns else np.array([0.0])

    def _compute_sharpe(self, returns: np.ndarray, rf: float = 0.0) -> float:
        """Compute annualized Sharpe ratio."""
        if len(returns) < 2 or np.std(returns) == 0:
            return 0.0
        excess = returns - rf
        return float(np.mean(excess) / np.std(excess) * np.sqrt(252))

    def _simple_adf(self, x: np.ndarray) -> float:
        """Simplified ADF test statistic."""
        n = len(x)
        if n < 10:
            return 0.0
        dx = np.diff(x)
        x_lag = x[:-1]
        beta = np.polyfit(x_lag, dx, 1)[0]
        return float(beta * np.sqrt(n) / np.std(dx)) if np.std(dx) > 0 else 0.0

    def _estimate_half_life(self, spread: np.ndarray) -> float:
        """Estimate mean reversion half-life."""
        if len(spread) < 10:
            return 0.0
        spread_lag = spread[:-1]
        spread_diff = np.diff(spread)
        beta = np.polyfit(spread_lag, spread_diff, 1)[0]
        if beta >= 0:
            return float('inf')
        return float(-np.log(2) / beta)
