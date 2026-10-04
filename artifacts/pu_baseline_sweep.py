"""PU baseline sweep: buy-and-hold, trend, and random-null baselines on the
SAME data, friction, SL/TP rules, and metrics as the deep 5000-bar sweep, then
a diff against the 14 strategy labels in deep_sweep_5000.json.

Framework demand ("baselines the system must beat after costs"):
  - baseline_buy_hold_long / _short : ride the window once, pay spread+slippage
    once (SL/TP set far enough to never trigger; exits END_OF_SERIES). The
    long/short pair exposes direction beta — if BH-short beats every strategy
    label, the sweep's "alpha" is just the bear move of the window.
  - baseline_trend_50_200           : classic time-series trend (EMA50 vs
    EMA200 regime entries), SL 30 / TP 60 pips — the same risk envelope the
    sweep's generic ruleset trades under.
  - baseline_random_null_seed42     : seeded random entries (p=0.02 per bar per
    side), same SL/TP + friction — the null distribution for "any entries at
    all under these rules".
Cost-stress pass (retail fragility test): trend + random re-run at spread
3.0 / slippage 1.0 pips (2x). If trend's edge dies at 2x costs, it is fragile.

Writes artifacts/backtest_results/baseline_sweep_5000.json with per-baseline
metrics + a comparison block against the 14-label sweep.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "artifacts" / "backtest_results" / "baseline_sweep_5000.json"
DEEP = ROOT / "artifacts" / "backtest_results" / "deep_sweep_5000.json"
BARS = 5000
SYMBOL, TF = "EURUSD", "H1"
SEED = 42


def metrics_dict(m) -> dict:
    d = m.to_dict() if hasattr(m, "to_dict") else dict(getattr(m, "__dict__", {}))
    keep = ("sharpe_ratio", "total_trades", "win_rate", "profit_factor",
            "expectancy_r", "max_drawdown_pct", "total_pnl_pips", "avg_pnl")
    return {k: d.get(k) for k in keep if k in d} or d


def run(engine, name, bars_df, el, es, sl=30.0, tp=60.0):
    t0 = time.time()
    m = engine.run_strategy_backtest(
        bars_df=bars_df, entries_long=el, entries_short=es,
        symbol=SYMBOL, timeframe=TF, sl_pips=sl, tp_pips=tp,
        strategy_name=name,
    )
    entry = {"baseline": name, "sl_pips": sl, "tp_pips": tp,
             "seconds": round(time.time() - t0, 2)}
    entry.update(metrics_dict(m))
    # r-multiples are only meaningful when SL is the risk unit (skip for BH).
    if sl < 1e5:
        rms = [t["r_multiple"] for t in getattr(engine, "_last_trades", [])] or None
        if rms:
            entry["r_multiple_mean"] = round(float(np.mean(rms)), 4)
    print(f"[BASELINE] {name}: trades={entry.get('total_trades')} "
          f"sharpe={entry.get('sharpe_ratio')} pf={entry.get('profit_factor')} "
          f"win%={entry.get('win_rate')} pnl_pips={entry.get('total_pnl_pips')} "
          f"({entry['seconds']}s)", flush=True)
    return entry


def main():
    from research.mt5_backtest_engine import MT5BrokerBacktestEngine

    df = MT5BrokerBacktestEngine().fetch_market_data(SYMBOL, TF, BARS)
    n = len(df)
    close = df["close"].astype(float).values
    print(f"data: {n} bars {df.index[0]} -> {df.index[-1]} "
          f"first={close[0]:.5f} last={close[-1]:.5f} "
          f"window_return={100*(close[-1]/close[0]-1):.2f}%", flush=True)

    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values
    ema200 = pd.Series(close).ewm(span=200, adjust=False).mean().values
    rng = np.random.default_rng(SEED)

    far_sl, far_tp = 1_000_000.0, 1_000_000_000.0  # never triggers: pure hold
    bh_long = np.zeros(n, dtype=bool); bh_long[1] = True
    bh_short = np.zeros(n, dtype=bool); bh_short[1] = True
    trend_l = ema50 > ema200
    trend_s = ema50 < ema200
    rand_l = rng.random(n) < 0.02
    rand_s = rng.random(n) < 0.02

    runs = []
    # 1) Buy-and-hold pair (direction beta of the window).
    e = MT5BrokerBacktestEngine()  # spread 1.5, slip 0.5 (sweep friction)
    runs.append(run(e, "baseline_buy_hold_long", df, bh_long, np.zeros(n, bool), sl=far_sl, tp=far_tp))
    runs.append(run(e, "baseline_buy_hold_short", df, np.zeros(n, bool), bh_short, sl=far_sl, tp=far_tp))
    # 2) Trend 50/200 under the sweep's SL/TP envelope.
    runs.append(run(e, "baseline_trend_50_200", df, trend_l, trend_s))
    # 3) Random null under identical rules.
    runs.append(run(e, "baseline_random_null_seed42", df, rand_l, rand_s))
    # 4) Cost-stress pass (2x friction) on rules-based baselines.
    e2x = MT5BrokerBacktestEngine(spread_pips=3.0, slippage_pips=1.0)
    runs.append(run(e2x, "baseline_trend_50_200_costx2", df, trend_l, trend_s))
    runs.append(run(e2x, "baseline_random_null_seed42_costx2", df, rand_l, rand_s))

    # --- diff against the 14-label deep sweep ---
    comparison = {"source": "deep_sweep_5000.json", "labels": 0,
                  "labels_sharpe": None, "beaten_on_sharpe": {}}
    try:
        deep = json.loads(DEEP.read_text(encoding="utf-8"))
        label_sharpes = [r.get("backtest", {}).get("sharpe") for r in deep.get("runs", [])
                         if isinstance(r.get("backtest", {}).get("sharpe"), (int, float))]
        comparison["labels"] = len(label_sharpes)
        comparison["labels_sharpe"] = label_sharpes[0] if label_sharpes else None
        comparison["labels_sharpe_all_identical"] = len(set(map(str, label_sharpes))) <= 1
        for r in runs:
            bs = r.get("sharpe_ratio")
            if isinstance(bs, (int, float)) and label_sharpes:
                comparison["beaten_on_sharpe"][r["baseline"]] = \
                    sum(1 for s in label_sharpes if bs > s)
    except Exception as exc:
        comparison["error"] = str(exc)[:300]

    out = {"symbol": SYMBOL, "timeframe": TF, "bars": n,
           "friction": {"spread_pips": 1.5, "slippage_pips": 0.5,
                        "costx2": {"spread_pips": 3.0, "slippage_pips": 1.0}},
           "seed": SEED, "runs": runs, "comparison": comparison}
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"WROTE {OUT}", flush=True)
    print("BASELINE-SWEEP-DONE", flush=True)


if __name__ == "__main__":
    main()
