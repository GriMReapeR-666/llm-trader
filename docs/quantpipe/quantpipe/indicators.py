from __future__ import annotations
import numpy as np, pandas as pd


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n): return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def compute(df: pd.DataFrame, specs: dict) -> dict[str, pd.Series]:
    out = {}
    for name, sp in specs.items():
        t = sp["type"]; n = int(sp.get("period", 14)); src = df[sp.get("source", "close")]
        if t == "sma": out[name] = sma(src, n)
        elif t == "ema": out[name] = ema(src, n)
        elif t == "rsi": out[name] = rsi(src, n)
        elif t == "atr": out[name] = atr(df, n)
        elif t == "highest": out[name] = df["high"].rolling(n).max().shift(int(sp.get("shift", 1)))
        elif t == "lowest": out[name] = df["low"].rolling(n).min().shift(int(sp.get("shift", 1)))
        elif t in ("macd", "macd_signal", "macd_hist"):
            f, s_, g = int(sp.get("fast", 12)), int(sp.get("slow", 26)), int(sp.get("signal", 9))
            m = ema(src, f) - ema(src, s_); sg = ema(m, g)
            out[name] = {"macd": m, "macd_signal": sg, "macd_hist": m - sg}[t]
        elif t in ("bb_upper", "bb_lower", "bb_mid"):
            m = sma(src, n); sd = src.rolling(n).std(); k = float(sp.get("mult", 2.0))
            out[name] = {"bb_upper": m + k * sd, "bb_lower": m - k * sd, "bb_mid": m}[t]
        elif t == "stoch_k":
            lo = df["low"].rolling(n).min(); hi = df["high"].rolling(n).max()
            out[name] = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
        elif t == "adx":
            up = df["high"].diff(); dn = -df["low"].diff()
            pdm = ((up > dn) & (up > 0)) * up; mdm = ((dn > up) & (dn > 0)) * dn
            a = atr(df, n)
            pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
            mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / a
            dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
            out[name] = dx.ewm(alpha=1 / n, adjust=False).mean()
        else:
            raise ValueError(f"unknown indicator type {t}")
    return out


INDICATOR_TYPES = ["sma", "ema", "rsi", "atr", "highest", "lowest", "macd", "macd_signal",
                   "macd_hist", "bb_upper", "bb_lower", "bb_mid", "stoch_k", "adx"]
