"""Declarative strategy spec (JSON DSL). The LLM edits specs - never arbitrary code - so
strategy creation is safe to automate, diffable, versionable and identical in backtest and live."""
from __future__ import annotations
import copy
import numpy as np, pandas as pd
from .indicators import compute, atr as _atr, INDICATOR_TYPES
from .models import TF_MINUTES
from .events import EVENT_FIELDS, bar_features

OPS = [">", "<", ">=", "<=", "crosses_above", "crosses_below"]
SERIES = ["open", "high", "low", "close"]

SPEC_DOC = {
    "name": "str", "symbol": "EURUSD", "timeframe": "|".join(TF_MINUTES),
    "indicators": {"<id>": {"type": "|".join(INDICATOR_TYPES), "period": 14, "source": "close"}},
    "entry_long": [["<id|series|number>", "|".join(OPS), "<id|series|number>"]],
    "entry_short": "same shape (all conditions ANDed)",
    "exit_long": "optional conditions", "exit_short": "optional conditions",
    "filters": {"hours_utc": [7, 20], "weekdays": [0, 1, 2, 3, 4], "min_adx": "optional number (needs indicator 'adx')",
                "event_blackout": {"before_min": 30, "after_min": 30}},
    "events": {"min_impact": "1-3 (default 3=high)", "decay_hours": 24,
               "fields usable as operands": ["ev_mins_since", "ev_mins_to_next", "ev_surprise_net (+ = base ccy bullish, decays)", "ev_in_bar"]},
    "risk": {"risk_pct": 1.0, "sl_atr": 2.0, "tp_atr": 3.0, "atr_ind": "atr (indicator id)",
             "fixed_lots": "optional (overrides risk_pct)", "max_bars": "optional time stop",
             "flatten_before_event_min": "optional: close open trades this many minutes before a scheduled event",
             "allow_reverse": True},
}


def validate(spec: dict) -> list[str]:
    err = []
    for k in ("name", "symbol", "timeframe", "indicators", "risk"):
        if k not in spec: err.append(f"missing '{k}'")
    if err: return err
    if spec["timeframe"] not in TF_MINUTES: err.append(f"timeframe must be one of {list(TF_MINUTES)}")
    ids = set(spec["indicators"])
    for k, v in spec["indicators"].items():
        if v.get("type") not in INDICATOR_TYPES: err.append(f"indicator {k}: bad type {v.get('type')}")
        if int(v.get("period", 14)) < 1 or int(v.get("period", 14)) > 5000: err.append(f"indicator {k}: bad period")
    ok_ops = set(OPS)
    for side in ("entry_long", "entry_short", "exit_long", "exit_short"):
        for c in spec.get(side, []):
            if len(c) != 3 or c[1] not in ok_ops: err.append(f"{side}: bad condition {c}"); continue
            for o in (c[0], c[2]):
                if not isinstance(o, (int, float)) and o not in ids and o not in SERIES and o not in EVENT_FIELDS:
                    err.append(f"{side}: unknown operand '{o}'")
    if not spec.get("entry_long") and not spec.get("entry_short"): err.append("need entry_long and/or entry_short")
    r = spec["risk"]
    if not r.get("fixed_lots"):
        if not r.get("sl_atr"): err.append("risk.sl_atr required unless fixed_lots set")
        if r.get("atr_ind", "atr") not in ids and r.get("sl_atr"): err.append("risk.atr_ind must reference an ATR indicator id")
    if r.get("risk_pct", 1) > 5: err.append("risk.risk_pct > 5 is rejected")
    return err


def _operand(o, df, ind):
    if isinstance(o, (int, float)): return pd.Series(float(o), index=df.index)
    return ind[o] if o in ind else df[o]


def _cond(c, df, ind) -> pd.Series:
    a, op, b = _operand(c[0], df, ind), c[1], _operand(c[2], df, ind)
    if op == ">": r = a > b
    elif op == "<": r = a < b
    elif op == ">=": r = a >= b
    elif op == "<=": r = a <= b
    elif op == "crosses_above": r = (a > b) & (a.shift(1) <= b.shift(1))
    else: r = (a < b) & (a.shift(1) >= b.shift(1))
    return r.fillna(False)


