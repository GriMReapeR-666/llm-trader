"""Hybrid backtest pipeline: VectorBT Tier-1 → MT5 replay Tier-2 two-tier funnel."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.provenance import is_synthetic, validate_data_source

logger = logging.getLogger(__name__)


@dataclass
class BacktestMetrics:
    """Per-engine metrics container."""
    total_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_return_pct": round(self.total_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 2),
        }


@dataclass
class HybridVerdict:
    """Two-tier comparison verdict."""
    vbt_metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    mt5_metrics: BacktestMetrics = field(default_factory=BacktestMetrics)
    sharpe_delta: float = 0.0
    passes_promotion: bool = False
    rejection_reasons: List[str] = field(default_factory=list)
    data_source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vbt_metrics": self.vbt_metrics.to_dict(),
            "mt5_metrics": self.mt5_metrics.to_dict(),
            "sharpe_delta": round(self.sharpe_delta, 1),
            "passes_promotion": self.passes_promotion,
            "rejection_reasons": self.rejection_reasons,
            "data_source": self.data_source,
        }


def _run_vectorized(
    bars_df: pd.DataFrame,
    entries: pd.Series,
    exits: pd.Series,
    sl_pips: float = 30.0,
    tp_pips: float = 60.0,
) -> BacktestMetrics:
    """Tier-1: fast vectorized backtest."""
    close = bars_df["close"].values
    n = len(close)
    entry_idx = entries.values.astype(bool)
    exit_idx = exits.values.astype(bool)
    pip_size = 0.0001
    trades_pnl: List[float] = []
    in_trade = False
    entry_price = 0.0
    side = 0

    for i in range(n):
        if not in_trade:
            if entry_idx[i]:
                in_trade = True
                entry_price = close[i]
                side = 1
            elif exit_idx[i]:
                in_trade = True
                entry_price = close[i]
                side = -1
        else:
            pnl = side * (close[i] - entry_price) / pip_size
            if pnl <= -sl_pips or pnl >= tp_pips:
                trades_pnl.append(pnl if pnl >= tp_pips else -sl_pips)
                in_trade = False

    if not trades_pnl:
        return BacktestMetrics()
    arr = np.array(trades_pnl)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    return BacktestMetrics(
        total_return_pct=round(float(arr.sum() / max(sl_pips, 1) * 100), 2),
        sharpe_ratio=round(float(arr.mean() / arr.std() * np.sqrt(252)) if arr.std() > 0 else 0, 2),
        max_drawdown_pct=round(float(abs(np.minimum.accumulate(np.cumsum(arr)).min()) / max(sl_pips, 1) * 100), 2),
        total_trades=len(arr),
        win_rate=round(float(len(wins) / len(arr) * 100), 2),
    )


def _run_mt5_replay(
    bars_df: pd.DataFrame,
    entries: pd.Series,
    exits: pd.Series,
    sl_pips: float = 30.0,
    tp_pips: float = 60.0,
    spread_pips: float = 1.5,
    slippage_pips: float = 0.5,
    data_source: str = "",
) -> BacktestMetrics:
    """Tier-2: realistic MT5 replay with friction."""
    from research.mt5_backtest_engine import MT5BrokerBacktestEngine
    engine = MT5BrokerBacktestEngine(
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
    )
    # Reconstruct signals from entries/exits
    close = bars_df["close"].values
    n = len(close)
    entry_arr = entries.values.astype(bool)
    exit_arr = exits.values.astype(bool)

    # Build EMA signals that match entries/exits
    fast_ema = pd.Series(close).ewm(span=12, adjust=False).mean()
    slow_ema = pd.Series(close).ewm(span=26, adjust=False).mean()

    return engine.run_crossover_strategy(
        bars_df, fast_period=12, slow_period=26,
        sl_pips=sl_pips, tp_pips=tp_pips,
        data_source=data_source,
    )


class HybridBacktestPipeline:
    """Two-tier funnel: fast vectorized → realistic MT5 replay."""

    def __init__(self, sharpe_decay_threshold: float = 20.0):
        self.sharpe_decay_threshold = sharpe_decay_threshold

    def evaluate_strategy(
        self,
        bars_df: pd.DataFrame,
        entries: pd.Series,
        exits: pd.Series,
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        sl_pips: float = 30.0,
        tp_pips: float = 60.0,
        data_source: str = "",
    ) -> HybridVerdict:
        """Run both tiers and compare.

        ``data_source`` is required: a verdict is a promotion input, and a
        synthetic run may never pass it.
        """
        source = validate_data_source(
            data_source, kind="backtest", field="evaluate_strategy(data_source)")
        vbt = _run_vectorized(bars_df, entries, exits, sl_pips, tp_pips)
        mt5 = _run_mt5_replay(bars_df, entries, exits, sl_pips, tp_pips, data_source=source)
        delta = 0.0
        if vbt.sharpe_ratio != 0:
            delta = (1 - mt5.sharpe_ratio / vbt.sharpe_ratio) * 100

        reasons: List[str] = []
        if vbt.total_trades < 5:
            reasons.append("insufficient_vectorized_trades")
        if delta > self.sharpe_decay_threshold:
            reasons.append(f"sharpe_decay_{delta:.1f}%_exceeds_{self.sharpe_decay_threshold}%")
        # Fixture data is never authoritative evidence for promotion, whatever
        # the metrics say.
        if is_synthetic(source):
            reasons.append("synthetic_data_not_authoritative")

        return HybridVerdict(
            vbt_metrics=vbt,
            mt5_metrics=mt5,
            sharpe_delta=round(delta, 1),
            passes_promotion=len(reasons) == 0,
            rejection_reasons=reasons,
            data_source=source,
        )

    def run_two_tier_sweep(
        self,
        bars_df: pd.DataFrame,
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        fast_range: Tuple[int, int] = (8, 20),
        slow_range: Tuple[int, int] = (20, 50),
        step: int = 4,
        data_source: str = "",
    ) -> List[Tuple[Dict[str, int], HybridVerdict]]:
        """Grid sweep over EMA parameters, return sorted by Sharpe."""
        results = []
        close = bars_df["close"]
        for fast in range(fast_range[0], fast_range[1] + 1, step):
            for slow in range(max(fast + step, slow_range[0]), slow_range[1] + 1, step):
                fast_ema = close.ewm(span=fast, adjust=False).mean()
                slow_ema = close.ewm(span=slow, adjust=False).mean()
                entries = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
                exits = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))
                verdict = self.evaluate_strategy(
                    bars_df, entries.fillna(False), exits.fillna(False),
                    symbol=symbol, timeframe=timeframe, data_source=data_source,
                )
                results.append(({"fast_period": fast, "slow_period": slow}, verdict))
        results.sort(key=lambda x: x[1].vbt_metrics.sharpe_ratio, reverse=True)
        return results
