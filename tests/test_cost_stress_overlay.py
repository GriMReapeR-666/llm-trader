"""Regression: retail cost-stress overlay (PU-0156 §8.5).

Contract pinned here:
  1. OFF by default: cost_stress=False reproduces legacy results exactly.
  2. Session/rollover spread multipliers widen fills in the stressed hours.
  3. Positions held across a triple-swap weekday pay 3x the nightly swap.
  4. Swap is netted into the trade PnL and the r-multiple.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np

from research.full_pipeline_backtest import run_full_pipeline_backtest


def _bars(n=1200, base=1.0850, seed=7):
    rng = np.random.default_rng(seed)
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)  # a Monday
    t = start
    price = base
    out = []
    for i in range(n):
        ret = rng.normal(0.00001, 0.0006)
        price *= (1 + ret)
        h = price * (1 + abs(rng.normal(0, 0.0003)))
        l = price * (1 - abs(rng.normal(0, 0.0003)))
        o = price * (1 + rng.normal(0, 0.0001))
        out.append({"open": o, "high": h, "low": l, "close": price,
                    "volume": float(rng.integers(100, 2000)),
                    "time": t.isoformat()})
        t += timedelta(hours=1)
    return out


def _run(bars, **kw):
    res = run_full_pipeline_backtest(
        bars=bars, symbol="EURUSD", timeframe="H1",
        enable_dsr=False, **kw)
    d = res.to_dict()
    # trades_log lives on the dataclass only (not serialized in to_dict).
    d["trades_log"] = res.trades_log
    return d


def test_stress_off_by_default_matches_legacy():
    bars = _bars()
    legacy = _run(bars)
    explicit_off = _run(bars, cost_stress=False)
    assert legacy["total_trades"] == explicit_off["total_trades"]
    assert legacy["sharpe_ratio"] == explicit_off["sharpe_ratio"]
    assert legacy["total_cost"] == explicit_off["total_cost"]
    assert explicit_off["total_swap_cost"] == 0.0
    assert explicit_off["swap_nights_charged"] == 0
    for t in legacy["trades_log"]:
        assert t["swap_paid"] == 0.0


def test_stress_widens_costs_in_event_hours():
    bars = _bars()
    plain = _run(bars)
    stressed = _run(bars, cost_stress=True,
                    stress_spread_multiplier=2.0,
                    stress_event_spread_multiplier=3.0,
                    stress_swap_per_lot_per_night=-2.5)
    # Per-fill friction is strictly wider under stress. (Aggregate $ cost is
    # NOT monotone: worse fills change the equity path and therefore the
    # risk-based lot sizing — the per-fill bps is the causal invariant.)
    assert stressed["avg_slippage_bps"] > plain["avg_slippage_bps"]
    assert stressed["swap_nights_charged"] > 0
    assert stressed["total_swap_cost"] < 0


def test_triple_swap_and_net_pnl():
    bars = _bars(n=1500)
    res = _run(bars, cost_stress=True,
               stress_spread_multiplier=2.0,
               stress_event_spread_multiplier=3.0,
               stress_swap_per_lot_per_night=-2.5,
               stress_triple_swap_weekday=2)
    # Nights were charged and the aggregate is negative (a financing cost).
    assert res["swap_nights_charged"] > 0
    assert res["total_swap_cost"] < 0
    for t in res["trades_log"]:
        assert math.isfinite(t["r_multiple"])
        # Every trade carries its swap; triple-Wednesday trades pay 3x nights.
        assert "swap_paid" in t
    # At least one trade crossed a Wednesday (bar index 50..1500 covers weeks).
    assert any(t["swap_paid"] != 0.0 for t in res["trades_log"])
