"""Alpha Ensemble Backtest Harness — IC-Weighted vs Equal-Weight Validation.

Runs both weighting schemes on the same OHLCV data and compares:
  - Sharpe ratio, Sortino ratio, Calmar ratio
  - Max drawdown, win rate, profit factor
  - Trade frequency / turnover
  - Per-regime performance (trending vs ranging)

Usage:
    python research/alpha_backtest_harness.py
    python research/alpha_backtest_harness.py --symbol EURUSD --timeframe H1 --bars 5000

The harness generates synthetic OHLCV with embedded alpha signals so the
comparison is deterministic and reproducible.  It also supports loading
real data from CSV/JSON if available.
"""
from __future__ import annotations
import logging
import time

logger = logging.getLogger(__name__)

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ── project imports ──────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.alpha_factors import (
    _ALPHA_REGISTRY,
    compute_alpha_factors,
)
from core.alpha_ensemble import (
    EnsembleResult,
    combine_alphas,
    interpret_composite,
    zscore_normalize,
)

# MT5 copy_rates_from_pos rejects oversized per-call counts (~100k).
_MT5_MAX_BARS_PER_CALL = 50_000


# ═════════════════════════════════════════════════════════════════════════════
# 1. Synthetic OHLCV Generator
# ═════════════════════════════════════════════════════════════════════════════

