"""Diagnostics, stress matrix, optimisation, walk-forward. Designed to give an LLM compact,
structured evidence it can reason over to propose spec changes."""
from __future__ import annotations
import itertools, random
import numpy as np, pandas as pd
from .backtest import run_backtest
from .strategy import set_path

RANK_KEYS = ("sharpe", "net_profit", "calmar", "profit_factor", "expectancy_r", "return_pct")


def _grp(df: pd.DataFrame, col: str) -> dict:
    out = {}
    for k, g in df.groupby(col):
        out[str(k)] = {"n": int(len(g)), "pnl": round(float(g.pnl.sum()), 2),
                       "win_rate_pct": round(float((g.pnl > 0).mean() * 100), 1),
                       "avg_r": round(float(g.r.dropna().mean()), 3) if g.r.notna().any() else None}
    return out


def diagnostics(trades: list, metrics: dict) -> dict:
    if not trades:
        return {"flags": ["NO_TRADES: entry rules never fire or all trades skipped (lot/margin limits)"], "skipped": metrics.get("skipped_trades")}
    df = pd.DataFrame(trades)
    gross_win = df.loc[df.gross > 0, "gross"].sum()
    costs = df.commission.sum() + df.spread_cost.sum() - df.swap.clip(upper=0).sum()
    top3 = df.pnl.nlargest(3).sum(); total = df.pnl.sum()
    d = {"by_side": _grp(df, "side"), "by_exit_reason": _grp(df, "reason"), "by_regime": _grp(df, "regime"), "by_event_context": _grp(df, "ev_ctx"),
         "by_hour": _grp(df, "hour"), "by_weekday": _grp(df, "weekday"),
         "cost_drag_pct_of_gross_profit": round(float(costs / gross_win * 100), 1) if gross_win > 0 else None,
         "avg_bars_winners": round(float(df.loc[df.pnl > 0, "bars"].mean()), 1) if (df.pnl > 0).any() else None,
         "avg_bars_losers": round(float(df.loc[df.pnl <= 0, "bars"].mean()), 1) if (df.pnl <= 0).any() else None}
    flags = []
    if len(df) < 30: flags.append(f"LOW_SAMPLE: only {len(df)} trades - metrics statistically weak")
    if d["cost_drag_pct_of_gross_profit"] and d["cost_drag_pct_of_gross_profit"] > 40:
        flags.append("COST_DRAG: costs eat >40% of gross profit -> widen targets, trade a higher timeframe, or reduce frequency")
    if total > 0 and top3 / total > 0.6: flags.append("CONCENTRATION: top 3 trades make >60% of profit - likely fragile")
    reg = d["by_regime"]
    if len(reg) > 1 and sum(1 for v in reg.values() if v["pnl"] < 0) >= 1 and total > 0:
        worst = min(reg, key=lambda k: reg[k]["pnl"])
        if reg[worst]["pnl"] < 0: flags.append(f"REGIME_WEAKNESS: loses in '{worst}' - consider an ADX/ATR filter")
    sides = d["by_side"]
    if len(sides) == 2:
        bad = [k for k, v in sides.items() if v["pnl"] < 0]
        if bad: flags.append(f"SIDE_ASYMMETRY: {bad[0]} side is net negative - consider disabling it")
    ec = d["by_event_context"]
    if "post_event" in ec and ec["post_event"]["pnl"] < 0 and ec["post_event"]["n"] >= 5:
        flags.append("EVENT_LOSSES: trades opened within 4h after high-impact releases lose money -> add event_blackout or trade the surprise (ev_surprise_net)")
    sl = d["by_exit_reason"].get("sl", {}).get("n", 0) + d["by_exit_reason"].get("sl_gap", {}).get("n", 0)
    if sl / len(df) > 0.7: flags.append("STOP_HEAVY: >70% exits are stop-outs - stops may be too tight or entries too early")
    if metrics.get("max_consec_losses", 0) >= 10: flags.append("LOSING_STREAKS: 10+ consecutive losses; check psychological/prop-firm limits")
    if metrics.get("max_drawdown_pct", 0) < -20: flags.append("DEEP_DRAWDOWN: >20% - lower risk_pct")
    d["flags"] = flags
    return d


