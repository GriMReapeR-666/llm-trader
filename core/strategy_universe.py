"""Strategy Universe Matrix Engine — Core Taxonomy & Fast Evaluation.

Formalizes the 15 strategy styles and 12 trading types across market regimes,
capital scales, and asset classes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numba as nb
import numpy as np
import polars as pl



class StrategyStyle(str, Enum):
    TREND_FOLLOWING = "TREND_FOLLOWING"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    MOMENTUM = "MOMENTUM"
    VOLATILITY = "VOLATILITY"
    CARRY = "CARRY"
    STATISTICAL_ARBITRAGE = "STATISTICAL_ARBITRAGE"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    CROSS_ASSET_MACRO = "CROSS_ASSET_MACRO"
    INTRADAY_SCALPING = "INTRADAY_SCALPING"
    SWING = "SWING"
    POSITION_TRADING = "POSITION_TRADING"
    ML_SIGNAL = "ML_SIGNAL"
    RL_POLICY = "RL_POLICY"


class TradingType(str, Enum):
    LONG_ONLY = "LONG_ONLY"
    SHORT_ONLY = "SHORT_ONLY"
    LONG_SHORT = "LONG_SHORT"
    DIRECTIONAL = "DIRECTIONAL"
    PAIRS_SPREAD = "PAIRS_SPREAD"
    FUTURES_PERP = "FUTURES_PERP"
    OPTIONS_BASED = "OPTIONS_BASED"
    SPOT = "SPOT"
    CROSS_EXCHANGE_ARB = "CROSS_EXCHANGE_ARB"
    EXECUTION_ALGO = "EXECUTION_ALGO"
    HEDGED_PORTFOLIO = "HEDGED_PORTFOLIO"
    MULTI_ASSET_ALLOCATION = "MULTI_ASSET_ALLOCATION"


class MarketRegime(str, Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_TREND = "BEAR_TREND"
    SIDEWAYS_CONSOLIDATION = "SIDEWAYS_CONSOLIDATION"
    HIGH_VOL_EXPANSION = "HIGH_VOL_EXPANSION"
    LOW_VOL_COMPRESSION = "LOW_VOL_COMPRESSION"
    STRESS_LIQUIDITY_SHOCK = "STRESS_LIQUIDITY_SHOCK"


class CapitalScale(str, Enum):
    MICRO = "MICRO"            # $500 - $2,000
    SMALL = "SMALL"            # $10,000
    MEDIUM = "MEDIUM"          # $100,000
    INSTITUTIONAL = "INSTITUTIONAL"  # $1,000,000+


@dataclass
class StrategySpec:
    strategy_id: str
    name: str
    style: StrategyStyle
    trading_type: TradingType
    regime_suitability: List[MarketRegime]
    target_symbols: List[str]
    timeframes: List[str]
    capital_scale: CapitalScale = CapitalScale.SMALL
    parameters: Dict[str, Any] = field(default_factory=dict)
    execution_authority: bool = False
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "style": self.style.value,
            "trading_type": self.trading_type.value,
            "regime_suitability": [r.value for r in self.regime_suitability],
            "target_symbols": self.target_symbols,
            "timeframes": self.timeframes,
            "capital_scale": self.capital_scale.value,
            "parameters": self.parameters,
            "execution_authority": self.execution_authority,
            "created_at": self.created_at,
        }


# ============================================================================
# High-Speed Numba @njit Indicator & Simulation Kernels
# ============================================================================

@nb.njit(fastmath=True)
def compute_ema_numba(prices: np.ndarray, period: int) -> np.ndarray:
    """Recursive exponential moving average with alpha = 2.0 / (period + 1.0)."""
    n = len(prices)
    ema = np.empty(n, dtype=np.float64)
    if n == 0:
        return ema
    alpha = 2.0 / (period + 1.0)
    ema[0] = prices[0]
    for i in range(1, n):
        ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i - 1]
    return ema


@nb.njit(fastmath=True)
def compute_bollinger_bands_numba(
    prices: np.ndarray, period: int, num_std: float = 2.0
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Computes (upper, mid, lower) rolling mean and standard deviation bands."""
    n = len(prices)
    mid = np.empty(n, dtype=np.float64)
    upper = np.empty(n, dtype=np.float64)
    lower = np.empty(n, dtype=np.float64)
    for i in range(n):
        start_idx = max(0, i - period + 1)
        window = prices[start_idx : i + 1]
        m = np.mean(window)
        s = np.std(window)
        mid[i] = m
        upper[i] = m + num_std * s
        lower[i] = m - num_std * s
    return upper, mid, lower


