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

