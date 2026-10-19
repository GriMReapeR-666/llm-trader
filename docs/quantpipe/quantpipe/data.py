"""Datasets: synthetic generator, CSV, MT5 download, scenario transforms."""
from __future__ import annotations
import numpy as np, pandas as pd
from .models import HOME, Scenario, TF_MINUTES

DS_DIR = HOME / "datasets"
DS_DIR.mkdir(parents=True, exist_ok=True)
OHLC = ["open", "high", "low", "close"]


def synthetic(symbol="EURUSD", tf="H1", bars=20000, seed=1, start_price=1.10, drift=0.0) -> pd.DataFrame:
    """Regime-switching GBM (trend / range / high-vol) with intrabar OHLC. Bid prices."""
    rng = np.random.default_rng(seed)
    mins = TF_MINUTES[tf]
    idx = pd.date_range("2020-01-06", periods=int(bars * 1.5), freq=f"{mins}min")
    idx = idx[idx.dayofweek < 5][:bars]
    n = len(idx)
    # regimes persist ~300 bars
    reg = np.zeros(n, int); r = 0
    for i in range(n):
        if rng.random() < 1 / 300:
            r = rng.integers(0, 3)
        reg[i] = r
    base_vol = 0.0004 * np.sqrt(mins / 60)
    vol = base_vol * np.array([1.0, 0.7, 1.8])[reg]
    mu = np.array([0.00006, 0.0, -0.00004])[reg] * (mins / 60) + drift
    # mean-reverting AR noise in the "range" regime, momentum in "trend"
    eps = rng.standard_normal(n)
    ret = np.zeros(n)
    for i in range(1, n):
        phi = 0.12 if reg[i] == 0 else (-0.25 if reg[i] == 1 else 0.0)
        ret[i] = mu[i] + phi * ret[i - 1] + vol[i] * eps[i]
    close = start_price * np.exp(np.cumsum(ret))
    open_ = np.r_[start_price, close[:-1]] * np.exp(rng.normal(0, vol * 0.1))
    ext = np.abs(rng.normal(0, vol * 0.6, n)) * close
    high = np.maximum(open_, close) + ext
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.6, n)) * close
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "tick_volume": rng.integers(100, 2000, n)}, index=idx)
    df.index.name = "time"
    return df


def save_dataset(name: str, df: pd.DataFrame, meta: dict | None = None):
    df.to_csv(DS_DIR / f"{name}.csv")
    import json
    (DS_DIR / f"{name}.json").write_text(json.dumps(meta or {}))


def load_dataset(name: str) -> pd.DataFrame:
    p = DS_DIR / f"{name}.csv"
    if not p.exists():
        raise FileNotFoundError(f"dataset '{name}' not found; have: {list_datasets()}")
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    return df


def list_datasets() -> list[str]:
    return sorted(p.stem for p in DS_DIR.glob("*.csv"))


def import_csv(name: str, path: str) -> dict:
    df = pd.read_csv(path)
    df.columns = [c.lower().strip("<>") for c in df.columns]
    if "date" in df.columns and "time" in df.columns and df["time"].astype(str).str.contains(":").any():
        df["time"] = df["date"].astype(str) + " " + df["time"].astype(str)
    tcol = "time" if "time" in df.columns else df.columns[0]
    df[tcol] = pd.to_datetime(df[tcol])
    df = df.set_index(tcol).sort_index()
    df.index.name = "time"
    save_dataset(name, df[[c for c in df.columns if c in OHLC + ["tick_volume"]]])
    return {"name": name, "bars": len(df), "start": str(df.index[0]), "end": str(df.index[-1])}


def apply_scenario(df: pd.DataFrame, sc: Scenario) -> pd.DataFrame:
    """Rescale volatility / inject news gaps. Spread & slippage multipliers are applied in the engine."""
    if sc.vol_mult == 1.0 and sc.gap_prob == 0.0:
        return df
    rng = np.random.default_rng(sc.seed)
    c = df["close"].to_numpy(float)
    prev = np.r_[c[0], c[:-1]]
    lr = np.log(c / prev) * sc.vol_mult
    if sc.gap_prob > 0:
        jumps = (rng.random(len(c)) < sc.gap_prob) * rng.choice([-1.0, 1.0], len(c)) * sc.gap_pct
        lr = lr + jumps
    lr[0] = 0.0
    newc = c[0] * np.exp(np.cumsum(lr))
    out = df.copy()
    for col in ("open", "high", "low"):
        out[col] = newc * (df[col].to_numpy(float) / c) ** sc.vol_mult
    out["close"] = newc
    out["high"] = out[OHLC].max(axis=1)
    out["low"] = out[OHLC].min(axis=1)
    return out


def split(df: pd.DataFrame, segment: str, train_frac=0.7) -> tuple[pd.DataFrame, int]:
    """Returns (frame, start_idx). 'holdout' keeps a 300-bar warmup prefix before the holdout."""
    n = len(df); k = int(n * train_frac); warm = 300
    if segment == "all":
        return df, 0
    if segment == "train":
        return df.iloc[:k], 0
    if segment == "holdout":
        a = max(0, k - warm)
        return df.iloc[a:], k - a
    raise ValueError("segment must be train|holdout|all")


def fetch_mt5(symbol: str, tf: str, start: str, end: str) -> pd.DataFrame:
    import MetaTrader5 as mt5  # Windows only
    from datetime import datetime
    tfc = getattr(mt5, f"TIMEFRAME_{tf}")
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    r = mt5.copy_rates_range(symbol, tfc, datetime.fromisoformat(start), datetime.fromisoformat(end))
    if r is None or len(r) == 0:
        raise RuntimeError(f"no rates: {mt5.last_error()}")
    df = pd.DataFrame(r)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("time")[OHLC + ["tick_volume"]]