@nb.njit(fastmath=True)
def simulate_positions_numba(
    prices: np.ndarray,
    signals: np.ndarray,
    spread_bps: float = 2.0,
    slippage_bps: float = 1.0,
    initial_capital: float = 10000.0,
) -> Tuple[np.ndarray, np.ndarray, int, float, float]:
    """Vectorized position state machine with transaction costs.

    Returns:
        (equity, trade_pnls, trade_count, total_return, max_dd)
    """
    n = len(prices)
    if n == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
            0,
            0.0,
            0.0,
        )

    equity = np.empty(n, dtype=np.float64)
    equity[0] = initial_capital
    trade_pnls = np.empty(n, dtype=np.float64)
    trade_idx = 0
    trade_count = 0

    curr_pos = 0  # -1, 0, 1
    entry_price = 0.0
    cost_fraction = (spread_bps + slippage_bps) / 10000.0

    peak_equity = initial_capital
    max_dd = 0.0

    for i in range(1, n):
        desired_pos = signals[i - 1]  # enter next bar on previous signal
        price = prices[i]
        bar_pnl = 0.0

        if desired_pos != curr_pos:
            # Close existing position
            if curr_pos != 0:
                trade_count += 1
                exit_price = price * (1.0 - cost_fraction if curr_pos == 1 else 1.0 + cost_fraction)
                if curr_pos == 1:
                    raw_trade_pnl = (exit_price - entry_price) / entry_price * equity[i - 1]
                else:
                    raw_trade_pnl = (entry_price - exit_price) / entry_price * equity[i - 1]
                trade_pnls[trade_idx] = raw_trade_pnl
                trade_idx += 1
                bar_pnl += raw_trade_pnl

            # Open new position
            curr_pos = desired_pos
            if curr_pos != 0:
                entry_price = price * (1.0 + cost_fraction if curr_pos == 1 else 1.0 - cost_fraction)
        else:
            if curr_pos != 0:
                prev_price = prices[i - 1]
                if curr_pos == 1:
                    bar_pnl = (price - prev_price) / prev_price * equity[i - 1]
                else:
                    bar_pnl = (prev_price - price) / prev_price * equity[i - 1]

        equity[i] = max(0.0, equity[i - 1] + bar_pnl)
        if equity[i] > peak_equity:
            peak_equity = equity[i]
        dd = (peak_equity - equity[i]) / peak_equity if peak_equity > 0.0 else 0.0
        if dd > max_dd:
            max_dd = dd

    total_return = (equity[-1] - initial_capital) / initial_capital if initial_capital > 0.0 else 0.0
    return equity, trade_pnls[:trade_idx], trade_count, total_return, max_dd


@nb.njit(fastmath=True)
def calculate_fast_metrics_numba(
    equity: np.ndarray, returns: np.ndarray
) -> Tuple[float, float, float, float, float, float]:
    """Computes annualized Sharpe, Sortino, Calmar, Win Rate, Profit Factor, and Max Drawdown."""
    n = len(returns)
    if n < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    sharpe = (mean_ret / std_ret * np.sqrt(252.0)) if std_ret > 1e-8 else 0.0

    # Downside std for Sortino
    neg_rets = returns[returns < 0.0]
    downside_std = np.std(neg_rets) if len(neg_rets) > 1 else std_ret
    sortino = (mean_ret / downside_std * np.sqrt(252.0)) if downside_std > 1e-8 else 0.0

    # Drawdown
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0.0 else 0.0
        if dd > max_dd:
            max_dd = dd

    total_return = (equity[-1] - equity[0]) / equity[0] if equity[0] > 0.0 else 0.0
    calmar = (total_return / max_dd) if max_dd > 1e-6 else 0.0

    # Win rate & Profit factor
    pos = returns[returns > 0.0]
    neg = returns[returns < 0.0]
    win_rate = len(pos) / n if n > 0 else 0.0
    sum_pos = np.sum(pos) if len(pos) > 0 else 0.0
    sum_neg = np.abs(np.sum(neg)) if len(neg) > 0 else 0.0
    profit_factor = (sum_pos / sum_neg) if sum_neg > 1e-8 else (10.0 if sum_pos > 0 else 0.0)

    return sharpe, sortino, calmar, win_rate, profit_factor, max_dd


