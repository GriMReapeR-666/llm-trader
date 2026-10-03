"""Technical indicators calculation engine."""
from __future__ import annotations

from typing import List, Tuple
import numpy as np
import pandas as pd

try:
    from alpha_factors import compute_alpha_factors
except ImportError:
    try:
        from core.alpha_factors import compute_alpha_factors
    except ImportError:
        compute_alpha_factors = None


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    prev_c = np.empty_like(c)
    prev_c[0] = np.nan
    prev_c[1:] = c[:-1]
    tr1 = h - l
    tr2 = np.abs(h - prev_c)
    tr3 = np.abs(l - prev_c)
    tr = np.fmax(tr1, np.fmax(tr2, tr3))
    return pd.Series(tr, index=high.index)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _true_range(high, low, close).ewm(alpha=1/period, adjust=False).mean()


# ---------------------------------------------------------------------------
# High-Performance O(1) Incremental Streaming Indicators
# ---------------------------------------------------------------------------

def ema_step(prev_ema: float, price: float, period: int) -> float:
    """O(1) incremental EMA calculation for a new tick/bar."""
    alpha = 2.0 / (period + 1.0)
    return alpha * price + (1.0 - alpha) * prev_ema


def atr_step(prev_atr: float, high: float, low: float, prev_close: float, period: int = 14) -> float:
    """O(1) incremental ATR calculation for a new bar."""
    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
    alpha = 1.0 / period
    return alpha * tr + (1.0 - alpha) * prev_atr


def rsi_step(prev_avg_gain: float, prev_avg_loss: float, current_price: float, prev_price: float, period: int = 14) -> Tuple[float, float, float]:
    """O(1) incremental RSI update. Returns (rsi, new_avg_gain, new_avg_loss)."""
    delta = current_price - prev_price
    gain = max(delta, 0.0)
    loss = max(-delta, 0.0)
    alpha = 1.0 / period
    new_avg_gain = alpha * gain + (1.0 - alpha) * prev_avg_gain
    new_avg_loss = alpha * loss + (1.0 - alpha) * prev_avg_loss
    if new_avg_loss == 0.0:
        rsi_val = 100.0 if new_avg_gain > 0.0 else 50.0
    else:
        rs = new_avg_gain / new_avg_loss
        rsi_val = 100.0 - (100.0 / (1.0 + rs))
    return rsi_val, new_avg_gain, new_avg_loss


def macd_step(
    prev_fast_ema: float,
    prev_slow_ema: float,
    prev_signal: float,
    price: float,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[float, float, float, float, float]:
    """O(1) incremental MACD update.

    Returns: (macd_line, signal_line, histogram, new_fast_ema, new_slow_ema)
    """
    new_fast = ema_step(prev_fast_ema, price, fast)
    new_slow = ema_step(prev_slow_ema, price, slow)
    macd_line = new_fast - new_slow
    new_sig = ema_step(prev_signal, macd_line, signal)
    hist = macd_line - new_sig
    return macd_line, new_sig, hist, new_fast, new_slow


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    # O(n) convolution instead of O(n*period) rolling apply.
    raw = series.values.astype(float)
    conv = np.convolve(raw, weights[::-1], mode="full")[:len(raw)]
    weight_sum = weights.sum()
    out = np.where(weight_sum > 0, conv / weight_sum, np.nan)
    out[:period - 1] = np.nan
    return pd.Series(out, index=series.index, name=series.name)


def hma(series: pd.Series, period: int) -> pd.Series:
    half = int(period / 2)
    sqrt_n = int(np.sqrt(period))
    wma_half = wma(series, half)
    wma_full = wma(series, period)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_n)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ADX.

    Directional movement follows the classic selection rule: on each bar the
    larger of the up-move (high - prev_high) and down-move (prev_low - low)
    wins, and the losing side contributes zero.  Smoothed +DM/-DM are divided
    by Wilder-smoothed true range, DX is smoothed once more into ADX.
    """
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index)
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index)

    tr = _true_range(high, low, close).ewm(alpha=1/period, adjust=False).mean()
    tr_safe = tr.replace(0, np.nan)

    plus_di = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / tr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / tr_safe

    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    return dx.ewm(alpha=1/period, adjust=False).mean().fillna(0)


def compute_indicators(df: pd.DataFrame, requests: List[str]) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    high = out["high"]
    low = out["low"]
    for req in requests:
        req = req.upper().strip()
        if req.startswith("EMA("):
            p = int(req.split("(")[1].split(")")[0])
            out[f"ema_{p}"] = ema(close, p)
        elif req == "RSI":
            out["rsi_14"] = rsi(close, 14)
        elif req.startswith("RSI("):
            p = int(req.split("(")[1].split(")")[0])
            out[f"rsi_{p}"] = rsi(close, p)
        elif req == "MACD":
            m, s, h = macd(close)
            out["macd_line"] = m
            out["macd_signal"] = s
            out["macd_hist"] = h
            out["macd_histogram"] = h  # alias used by trading_tools consumers
        elif req.startswith("MACD("):
            nums = req.split("(")[1].split(")")[0].split(",")
            f, sl, si = map(int, nums)
            m, s, h = macd(close, f, sl, si)
            out["macd_line"] = m
            out["macd_signal"] = s
            out["macd_hist"] = h
            out["macd_histogram"] = h  # alias used by trading_tools consumers
        elif req == "ATR":
            out["atr_14"] = atr(high, low, close, 14)
        elif req.startswith("ATR("):
            p = int(req.split("(")[1].split(")")[0])
            out[f"atr_{p}"] = atr(high, low, close, p)
        elif req == "HMA":
            out["hma_21"] = hma(close, 21)
        elif req.startswith("HMA("):
            p = int(req.split("(")[1].split(")")[0])
            out[f"hma_{p}"] = hma(close, p)
        elif req == "ADX":
            out["adx_14"] = adx(high, low, close, 14)
        elif req.startswith("ADX("):
            p = int(req.split("(")[1].split(")")[0])
            out[f"adx_{p}"] = adx(high, low, close, p)
        elif req.startswith("ALPHA"):
            # Delegate to the Alpha101 alpha factor library.
            # Collect all ALPHA requests and compute in one batch.
            pass  # handled below after the loop
        else:
            raise ValueError(f"Unknown indicator request: {req}")

    # --- Batch-compute any ALPHA factor requests ---
    alpha_requests = [r.strip() for r in requests if r.upper().strip().startswith("ALPHA")]
    if alpha_requests and compute_alpha_factors is not None:
        out = compute_alpha_factors(out, alpha_requests)

    return out
