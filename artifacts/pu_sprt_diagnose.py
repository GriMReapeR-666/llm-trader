"""PU-0156 follow-up: WHY does sprt_hypothesis_gate lose least (-0.90 Sharpe)?

Method (all engine-identical to sweep Stage 1):
  1. Bare-engine runs (same defaults: TCE, SL 1.5 ATR / TP 3.0 ATR, thr 0.75)
     for all 13 registry labels + the generic default, on the same live
     EURUSD/H1 5000-bar window the deep sweep used.
  2. Mechanism stats from trades_log: hold length, side split, direction-flip
     rate between consecutive entries, exit-reason mix, r-multiple spread.
  3. Signal-side stats: entry-eligible occupancy, eligible-direction flip
     rate, re-entry transitions, and conditional forward drift (bps) at
     H=12/24 for entry-eligible bars.
  4. Transplant preview: wrap the 4 worst labels' raw scores in the sprt
     accumulator (llr(t) = clip(llr(t-1) + 0.2*sign(raw_t), -2, 2)) and rerun
     the bare engine — direct evidence on whether the mechanism transfers.

Output: artifacts/backtest_results/sprt_diagnosis.json + stdout tables.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "research"))

import numpy as np

from research.full_pipeline_backtest import run_full_pipeline_backtest
from data.market_data_provider import fetch_ohlc
from research.continuous_backtest_pipeline import _periods_per_year
from research.strategy_signal_registry import build_signal, registry_names

BARS = 5000
SYMBOL, TF, THR = "EURUSD", "H1", 0.75
HORIZONS = (12, 24)
# PU-0156 family check: the 5 persistent-vote trend-family labels get the
# sprt-confirm wrapper (fade/noise voters are excluded — the earlier
# transplant preview showed the wrapper harms them).
TRANSPLANT = ["agent_regime_supervisor", "kronos_h1", "gold_momentum_h4",
              "macro_nfp_pullback", "model_bocpd_changepoint"]


def sprt_confirm(raw: np.ndarray, step: float = 0.2, clip: float = 2.0) -> np.ndarray:
    """The sprt mechanism applied to another signal's direction votes."""
    signs = np.sign(raw)
    llr = np.zeros_like(raw, dtype=np.float64)
    for i in range(1, len(raw)):
        llr[i] = np.clip(llr[i - 1] + step * signs[i], -clip, clip)
    return llr


def signal_side_stats(s: np.ndarray, closes: np.ndarray) -> dict:
    thr = THR
    elig = np.abs(s) >= thr
    d = np.sign(s)
    occ = float(elig.mean())
    # flip rate across consecutive eligible bars
    idx = np.flatnonzero(elig)
    flips = int(np.sum(d[idx[1:]] != d[idx[:-1]])) if len(idx) > 1 else 0
    flip_rate = flips / max(len(idx) - 1, 1)
    # re-entry transitions (ineligible -> eligible)
    reentries = int(np.sum(~elig[:-1] & elig[1:]))
    out = {"occupancy": round(occ, 3), "dir_flip_rate": round(flip_rate, 3),
           "reentries": reentries}
    for h in HORIZONS:
        if len(closes) <= h:
            continue
        fwd = (closes[h:] - closes[:-h]) / closes[:-h] * 1e4  # bps
        e = elig[: len(fwd)]
        dd = d[: len(fwd)]
        if e.sum() >= 10:
            edge = float(np.mean(dd[e] * fwd[e]))
            hit = float(np.mean(dd[e] * fwd[e] > 0))
            out[f"edge_bps_h{h}"] = round(edge, 2)
            out[f"hit_h{h}"] = round(hit, 3)
    return out


def trade_mechanics(res) -> dict:
    trades = list(getattr(res, "trades_log", []) or [])
    if not trades:
        return {}
    holds = [t["exit_bar"] - t["entry_bar"] for t in trades]
    sides = [t["side"] for t in trades]
    rs = [t["r_multiple"] for t in trades]
    n_long = sum(1 for s_ in sides if s_ == "BUY")
    r_long = [t["r_multiple"] for t in trades if t["side"] == "BUY"]
    r_short = [t["r_multiple"] for t in trades if t["side"] == "SELL"]
    flips = sum(1 for a, b in zip(sides[1:], sides[:-1]) if a != b)
    reasons: dict = {}
    for t in trades:
        reasons[t.get("exit_reason") or "?"] = reasons.get(t.get("exit_reason") or "?", 0) + 1
    return {
        "avg_hold_bars": round(float(np.mean(holds)), 1),
        "pct_hold_gt_12": round(float(np.mean([h > 12 for h in holds])), 3),
        "n_long": n_long, "n_short": len(trades) - n_long,
        "mean_r_long": round(float(np.mean(r_long)), 3) if r_long else None,
        "mean_r_short": round(float(np.mean(r_short)), 3) if r_short else None,
        "entry_dir_flip_rate": round(flips / max(len(trades) - 1, 1), 3),
        "std_r": round(float(np.std(rs)), 3),
        "exit_mix": reasons,
    }