# ============================================================================
# Polars Data Ingestion & Universe Generation Engine
# ============================================================================

def load_market_dataset_polars(data_dict: Dict[str, Any]) -> pl.DataFrame:
    """Ingest market dictionary or raw columns into a Polars DataFrame with float casting."""
    df = pl.DataFrame(data_dict)
    if "close" in df.columns:
        df = df.with_columns(pl.col("close").cast(pl.Float64))
    return df


class StrategyUniverseGenerator:
    """Generates strategy specifications across the 15x12 universe matrix."""

    def __init__(self, seed: int = 42):
        self.seed = seed

    def generate_matrix(
        self,
        symbols: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        scales: Optional[List[CapitalScale]] = None,
    ) -> List[StrategySpec]:
        target_symbols = symbols or ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD"]
        target_tfs = timeframes or ["M15", "H1", "D1"]
        target_scales = scales or [CapitalScale.SMALL]

        matrix: List[StrategySpec] = []
        for style in StrategyStyle:
            # Map style to primary compatible trading types
            compatible_types = [TradingType.LONG_SHORT, TradingType.DIRECTIONAL]
            if style == StrategyStyle.STATISTICAL_ARBITRAGE:
                compatible_types = [TradingType.PAIRS_SPREAD, TradingType.HEDGED_PORTFOLIO]
            elif style == StrategyStyle.MARKET_NEUTRAL:
                compatible_types = [TradingType.HEDGED_PORTFOLIO, TradingType.LONG_SHORT]
            elif style == StrategyStyle.CARRY:
                compatible_types = [TradingType.LONG_ONLY, TradingType.MULTI_ASSET_ALLOCATION]
            elif style == StrategyStyle.INTRADAY_SCALPING:
                compatible_types = [TradingType.EXECUTION_ALGO, TradingType.LONG_SHORT]

            for t_type in compatible_types:
                strat_id = f"{style.value.lower()}_{t_type.value.lower()}_{target_symbols[0].lower()}"
                spec = StrategySpec(
                    strategy_id=strat_id,
                    name=f"{style.value.replace('_', ' ').title()} - {t_type.value.replace('_', ' ').title()}",
                    style=style,
                    trading_type=t_type,
                    regime_suitability=[
                        MarketRegime.BULL_TREND if "TREND" in style.value else MarketRegime.SIDEWAYS_CONSOLIDATION,
                        MarketRegime.HIGH_VOL_EXPANSION if "VOL" in style.value else MarketRegime.LOW_VOL_COMPRESSION,
                    ],
                    target_symbols=target_symbols,
                    timeframes=target_tfs,
                    capital_scale=target_scales[0],
                    parameters={"fast_period": 10, "slow_period": 30, "threshold": 2.0},
                )
                matrix.append(spec)
        return matrix

    def generate_baseline_signals(self, spec: StrategySpec, df: pl.DataFrame) -> np.ndarray:
        """Compute deterministic baseline signals using Numba kernels on Polars columns."""
        prices = df["close"].to_numpy()
        n = len(prices)
        signals = np.zeros(n, dtype=np.int32)
        if n == 0:
            return signals

        if spec.style == StrategyStyle.TREND_FOLLOWING:
            fast_p = int(spec.parameters.get("fast_period", 10))
            slow_p = int(spec.parameters.get("slow_period", 30))
            fast = compute_ema_numba(prices, fast_p)
            slow = compute_ema_numba(prices, slow_p)
            for i in range(n):
                if fast[i] > slow[i]:
                    signals[i] = 1
                elif fast[i] < slow[i]:
                    signals[i] = -1 if spec.trading_type == TradingType.LONG_SHORT else 0

        elif spec.style == StrategyStyle.MEAN_REVERSION:
            period = int(spec.parameters.get("fast_period", 20))
            threshold = float(spec.parameters.get("threshold", 2.0))
            upper, mid, lower = compute_bollinger_bands_numba(
                prices, period, threshold
            )
            for i in range(n):
                if prices[i] < lower[i]:
                    signals[i] = 1
                elif prices[i] > upper[i]:
                    signals[i] = -1 if spec.trading_type == TradingType.LONG_SHORT else 0

        else:
            # Deterministic momentum breakout baseline for other styles
            lookback = 15
            for i in range(lookback, n):
                if prices[i] > np.max(prices[i - lookback : i]):
                    signals[i] = 1
                elif prices[i] < np.min(prices[i - lookback : i]):
                    signals[i] = -1 if spec.trading_type == TradingType.LONG_SHORT else 0

        return signals