def generate_synthetic_ohlcv(
    n_bars: int = 3000,
    base_price: float = 1.1000,
    volatility: float = 0.0008,
    trend_strength: float = 0.0001,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate realistic synthetic OHLCV data with regime switches.

    The price process has:
      - Mean-reverting noise (Ornstein-Uhlenbeck)
      - Occasional trend regimes (~20% of the time)
      - Realistic high/low wicks
      - Volume correlated with volatility
    """
    rng = np.random.RandomState(seed)

    # Price process with regime switching
    close = np.zeros(n_bars, dtype=np.float64)
    close[0] = base_price

    # Regime: 0 = mean-revert, 1 = trend-up, -1 = trend-down
    regime = np.zeros(n_bars, dtype=np.int32)
    for i in range(1, n_bars):
        if rng.random() < 0.02:  # 2% chance of regime switch
            regime[i] = rng.choice([-1, 0, 0, 0, 1])
        else:
            regime[i] = regime[i - 1]

    for i in range(1, n_bars):
        noise = rng.normal(0, volatility)
        trend = regime[i] * trend_strength
        mean_rev = -0.01 * (close[i - 1] - base_price)
        close[i] = close[i - 1] * (1 + noise + trend + mean_rev)

    # High / Low with realistic wicks
    high = close * (1 + np.abs(rng.normal(0, volatility * 0.5, n_bars)))
    low = close * (1 - np.abs(rng.normal(0, volatility * 0.5, n_bars)))
    open_ = np.roll(close, 1)
    open_[0] = base_price

    # Volume: correlated with absolute returns and regime
    abs_ret = np.abs(np.diff(close, prepend=close[0])) / np.maximum(close, 1e-10)
    base_vol = 1000.0
    volume = base_vol * (1 + 10 * abs_ret) * (1 + 0.5 * np.abs(regime).astype(float))
    volume *= rng.uniform(0.7, 1.3, n_bars)

    # Time index
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h")

    return pd.DataFrame({
        "time": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def _initialize_with_env(mt5_mod: Any) -> bool:
    """Retry MT5 initialize() with credentials loaded from the project .env.

    Mirrors mt5_trader's login convention: MT5_LOGIN / MT5_PASSWORD /
    MT5_SERVER environment variables (via python-dotenv when available).
    Returns False without leaking credentials when the env vars are absent.
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    login = os.environ.get("MT5_LOGIN", "")
    password = os.environ.get("MT5_PASSWORD", "")
    server = os.environ.get("MT5_SERVER", "")
    if not (login and password and server):
        return False
    try:
        return bool(mt5_mod.initialize(
            login=int(login), password=password, server=server,
        ))
    except (TypeError, ValueError):
        return False


def load_mt5_bars(symbol: str, timeframe: str, n_bars: int) -> pd.DataFrame:
    """Load real broker OHLCV bars via the project's fail-closed MT5 path.

    Uses data.market_data_provider.fetch_ohlc — the same zero-mock data path
    the live loop uses: it refuses synthetic fallbacks and raises a clear
    RuntimeError when the MT5 terminal is unavailable or the symbol is
    unknown to the broker.

    Standalone callers (CLI) may not have the terminal IPC session open yet,
    so this explicitly initializes MetaTrader5 when needed and leaves the
    process-global session attached (the engine loop and other research
    pipelines share one session per process; tearing it down here would
    kill their live connection). The OS closes the socket on exit.

    Returns a frame matching the harness contract:
        columns time/open/high/low/close/volume, sorted ascending by time.
    """
    import MetaTrader5 as _mt5
    from data.market_data_provider import fetch_ohlc

    if _mt5.terminal_info() is None:
        from data.market_data_provider import (
            _ATTACH_MAX_ATTEMPTS,
            _ATTACH_BACKOFF_S,
            _ATTACH_SLEEP,
        )

        # Own the process-global MT5 session (leave it attached; tearing it
        # down here would kill the engine loop / other research pipelines in
        # the same process).  Retry discipline mirrors _ensure_mt5_attached()
        # in data.market_data_provider — same attempt/backoff ladder and the
        # same "skip the sleep on the first attempt" convention — but this path
        # is for an explicit initialize (possibly credentialed) call site, so a
        # raising initialize() still fails closed fast while only transient
        # False/None outcomes are retried.  AS OF writing, load_mt5_bars owns the
        # session directly; a later refactor should route this path through
        # _ensure_mt5_attached() and layer an explicit initialize on top, to avoid
        # maintaining a second copy of the ladder.
        init = getattr(_mt5, "initialize", None)
        if init is None:
            raise RuntimeError(
                "MetaTrader5.initialize is unavailable; "
                "start the MetaTrader 5 terminal and retry"
            )

        attached = False
        for attempt in range(_ATTACH_MAX_ATTEMPTS):
            if attempt:
                _ATTACH_SLEEP(_ATTACH_BACKOFF_S[min(attempt - 1,
                                                    len(_ATTACH_BACKOFF_S) - 1)])
            try:
                attached = bool(init())
            except Exception as exc:
                logger.warning(
                    "MT5 init attempt %d/%d failed with exception; failing closed: %s",
                    attempt + 1, _ATTACH_MAX_ATTEMPTS, exc,
                )
                raise RuntimeError(
                    "MT5 initialize() failed: %s — "
                    "start the MetaTrader 5 terminal and retry"
                    % (exc,)
                ) from exc
            if not attached:
                logger.warning(
                    "MT5 init attempt %d/%d refused by terminal; retrying in %.2fs",
                    attempt + 1, _ATTACH_MAX_ATTEMPTS,
                    _ATTACH_BACKOFF_S[min(attempt, len(_ATTACH_BACKOFF_S) - 1)],
                )
                continue
            # init() returned True — verify the terminal is actually reachable,
            # matching _ensure_mt5_attached()'s post-success terminal_info check.
            try:
                if _mt5.terminal_info() is not None:
                    attached = True
                    break
            except Exception:
                pass  # can't confirm reachability; treat the init() True as pending a retry
            attached = False
        else:
            raise RuntimeError(
                "MT5 initialize() failed after %d attempts; "
                "start the MetaTrader 5 terminal and retry"
                % _ATTACH_MAX_ATTEMPTS
            )

    if n_bars <= _MT5_MAX_BARS_PER_CALL:
        raw = fetch_ohlc(symbol, timeframe, count=n_bars)
    else:
        # copy_rates_from_pos rejects very large counts per call — page
        # backward in 50k-bar chunks (oldest pages last) and stitch.
        import MetaTrader5 as mt5_direct
        from data.market_data_provider import tf_const as _tf_const

        tf = _tf_const(timeframe)
        frames = []
        start = 0
        while len(frames) * _MT5_MAX_BARS_PER_CALL < n_bars:
            rates = mt5_direct.copy_rates_from_pos(symbol, tf, start, _MT5_MAX_BARS_PER_CALL)
            if rates is None or len(rates) == 0:
                break  # history exhausted or terminal hiccup — use what we have
            page = pd.DataFrame(rates)
            # raw rates carry time as epoch seconds (fetch_ohlc normally
            # does this conversion for the single-call path)
            page["time"] = pd.to_datetime(page["time"], unit="s")
            frames.append(page)
            if len(rates) < _MT5_MAX_BARS_PER_CALL:
                break  # short page = oldest reachable bar reached
            start += _MT5_MAX_BARS_PER_CALL
        if not frames:
            raise RuntimeError(
                f"MT5 returned no bars for {symbol}/{timeframe} across paged "
                "requests; refusing external or synthetic fallback"
            )
        raw = pd.concat(frames).drop_duplicates(subset="time").sort_values("time")
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"MT5 returned no bars for {symbol} {timeframe}")

    df = raw.reset_index()  # 'time' index -> column
    if "volume" not in df.columns:
        if "tick_volume" in df.columns:
            df = df.rename(columns={"tick_volume": "volume"})
        elif "real_volume" in df.columns:
            df = df.rename(columns={"real_volume": "volume"})

    keep = [c for c in ("time", "open", "high", "low", "close", "volume") if c in df.columns]
    missing = {"open", "high", "low", "close"} - set(keep)
    if missing:
        raise RuntimeError(
            f"MT5 bars missing OHLC columns {sorted(missing)}; got {list(df.columns)}"
        )

    df = df[keep]
    df["time"] = pd.to_datetime(df["time"])
    df = (
        df.dropna(subset=["open", "high", "low", "close"])
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 2. Signal Generation from Ensemble Composite
# ═════════════════════════════════════════════════════════════════════════════

def composite_to_signals(
    composite: pd.Series,
    entry_threshold: float = 2.0,
    exit_threshold: float = 0.0,
) -> Tuple[pd.Series, pd.Series]:
    """Convert a z-scored composite signal to boolean entry/exit signals.

    Rules:
      - LONG entry:  composite crosses above +entry_threshold
      - LONG exit:   composite crosses below +exit_threshold
      - SHORT entry: composite crosses below -entry_threshold
      - SHORT exit:  composite crosses above -exit_threshold

    Returns (long_entries, long_exits) boolean Series.
    """
    prev = composite.shift(1)

    # Long entries: cross above threshold
    long_entries = (composite > entry_threshold) & (prev <= entry_threshold)
    # Long exits: cross below exit threshold
    long_exits = (composite < exit_threshold) & (prev >= exit_threshold)

    return long_entries.fillna(False), long_exits.fillna(False)


def composite_to_long_short_signals(
    composite: pd.Series,
    entry_threshold: float = 2.0,
    exit_threshold: float = 0.0,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Convert composite to long+short entry/exit signals."""
    prev = composite.shift(1)

    long_entries = (composite > entry_threshold) & (prev <= entry_threshold)
    long_exits = (composite < exit_threshold) & (prev >= exit_threshold)
    short_entries = (composite < -entry_threshold) & (prev >= -entry_threshold)
    short_exits = (composite > -exit_threshold) & (prev <= -exit_threshold)

    return (
        long_entries.fillna(False),
        long_exits.fillna(False),
        short_entries.fillna(False),
        short_exits.fillna(False),
    )


def apply_cost_edge_filter(
    composite: pd.Series,
    close: pd.Series,
    long_entries: pd.Series,
    long_exits: pd.Series,
    short_entries: pd.Series,
    short_exits: pd.Series,
    *,
    round_trip_cost_pct: float,
    min_edge_multiple: float = 1.5,
    lookback: int = 480,
    horizon: int = 4,
    min_history: int = 120,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, Dict[str, Any]]:
    """Block entries whose recently-measured edge cannot pay for friction.

    The 4-year real-data run showed ~9k trades at PF 0.22: the composite's
    raw z-cross fires far more often than its predictive slope can cover the
    round-trip cost. This filter estimates, at each bar, the return per
    z-unit over the intended holding horizon from a *causal* rolling
    regression, and only lets an entry through when

        |beta * z| * horizon  >=  min_edge_multiple * round_trip_cost

    Causality: beta at bar t is fit on data up to t-1 (the regression inputs
    are shifted one bar back before rolling). Exits are never filtered —
    once in a trade, risk management owns the exit.

    Returns (filtered long/short entries, exits unchanged, stats dict).
    """
    z = composite.astype(float)
    # Forward one-bar return of close; the regression pairs it with the
    # composite known at the PRIOR bar, then beta itself is shifted again so
    # the value used at t only sees information through t-1.
    fwd = close.astype(float).pct_change().shift(-1)
    x = z.shift(1)
    y = fwd.shift(1)  # return realized between t-1 and t, known at t

    win = max(int(lookback), 30)
    mean_x = x.rolling(win, min_periods=min_history).mean()
    mean_y = y.rolling(win, min_periods=min_history).mean()
    cov = (x - mean_x).mul(y - mean_y).rolling(win, min_periods=min_history).mean()
    var = (x - mean_x).pow(2).rolling(win, min_periods=min_history).mean()
    beta = (cov / var.where(var > 1e-12)).shift(0).clip(-0.01, 0.01)

    # Expected return over the holding horizon if the recent slope persists.
    edge_long = beta * z * horizon          # z > 0 side
    edge_short = -(beta * z * horizon)      # z < 0 side

    required = float(min_edge_multiple) * float(round_trip_cost_pct)

    ok_long = (edge_long >= required).fillna(False)
    ok_short = (edge_short >= required).fillna(False)
    # Without enough history the filter is undecidable — stay out (fail-closed).

    fl = long_entries & ok_long
    fs = short_entries & ok_short

    raw_e = int(long_entries.sum() + short_entries.sum())
    kept_e = int(fl.sum() + fs.sum())
    stats: Dict[str, Any] = {
        "raw_entries": raw_e,
        "kept_entries": kept_e,
        "blocked_entries": raw_e - kept_e,
        "block_rate_pct": round(100.0 * (raw_e - kept_e) / raw_e, 2) if raw_e else 0.0,
        "required_edge_pct": round(required, 6),
        "lookback": win,
        "horizon": horizon,
    }
    return fl, long_exits, fs, short_exits, stats


def apply_trend_regime_gate(
    close: pd.Series,
    long_entries: pd.Series,
    long_exits: pd.Series,
    short_entries: pd.Series,
    short_exits: pd.Series,
    *,
    lookback: int = 20,
    min_strength: float = 0.5,
    min_history: int = 120,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, Dict[str, Any]]:
    """Allow entries only in trending bars — where the ensemble's edge lives.

    The 4-year real-data regime split showed the IC-weighted composite has
    positive Sharpe in trending bars and ~zero in ranging bars. This gate
    classifies each bar with a *causal* trend-strength measure (|rolling
    mean return| / rolling vol over `lookback` bars — an ADX-style ratio)
    and blocks entries in non-trending bars.

    Causality: the strength at bar t uses returns through t (rolling window
    ending at t, no forward shift), and the classification is compared
    against a **expanding-history quantile** of past strength values, never
    the full-sample median (which would leak the future).

    Exits are never filtered — once in a trade, risk management owns the
    exit regardless of regime.

    Returns (filtered long/short entries, exits unchanged, stats dict).
    """
    rets = close.astype(float).pct_change()
    win = max(int(lookback), 5)
    mean_abs = rets.rolling(win).mean().abs()
    vol = rets.rolling(win).std()
    strength = (mean_abs / vol.where(vol > 1e-12)).clip(upper=10.0)

    # Causal bar-level threshold: expanding quantile of PAST strength only.
    # At bar t the threshold sees strength[0..t-1] — strictly backward-looking.
    prev = strength.shift(1)
    q = float(np.clip(min_strength, 0.0, 1.0))
    thr = prev.expanding(min_periods=min_history).quantile(q)
    trending = (strength > thr).fillna(False)

    fl = long_entries & trending
    fs = short_entries & trending

    raw_e = int(long_entries.sum() + short_entries.sum())
    kept_e = int(fl.sum() + fs.sum())
    stats: Dict[str, Any] = {
        "raw_entries": raw_e,
        "kept_entries": kept_e,
        "blocked_entries": raw_e - kept_e,
        "block_rate_pct": round(100.0 * (raw_e - kept_e) / raw_e, 2) if raw_e else 0.0,
        "trending_bars": int(trending.sum()),
        "trending_share_pct": round(100.0 * trending.mean(), 2) if len(trending) else 0.0,
        "quantile": q,
        "lookback": win,
    }
    return fl, long_exits, fs, short_exits, stats


# ═════════════════════════════════════════════════════════════════════════════
# 3. Vectorized Backtest Engine (self-contained, no dependencies)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    """Results from a single backtest run."""
    name: str
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    # Sharpe computed over *traded* bars only (bars while a position is
    # open). The full-curve Sharpe subtracts the per-bar risk-free rate
    # from every bar, so a mostly-flat sleeve accrues negative excess
    # return on ~99% of bars and reports Sharpe -20 on a +0.2% book —
    # a cash-drag artifact, not a strategy property. Traded Sharpe
    # measures risk-adjusted performance of the strategy while it is
    # actually in the market; the annualization uses the fraction of
    # bars actually traded so it stays comparable across exposures.
    traded_sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_trade_pnl: float = 0.0
    expectancy_pct: float = 0.0
    final_equity: float = 0.0
    equity_curve: Optional[np.ndarray] = None
    # Per-bar mask (len == equity_curve): True where a position was held
    # at bar start or opened during it. Lets DSR consume the traded-bar
    # return series (same sample the traded Sharpe describes).
    in_market_mask: Optional[np.ndarray] = None
    regime_returns: Optional[Dict[str, float]] = None
    # S.Ξ DSR gate results
    dsr_confidence: float = 0.0     # DSR confidence Φ(z) = 1 − p (higher = more significant)
    dsr_pvalue: float = 0.0         # DEPRECATED compat alias for dsr_confidence (legacy name)
    dsr_passed: Optional[bool] = None
    dsr_n_trials: int = 0
    dsr_raw_sharpe: float = 0.0
    dsr_expected_max: float = 0.0
    dsr_pbo_proxy: float = 0.0

    def __post_init__(self) -> None:
        # Keep dsr_confidence and its deprecated alias in sync at construction.
        # (If both are set explicitly and disagree, both are respected as-is.)
        if self.dsr_confidence == 0.0 and self.dsr_pvalue != 0.0:
            self.dsr_confidence = self.dsr_pvalue
        elif self.dsr_pvalue == 0.0 and self.dsr_confidence != 0.0:
            self.dsr_pvalue = self.dsr_confidence

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "total_return_pct": round(self.total_return_pct, 4),
            "annualized_return_pct": round(self.annualized_return_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "traded_sharpe_ratio": round(self.traded_sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "calmar_ratio": round(self.calmar_ratio, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": round(self.profit_factor, 4),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_trade_pnl": round(self.avg_trade_pnl, 6),
            "expectancy_pct": round(self.expectancy_pct, 4),
            "final_equity": round(self.final_equity, 2),
            "dsr_confidence": round(self.dsr_confidence, 6),
            "dsr_pvalue": round(self.dsr_confidence, 6),  # legacy key name, carries confidence
            "dsr_passed": self.dsr_passed,
            "dsr_n_trials": self.dsr_n_trials,
        }
        if self.regime_returns:
            d["regime_returns"] = {k: round(v, 4) for k, v in self.regime_returns.items()}
        return d


def run_backtest(
    df: pd.DataFrame,
    long_entries: pd.Series,
    long_exits: pd.Series,
    short_entries: Optional[pd.Series] = None,
    short_exits: Optional[pd.Series] = None,
    initial_cash: float = 100_000.0,
    commission_pct: float = 0.0002,
    slippage_pips: float = 0.00005,
    risk_per_trade_pct: float = 0.02,
    name: str = "backtest",
    periods_per_year: Optional[int] = None,
) -> BacktestResult:
    """Self-contained vectorized backtest supporting long+short."""
    close = df["close"].values.astype(np.float64)
    n = len(close)
    if periods_per_year is None:
        periods_per_year = _infer_periods_per_year(df)

    le = np.asarray(long_entries, dtype=bool)
    lx = np.asarray(long_exits, dtype=bool)
    se = np.asarray(short_entries, dtype=bool) if short_entries is not None else np.zeros(n, dtype=bool)
    sx = np.asarray(short_exits, dtype=bool) if short_exits is not None else np.zeros(n, dtype=bool)

    cash = initial_cash
    equity = np.zeros(n, dtype=np.float64)
    in_market = np.zeros(n, dtype=bool)
    position = 0.0  # positive = long units, negative = short units
    entry_price = 0.0
    trades_pnl: List[float] = []

    for i in range(n):
        p = close[i]
        held = position != 0.0  # position at start of bar i

        # ── exits ──
        if position > 0 and (lx[i] or i == n - 1):
            exit_p = p * (1 - commission_pct - slippage_pips)
            pnl = position * (exit_p - entry_price)
            cash += position * exit_p
            trades_pnl.append(pnl)
            position = 0.0

        elif position < 0 and (sx[i] or i == n - 1):
            exit_p = p * (1 + commission_pct + slippage_pips)
            pnl = position * (exit_p - entry_price)  # position is negative
            cash += abs(position) * entry_price + pnl
            trades_pnl.append(pnl)
            position = 0.0

        # ── entries ──
        if position == 0 and i < n - 1:
            if le[i]:
                alloc = cash * risk_per_trade_pct
                entry_p = p * (1 + commission_pct + slippage_pips)
                position = alloc / entry_p
                cash -= alloc
                entry_price = entry_p

            elif se[i]:
                alloc = cash * risk_per_trade_pct
                entry_p = p * (1 - commission_pct - slippage_pips)
                position = -(alloc / entry_p)  # short
                cash -= alloc
                entry_price = entry_p

        # mark-to-market
        if position > 0:
            equity[i] = cash + position * p
        elif position < 0:
            equity[i] = cash + abs(position) * entry_price + position * (p - entry_price)
        else:
            equity[i] = cash
        # Bar is "traded" if a position was held at its start or was opened
        # during it — the exit bar realizes the round trip, so it counts.
        in_market[i] = held or position != 0.0

    # ── metrics ──
    final = float(equity[-1])
    total_ret = (final - initial_cash) / initial_cash * 100

    eq = pd.Series(equity)
    rets = eq.pct_change().dropna().values
    rf_per = (1.0 + 0.04) ** (1.0 / periods_per_year) - 1.0
    excess = rets - rf_per

    std = np.std(rets)
    sharpe = float(np.mean(excess) / std * np.sqrt(periods_per_year)) if std > 1e-10 else 0.0

    # Traded Sharpe: per-bar returns over bars where a position is open,
    # annualized by the traded fraction of the year (exposure-adjusted,
    # comparable across strategies with different time-in-market).
    traded_rets = rets[in_market[1:]]
    traded_share = float(in_market.mean()) if n > 0 else 0.0
    if len(traded_rets) > 2 and np.std(traded_rets) > 1e-10 and traded_share > 1e-6:
        traded_sharpe = float(
            np.mean(traded_rets) / np.std(traded_rets)
            * np.sqrt(periods_per_year * traded_share)
        )
    else:
        traded_sharpe = 0.0

    down = rets[rets < 0]
    down_std = np.std(down) if len(down) > 0 else 0.0
    sortino = float(np.mean(excess) / down_std * np.sqrt(periods_per_year)) if down_std > 1e-10 else 0.0

    peaks = np.maximum.accumulate(equity)
    dd = (equity - peaks) / np.where(peaks > 0, peaks, 1)
    max_dd = float(np.abs(np.min(dd))) * 100
    ann_ret = total_ret * (periods_per_year / max(n, 1))
    calmar = ann_ret / max_dd if max_dd > 0.01 else 0.0

    n_trades = len(trades_pnl)
    wins = [t for t in trades_pnl if t > 0]
    losses = [t for t in trades_pnl if t < 0]
    win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    avg_pnl = sum(trades_pnl) / n_trades if n_trades > 0 else 0.0
    expectancy = avg_pnl / initial_cash * 100 if initial_cash > 0 else 0.0

    return BacktestResult(
        name=name,
        total_return_pct=total_ret,
        annualized_return_pct=ann_ret,
        sharpe_ratio=sharpe,
        traded_sharpe_ratio=traded_sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown_pct=max_dd,
        win_rate_pct=win_rate,
        profit_factor=pf,
        total_trades=n_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        avg_trade_pnl=avg_pnl,
        expectancy_pct=expectancy,
        final_equity=final,
        equity_curve=equity,
        in_market_mask=in_market,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3b. Robustness Stress Passes (cost / liquidity)
# ═════════════════════════════════════════════════════════════════════════════
# From the architecture doc §2.2.2 robustness matrix:
#   Transaction Cost Stress: double assumed costs → Sharpe must stay > 0.5
#   Liquidity Stress:        halve assumed volume/ADV → strategy still viable
# An edge that dies when costs double or depth halves is not an edge — it is
# a curve-fit artifact of the friction assumptions.


def _infer_periods_per_year(df: pd.DataFrame, default: int = 252 * 24) -> int:
    """Infer annualization factor from bar timestamps (H1 default convention).

    The harness historically assumed H1 bars (252 trading days x 24 h).
    Feeding M15 real data through that default mis-scales every Sharpe by
    sqrt(4) — enough to flip gate verdicts. When a 'time' column exists the
    factor is derived from the median bar spacing instead; constant-hourly
    synthetic data resolves to the historical default (252*24).
    """
    try:
        if "time" not in df.columns or len(df) < 3:
            return default
        t = pd.to_datetime(df["time"])
        med = t.diff().dropna().median()
        if med is None or pd.isna(med) or med <= pd.Timedelta(0):
            return default
        sec = med.total_seconds()
        return int(round(252 * 24 * 3600 / sec))
    except Exception:
        return default


@dataclass
class StressPassResult:
    """Outcome of one robustness stress pass."""
    name: str
    description: str
    baseline_sharpe: float
    stressed_sharpe: float
    baseline_return_pct: float
    stressed_return_pct: float
    sharpe_degradation: float     # baseline - stressed (positive = worse)
    cost_multiplier: float = 1.0  # applied to commission/slippage
    volume_multiplier: float = 1.0  # applied to fill capacity assumption
    passed: Optional[bool] = None
    threshold: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "baseline_sharpe": round(self.baseline_sharpe, 4),
            "stressed_sharpe": round(self.stressed_sharpe, 4),
            "baseline_return_pct": round(self.baseline_return_pct, 4),
            "stressed_return_pct": round(self.stressed_return_pct, 4),
            "sharpe_degradation": round(self.sharpe_degradation, 4),
            "cost_multiplier": self.cost_multiplier,
            "volume_multiplier": self.volume_multiplier,
            "passed": self.passed,
            "threshold": self.threshold,
            "reason": self.reason,
        }


def _run_stressed_backtest(
    df: pd.DataFrame,
    signals: Tuple[pd.Series, pd.Series, pd.Series, pd.Series],
    *,
    initial_cash: float,
    commission_pct: float,
    slippage_pips: float,
    risk_per_trade_pct: float,
    cost_multiplier: float = 1.0,
    volume_multiplier: float = 1.0,
    name: str = "stressed",
) -> BacktestResult:
    """Re-run a backtest with stressed cost/liquidity assumptions.

    cost_multiplier scales commission + slippage (e.g. 2.0 = double costs).
    volume_multiplier simulates thinner liquidity: with halved ADV the
    realistic fill degrades, modeled here as additional slippage proportional
    to the participation ratio (2x thinner book → 2x extra friction).
    """
    stressed_commission = commission_pct * cost_multiplier
    stressed_slippage = slippage_pips * cost_multiplier
    # Liquidity stress: extra slippage on top of the doubled base costs so
    # both stresses are independently meaningful.
    if volume_multiplier < 1.0:
        stressed_slippage *= (1.0 / volume_multiplier)
    return run_backtest(
        df, *signals,
        initial_cash=initial_cash,
        commission_pct=stressed_commission,
        slippage_pips=stressed_slippage,
        risk_per_trade_pct=risk_per_trade_pct,
        name=name,
    )


def run_stress_passes(
    df: pd.DataFrame,
    signals: Tuple[pd.Series, pd.Series, pd.Series, pd.Series],
    baseline: BacktestResult,
    *,
    initial_cash: float = 100_000.0,
    commission_pct: float = 0.0002,
    slippage_pips: float = 0.00005,
    risk_per_trade_pct: float = 0.02,
    cost_multiplier: float = 2.0,
    volume_multiplier: float = 0.5,
    cost_sharpe_floor: float = 0.5,
    liquidity_return_floor_pct: float = 0.0,
    liquidity_sharpe_retention: float = 0.5,
) -> List[StressPassResult]:
    """Run cost-stress and liquidity-stress robustness passes.

    Gates (doc §2.2.2):
      Cost stress:      Sharpe under doubled costs must stay above
                        `cost_sharpe_floor` (default 0.5).
      Liquidity stress: under halved liquidity, the strategy stays viable —
                        return above `liquidity_return_floor_pct` AND Sharpe
                        retention >= `liquidity_sharpe_retention` of baseline
                        (skipped when baseline Sharpe <= 0, where retention is
                        meaningless).
    """
    results: List[StressPassResult] = []

    # ── Pass 1: transaction-cost stress (double costs) ──
    cost_bt = _run_stressed_backtest(
        df, signals,
        initial_cash=initial_cash, commission_pct=commission_pct,
        slippage_pips=slippage_pips, risk_per_trade_pct=risk_per_trade_pct,
        cost_multiplier=cost_multiplier, name="cost_stress_x2",
    )
    cost_pass = StressPassResult(
        name="cost_stress",
        description=f"costs x{cost_multiplier:.1f} (commission+slippage)",
        baseline_sharpe=baseline.traded_sharpe_ratio,
        stressed_sharpe=cost_bt.traded_sharpe_ratio,
        baseline_return_pct=baseline.total_return_pct,
        stressed_return_pct=cost_bt.total_return_pct,
        sharpe_degradation=baseline.traded_sharpe_ratio - cost_bt.traded_sharpe_ratio,
        cost_multiplier=cost_multiplier,
        threshold=cost_sharpe_floor,
    )
    cost_pass.passed = cost_bt.traded_sharpe_ratio > cost_sharpe_floor
    cost_pass.reason = (
        f"Traded Sharpe {cost_bt.traded_sharpe_ratio:.2f} > {cost_sharpe_floor:.2f} floor"
        if cost_pass.passed
        else f"Traded Sharpe {cost_bt.traded_sharpe_ratio:.2f} <= {cost_sharpe_floor:.2f} floor "
             f"— edge does not survive doubled costs"
    )
    results.append(cost_pass)

    # ── Pass 2: liquidity stress (halve fill capacity) ──
    liq_bt = _run_stressed_backtest(
        df, signals,
        initial_cash=initial_cash, commission_pct=commission_pct,
        slippage_pips=slippage_pips, risk_per_trade_pct=risk_per_trade_pct,
        cost_multiplier=cost_multiplier,
        volume_multiplier=volume_multiplier, name="liquidity_stress_x0.5",
    )
    liq_retention = (
        liq_bt.traded_sharpe_ratio / baseline.traded_sharpe_ratio
        if baseline.traded_sharpe_ratio > 0 else 0.0
    )
    liq_pass = StressPassResult(
        name="liquidity_stress",
        description=f"liquidity x{volume_multiplier:.1f} (halved ADV, degraded fills)",
        baseline_sharpe=baseline.traded_sharpe_ratio,
        stressed_sharpe=liq_bt.traded_sharpe_ratio,
        baseline_return_pct=baseline.total_return_pct,
        stressed_return_pct=liq_bt.total_return_pct,
        sharpe_degradation=baseline.traded_sharpe_ratio - liq_bt.traded_sharpe_ratio,
        volume_multiplier=volume_multiplier,
        threshold=liquidity_sharpe_retention,
    )
    if baseline.traded_sharpe_ratio <= 0:
        liq_pass.passed = None
        liq_pass.reason = "baseline traded Sharpe <= 0 — retention gate not meaningful"
    else:
        viable = liq_bt.total_return_pct > liquidity_return_floor_pct
        retained = liq_retention >= liquidity_sharpe_retention
        liq_pass.passed = bool(viable and retained)
        liq_pass.reason = (
            f"Traded Sharpe retention {liq_retention:.0%} "
            f"(>= {liquidity_sharpe_retention:.0%} required), "
            f"return {liq_bt.total_return_pct:.2f}%"
        )
        if not viable:
            liq_pass.reason += " — unprofitable under halved liquidity"
        elif not retained:
            liq_pass.reason += " — Sharpe collapses with depth"
    results.append(liq_pass)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# 4. Alpha Backtest Harness
# ═════════════════════════════════════════════════════════════════════════════

# Default alpha columns that work well on single-asset time-series
# Column names in the DataFrame use lowercase: alpha_012, alpha_053, etc.
DEFAULT_ALPHA_COLS: List[str] = [
    f"alpha_{k[-3:]}" for k in _ALPHA_REGISTRY.keys()
    if k not in ("ALPHA037",)  # ALPHA037 needs 200+ bars, skip for small datasets
]


@dataclass
class HarnessResult:
    """Comparison of IC-weighted vs equal-weight ensemble backtests."""
    ic_weighted: BacktestResult
    equal_weight: BacktestResult
    hold_baseline: BacktestResult
    alpha_quality_report: str
    comparison_summary: Dict[str, Any]
    regime_comparison: Optional[Dict[str, Any]] = None
    stress_passes: Optional[List[StressPassResult]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def stress_summary(self) -> Dict[str, Any]:
        """Compact stress verdicts for downstream gates/reports."""
        if not self.stress_passes:
            return {"ran": False}
        return {
            "ran": True,
            "all_passed": all(p.passed for p in self.stress_passes
                              if p.passed is not None),
            "passes": [p.to_dict() for p in self.stress_passes],
        }


def _compute_hold_return(df: pd.DataFrame, initial_cash: float = 100_000.0) -> BacktestResult:
    """Compute buy-and-hold baseline."""
    close = df["close"].values
    n = len(close)
    shares = initial_cash / close[0]
    equity = shares * close
    total_ret = (equity[-1] - initial_cash) / initial_cash * 100

    eq = pd.Series(equity)
    rets = eq.pct_change().dropna().values
    std = np.std(rets)
    ppy = _infer_periods_per_year(df)
    rf_per = (1.0 + 0.04) ** (1.0 / ppy) - 1.0
    excess = rets - rf_per
    sharpe = float(np.mean(excess) / std * np.sqrt(ppy)) if std > 1e-10 else 0.0

    peaks = np.maximum.accumulate(equity)
    dd = (equity - peaks) / np.where(peaks > 0, peaks, 1)
    max_dd = float(np.abs(np.min(dd))) * 100

    return BacktestResult(
        name="buy_and_hold",
        total_return_pct=total_ret,
        annualized_return_pct=total_ret * (ppy / max(n, 1)),
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        win_rate_pct=100.0 if total_ret > 0 else 0.0,
        total_trades=1,
        final_equity=equity[-1],
        equity_curve=equity,
    )


def run_alpha_backtest_harness(
    df: pd.DataFrame,
    alpha_cols: Optional[List[str]] = None,
    entry_threshold: float = 2.0,
    exit_threshold: float = 0.0,
    ic_window: int = 60,
    normalize_method: str = "zscore",
    initial_cash: float = 100_000.0,
    commission_pct: float = 0.0002,
    slippage_pips: float = 0.00005,
    risk_per_trade_pct: float = 0.02,
    data_source: str = "synthetic",
    apply_edge_filter: bool = True,
    min_edge_multiple: float = 1.5,
    edge_lookback: int = 480,
    edge_horizon: int = 4,
    apply_trend_gate: bool = True,
    trend_quantile: float = 0.5,
    trend_lookback: int = 20,
    mutual_orthogonalize: bool = False,
) -> HarnessResult:
    """Run the full backtest harness comparing IC-weighted vs equal-weight.

    Steps:
      1. Compute all alpha factors from OHLCV data
      2. Run combine_alphas(weighting="ic_ir") → IC-weighted ensemble
      3. Run combine_alphas(weighting="equal") → equal-weight ensemble
      4. Convert both composite signals to entry/exit signals
      5. Backtest both + buy-and-hold baseline
      6. Compare metrics
    """
    if alpha_cols is None:
        alpha_cols = DEFAULT_ALPHA_COLS

    # Convert column names (alpha_012) to request keys (ALPHA012)
    alpha_requests = []
    for c in alpha_cols:
        num = c.replace("alpha_", "").replace("ALPHA", "")
        alpha_requests.append(f"ALPHA{num.zfill(3)}")
    df_computed = compute_alpha_factors(df.copy(), alpha_requests)

    valid_cols = [c for c in alpha_cols if c in df_computed.columns]
    if not valid_cols:
        raise ValueError(f"No alpha columns found. Available: {[c for c in df_computed.columns if 'alpha' in c.lower()]}")

    # Add forward returns
    df_computed["_fwd_return"] = df_computed["close"].pct_change(1).shift(-1)

    # Stride the IC evaluation on very large histories — the rolling IC only
    # feeds scalar ensemble weights, and the strided estimate is causal
    # (anchors backward-looking, forward-filled). 1 = dense for small data.
    ic_stride = 1 if len(df) <= 20_000 else max(1, len(df) // 4_000)

    # ── IC-weighted ensemble ──
    ic_result = combine_alphas(
        df_computed, valid_cols,
        forward_return_col="_fwd_return",
        ic_window=ic_window,
        ic_stride=ic_stride,
        normalize_method=normalize_method,
        weighting="ic_ir",
        mutual_orthogonalize=mutual_orthogonalize,
        mutual_ortho_stride=ic_stride,
    )

    # ── Equal-weight ensemble ──
    eq_result = combine_alphas(
        df_computed, valid_cols,
        forward_return_col="_fwd_return",
        ic_window=ic_window,
        ic_stride=ic_stride,
        normalize_method=normalize_method,
        weighting="equal",
    )

    # ── Signal generation ──
    ic_long_e, ic_long_x, ic_short_e, ic_short_x = composite_to_long_short_signals(
        ic_result.composite, entry_threshold, exit_threshold,
    )
    eq_long_e, eq_long_x, eq_short_e, eq_short_x = composite_to_long_short_signals(
        eq_result.composite, entry_threshold, exit_threshold,
    )

    # ── Cost-aware minimum-edge filter (IC-weighted scheme only) ──
    # Equal-weight keeps raw signals so the comparison stays apples-to-apples:
    # it shows what the filter buys vs the unfiltered book.
    edge_filter_stats: Optional[Dict[str, Any]] = None
    if apply_edge_filter:
        try:
            rt_cost = 2.0 * (commission_pct + slippage_pips)
            (
                ic_long_e, ic_long_x, ic_short_e, ic_short_x, edge_filter_stats
            ) = apply_cost_edge_filter(
                ic_result.composite, df["close"],
                ic_long_e, ic_long_x, ic_short_e, ic_short_x,
                round_trip_cost_pct=rt_cost,
                min_edge_multiple=min_edge_multiple,
                lookback=edge_lookback,
                horizon=edge_horizon,
            )
        except Exception as exc:
            logger.warning("Cost-edge filter failed (signals unfiltered): %s", exc)
            edge_filter_stats = {"error": str(exc)}

    # ── Trend-regime gate (IC-weighted scheme only) ──
    # Entries only in trending bars (causal expanding-quantile classification);
    # equal-weight keeps raw signals so the comparison shows what gating buys.
    trend_gate_stats: Optional[Dict[str, Any]] = None
    if apply_trend_gate:
        try:
            (
                ic_long_e, ic_long_x, ic_short_e, ic_short_x, trend_gate_stats
            ) = apply_trend_regime_gate(
                df["close"],
                ic_long_e, ic_long_x, ic_short_e, ic_short_x,
                lookback=trend_lookback,
                min_strength=trend_quantile,
            )
        except Exception as exc:
            logger.warning("Trend-regime gate failed (signals ungated): %s", exc)
            trend_gate_stats = {"error": str(exc)}

    # ── Backtests ──
    ic_bt = run_backtest(
        df, ic_long_e, ic_long_x, ic_short_e, ic_short_x,
        initial_cash=initial_cash, commission_pct=commission_pct,
        slippage_pips=slippage_pips, risk_per_trade_pct=risk_per_trade_pct,
        name="ic_weighted",
    )
    eq_bt = run_backtest(
        df, eq_long_e, eq_long_x, eq_short_e, eq_short_x,
        initial_cash=initial_cash, commission_pct=commission_pct,
        slippage_pips=slippage_pips, risk_per_trade_pct=risk_per_trade_pct,
        name="equal_weight",
    )
    hold_bt = _compute_hold_return(df, initial_cash)

    # ── Quality report ──
    quality_report = _format_quality_comparison(ic_result, eq_result)

    # ── Comparison summary ──
    comparison = _build_comparison(ic_bt, eq_bt, hold_bt)

    # ── Regime analysis ──
    regime = _regime_analysis(df, ic_result, eq_result, entry_threshold, exit_threshold)

    # ── S.Ξ DSR Gate: validate both ensemble Sharpes ──
    # Tag trials by hypothesis class so multiple-testing deflation is
    # scoped to the family actually being tested, not the whole ledger.
    try:
        from research.param_stability_sweep import PERSISTENCE_ALPHA_COLS
        _persist_set = set(PERSISTENCE_ALPHA_COLS)
    except ImportError:  # pragma: no cover - sweep module always present
        _persist_set = {c for c in alpha_cols if c.lower().startswith("alpha_p")}
    _n_persist = sum(1 for c in alpha_cols if c in _persist_set)
    _dsr_strategy_class = (
        "persistence_tsmom" if _n_persist * 2 >= len(alpha_cols) else "alpha101_short"
    )
    for _bt, _label in [(ic_bt, "ic_weighted"), (eq_bt, "equal_weight")]:
        if _bt.total_trades >= 5 and _bt.sharpe_ratio != 0.0:
            try:
                from research.backtest_dsr_integrator import BacktestDSRIntegrator
                _dsr = BacktestDSRIntegrator(strategy_class=_dsr_strategy_class)
                # Feed DSR a single coherent unit system: the TRADED-bar
                # return series. Sharpe, skew, kurtosis, and n must all
                # describe the same sample — mixing the annualized traded
                # SR with all-bar moments (dominated by flat cash bars)
                # produces negative/near-zero estimator variance and
                # meaningless z-statistics (0.0000 / 1.0000 confidence
                # artifacts on +PF books).
                _returns = None
                if _bt.equity_curve is not None and len(_bt.equity_curve) > 2:
                    _eq = np.asarray(_bt.equity_curve, dtype=np.float64)
                    _all_rets = np.diff(_eq) / np.maximum(_eq[:-1], 1e-10)
                    _mask = getattr(_bt, "in_market_mask", None)
                    if _mask is not None and len(_all_rets) == len(_mask):
                        _returns = _all_rets[_mask[1:]]
                    else:
                        _returns = _all_rets
                _n_obs = len(_returns) if _returns is not None else 0
                _report = _dsr.validate_backtest(
                    strategy_id=f"alpha_harness_{_label}",
                    sharpe=_bt.traded_sharpe_ratio,
                    n_observations=_n_obs,
                    returns=_returns,
                    metadata={"strategy_class": _dsr_strategy_class},
                )
                _bt.dsr_confidence = _report.dsr
                _bt.dsr_pvalue = _report.dsr  # compat alias
                _bt.dsr_passed = _report.passed_gate
                _bt.dsr_n_trials = _report.n_trials
                _bt.dsr_raw_sharpe = _report.raw_sharpe
                _bt.dsr_expected_max = _report.expected_max_sharpe
                _bt.dsr_pbo_proxy = _report.pbo_proxy
            except Exception as _dsr_exc:  # pragma: no cover - defensive
                logging.getLogger(__name__).warning(
                    "DSR gate skipped for %s: %s", _label, _dsr_exc
                )

    # ── Robustness stress passes (cost x2, liquidity x0.5) on the IC-weighted
    #    scheme — the one intended for deployment ──
    stress: Optional[List[StressPassResult]] = None
    if ic_bt.total_trades >= 3:
        try:
            stress = run_stress_passes(
                df,
                (ic_long_e, ic_long_x, ic_short_e, ic_short_x),
                ic_bt,
                initial_cash=initial_cash,
                commission_pct=commission_pct,
                slippage_pips=slippage_pips,
                risk_per_trade_pct=risk_per_trade_pct,
            )
        except Exception as exc:
            logger.warning("Stress passes failed: %s", exc)

    return HarnessResult(
        ic_weighted=ic_bt,
        equal_weight=eq_bt,
        hold_baseline=hold_bt,
        alpha_quality_report=quality_report,
        comparison_summary=comparison,
        regime_comparison=regime,
        stress_passes=stress,
        metadata={
            "data_source": data_source,
            "n_bars": int(len(df)),
            "time_range": [
                str(df["time"].iloc[0]), str(df["time"].iloc[-1])
            ] if "time" in df.columns and len(df) else None,
            "edge_filter": edge_filter_stats,
            "trend_gate": trend_gate_stats,
            "entry_threshold": entry_threshold,
        },
    )


# ═════════════════════════════════════════════════════════════════════════════
# 5. Comparison & Reporting
# ═════════════════════════════════════════════════════════════════════════════

def _format_quality_comparison(
    ic_result: EnsembleResult,
    eq_result: EnsembleResult,
) -> str:
    """Format a side-by-side quality comparison of both ensembles."""
    lines = [
        "=" * 80,
        "ALPHA ENSEMBLE QUALITY COMPARISON",
        "=" * 80,
        "",
        f"{'Alpha':<14} {'IC_Wt':>7} {'Eq_Wt':>7} {'IC_Mean':>9} {'IC_IR':>8} {'IC+%':>7}",
        "-" * 80,
    ]

    all_names = sorted(set(list(ic_result.alpha_qualities.keys()) + list(eq_result.alpha_qualities.keys())))
    for name in all_names:
        ic_q = ic_result.alpha_qualities.get(name)
        eq_w = eq_result.alpha_weights.get(name, 0.0)
        ic_w = ic_result.alpha_weights.get(name, 0.0)
        ic_mean = ic_q.ic_mean if ic_q else 0.0
        ic_ir = ic_q.ic_ir if ic_q else 0.0
        ic_pos = ic_q.ic_positive_pct if ic_q else 0.0
        lines.append(
            f"{name:<14} {ic_w:>7.3f} {eq_w:>7.3f} {ic_mean:>+9.4f} {ic_ir:>+8.3f} {ic_pos:>6.1%}"
        )

    lines.append("-" * 80)
    lines.append(
        f"{'OVERALL':<14} {'':>7} {'':>7} "
        f"{ic_result.ic_mean_overall:>+9.4f} {'':>8} {'':>7}"
    )
    lines.append(
        f"Alphas used: IC={ic_result.n_alphas_used}  Eq={eq_result.n_alphas_used}"
    )
    lines.append("=" * 80)
    return "\n".join(lines)


def _build_comparison(
    ic_bt: BacktestResult,
    eq_bt: BacktestResult,
    hold_bt: BacktestResult,
) -> Dict[str, Any]:
    """Build a structured comparison dict."""
    metrics = [
        "total_return_pct", "sharpe_ratio", "sortino_ratio", "calmar_ratio",
        "max_drawdown_pct", "win_rate_pct", "profit_factor", "total_trades",
        "expectancy_pct",
    ]

    comparison = {}
    for m in metrics:
        ic_val = getattr(ic_bt, m)
        eq_val = getattr(eq_bt, m)
        hold_val = getattr(hold_bt, m)
        diff = ic_val - eq_val

        # For drawdown, lower is better
        if m == "max_drawdown_pct":
            advantage = "IC" if ic_val < eq_val else ("EQ" if eq_val < ic_val else "TIE")
        else:
            advantage = "IC" if ic_val > eq_val else ("EQ" if eq_val > ic_val else "TIE")

        comparison[m] = {
            "ic_weighted": round(ic_val, 4),
            "equal_weight": round(eq_val, 4),
            "buy_and_hold": round(hold_val, 4),
            "ic_advantage": round(diff, 4),
            "winner": advantage,
        }

    # Overall verdict
    ic_wins = sum(1 for v in comparison.values() if v["winner"] == "IC")
    eq_wins = sum(1 for v in comparison.values() if v["winner"] == "EQ")
    comparison["verdict"] = {
        "ic_wins": ic_wins,
        "equal_wins": eq_wins,
        "total_metrics": len(metrics),
        "ic_advantage_pct": round(ic_wins / len(metrics) * 100, 1),
        "recommendation": (
            "IC-weighted ensemble outperforms" if ic_wins > eq_wins
            else "Equal-weight is competitive" if eq_wins > ic_wins
            else "No clear advantage"
        ),
    }

    return comparison


def _regime_analysis(
    df: pd.DataFrame,
    ic_result: EnsembleResult,
    eq_result: EnsembleResult,
    entry_threshold: float,
    exit_threshold: float,
) -> Dict[str, Any]:
    """Analyze performance in trending vs ranging regimes."""
    close = df["close"]
    # Classify regimes using ADX-like measure (trend strength)
    returns = close.pct_change()
    rolling_vol = returns.rolling(20).std()
    rolling_mean = returns.rolling(20).mean()
    trend_strength = rolling_mean.abs() / rolling_vol.clip(lower=1e-10)

    # High trend = trending, low = ranging
    median_ts = trend_strength.median()
    trending_mask = trend_strength > median_ts
    ranging_mask = ~trending_mask

    # Compute composite returns in each regime
    ic_composite_ret = ic_result.composite.pct_change().fillna(0)
    eq_composite_ret = eq_result.composite.pct_change().fillna(0)

    _ppy = _infer_periods_per_year(df)

    def _regime_sharpe(rets: pd.Series, mask: pd.Series) -> float:
        r = rets[mask].values
        r = r[np.isfinite(r)]
        if len(r) < 10:
            return 0.0
        std = np.std(r)
        return float(np.mean(r) / std * np.sqrt(_ppy)) if std > 1e-10 else 0.0

    return {
        "trending_ic_sharpe": _regime_sharpe(ic_composite_ret, trending_mask),
        "trending_eq_sharpe": _regime_sharpe(eq_composite_ret, trending_mask),
        "ranging_ic_sharpe": _regime_sharpe(ic_composite_ret, ranging_mask),
        "ranging_eq_sharpe": _regime_sharpe(eq_composite_ret, ranging_mask),
        "trending_bars": int(trending_mask.sum()),
        "ranging_bars": int(ranging_mask.sum()),
    }


# ═════════════════════════════════════════════════════════════════════════════
# 6. Report Formatting
# ═════════════════════════════════════════════════════════════════════════════

def format_harness_report(result: HarnessResult) -> str:
    """Format the full harness comparison as a human-readable report."""
    lines = []
    c = result.comparison_summary

    lines.append("=" * 80)
    lines.append("  ALPHA ENSEMBLE BACKTEST HARNESS - IC-WEIGHTED vs EQUAL-WEIGHT")
    lines.append("=" * 80)
    lines.append("")

    # ── Metrics comparison table ──
    lines.append(f"{'Metric':<22} {'IC-Weighted':>14} {'Equal-Weight':>14} {'Buy&Hold':>14} {'Winner':>8}")
    lines.append("-" * 80)

    key_metrics = [
        ("Total Return %", "total_return_pct", ".2f"),
        ("Sharpe Ratio", "sharpe_ratio", ".4f"),
        ("Sortino Ratio", "sortino_ratio", ".4f"),
        ("Calmar Ratio", "calmar_ratio", ".4f"),
        ("Max Drawdown %", "max_drawdown_pct", ".2f"),
        ("Win Rate %", "win_rate_pct", ".1f"),
        ("Profit Factor", "profit_factor", ".4f"),
        ("Total Trades", "total_trades", "d"),
        ("Expectancy %", "expectancy_pct", ".4f"),
    ]

    for label, key, fmt in key_metrics:
        ic_v = c[key]["ic_weighted"]
        eq_v = c[key]["equal_weight"]
        bh_v = c[key]["buy_and_hold"]
        winner = c[key]["winner"]
        marker = "[IC]" if winner == "IC" else ("[EQ]" if winner == "EQ" else "[=]")
        lines.append(
            f"{label:<22} {ic_v:>14{fmt}} {eq_v:>14{fmt}} {bh_v:>14{fmt}} {marker:>5} {winner}"
        )

    lines.append("-" * 80)
    lines.append("")

    # ── Verdict ──
    v = c["verdict"]
    lines.append(f"VERDICT: IC-weighted wins {v['ic_wins']}/{v['total_metrics']} metrics "
                 f"({v['ic_advantage_pct']:.0f}%)")
    lines.append(f"  -> {v['recommendation']}")
    lines.append("")

    # ── Regime analysis ──
    if result.regime_comparison:
        rc = result.regime_comparison
        lines.append("REGIME ANALYSIS:")
        lines.append(f"  Trending bars: {rc['trending_bars']}  |  Ranging bars: {rc['ranging_bars']}")
        lines.append(f"  {'Regime':<12} {'IC-Weighted Sharpe':>20} {'Equal-Weight Sharpe':>22}")
        lines.append(f"  {'Trending':<12} {rc['trending_ic_sharpe']:>+20.4f} {rc['trending_eq_sharpe']:>+22.4f}")
        lines.append(f"  {'Ranging':<12} {rc['ranging_ic_sharpe']:>+20.4f} {rc['ranging_eq_sharpe']:>+22.4f}")
        lines.append("")

    # ── Alpha quality ──
    lines.append(result.alpha_quality_report)
    lines.append("")

    # ── Robustness stress passes ──
    if result.stress_passes:
        lines.append("ROBUSTNESS STRESS PASSES:")
        for p in result.stress_passes:
            if p.passed is True:
                status, color = "PASS", ""
            elif p.passed is False:
                status, color = "FAIL", " — NOT ROBUST"
            else:
                status, color = "SKIP", ""
            lines.append(
                f"  [{status}] {p.name:<18} {p.description:<44} "
                f"Sharpe {p.baseline_sharpe:.2f} -> {p.stressed_sharpe:.2f}"
                f"{color}"
            )
            lines.append(f"         {p.reason}")
        lines.append("")

    # ── Trade details ──
    lines.append("TRADE DETAILS:")
    for bt in [result.ic_weighted, result.equal_weight, result.hold_baseline]:
        lines.append(
            f"  {bt.name:<16} trades={bt.total_trades:>4}  "
            f"wins={bt.winning_trades:>4}  losses={bt.losing_trades:>4}  "
            f"final=${bt.final_equity:>12,.2f}"
        )

    lines.append("=" * 80)
    return "\n".join(lines)


def save_harness_report(
    result: HarnessResult,
    output_path: str = "alpha_backtest_report.json",
) -> str:
    """Save the full harness result as JSON."""
    report = {
        "ic_weighted": result.ic_weighted.to_dict(),
        "equal_weight": result.equal_weight.to_dict(),
        "buy_and_hold": result.hold_baseline.to_dict(),
        "comparison": result.comparison_summary,
        "regime": result.regime_comparison,
        "stress_passes": result.stress_summary(),
        "data_source": str(result.metadata.get("data_source", "unknown")) if result.metadata else "unknown",
        "time_range": result.metadata.get("time_range") if result.metadata else None,
        "edge_filter": result.metadata.get("edge_filter") if result.metadata else None,
        "trend_gate": result.metadata.get("trend_gate") if result.metadata else None,
        "entry_threshold": result.metadata.get("entry_threshold") if result.metadata else None,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    return output_path


# ═════════════════════════════════════════════════════════════════════════════
# 7. CLI Entry Point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Alpha Ensemble Backtest Harness")
    # Lazy import to avoid a heavy import chain at module load
    from research.param_stability_sweep import PERSISTENCE_ALPHA_COLS

    parser.add_argument(
        "--source", choices=["synthetic", "mt5"], default="synthetic",
        help="Data source: synthetic bars or real MT5 broker bars (fail-closed)",
    )
    parser.add_argument("--entry-threshold", type=float, default=2.0, help="Entry z-score threshold")
    parser.add_argument(
        "--no-edge-filter", action="store_true",
        help="Disable the cost-aware minimum-edge entry filter",
    )
    parser.add_argument("--min-edge-multiple", type=float, default=1.5, help="Required edge as multiple of round-trip cost")
    parser.add_argument("--no-trend-gate", action="store_true", help="Disable the trend-regime entry gate")
    parser.add_argument("--trend-quantile", type=float, default=0.5, help="Trend-strength quantile bar (0.5 = median of past history)")
    parser.add_argument("--trend-lookback", type=int, default=20, help="Rolling window for causal trend-strength measure")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol name (mt5 source)")
    parser.add_argument("--timeframe", default="H1", help="Timeframe (mt5 source)")
    parser.add_argument("--bars", type=int, default=3000, help="Number of bars")
    parser.add_argument("--seed", default=None, type=int, help="RNG seed for reproducibility (synthetic source)")
    parser.add_argument(
        "--stress-only", action="store_true",
        help="Run only the robustness stress passes (cost x2, liquidity x0.5) — still requires the full harness pass to get baseline + signals",
    )

    parser.add_argument("--exit-threshold", type=float, default=0.0, help="Exit z-score threshold")
    parser.add_argument(
        "--alpha-set", choices=["default", "persistence"], default="default",
        help="Alpha family: default (Alpha101 short-horizon) or persistence (multi-day TSMOM)",
    )
    parser.add_argument(
        "--edge-horizon", type=int, default=None,
        help="Holding-horizon bars for the cost edge filter (default: 4 for default set, 480 for persistence)",
    )
    parser.add_argument(
        "--mutual-ortho", action="store_true",
        help="Residualize each alpha against its siblings before IC weighting "
             "(de-correlates highly-correlated alpha families)",
    )
    parser.add_argument("--ic-window", type=int, default=60, help="Rolling IC window")
    parser.add_argument("--cash", type=float, default=100_000, help="Initial cash")
    parser.add_argument("--output", default="alpha_backtest_report.json", help="JSON output path")
    parser.add_argument("--html", default="alpha_backtest_report.html", help="HTML output path")
    args = parser.parse_args()

    if args.source == "mt5":
        print(f"Loading {args.bars} real MT5 bars: {args.symbol} {args.timeframe} (fail-closed)...")
        try:
            df = load_mt5_bars(args.symbol, args.timeframe, args.bars)
        except Exception as exc:
            logger.error("MT5 data load failed: %s", exc)
            print(f"ERROR: {exc}")
            print(
                "The harness refuses synthetic fallback — start the MT5 terminal "
                "and verify the symbol name, then retry."
            )
            return 1
        data_source = f"mt5:{args.symbol}:{args.timeframe}"
    else:
        print(f"Generating {args.bars} bars of synthetic {args.symbol} {args.timeframe} data...")
        df = generate_synthetic_ohlcv(n_bars=args.bars, seed=args.seed)
        data_source = f"synthetic(seed={args.seed})"
    print(f"  Bars: {len(df)}")
    print(f"  Source: {data_source}")
    print(f"  Price range: {df['close'].min():.5f} - {df['close'].max():.5f}")
    print()

    print("Running backtest harness...")
    if args.edge_horizon is None:
        args.edge_horizon = 480 if args.alpha_set == "persistence" else 4
    alpha_cols = PERSISTENCE_ALPHA_COLS if args.alpha_set == "persistence" else DEFAULT_ALPHA_COLS
    result = run_alpha_backtest_harness(
        df,
        alpha_cols=alpha_cols,
        entry_threshold=args.entry_threshold,
        exit_threshold=args.exit_threshold,
        ic_window=args.ic_window,
        initial_cash=args.cash,
        data_source=data_source,
        apply_edge_filter=not args.no_edge_filter,
        min_edge_multiple=args.min_edge_multiple,
        edge_horizon=args.edge_horizon,
        apply_trend_gate=not args.no_trend_gate,
        trend_quantile=args.trend_quantile,
        trend_lookback=args.trend_lookback,
        mutual_orthogonalize=args.mutual_ortho,
    )

    # Print report
    report = format_harness_report(result)
    print(report)

    if args.stress_only:
        # Keep the output focused on the robustness gates section.
        sp = result.stress_summary()
        print("\n=== STRESS PASSES (--stress-only) ===")
        for r in result.stress_passes or []:
            verdict = {True: "PASS", False: "FAIL", None: "SKIP"}[r.passed]
            print(
                f"  {r.name:<18} -> {verdict:<4} | {r.description} | {r.reason}"
            )
        print(f"ran={sp.get('ran')} all_passed={sp.get('all_passed')}")

    # Save JSON
    json_path = save_harness_report(result, args.output)
    print(f"\nJSON report saved: {json_path}")
    print(f"data_source: {data_source}")

    # Save HTML (uses alpha_dashboard for visual report)
    try:
        from ui.alpha_dashboard import generate_dashboard_html
        # Generate a simple HTML report
        _save_html_report(result, args.html)
        print(f"HTML report saved: {args.html}")
    except Exception as exc:
        logger.debug("report generation failed: %s", exc)


def _save_html_report(result: HarnessResult, output_path: str):
    """Generate a self-contained HTML comparison report."""
    c = result.comparison_summary
    ic = result.ic_weighted
    eq = result.equal_weight
    bh = result.hold_baseline

    # Build equity curves as plotly traces
    ic_eq = ic.equity_curve.tolist() if ic.equity_curve is not None else []
    eq_eq = eq.equity_curve.tolist() if eq.equity_curve is not None else []
    bh_eq = bh.equity_curve.tolist() if bh.equity_curve is not None else []

    traces = [
        {"x": list(range(len(ic_eq))), "y": ic_eq, "type": "scatter", "mode": "lines",
         "name": "IC-Weighted", "line": {"color": "#00d4ff", "width": 2}},
        {"x": list(range(len(eq_eq))), "y": eq_eq, "type": "scatter", "mode": "lines",
         "name": "Equal-Weight", "line": {"color": "#f59e0b", "width": 2}},
        {"x": list(range(len(bh_eq))), "y": bh_eq, "type": "scatter", "mode": "lines",
         "name": "Buy & Hold", "line": {"color": "#6b7b8d", "width": 1, "dash": "dash"}},
    ]
    layout = {
        "paper_bgcolor": "#111827", "plot_bgcolor": "#111827",
        "font": {"color": "#e0e0e0"}, "height": 400,
        "xaxis": {"title": "Bar", "gridcolor": "#1e2a3a"},
        "yaxis": {"title": "Equity ($)", "gridcolor": "#1e2a3a"},
        "legend": {"orientation": "h", "y": -0.2},
        "margin": {"l": 70, "r": 20, "t": 20, "b": 60},
    }

    # Build metrics table
    rows = ""
    for label, key in [
        ("Total Return", "total_return_pct"), ("Sharpe", "sharpe_ratio"),
        ("Sortino", "sortino_ratio"), ("Max Drawdown", "max_drawdown_pct"),
        ("Win Rate", "win_rate_pct"), ("Profit Factor", "profit_factor"),
        ("Trades", "total_trades"),
    ]:
        ic_v = c[key]["ic_weighted"]
        eq_v = c[key]["equal_weight"]
        bh_v = c[key]["buy_and_hold"]
        winner = c[key]["winner"]
        fmt = ".2f" if key != "total_trades" else "d"
        marker = "🟢" if winner == "IC" else ("🟡" if winner == "EQ" else "⚪")
        rows += f"<tr><td>{label}</td><td>{ic_v:{fmt}}</td><td>{eq_v:{fmt}}</td><td>{bh_v:{fmt}}</td><td>{marker}</td></tr>\n"

    verdict = c["verdict"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>Alpha Backtest Report</title>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0e17;color:#e0e0e0;padding:20px}}
.header{{text-align:center;padding:20px 0 30px;border-bottom:1px solid #1e2a3a;margin-bottom:30px}}
.header h1{{font-size:24px;color:#00d4ff}} .header .sub{{color:#6b7b8d;font-size:13px;margin-top:5px}}
.card{{background:#111827;border:1px solid #1e2a3a;border-radius:12px;padding:20px;margin-bottom:20px}}
.card h2{{font-size:16px;color:#00d4ff;margin-bottom:15px;border-bottom:1px solid #1e2a3a;padding-bottom:10px}}
table{{width:100%;border-collapse:collapse}}
th,td{{padding:10px 14px;text-align:right;border-bottom:1px solid #1e2a3a;font-size:13px}}
th:first-child,td:first-child{{text-align:left}}
th{{color:#6b7b8d;font-weight:600;font-size:12px;text-transform:uppercase}}
.verdict{{font-size:18px;text-align:center;padding:15px;background:#10b98120;border-radius:10px;color:#10b981;font-weight:600}}
.chart{{height:400px}}
</style>
</head>
<body>
<div class="header">
  <h1>📊 Alpha Ensemble Backtest Report</h1>
  <div class="sub">IC-Weighted vs Equal-Weight vs Buy&Hold</div>
</div>
<div class="card"><div class="verdict">{verdict['recommendation']} — IC wins {verdict['ic_wins']}/{verdict['total_metrics']} metrics ({verdict['ic_advantage_pct']:.0f}%)</div></div>
<div class="card"><h2>Performance Comparison</h2><table>
<tr><th>Metric</th><th>IC-Weighted</th><th>Equal-Weight</th><th>Buy&Hold</th><th></th></tr>
{rows}</table></div>
<div class="card"><h2>Equity Curves</h2><div id="equity_chart" class="chart"></div></div>
<script>
Plotly.newPlot('equity_chart',{json.dumps(traces)},{json.dumps(layout)},{{responsive:true}});
</script>
</body></html>"""

    Path(output_path).write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