def stress_matrix(df, spec, brokers: dict, scenarios: dict, start_idx=0, events=None) -> dict:
    rows = []
    for bn, b in brokers.items():
        for sn, sc in scenarios.items():
            m = run_backtest(df, spec, b, sc, start_idx=start_idx, events=events)["metrics"]
            rows.append({"broker": bn, "scenario": sn, **{k: m.get(k) for k in
                         ("net_profit", "return_pct", "max_drawdown_pct", "sharpe", "profit_factor", "trades", "total_costs")}})
    profitable = sum(1 for r in rows if (r["net_profit"] or 0) > 0)
    rets = [r["return_pct"] or 0 for r in rows]
    return {"cells": rows, "profitable_cells": f"{profitable}/{len(rows)}",
            "worst_return_pct": min(rets), "median_return_pct": float(np.median(rets)),
            "worst_drawdown_pct": min(r["max_drawdown_pct"] or 0 for r in rows),
            "robust": profitable / len(rows) >= 0.7 and min(rets) > -15}


def optimize(df, spec, grid: dict, broker, scenario=None, metric="sharpe", min_trades=30, max_combos=150,
             start_idx=0, seed=0, events=None) -> dict:
    assert metric in RANK_KEYS, f"metric must be one of {RANK_KEYS}"
    keys = list(grid); combos = list(itertools.product(*[grid[k] for k in keys]))
    total = len(combos)
    if total > max_combos:
        random.Random(seed).shuffle(combos); combos = combos[:max_combos]
    res = []
    for c in combos:
        s = spec
        for k, v in zip(keys, c): s = set_path(s, k, v)
        m = run_backtest(df, s, broker, scenario, start_idx=start_idx, events=events)["metrics"]
        if m.get("trades", 0) < min_trades or metric not in m or m[metric] is None: continue
        res.append({"params": dict(zip(keys, c)), "score": m[metric], "net_profit": m["net_profit"],
                    "max_drawdown_pct": m["max_drawdown_pct"], "trades": m["trades"], "profit_factor": m.get("profit_factor")})
    res.sort(key=lambda r: r["score"], reverse=True)
    scores = [r["score"] for r in res]
    return {"tested": len(combos), "grid_size": total, "qualified": len(res), "metric": metric, "top": res[:10],
            "score_spread": {"best": scores[0], "median": float(np.median(scores))} if scores else None,
            "warning": "In-sample ranking only. Run walk_forward before trusting any result; "
                       "a best score far above the median usually means curve-fitting."}


def walk_forward(df, spec, grid, broker, scenario=None, folds=4, train_frac=0.5, metric="sharpe",
                 min_trades=15, max_combos=60, warm=300, events=None) -> dict:
    n = len(df); train_len = int(n * train_frac); test_len = (n - train_len) // folds
    out = []
    for k in range(folds):
        ts = train_len + k * test_len; te = ts + test_len if k < folds - 1 else n
        a = max(0, ts - train_len)
        tr_df = df.iloc[a:ts]
        opt = optimize(tr_df, spec, grid, broker, scenario, metric, min_trades, max_combos, start_idx=min(warm, len(tr_df) // 4), events=events)
        if not opt["top"]:
            out.append({"fold": k, "error": "no qualifying params in-sample"}); continue
        best = opt["top"][0]; s = spec
        for p, v in best["params"].items(): s = set_path(s, p, v)
        te_df = df.iloc[max(0, ts - warm):te]
        oos = run_backtest(te_df, s, broker, scenario, start_idx=ts - max(0, ts - warm), events=events)["metrics"]
        out.append({"fold": k, "params": best["params"], "is_score": best["score"], "oos_score": oos.get(metric),
                    "oos_return_pct": oos.get("return_pct"), "oos_dd_pct": oos.get("max_drawdown_pct"), "oos_trades": oos.get("trades")})
    ok = [f for f in out if "oos_score" in f and f["oos_score"] is not None]
    is_m = np.mean([f["is_score"] for f in ok]) if ok else None
    oos_m = np.mean([f["oos_score"] for f in ok]) if ok else None
    prof = sum(1 for f in ok if (f["oos_return_pct"] or 0) > 0)
    wfe = float(oos_m / is_m) if ok and is_m and is_m > 0 else None
    return {"folds": out, "profitable_oos_folds": f"{prof}/{len(out)}", "mean_is_score": is_m, "mean_oos_score": oos_m,
            "walk_forward_efficiency": wfe,
            "verdict": ("PASS" if ok and prof / len(out) >= 0.6 and (wfe or 0) >= 0.4 else "FAIL"),
            "note": "PASS needs >=60% profitable OOS folds and OOS/IS score >= 0.4."}
