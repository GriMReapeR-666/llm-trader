"""Economic-calendar layer: import, point-in-time surprise z-scores, bar alignment, event study.

ALIGNMENT RULES (what 'no lookahead' means here)
 * Release times are UTC. Price data must also be UTC (convert broker server time with server_to_utc()).
 * A bar [open, open+tf) may only 'see' an event if release_time <= bar CLOSE.  Scheduled times of FUTURE
   events are legitimately known in advance (used for blackout / flatten), their ACTUAL values are not.
 * Surprise = actual - forecast, standardised by the EXPANDING std of strictly PRIOR surprises of the same
   event series (min 4 obs). No future data enters the scaling.
 * 'actual' must be the FIRST-RELEASE print. Revised values leak the future - keep 'revised' separate.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from .models import HOME, TF_MINUTES

EV_DIR = HOME / "events"; EV_DIR.mkdir(parents=True, exist_ok=True)
IMPACT = {"low": 1, "l": 1, "medium": 2, "med": 2, "m": 2, "moderate": 2, "high": 3, "h": 3}
INVERSE_WORDS = ("unemployment", "jobless", "claims")      # higher = bad for the currency
EVENT_FIELDS = ["ev_mins_since", "ev_mins_to_next", "ev_surprise_net", "ev_in_bar"]
COLS = ["time", "currency", "event", "impact", "actual", "forecast", "previous", "polarity"]


def server_to_utc(index: pd.DatetimeIndex, mode: str = "utc") -> pd.DatetimeIndex:
    """mode: 'utc' | 'fixed:+2' | 'ny_close' (GMT+2 winter / GMT+3 summer, i.e. NY-local + 7h - the common MT5 broker setup)."""
    if mode == "utc": return index
    if mode.startswith("fixed:"): return index - pd.Timedelta(hours=float(mode[6:]))
    if mode == "ny_close":
        ny = (index - pd.Timedelta(hours=7)).tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
        return ny.tz_convert("UTC").tz_localize(None)
    raise ValueError("mode must be utc | fixed:+N | ny_close")


def _finish(ev: pd.DataFrame) -> pd.DataFrame:
    ev = ev.copy()
    ev["time"] = pd.to_datetime(ev["time"])
    ev["impact"] = ev["impact"].map(lambda x: x if isinstance(x, (int, np.integer)) else IMPACT.get(str(x).strip().lower(), 1)).astype(int)
    for c in ("actual", "forecast", "previous"):
        ev[c] = pd.to_numeric(ev.get(c), errors="coerce")
    if "polarity" not in ev: ev["polarity"] = 1
    ev["polarity"] = ev["polarity"].fillna(1).astype(int)
    inv = ev["event"].str.lower().str.contains("|".join(INVERSE_WORDS))
    ev.loc[inv & (ev["polarity"] == 1), "polarity"] = -1
    ev["currency"] = ev["currency"].str.upper()
    ev = ev.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    # point-in-time standardised surprise
    s = ev["actual"] - ev["forecast"]
    z = pd.Series(np.nan, index=ev.index)
    for _, g in ev.groupby(["currency", "event"]):
        sg = s.loc[g.index]
        sd = sg.shift(1).expanding(min_periods=4).std()
        z.loc[g.index] = sg / sd.replace(0, np.nan)
    ev["surprise_z"] = (z.clip(-4, 4) * ev["polarity"])           # + = good for the currency
    return ev


def save_events(name: str, ev: pd.DataFrame): _finish(ev).to_csv(EV_DIR / f"{name}.csv", index=False)
def list_event_sets() -> list[str]: return sorted(p.stem for p in EV_DIR.glob("*.csv"))


def load_events(name: str) -> pd.DataFrame:
    p = EV_DIR / f"{name}.csv"
    if not p.exists(): raise FileNotFoundError(f"event set '{name}' not found; have {list_event_sets()}")
    return pd.read_csv(p, parse_dates=["time"])


def import_events_csv(name: str, path: str, server_tz: str = "utc") -> dict:
    """CSV columns (case-insens.): time|date+time, currency, event, impact, actual, forecast, previous."""
    df = pd.read_csv(path); df.columns = [c.lower().strip() for c in df.columns]
    if "time" not in df and {"date", "time"} <= set(df.columns): pass
    if "date" in df.columns and "time" in df.columns and not np.issubdtype(df["time"].dtype, np.datetime64):
        df["time"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
    else:
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["time"] = server_to_utc(pd.DatetimeIndex(df["time"]), server_tz)
    for c in ("event", "currency", "impact"):
        if c not in df: raise ValueError(f"missing column '{c}'")
    save_events(name, df)
    ev = load_events(name)
    return {"name": name, "events": len(ev), "start": str(ev.time.min()), "end": str(ev.time.max()),
            "with_surprise": int(ev.surprise_z.notna().sum()), "currencies": sorted(ev.currency.unique())}


def synthetic_events(start="2020-01-01", end="2024-12-31", seed=3) -> pd.DataFrame:
    """Plausible schedule (NFP, CPI, FOMC, ECB, EZ CPI) with random surprises - for pipeline testing only."""
    rng = np.random.default_rng(seed); rows = []
    months = pd.date_range(start, end, freq="MS")
    for m in months:
        fri = m + pd.offsets.Week(weekday=4) if m.dayofweek != 4 else m
        rows.append((fri + pd.Timedelta(hours=13, minutes=30), "USD", "Non-Farm Payrolls", 3, 180, 60))
        rows.append((fri + pd.Timedelta(hours=13, minutes=30), "USD", "Unemployment Rate", 3, 4.0, 0.15))
        rows.append((m + pd.Timedelta(days=11, hours=13, minutes=30), "USD", "CPI m/m", 3, 0.3, 0.12))
        rows.append((m + pd.Timedelta(days=1, hours=10), "EUR", "CPI y/y flash", 2, 2.2, 0.25))
        if m.month in (1, 3, 5, 6, 7, 9, 11, 12):
            rows.append((m + pd.Timedelta(days=15, hours=19), "USD", "FOMC Rate Decision", 3, 2.0, 0.12))
            rows.append((m + pd.Timedelta(days=9, hours=12, minutes=15), "EUR", "ECB Rate Decision", 3, 1.5, 0.12))
    ev = pd.DataFrame(rows, columns=["time", "currency", "event", "impact", "base", "sd"])
    ev["forecast"] = ev["base"] + rng.normal(0, ev["sd"] * 0.3)
    ev["actual"] = ev["forecast"] + rng.normal(0, ev["sd"])
    ev["previous"] = ev["forecast"]
    ev = ev[(ev.time >= start) & (ev.time <= end)]
    return ev[["time", "currency", "event", "impact", "actual", "forecast", "previous"]]


def _ccys(symbol: str):
    s = symbol.upper(); return s[:3], s[3:6]


def bar_features(index: pd.DatetimeIndex, tf: str, ev: pd.DataFrame, symbol: str, min_impact: int = 3,
                 decay_hours: float = 24.0) -> pd.DataFrame:
    """Per-bar fundamental features, all computable at bar CLOSE (no lookahead)."""
    base, quote = _ccys(symbol)
    e = ev[(ev.impact >= min_impact) & ev.currency.isin([base, quote])].sort_values("time")
    t = e["time"].to_numpy("datetime64[ns]")
    bo = index.to_numpy("datetime64[ns]"); bc = bo + np.timedelta64(TF_MINUTES[tf], "m")
    pos = np.searchsorted(t, bc, side="right")                    # events with time <= bar close
    pos_open = np.searchsorted(t, bo, side="left")
    mins = np.timedelta64(1, "m")
    since = np.full(len(index), np.nan); nxt = np.full(len(index), np.nan)
    if len(t):
        has_prev = pos > 0
        since[has_prev] = (bc[has_prev] - t[pos[has_prev] - 1]) / mins
        has_next = pos < len(t)
        nxt[has_next] = (t[pos[has_next]] - bc[has_next]) / mins
    sign = np.where(e.currency.to_numpy() == base, 1.0, -1.0)
    contrib = np.nan_to_num(e["surprise_z"].to_numpy(float)) * sign
    cs = np.r_[0.0, np.cumsum(contrib)]
    lo = np.searchsorted(t, bc - np.timedelta64(int(decay_hours * 60), "m"), side="right")
    return pd.DataFrame({"ev_mins_since": since, "ev_mins_to_next": nxt,
                         "ev_surprise_net": cs[pos] - cs[np.minimum(lo, pos)],
                         "ev_in_bar": (pos > pos_open).astype(float)}, index=index)


def apply_event_shocks(df: pd.DataFrame, ev: pd.DataFrame, symbol: str, tf: str, bps_per_z: float = 8.0,
                       min_impact: int = 3) -> pd.DataFrame:
    """TEST ONLY: make synthetic prices react persistently to surprises, so alignment bugs become visible."""
    f = bar_features(df.index, tf, ev, symbol, min_impact, decay_hours=0.0001)
    base, quote = _ccys(symbol)
    e = ev[(ev.impact >= min_impact) & ev.currency.isin([base, quote])]
    bo = df.index.to_numpy("datetime64[ns]"); bc = bo + np.timedelta64(TF_MINUTES[tf], "m")
    jump = np.zeros(len(df))
    for _, r in e.iterrows():
        if np.isnan(r.surprise_z): continue
        i = np.searchsorted(bc, np.datetime64(r.time), side="right")      # first bar closing AFTER the event
        if i < len(df):
            jump[i] += r.surprise_z * (1 if r.currency == base else -1) * bps_per_z / 1e4
    fac = np.exp(np.cumsum(jump)); out = df.copy()
    for c in ("high", "low", "close"): out[c] = df[c].to_numpy() * fac
    out["open"] = df["open"].to_numpy() * np.r_[1.0, fac[:-1]]           # open of reaction bar is pre-event
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1); out["low"] = out[["open", "high", "low", "close"]].min(axis=1)
    return out


def event_study(df: pd.DataFrame, ev: pd.DataFrame, symbol: str, tf: str, event: str | None = None,
                horizons=(1, 4, 12, 24), min_impact=3, z_cut=0.5) -> dict:
    """Mean post-event return (bps, + = base currency up) bucketed by point-in-time surprise."""
    base, quote = _ccys(symbol)
    e = ev[(ev.impact >= min_impact) & ev.currency.isin([base, quote]) & ev.surprise_z.notna()]
    if event: e = e[e.event.str.contains(event, case=False, regex=False)]
    bc = df.index.to_numpy("datetime64[ns]") + np.timedelta64(TF_MINUTES[tf], "m"); C = df["close"].to_numpy(float)
    rows = []
    for _, r in e.iterrows():
        i0 = np.searchsorted(bc, np.datetime64(r.time), side="right") - 1      # last bar closed at/before release
        if i0 < 0: continue
        net = r.surprise_z * (1 if r.currency == base else -1)
        rows.append({"net": net, **{h: (C[i0 + h] / C[i0] - 1) * 1e4 for h in horizons if i0 + h < len(C)}})
    if not rows: return {"n": 0, "note": "no usable events (need >=5 prior releases per series for z-scores)"}
    d = pd.DataFrame(rows); d["bucket"] = np.where(d.net > z_cut, "bullish_base", np.where(d.net < -z_cut, "bearish_base", "neutral"))
    out = {"n_events": len(d), "buckets": {}}
    for b, g in d.groupby("bucket"):
        out["buckets"][b] = {"n": len(g)} | {f"h{h}_mean_bps": round(float(g[h].mean()), 2) for h in horizons if h in g}
    for h in horizons:
        if h in d and d[h].std() > 0:
            x = d["net"]; y = d[h].dropna(); xx = x.loc[y.index]
            out.setdefault("corr_surprise_vs_return", {})[f"h{h}"] = round(float(np.corrcoef(xx, y)[0, 1]), 3)
    return out


def event_replay(trades: list, ev: pd.DataFrame, symbol: str, pre_hours=2, post_hours=6, min_impact=3) -> dict:
    """Replay every historical event occurrence and attribute strategy trades that opened in its window."""
    if not trades: return {"note": "no trades"}
    base, quote = _ccys(symbol)
    e = ev[(ev.impact >= min_impact) & ev.currency.isin([base, quote])]
    t = pd.DataFrame(trades); t["entry"] = pd.to_datetime(t.entry_time)
    rows = []
    for _, r in e.iterrows():
        w = t[(t.entry >= r.time - pd.Timedelta(hours=pre_hours)) & (t.entry <= r.time + pd.Timedelta(hours=post_hours))]
        if len(w):
            net = (r.surprise_z if pd.notna(r.surprise_z) else 0) * (1 if r.currency == base else -1)
            rows.append({"event": r.event, "ccy": r.currency, "side_sign": float(np.sign(net)), "n": len(w), "pnl": float(w.pnl.sum())})
    if not rows: return {"note": "no trades opened inside any event window"}
    d = pd.DataFrame(rows)
    by = d.groupby("event").agg(occurrences=("n", "size"), trades=("n", "sum"), pnl=("pnl", "sum")).round(2)
    covered = float(d.pnl.sum()); total = float(t.pnl.sum())
    return {"by_event": by.to_dict("index"), "event_window_pnl": round(covered, 2), "total_pnl": round(total, 2),
            "share_of_trades_in_event_windows_pct": round(float(d.n.sum() / len(t) * 100), 1),
            "pnl_after_bullish_surprise": round(float(d[d.side_sign > 0].pnl.sum()), 2),
            "pnl_after_bearish_surprise": round(float(d[d.side_sign < 0].pnl.sum()), 2)}


def export_for_mql5(ev: pd.DataFrame, path: str):
    """CSV the generated EA reads from MQL5\\Files (FILE_COMMON): epoch_utc,currency,impact,z_signed (same z as Python)."""
    out = pd.DataFrame({"epoch_utc": (ev.time.astype("datetime64[s]").astype("int64")), "currency": ev.currency,
                        "impact": ev.impact, "z": ev.surprise_z.fillna(0).round(4)})
    out.to_csv(path, index=False)