# ============================================================================
# Parallel Universe Diagnostics Runner & Telemetry Event Emission
# ============================================================================

@dataclass
class UniverseEvaluationReport:
    total_strategies: int
    evaluated_at: float
    results: List[Dict[str, Any]]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_strategies": self.total_strategies,
            "evaluated_at": self.evaluated_at,
            "results": self.results,
            "execution_authority": self.execution_authority,
        }


def evaluate_strategy_universe(
    specs: List[StrategySpec],
    dataset: pl.DataFrame,
    spread_bps: float = 2.0,
    slippage_bps: float = 1.0,
    initial_capital: float = 10000.0,
    emit_telemetry: bool = True,
) -> UniverseEvaluationReport:
    """Evaluates all strategy specs across common dataset using Polars + Numba acceleration."""
    if "close" in dataset.columns and dataset.height > 0:
        prices = dataset["close"].to_numpy()
    else:
        prices = np.empty(0, dtype=np.float64)

    generator = StrategyUniverseGenerator()
    results: List[Dict[str, Any]] = []

    # Optional EventBus emission
    emit_event_fn = None
    if emit_telemetry:
        try:
            import core.agent_event_bus as aeb
            emit_event_fn = getattr(aeb, "emit_agent_event", None)
        except (ImportError, AttributeError):
            emit_event_fn = None

    for spec in specs:
        t0 = time.perf_counter()
        signals = generator.generate_baseline_signals(spec, dataset)
        equity, trade_pnls, trade_count, total_ret, max_dd = simulate_positions_numba(
            prices,
            signals,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            initial_capital=initial_capital,
        )
        returns = (
            np.diff(equity) / equity[:-1]
            if len(equity) > 1
            else np.empty(0, dtype=np.float64)
        )
        sharpe, sortino, calmar, win_rate, pf, max_dd_pct = calculate_fast_metrics_numba(
            equity, returns
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0

        res = {
            "strategy_id": spec.strategy_id,
            "style": spec.style.value,
            "trading_type": spec.trading_type.value,
            "total_trades": trade_count,
            "total_return_pct": round(total_ret * 100.0, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "calmar": round(calmar, 3),
            "max_drawdown_pct": round(max_dd_pct * 100.0, 2),
            "win_rate_pct": round(win_rate * 100.0, 2),
            "profit_factor": round(pf, 2),
            "compute_latency_ms": round(latency_ms, 2),
        }
        results.append(res)

        if emit_event_fn:
            try:
                emit_event_fn(
                    sender="StrategyUniverseEngine",
                    event_type="STRATEGY_UNIVERSE_EVALUATED",
                    payload=res,
                )
            except Exception:
                pass

    return UniverseEvaluationReport(
        total_strategies=len(results),
        evaluated_at=time.time(),
        results=results,
        execution_authority=False,
    )



