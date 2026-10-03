"""Walk-Forward Backtesting Framework.

Implements walk-forward analysis from Thokal (2026):
  - Expanding/rolling window training
  - Periodic model retraining
  - Out-of-sample evaluation
  - Performance metrics (Sharpe, max drawdown, cumulative returns)

References:
  - Thokal, A. (2026). AI-Driven Quantitative Trading System for
    Portfolio Optimization and Investment Decision-Making.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardFold:
    """Single walk-forward fold result."""
    fold_id: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_sharpe: float
    test_sharpe: float
    test_return: float
    test_max_drawdown: float
    n_trades: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fold_id": self.fold_id,
            "train_range": f"{self.train_start}-{self.train_end}",
            "test_range": f"{self.test_start}-{self.test_end}",
            "train_sharpe": round(self.train_sharpe, 4),
            "test_sharpe": round(self.test_sharpe, 4),
            "test_return_pct": round(self.test_return * 100, 2),
            "test_max_dd_pct": round(self.test_max_drawdown * 100, 2),
            "n_trades": self.n_trades,
        }


@dataclass
class WalkForwardResult:
    """Full walk-forward analysis result."""
    folds: List[WalkForwardFold]
    avg_test_sharpe: float
    avg_test_return: float
    avg_test_max_dd: float
    total_folds: int
    profitable_folds: int
    robustness_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_folds": self.total_folds,
            "profitable_folds": self.profitable_folds,
            "avg_test_sharpe": round(self.avg_test_sharpe, 4),
            "avg_test_return_pct": round(self.avg_test_return * 100, 2),
            "avg_test_max_dd_pct": round(self.avg_test_max_dd * 100, 2),
            "robustness_score": round(self.robustness_score, 4),
            "folds": [f.to_dict() for f in self.folds],
        }


class WalkForwardAnalyzer:
    """Walk-forward backtesting framework.

    Splits data into rolling/expanding windows, trains on historical
    data, and evaluates on out-of-sample periods.
    """

    def __init__(
        self,
        train_window: int = 500,
        test_window: int = 250,
        step_size: Optional[int] = None,
        expanding: bool = False,
    ):
        self.train_window = train_window
        self.test_window = test_window
        self.step_size = step_size or test_window
        self.expanding = expanding

    def run(
        self,
        ohlcv: pd.DataFrame,
        strategy_fn: Callable[[pd.DataFrame, pd.DataFrame], List[int]],
        price_col: str = "close",
    ) -> WalkForwardResult:
        """Run walk-forward analysis.

        Args:
            ohlcv: Full OHLCV dataset
            strategy_fn: Function(train_data, test_data) -> list of positions
            price_col: Column name for price data

        Returns:
            WalkForwardResult with fold-level metrics
        """
        prices = ohlcv[price_col].values
        n = len(prices)
        folds = []

        fold_id = 0
        start = 0

        while start + self.train_window + self.test_window <= n:
            train_end = start + self.train_window
            test_end = train_end + self.test_window

            train_data = ohlcv.iloc[start:train_end]
            test_data = ohlcv.iloc[train_end:test_end]

            try:
                positions = strategy_fn(train_data, test_data)
            except Exception as e:
                logger.warning(f"Fold {fold_id} strategy failed: {e}")
                start += self.step_size
                fold_id += 1
                continue

            train_returns = train_data[price_col].pct_change().dropna()
            test_returns = test_data[price_col].pct_change().dropna()

            if len(positions) > 0:
                pos_array = np.array(positions[:len(test_returns)])
                strategy_returns = pos_array * test_returns.values[:len(pos_array)]
            else:
                strategy_returns = test_returns.values

            train_sharpe = self._sharpe(train_returns.values)
            test_sharpe = self._sharpe(strategy_returns)
            test_return = float(np.sum(strategy_returns))
            test_max_dd = self._max_drawdown(strategy_returns)
            n_trades = self._count_trades(positions)

            folds.append(WalkForwardFold(
                fold_id=fold_id,
                train_start=start,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
                train_sharpe=train_sharpe,
                test_sharpe=test_sharpe,
                test_return=test_return,
                test_max_drawdown=test_max_dd,
                n_trades=n_trades,
            ))

            start += self.step_size
            fold_id += 1

        if not folds:
            return WalkForwardResult(
                folds=[], avg_test_sharpe=0, avg_test_return=0,
                avg_test_max_dd=0, total_folds=0, profitable_folds=0,
                robustness_score=0,
            )

        avg_sharpe = float(np.mean([f.test_sharpe for f in folds]))
        avg_return = float(np.mean([f.test_return for f in folds]))
        avg_dd = float(np.mean([f.test_max_drawdown for f in folds]))
        profitable = sum(1 for f in folds if f.test_return > 0)
        robustness = profitable / len(folds) if folds else 0

        return WalkForwardResult(
            folds=folds,
            avg_test_sharpe=avg_sharpe,
            avg_test_return=avg_return,
            avg_test_max_dd=avg_dd,
            total_folds=len(folds),
            profitable_folds=profitable,
            robustness_score=robustness,
        )

    def _sharpe(self, returns: np.ndarray, rf: float = 0.0) -> float:
        """Annualized Sharpe ratio."""
        if len(returns) < 2 or np.std(returns) == 0:
            return 0.0
        return float((np.mean(returns) - rf) / np.std(returns) * np.sqrt(252))

    def _max_drawdown(self, returns: np.ndarray) -> float:
        """Maximum drawdown."""
        cumulative = np.cumsum(returns)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        return float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

    def _count_trades(self, positions: List[int]) -> int:
        """Count position changes (trades)."""
        if not positions:
            return 0
        trades = 0
        for i in range(1, len(positions)):
            if positions[i] != positions[i - 1]:
                trades += 1
        return trades
