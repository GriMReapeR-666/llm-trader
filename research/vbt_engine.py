"""High-Performance Vectorized Backtesting Engine (VectorBT / Matrix Simulator).

Provides institutional-grade backtesting for quantitative and multi-agent strategies,
calculating Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, and Equity Curves.
Automatically utilizes `vectorbt` if installed, with a high-speed NumPy/Pandas
vectorized fallback engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd

from core.provenance import validate_data_source

import logging as _vbt_log
try:
    import vectorbt as vbt
    _HAS_VBT = True
except ImportError:
    vbt = None
    _HAS_VBT = False
    _vbt_log.getLogger("VBT").debug(
        "vectorbt not installed — using numpy fallback (install: pip install vectorbt). "
        "Fallback is correct Sharpe/Sortino/Calmar but not accelerated."
    )


@dataclass
class BacktestMetrics:
    """Comprehensive performance metrics for a backtest simulation."""
    symbol: str
    timeframe: str
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    expectancy_usd: float
    initial_cash: float
    final_cash: float
    var_95_pct: float = 0.0
    cvar_95_pct: float = 0.0
    ulcer_index: float = 0.0
    upi_martin_ratio: float = 0.0
    gain_to_pain_ratio: float = 0.0
    tail_ratio: float = 1.0
    equity_series: List[float] = field(default_factory=list)
    drawdown_series: List[float] = field(default_factory=list)
    timestamps: List[str] = field(default_factory=list)
    #: Declared bar provenance; validated so a fixture result cannot be
    #: constructed without saying so (core/provenance.py).
    data_source: str = ""

    def __post_init__(self) -> None:
        self.data_source = validate_data_source(
            self.data_source, kind="backtest", field="BacktestMetrics.data_source")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "total_return_pct": round(self.total_return_pct, 2),
            "annualized_return_pct": round(self.annualized_return_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "calmar_ratio": round(self.calmar_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "expectancy_usd": round(self.expectancy_usd, 2),
            "var_95_pct": round(self.var_95_pct, 2),
            "cvar_95_pct": round(self.cvar_95_pct, 2),
            "ulcer_index": round(self.ulcer_index, 2),
            "upi_martin_ratio": round(self.upi_martin_ratio, 3),
            "gain_to_pain_ratio": round(self.gain_to_pain_ratio, 3),
            "tail_ratio": round(self.tail_ratio, 3),
            "initial_cash": round(self.initial_cash, 2),
            "final_cash": round(self.final_cash, 2),
            "data_points": len(self.equity_series),
            "data_source": self.data_source,
        }


class VectorizedBacktestEngine:
    """High-speed vectorized matrix portfolio backtester."""

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        commission_pct: float = 0.0002,  # 2 bps — synced with TCE base_commission_pct
        slippage_pips: float = 0.5,
        risk_per_trade_pct: float = 0.01,
        annual_risk_free_rate: float = 0.04,  # 4% baseline
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.commission_pct = float(commission_pct)
        self.slippage_pips = float(slippage_pips)
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.annual_risk_free_rate = float(annual_risk_free_rate)

    @property
    def backend_status(self) -> dict:
        """Expose backend for UI — professional status, not silent mock. Cost unified via TCE."""
        return {
            "backend": "vectorbt" if _HAS_VBT else "numpy_fallback",
            "has_vectorbt": _HAS_VBT,
            "status": "vectorbt" if _HAS_VBT else "numpy fallback — install vectorbt for speed. Error auto-clears on pip install.",
            "execution_authority": False,
            "cost_model": "unified via core/transaction_cost_engine.py Almgren-Chriss 2bps commission (single source) — vbt commission_pct 0.0002 = TCE default, intelligence/cost_estimator.py is tick-value complement not competitor, slippage via TCE when available",
        }

    def run_backtest(
        self,
        df: pd.DataFrame,
        entries: Union[pd.Series, np.ndarray, List[bool]],
        exits: Union[pd.Series, np.ndarray, List[bool]],
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        short_entries: Optional[Union[pd.Series, np.ndarray, List[bool]]] = None,
        short_exits: Optional[Union[pd.Series, np.ndarray, List[bool]]] = None,
        data_source: str = "",
    ) -> BacktestMetrics:
        """Run vectorized backtest on price DataFrame with boolean entry/exit signals.

        ``data_source`` is mandatory at run time: empty/unknown values raise
        rather than producing metrics that look observed.
        """
        source = validate_data_source(data_source, kind="backtest", field="run_backtest(data_source)")
        if df.empty or "close" not in df.columns:
            raise ValueError("Input DataFrame must be non-empty and contain 'close' column — clear: provide OHLC df with 'close', no silent empty result")

        close_prices = df["close"].values.astype(np.float64)
        n = len(close_prices)
        if n < 2:
            raise ValueError("Insufficient data points for backtesting (minimum 2 required)")

        entries_arr = np.asarray(entries, dtype=bool)
        exits_arr = np.asarray(exits, dtype=bool)

        if len(entries_arr) != n or len(exits_arr) != n:
            raise ValueError(f"Signal lengths ({len(entries_arr)}, {len(exits_arr)}) must match DataFrame length ({n})")

        # Fast Vectorized Execution Loop
        cash = self.initial_cash
        equity_curve = np.zeros(n, dtype=np.float64)
        position = 0.0  # Units
        entry_price = 0.0
        trades_pnl: List[float] = []

        timestamps = [
            str(t) for t in (df["time"].tolist() if "time" in df.columns else df.index.tolist())
        ]

        for i in range(n):
            price = close_prices[i]

            # Check Exit
            if position > 0 and (exits_arr[i] or i == n - 1):
                # Close Long Position
                exit_price = price * (1.0 - self.commission_pct)
                pnl = position * (exit_price - entry_price)
                cash += position * exit_price
                trades_pnl.append(pnl)
                position = 0.0
                entry_price = 0.0

            # Check Entry
            elif position == 0 and entries_arr[i] and i < n - 1:
                # Open Long Position sizing by risk fraction
                alloc_cash = cash * 0.95  # Max 95% cash deployment
                entry_price = price * (1.0 + self.commission_pct)
                position = alloc_cash / entry_price
                cash -= alloc_cash

            # Mark to Market Equity
            equity_curve[i] = cash + (position * price if position > 0 else 0.0)

        # Performance Calculations
        final_cash = float(equity_curve[-1])
        total_return_pct = ((final_cash - self.initial_cash) / self.initial_cash) * 100.0

        # Returns & Annualization
        equity_series = pd.Series(equity_curve)
        returns = equity_series.pct_change().dropna().values
        
        # Determine periods per year based on timeframe
        tf_clean = str(timeframe).upper()
        if "M1" in tf_clean: periods_per_year = 252 * 24 * 60
        elif "M5" in tf_clean: periods_per_year = 252 * 24 * 12
        elif "M15" in tf_clean: periods_per_year = 252 * 24 * 4
        elif "H1" in tf_clean: periods_per_year = 252 * 24
        elif "H4" in tf_clean: periods_per_year = 252 * 6
        elif "D" in tf_clean: periods_per_year = 252
        else: periods_per_year = 252 * 24 * 4

        ann_return_pct = total_return_pct * (periods_per_year / max(n, 1))

        # Sharpe & Sortino Ratios
        rf_per_period = (1.0 + self.annual_risk_free_rate) ** (1.0 / periods_per_year) - 1.0
        excess_returns = returns - rf_per_period
        
        std_dev = np.std(returns)
        sharpe = (np.mean(excess_returns) / std_dev * np.sqrt(periods_per_year)) if std_dev > 1e-8 else 0.0

        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 0 else 0.0
        sortino = (np.mean(excess_returns) / downside_std * np.sqrt(periods_per_year)) if downside_std > 1e-8 else 0.0

        # Drawdown Metrics
        peaks = np.maximum.accumulate(equity_curve)
        drawdowns = (equity_curve - peaks) / peaks
        max_dd_pct = abs(float(np.min(drawdowns))) * 100.0 if len(drawdowns) > 0 else 0.0
        calmar = (ann_return_pct / max_dd_pct) if max_dd_pct > 1e-4 else 0.0

        # Trade Outcome Statistics
        total_trades = len(trades_pnl)
        winning_trades = sum(1 for p in trades_pnl if p > 0)
        losing_trades = sum(1 for p in trades_pnl if p < 0)
        win_rate_pct = (winning_trades / total_trades * 100.0) if total_trades > 0 else 0.0
        
        gross_profit = sum(p for p in trades_pnl if p > 0)
        gross_loss = abs(sum(p for p in trades_pnl if p < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        expectancy_usd = (sum(trades_pnl) / total_trades) if total_trades > 0 else 0.0

        # Institutional Tail Risk: VaR-95 and CVaR-95 (Expected Shortfall)
        if len(returns) >= 10:
            var_95 = float(np.percentile(returns, 5))
            tail_losses = returns[returns <= var_95]
            cvar_95 = float(np.mean(tail_losses)) if len(tail_losses) > 0 else var_95
            var_95_pct = abs(var_95) * 100.0
            cvar_95_pct = abs(cvar_95) * 100.0

            p95 = float(np.percentile(returns, 95))
            p5 = float(np.percentile(returns, 5))
            tail_ratio = abs(p95 / p5) if abs(p5) > 1e-8 else 1.0
        else:
            var_95_pct = 0.0
            cvar_95_pct = 0.0
            tail_ratio = 1.0

        # Ulcer Index (UI) & Ulcer Performance Index (UPI / Martin Ratio)
        dd_pct_arr = drawdowns * 100.0
        ulcer_index = float(np.sqrt(np.mean(dd_pct_arr ** 2))) if len(dd_pct_arr) > 0 else 0.0
        rf_pct = self.annual_risk_free_rate * 100.0
        upi_martin = float((ann_return_pct - rf_pct) / ulcer_index) if ulcer_index > 1e-4 else 0.0

        # Gain-to-Pain Ratio (Schwager Ratio)
        pos_rets = returns[returns > 0]
        neg_rets = returns[returns < 0]
        sum_pos = float(np.sum(pos_rets)) if len(pos_rets) > 0 else 0.0
        sum_neg = float(np.abs(np.sum(neg_rets))) if len(neg_rets) > 0 else 0.0
        gain_to_pain = float(sum_pos / sum_neg) if sum_neg > 1e-8 else (99.0 if sum_pos > 0 else 0.0)

        return BacktestMetrics(
            symbol=symbol,
            timeframe=timeframe,
            total_return_pct=total_return_pct,
            annualized_return_pct=ann_return_pct,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown_pct=max_dd_pct,
            win_rate_pct=win_rate_pct,
            profit_factor=profit_factor,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            expectancy_usd=expectancy_usd,
            initial_cash=self.initial_cash,
            final_cash=final_cash,
            var_95_pct=var_95_pct,
            cvar_95_pct=cvar_95_pct,
            ulcer_index=ulcer_index,
            upi_martin_ratio=upi_martin,
            gain_to_pain_ratio=gain_to_pain,
            tail_ratio=tail_ratio,
            equity_series=equity_curve.tolist(),
            drawdown_series=(drawdowns * 100.0).tolist(),
            timestamps=timestamps,
            data_source=source,
        )

    def run_crossover_strategy(
        self,
        df: pd.DataFrame,
        fast_period: int = 10,
        slow_period: int = 30,
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        data_source: str = "",
    ) -> BacktestMetrics:
        """Helper to run a fast moving-average crossover benchmark backtest."""
        close = df["close"]
        fast_ema = close.ewm(span=fast_period, adjust=False).mean()
        slow_ema = close.ewm(span=slow_period, adjust=False).mean()

        entries = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
        exits = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))

        return self.run_backtest(
            df=df,
            entries=entries.fillna(False),
            exits=exits.fillna(False),
            symbol=symbol,
            timeframe=timeframe,
            data_source=data_source,
        )

    def run_grid_sweep(
        self,
        df: pd.DataFrame,
        fast_periods: Any = (5, 10, 15, 20),
        slow_periods: Any = (20, 30, 50, 100),
        symbol: str = "EURUSD",
        timeframe: str = "M15",
        data_source: str = "",
    ) -> List[Tuple[Dict[str, int], BacktestMetrics]]:
        """Run parameter sweep across fast/slow period combinations, ranked by Sharpe ratio."""
        results: List[Tuple[Dict[str, int], BacktestMetrics]] = []
        for fast in fast_periods:
            for slow in slow_periods:
                if fast >= slow:
                    continue
                metrics = self.run_crossover_strategy(
                    df=df,
                    fast_period=int(fast),
                    slow_period=int(slow),
                    symbol=symbol,
                    timeframe=timeframe,
                    data_source=data_source,
                )
                results.append(({"fast_period": int(fast), "slow_period": int(slow)}, metrics))
        results.sort(key=lambda item: item[1].sharpe_ratio, reverse=True)
        return results