def _all(conds, df, ind):
    if not conds: return pd.Series(False, index=df.index)
    r = pd.Series(True, index=df.index)
    for c in conds: r &= _cond(c, df, ind)
    return r


def uses_events(spec: dict) -> bool:
    txt = str([spec.get(k) for k in ("entry_long", "entry_short", "exit_long", "exit_short")])
    return any(f in txt for f in EVENT_FIELDS) or "event_blackout" in spec.get("filters", {}) \
        or bool(spec["risk"].get("flatten_before_event_min"))


def compute_signals(df: pd.DataFrame, spec: dict, events=None) -> pd.DataFrame:
    ind = compute(df, spec["indicators"])
    if events is not None:
        cfg = spec.get("events", {})
        fe = bar_features(df.index, spec["timeframe"], events, spec["symbol"], cfg.get("min_impact", 3), cfg.get("decay_hours", 24))
        for c in fe: ind[c] = fe[c]
    elif uses_events(spec):
        raise ValueError("strategy uses event fields/filters: pass an events dataset")
    sig = pd.DataFrame(index=df.index)
    sig["long_entry"] = _all(spec.get("entry_long"), df, ind)
    sig["short_entry"] = _all(spec.get("entry_short"), df, ind)
    sig["long_exit"] = _all(spec.get("exit_long"), df, ind) if spec.get("exit_long") else False
    sig["short_exit"] = _all(spec.get("exit_short"), df, ind) if spec.get("exit_short") else False
    f = spec.get("filters", {})
    ok = pd.Series(True, index=df.index)
    if "hours_utc" in f:
        h0, h1 = f["hours_utc"]
        ok &= (df.index.hour >= h0) & (df.index.hour < h1) if h0 <= h1 else ((df.index.hour >= h0) | (df.index.hour < h1))
    if "weekdays" in f: ok &= df.index.dayofweek.isin(f["weekdays"])
    if "min_adx" in f:
        adx_id = next((k for k, v in spec["indicators"].items() if v["type"] == "adx"), None)
        if adx_id: ok &= ind[adx_id] >= f["min_adx"]
    if "event_blackout" in f and events is not None:
        bk = f["event_blackout"]
        ok &= ~((ind["ev_mins_to_next"] <= bk.get("before_min", 30)) | (ind["ev_mins_since"] <= bk.get("after_min", 30))).fillna(False)
    sig["long_entry"] &= ok; sig["short_entry"] &= ok
    for c in EVENT_FIELDS:
        sig[c] = ind[c] if c in ind else np.nan
    r = spec["risk"]
    sig["atr"] = ind[r.get("atr_ind", "atr")] if r.get("atr_ind", "atr") in ind else _atr(df, 14)
    return sig


def set_path(spec: dict, path: str, value) -> dict:
    """Return a copy with spec['a']['b'] = value for path 'a.b'."""
    s = copy.deepcopy(spec); cur = s
    keys = path.split(".")
    for k in keys[:-1]: cur = cur[k]
    cur[keys[-1]] = value
    return s


EXAMPLE_EMA_RSI = {
    "name": "ema_cross_rsi", "symbol": "EURUSD", "timeframe": "H1",
    "indicators": {"fast": {"type": "ema", "period": 20}, "slow": {"type": "ema", "period": 60},
                   "rsi": {"type": "rsi", "period": 14}, "atr": {"type": "atr", "period": 14}},
    "entry_long": [["fast", "crosses_above", "slow"], ["rsi", "<", 70]],
    "entry_short": [["fast", "crosses_below", "slow"], ["rsi", ">", 30]],
    "filters": {"hours_utc": [6, 20]},
    "risk": {"risk_pct": 1.0, "sl_atr": 2.0, "tp_atr": 4.0, "atr_ind": "atr", "max_bars": 120},
}
