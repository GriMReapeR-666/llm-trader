"""Alpha101 Factor Library for Single-Asset FX Trading.

Implements 20 of the most relevant formulaic alpha factors from the Alpha101
101 Formulaic Alphas paper, adapted for single-asset time-series (Forex).

All factors return pd.Series aligned to the input index.  Reusable primitives
(ts_rank, delta, delay, rank, scale, decay_linear, etc.) are defined at module
level so they can be composed into new custom factors.

References:
    Kakushadze, Z. (2016). "101 Formulaic Alphas." SSRN 2701346.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reusable Primitives (O(n) vectorized where possible)
# ---------------------------------------------------------------------------

def ts_rank(df: pd.Series, window: int) -> pd.Series:
    """Rolling percentile rank of the last value within the window.

    For each bar, rank the current value among the last *window* values.
    Returns values in (0, 1].
    """
    return df.rolling(window).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )


def ts_argmax(df: pd.Series, window: int) -> pd.Series:
    """Which bar in the rolling window hit the max (1-indexed from left)."""
    return df.rolling(window).apply(np.argmax, raw=True) + 1


def ts_argmin(df: pd.Series, window: int) -> pd.Series:
    """Which bar in the rolling window hit the min (1-indexed from left)."""
    return df.rolling(window).apply(np.argmin, raw=True) + 1


def ts_min(df: pd.Series, window: int) -> pd.Series:
    """Rolling minimum over the past *window* bars."""
    return df.rolling(window).min()


def ts_max(df: pd.Series, window: int) -> pd.Series:
    """Rolling maximum over the past *window* bars."""
    return df.rolling(window).max()


def delta(df: pd.Series, period: int = 1) -> pd.Series:
    """Momentum: current value minus value *period* bars ago."""
    return df.diff(period)


def delay(df: pd.Series, period: int = 1) -> pd.Series:
    """Lag the series by *period* bars."""
    return df.shift(period)


def rank(df: pd.Series) -> pd.Series:
    """Cross-sectional rank normalized to (0, 1].

    For a single series this is equivalent to the percentile rank of the
    full series (not rolling — useful for normalising a factor).
    """
    return df.rank(pct=True)


def scale(df: pd.Series, k: float = 1.0) -> pd.Series:
    """Rescale so that sum(abs(df)) == k."""
    denom = df.abs().sum()
    if denom == 0 or np.isnan(denom):
        return df * 0.0
    return df.mul(k).div(denom)


def decay_linear(df: pd.Series, period: int) -> pd.Series:
    """Linearly-weighted moving average (most recent bar has weight = period)."""
    weights = np.arange(1, period + 1, dtype=float)
    weight_sum = weights.sum()
    raw = df.values.astype(float)
    conv = np.convolve(raw, weights[::-1], mode="full")[: len(raw)]
    out = np.where(weight_sum > 0, conv / weight_sum, np.nan)
    out[: period - 1] = np.nan
    return pd.Series(out, index=df.index, name=df.name)


def signed_power(df: pd.Series, exp: float) -> pd.Series:
    """Raise each value to *exp*, preserving the sign."""
    return df.apply(lambda x: np.sign(x) * (abs(x) ** exp))


def log_safe(df: pd.Series) -> pd.Series:
    """Natural log, guarding against non-positive values."""
    return np.log(df.clip(lower=1e-10))


# ---------------------------------------------------------------------------
# Alpha Factors  (all accept an OHLCV DataFrame and return pd.Series)
# ---------------------------------------------------------------------------

def alpha_001(df: pd.DataFrame) -> pd.Series:
    """Alpha#1  — Volatility regime flip detector.

    rank(ts_argmax(SignedPower(returns<0 ? stddev(returns,20) : close, 2), 5)) - 0.5

    When recent negative returns are more volatile than usual, the signal
    flips to mean-reversion; otherwise it follows the price trend.
    """
    returns = df["close"].pct_change()
    inner = df["close"].copy()
    neg = returns < 0
    inner[neg] = returns.rolling(20).std()[neg]
    return rank(ts_argmax(signed_power(inner, 2.0), 5)) - 0.5


def alpha_005(df: pd.DataFrame) -> pd.Series:
    """Alpha#5  — Open vs VWAP deviation × close vs VWAP distance.

    rank(open - avg(vwap,10)) * (-1 * abs(rank(close - vwap)))

    Measures how far the open is from the recent VWAP average, weighted by
    how far the close deviates from VWAP.
    """
    vwap = _vwap(df)
    return rank(df["open"] - vwap.rolling(10).mean()) * (
        -1 * (rank(df["close"] - vwap)).abs()
    )


def alpha_009(df: pd.DataFrame) -> pd.Series:
    """Alpha#9  — Momentum flip detector.

    If the 5-bar min of daily deltas is positive → trend continuation.
    If the 5-bar max of daily deltas is negative → trend continuation.
    Otherwise → fade the move.

    Captures when a trend is accelerating or about to reverse.
    """
    d = delta(df["close"], 1)
    cond_up = ts_rank(ts_min(d, 5), 5) > 0.5   # sustained up
    cond_dn = ts_max(d, 5) < 0                   # sustained down
    alpha = -1 * d
    alpha[cond_up | cond_dn] = d[cond_up | cond_dn]
    return alpha


def alpha_012(df: pd.DataFrame) -> pd.Series:
    """Alpha#12  — Volume-price divergence (simplest, most robust alpha).

    sign(delta(volume, 1)) * (-1 * delta(close, 1))

    When volume rises but price falls (or vice versa), signals a likely
    reversal. This is a classic order-flow imbalance proxy.
    """
    return np.sign(delta(df["volume"], 1)) * (-1 * delta(df["close"], 1))


def alpha_014(df: pd.DataFrame) -> pd.Series:
    """Alpha#14  — Returns acceleration × open-volume correlation.

    -1 * rank(delta(returns, 3)) * correlation(open, volume, 10)

    Combines short-term return momentum with the relationship between
    opening price and trading activity.
    """
    returns = df["close"].pct_change()
    corr = df["open"].rolling(10).corr(df["volume"])
    corr = corr.replace([np.inf, -np.inf], 0).fillna(0)
    return -1 * rank(delta(returns, 3)) * corr


def alpha_017(df: pd.DataFrame) -> pd.Series:
    """Alpha#17  — Triple rank: price level × acceleration × relative volume.

    -1 * rank(ts_rank(close, 10)) * rank(delta(delta(close,1),1)) * rank(ts_rank(volume/adv20, 5))

    Combines price positioning, momentum acceleration, and volume surge.
    """
    adv20 = df["volume"].rolling(20).mean()
    rv = (df["volume"] / adv20.replace(0, np.nan)).fillna(1.0)
    return -1 * rank(ts_rank(df["close"], 10)) * rank(
        delta(delta(df["close"], 1), 1)
    ) * rank(ts_rank(rv, 5))


def alpha_019(df: pd.DataFrame) -> pd.Series:
    """Alpha#19  — Long-term mean reversion × short-term momentum.

    -1 * sign((close - delay(close,7)) + delta(close,7)) * (1 + rank(1 + sum(returns, 250)))

    Fades the 7-day momentum when the 250-bar cumulative return is high,
    and follows momentum when cumulative returns are low. Captures the
    long-term mean-reversion effect well-documented in FX.
    """
    returns = df["close"].pct_change()
    return (
        -1 * np.sign((df["close"] - delay(df["close"], 7)) + delta(df["close"], 7))
        * (1 + rank(1 + returns.rolling(250).sum()))
    )


def alpha_022(df: pd.DataFrame) -> pd.Series:
    """Alpha#22  — High-volume correlation shift × volatility.

    -1 * delta(correlation(high, volume, 5), 5) * rank(stddev(close, 20))

    When the high-volume relationship shifts while volatility is high,
    signals a strong directional move.
    """
    corr = df["high"].rolling(5).corr(df["volume"])
    corr = corr.replace([np.inf, -np.inf], 0).fillna(0)
    return -1 * delta(corr, 5) * rank(df["close"].rolling(20).std())


def alpha_023(df: pd.DataFrame) -> pd.Series:
    """Alpha#23  — Overbought high reversal.

    (sma(high, 20) < high) ? -1 * delta(high, 2) : 0

    When the current high exceeds the 20-bar SMA of highs, fade the
    2-day high change. A classic overbought signal on the high wick.
    """
    sma_high = df["high"].rolling(20).mean()
    cond = sma_high < df["high"]
    result = pd.Series(0.0, index=df.index)
    result[cond] = -1 * delta(df["high"], 2).fillna(0)[cond]
    return result


def alpha_025(df: pd.DataFrame) -> pd.Series:
    """Alpha#25  — Compound bearish signal.

    rank((-1 * returns) * adv20 * vwap * (high - close))

    Multi-factor bearish compound: negative returns, volume, VWAP, and
    high-close gap all reinforce the signal.
    """
    adv20 = df["volume"].rolling(20).mean()
    vwap = _vwap(df)
    returns = df["close"].pct_change()
    return rank((-1 * returns) * adv20 * vwap * (df["high"] - df["close"]))


def alpha_028(df: pd.DataFrame) -> pd.Series:
    """Alpha#28  — Value zone mean reversion.

    scale(correlation(adv20, low, 5) + (high+low)/2 - close)

    When price is below both the volume-weighted correlation zone and the
    midpoint of the bar, signals mean reversion upward.
    """
    adv20 = df["volume"].rolling(20).mean()
    corr = adv20.rolling(5).corr(df["low"])
    corr = corr.replace([np.inf, -np.inf], 0).fillna(0)
    raw = corr + (df["high"] + df["low"]) / 2 - df["close"]
    return scale(raw)


def alpha_030(df: pd.DataFrame) -> pd.Series:
    """Alpha#30  — Directional consensus × volume ratio.

    (1 - rank(sign连续close deltas)) * (sum(volume,5) / sum(volume,20))

    When price moves in the same direction for 3+ consecutive bars,
    the 5/20 volume ratio amplifies the signal.
    """
    d = delta(df["close"], 1)
    inner = np.sign(d) + np.sign(delay(d, 1)) + np.sign(delay(d, 2))
    return (1.0 - rank(inner)) * (
        df["volume"].rolling(5).sum() / df["volume"].rolling(20).sum().replace(0, np.nan)
    )


def alpha_034(df: pd.DataFrame) -> pd.Series:
    """Alpha#34  — Volatility compression + directional momentum.

    rank(2 - rank(stddev(returns,2)/stddev(returns,5)) - rank(delta(close,1)))

    When short-term volatility compresses relative to medium-term while
    price is trending, signals a breakout continuation.
    """
    returns = df["close"].pct_change()
    std2 = returns.rolling(2).std()
    std5 = returns.rolling(5).std().replace(0, np.nan)
    inner = (std2 / std5).replace([np.inf, -np.inf], 1).fillna(1)
    return rank(2 - rank(inner) - rank(delta(df["close"], 1)))


def alpha_035(df: pd.DataFrame) -> pd.Series:
    """Alpha#35  — Volume × range × return rank composite.

    ts_rank(volume, 32) * (1 - ts_rank(close+high-low, 16)) * (1 - ts_rank(returns, 32))

    When volume is high, the daily range is narrow, and returns are low,
    signals a compression breakout setup.
    """
    returns = df["close"].pct_change()
    rng = df["close"] + df["high"] - df["low"]
    return (
        ts_rank(df["volume"], 32)
        * (1 - ts_rank(rng, 16))
        * (1 - ts_rank(returns, 32))
    )


def alpha_036(df: pd.DataFrame) -> pd.Series:
    """Alpha#36  — Multi-factor composite (5 components).

    2.21*rank(corr(close-open, delay(volume,1), 15))
    + 0.7*rank(open-close)
    + 0.73*rank(ts_rank(delay(-returns, 6), 5))
    + rank(abs(corr(vwap, adv20, 6)))
    + 0.6*rank((sma(close,200)/200 - open) * (close - open))

    A robust composite combining mean-reversion (15-bar open-close vs lagged
    volume), short-term reversal (6-bar), long-term value (200-bar), and
    VWAP-volume correlation.
    """
    returns = df["close"].pct_change()
    adv20 = df["volume"].rolling(20).mean()
    vwap = _vwap(df)
    c1 = (df["close"] - df["open"]).rolling(15).corr(delay(df["volume"], 1))
    c1 = rank(c1.replace([np.inf, -np.inf], 0).fillna(0))
    c2 = rank(df["open"] - df["close"])
    c3 = rank(ts_rank(delay(-1 * returns, 6), 5))
    c4 = rank(vwap.rolling(6).corr(adv20).abs().replace([np.inf, -np.inf], 0).fillna(0))
    c5 = rank(((df["close"].rolling(200).mean() / 200) - df["open"]) * (df["close"] - df["open"]))
    return 2.21 * c1 + 0.7 * c2 + 0.73 * c3 + c4 + 0.6 * c5


def alpha_037(df: pd.DataFrame) -> pd.Series:
    """Alpha#37  — Long-term open-close reversal × short-term gap.

    rank(corr(delay(open-close, 1), close, 200)) + rank(open - close)

    Combines the 200-bar correlation of yesterday's gap with today's close
    (long-term reversal) with today's open-close gap (short-term).
    """
    gap = df["open"] - df["close"]
    long_corr = delay(gap, 1).rolling(200).corr(df["close"])
    long_corr = long_corr.replace([np.inf, -np.inf], 0).fillna(0)
    return rank(long_corr) + rank(gap)


def alpha_038(df: pd.DataFrame) -> pd.Series:
    """Alpha#38  — Gap detection × close-to-open pattern.

    -1 * rank(ts_rank(close, 10)) * rank(close / open)

    When price is at a relative high but the close/open ratio is extreme,
    signals a potential gap reversal.
    """
    ratio = (df["close"] / df["open"].replace(0, np.nan)).fillna(1)
    return -1 * rank(ts_rank(df["close"], 10)) * rank(ratio)


def alpha_041(df: pd.DataFrame) -> pd.Series:
    """Alpha#41  — VWAP deviation from geometric midpoint.

    sqrt(high * low) - vwap

    Simple value measure: geometric midpoint of the bar vs volume-weighted
    average price. Negative = price is "cheap" relative to VWAP.
    """
    vwap = _vwap(df)
    return np.sqrt(df["high"] * df["low"]) - vwap


def alpha_043(df: pd.DataFrame) -> pd.Series:
    """Alpha#43  — Relative volume surge × reversal momentum.

    ts_rank(volume/adv20, 20) * ts_rank(-1 * delta(close, 7), 8)

    Combines a 20-bar volume surge with 7-bar reversal momentum.
    """
    adv20 = df["volume"].rolling(20).mean()
    rv = (df["volume"] / adv20.replace(0, np.nan)).fillna(1.0)
    return ts_rank(rv, 20) * ts_rank(-1 * delta(df["close"], 7), 8)


def alpha_053(df: pd.DataFrame) -> pd.Series:
    """Alpha#53  — Williams %R-style position in bar.

    -1 * delta(((close-low) - (high-close)) / (close-low), 9)

    Measures where the close sits within the bar's range, then takes the
    9-bar momentum of that position. Captures intrabar exhaustion.
    """
    denom = (df["close"] - df["low"]).replace(0, 0.0001)
    inner = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / denom
    return -1 * delta(inner, 9)


def alpha_055(df: pd.DataFrame) -> pd.Series:
    """Alpha#55  — Stochastic oscillator × volume correlation.

    -1 * correlation(rank((close-ts_min(low,12))/(ts_max(high,12)-ts_min(low,12))), rank(volume), 6)

    The stochastic %K position ranked and correlated with ranked volume.
    """
    h12 = df["high"].rolling(12).max()
    l12 = df["low"].rolling(12).min()
    rng = (h12 - l12).replace(0, 0.0001)
    stoch = (df["close"] - l12) / rng
    corr = rank(stoch).rolling(6).corr(rank(df["volume"]))
    return -1 * corr.replace([np.inf, -np.inf], 0).fillna(0)


def alpha_060(df: pd.DataFrame) -> pd.Series:
    """Alpha#60  — Tick rule proxy × close extremity.

    -1 * (2 * scale(rank(((close-low)-(high-close))/(high-low) * volume))
          - scale(rank(ts_argmax(close, 10))))

    Combines the tick-rule imbalance (where close sits in the bar, scaled
    by volume) with the 10-bar high position.
    """
    rng = (df["high"] - df["low"]).replace(0, 0.0001)
    imbalance = ((df["close"] - df["low"]) - (df["high"] - df["close"])) * df["volume"] / rng
    return -(
        2 * scale(rank(imbalance))
        - scale(rank(ts_argmax(df["close"], 10)))
    )


def alpha_101(df: pd.DataFrame) -> pd.Series:
    """Alpha#101  — Composite multi-signal alpha.

    ((close - open) / ((high - low) + .001)) * volume

    A simplified version of the original Alpha#101 that captures the
    close-to-open directional strength scaled by the bar range and volume.
    Designed for single-asset time-series without market-cap weighting.
    """
    rng = (df["high"] - df["low"]).replace(0, 0.001)
    return ((df["close"] - df["open"]) / rng) * df["volume"]


# ---------------------------------------------------------------------------
# Higher-Persistence Family (multi-day momentum, long lookbacks)
# ---------------------------------------------------------------------------
# M15-based lookback translations: 1 trading day = 96 M15 bars (24h FX),
# so 5d = 480, 10d = 960, 20d = 1920, 40d = 3840. These capture the
# time-series momentum documented in FX at horizons of days to weeks,
# which the short-horizon Alpha101 set (5-20 bars) cannot express.

M15_BARS_PER_DAY = 96


def alpha_persistence_5d(df: pd.DataFrame) -> pd.Series:
    """5-day time-series momentum: total return over the last 480 bars.

    Classic FX time-series momentum (Moskowitz-Ooi-Pedersen style): buy
    when the multi-day drift is up, sell when it is down. Pure momentum,
    no ranking (single-asset).
    """
    return df["close"].pct_change(M15_BARS_PER_DAY * 5)


def _tsmom_z(df: pd.DataFrame, horizon_bars: int, vol_window: int | None = None) -> pd.Series:
    """Vol-standardized N-bar time-series momentum (z-score units).

    z = N-bar return / rolling std of the same-horizon return. Normalizing
    by the same-horizon std (not the single-bar vol) keeps the score
    dimensionally correct — momentum grows as sqrt(N) while single-bar vol
    does not, so naive scaling saturates any clip bound.
    """
    ret_n = df["close"].pct_change(horizon_bars)
    win = vol_window or horizon_bars
    vol_n = ret_n.rolling(win, min_periods=min(win, 60)).std()
    return (ret_n / vol_n.where(vol_n > 1e-12)).clip(-5, 5)


def alpha_persistence_10d(df: pd.DataFrame) -> pd.Series:
    """10-day momentum (960 bars) in z-score units."""
    return _tsmom_z(df, M15_BARS_PER_DAY * 10)


def alpha_persistence_20d(df: pd.DataFrame) -> pd.Series:
    """20-day momentum (1920 bars) in z-score units."""
    return _tsmom_z(df, M15_BARS_PER_DAY * 20)


def alpha_persistence_40d(df: pd.DataFrame) -> pd.Series:
    """40-day momentum (3840 bars) — the slowest persistence probe."""
    return _tsmom_z(df, M15_BARS_PER_DAY * 40)


def alpha_persistence_ma_gap(df: pd.DataFrame) -> pd.Series:
    """Slow/fast MA gap: 5d SMA minus 20d SMA, in z-score units.

    Positive in an established uptrend, negative in an established
    downtrend. Smoother than raw momentum — less sensitive to the latest
    bars. Standardized by the rolling std of the gap itself.
    """
    slow = df["close"].rolling(M15_BARS_PER_DAY * 20).mean()
    fast = df["close"].rolling(M15_BARS_PER_DAY * 5).mean()
    gap = (fast - slow) / slow
    vol = gap.rolling(M15_BARS_PER_DAY * 20, min_periods=480).std()
    return (gap / vol.where(vol > 1e-12)).clip(-5, 5)


def alpha_persistence_sign_agreement(df: pd.DataFrame) -> pd.Series:
    """Sign agreement across the 5/10/20/40-day momentum horizons.

    Bounded in [-1, 1]: +1 when all four horizons point the same way
    (strongest persistent regime), ~0 when they disagree. NaN until every
    horizon has warmed up (needs 3840 bars).
    """
    m5 = df["close"].pct_change(M15_BARS_PER_DAY * 5)
    m10 = df["close"].pct_change(M15_BARS_PER_DAY * 10)
    m20 = df["close"].pct_change(M15_BARS_PER_DAY * 20)
    m40 = df["close"].pct_change(M15_BARS_PER_DAY * 40)
    moms = pd.concat([m5, m10, m20, m40], axis=1)
    signs = np.sign(moms)
    valid = moms.notna().all(axis=1)
    out = signs.sum(axis=1) / 4.0
    out[~valid] = np.nan
    return out


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _vwap(df: pd.DataFrame) -> pd.Series:
    """Compute VWAP from OHLCV data.

    If 'vwap' column already exists, use it.  Otherwise approximate as
    (high + low + close) / 3 weighted by volume (typical price method).
    """
    if "vwap" in df.columns:
        return df["vwap"]
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return (cum_tp_vol / cum_vol).fillna(df["close"])


# ---------------------------------------------------------------------------
# Registry: maps request strings to alpha functions
# ---------------------------------------------------------------------------

_ALPHA_REGISTRY = {
    "ALPHA001": alpha_001,
    "ALPHA005": alpha_005,
    "ALPHAP01": alpha_persistence_5d,
    "ALPHAP02": alpha_persistence_10d,
    "ALPHAP03": alpha_persistence_20d,
    "ALPHAP04": alpha_persistence_40d,
    "ALPHAP05": alpha_persistence_ma_gap,
    "ALPHAP06": alpha_persistence_sign_agreement,
    "ALPHA009": alpha_009,
    "ALPHA012": alpha_012,
    "ALPHA014": alpha_014,
    "ALPHA017": alpha_017,
    "ALPHA019": alpha_019,
    "ALPHA022": alpha_022,
    "ALPHA023": alpha_023,
    "ALPHA025": alpha_025,
    "ALPHA028": alpha_028,
    "ALPHA030": alpha_030,
    "ALPHA034": alpha_034,
    "ALPHA035": alpha_035,
    "ALPHA036": alpha_036,
    "ALPHA037": alpha_037,
    "ALPHA038": alpha_038,
    "ALPHA041": alpha_041,
    "ALPHA043": alpha_043,
    "ALPHA053": alpha_053,
    "ALPHA055": alpha_055,
    "ALPHA060": alpha_060,
    "ALPHA101": alpha_101,
}


def compute_alpha_factors(df: pd.DataFrame, requests: list[str] | None = None) -> pd.DataFrame:
    """Compute Alpha101 alpha factors and append them as columns.

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV data with columns: open, high, low, close, volume.
    requests : list[str] | None
        Which alphas to compute.  Defaults to the full set.
        Accepts any key from _ALPHA_REGISTRY (case-insensitive).

    Returns
    -------
    pd.DataFrame
        Copy of *df* with alpha columns appended (e.g. ``alpha_012``).
    """
    if requests is None:
        requests = list(_ALPHA_REGISTRY.keys())

    out = df.copy()
    if "volume" not in out.columns and "tick_volume" in out.columns:
        out["volume"] = out["tick_volume"]

    for req in requests:
        key = req.upper().strip()
        if key in _ALPHA_REGISTRY:
            try:
                out[f"alpha_{key[-3:]}"] = _ALPHA_REGISTRY[key](out)
            except Exception as exc:
                log.warning("Alpha factor %s failed: %s — filling with NaN", key, exc)
                out[f"alpha_{key[-3:]}"] = np.nan
        else:
            raise ValueError(f"Unknown alpha factor: {req!r}. Available: {list(_ALPHA_REGISTRY.keys())}")

    return out


# Backward-compatible alias
compute_alpha_vector = compute_alpha_factors