def run_engine(name, bars, scores=None):
    kw = {}
    if scores is not None:
        kw["entry_signal_scores"] = scores
        kw["entry_signal_threshold"] = THR
    res = run_full_pipeline_backtest(
        bars=bars, symbol=SYMBOL, timeframe=TF,
        periods_per_year=_periods_per_year(TF), name=name, **kw)
    return {
        "sharpe": round(float(res.sharpe_ratio), 2),
        "trades": int(res.total_trades),
        "win_rate": round(float(res.win_rate_pct), 1),
        "pf": round(float(res.profit_factor), 2),
        "expectancy_r": round(float(res.expectancy_r), 3),
        "mechanics": trade_mechanics(res),
    }


def main():
    fetched = fetch_ohlc(symbol=SYMBOL, timeframe=TF, count=BARS)
    if fetched is None or getattr(fetched, "empty", False):
        raise SystemExit("live OHLC unavailable; refusing synthetic replay bars")
    records = fetched.to_dict("records") if hasattr(fetched, "to_dict") else list(fetched)
    closes = np.array([float(b["close"]) for b in records])
    highs = np.array([float(b["high"]) for b in records])
    lows = np.array([float(b["low"]) for b in records])
    print(f"bars={len(records)} span={fetched.index[0]}..{fetched.index[-1]}"
          if hasattr(fetched, "index") else f"bars={len(records)}")

    out = {"symbol": SYMBOL, "timeframe": TF, "bars": len(records),
           "threshold": THR, "labels": {}, "transplant": {}}

    # Generic default for reference
    out["labels"]["default_generic"] = run_engine("default_generic", records)
    print(f"default_generic        sharpe={out['labels']['default_generic']['sharpe']:>7.2f} "
          f"trades={out['labels']['default_generic']['trades']}")

    sigs = {}
    for name in registry_names():
        s = build_signal(name, closes, highs, lows)
        sigs[name] = s
        row = run_engine(name, records, s)
        row["signal"] = signal_side_stats(s, closes)
        out["labels"][name] = row
        print(f"{name:26s} sharpe={row['sharpe']:>7.2f} trades={row['trades']:>3} "
              f"pf={row['pf']:>5.2f} occ={row['signal'].get('occupancy')} "
              f"flip={row['signal'].get('dir_flip_rate')} "
              f"edge12={row['signal'].get('edge_bps_h12')} hit12={row['signal'].get('hit_h12')} "
              f"hold={row['mechanics'].get('avg_hold_bars')} "
              f"flipT={row['mechanics'].get('entry_dir_flip_rate')}")

    # Cross-label: how aligned is each eligible-direction with sprt's?
    sprt_d = np.sign(sigs["sprt_hypothesis_gate"])
    align = {}
    for name, s in sigs.items():
        if name == "sprt_hypothesis_gate":
            continue
        d = np.sign(s)
        e = (np.abs(s) >= THR) & (np.abs(sigs["sprt_hypothesis_gate"]) >= THR)
        if e.sum() > 50:
            align[name] = round(float(np.mean(d[e] == sprt_d[e])), 3)
    out["direction_alignment_with_sprt_when_both_eligible"] = align

    # Transplant preview: sprt-confirm wrapper on the 4 worst labels
    print("\n--- transplant preview: llr(sign(raw)) wrapper ---")
    for name in TRANSPLANT:
        base = out["labels"][name]
        wrapped = sprt_confirm(sigs[name])
        row = run_engine(f"{name}+sprt_confirm", records, wrapped)
        row["signal"] = signal_side_stats(wrapped, closes)
        out["transplant"][name] = {"base": {"sharpe": base["sharpe"], "trades": base["trades"],
                                            "pf": base["pf"]},
                                   "wrapped": row}
        print(f"{name:26s} base={base['sharpe']:>7.2f}/{base['trades']:>3}t -> "
              f"wrapped={row['sharpe']:>7.2f}/{row['trades']:>3}t pf "
              f"{base['pf']:>5.2f}->{row['pf']:>5.2f}")

    # Also: sprt-confirm on the generic? No -- instead transplant INTO sprt's
    # own family: does doubling the evidence bar change it? Skip; keep focused.

    # Family verdict: sharpe rank across every config measured this session
    ranks = sorted(
        [(v["sharpe"], k) for k, v in out["labels"].items()]
        + [(v["wrapped"]["sharpe"], k + "@sprt_confirm")
           for k, v in out["transplant"].items()]
    )
    out["sharpe_rank"] = [{"config": k, "sharpe": sh} for sh, k in ranks]
    print("\nsharpe rank (all configs, best first):")
    for sh, k in ranks:
        print(f"  {k:44s} {sh:>7.2f}")

    path = os.path.join("artifacts", "backtest_results", "sprt_diagnosis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
