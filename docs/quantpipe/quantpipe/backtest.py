"""Bar-based backtester with broker constraints (spread, commission, slippage, swap, lot rules,
stops level, leverage/margin, stop-out) and market-condition scenarios.
Conventions: data = BID prices. Long enters at ask (bid+spread), exits at bid. Short enters at bid,
exits at ask. Signals computed on bar close, filled at NEXT bar open. If SL and TP are both inside one
bar the SL is assumed hit first (conservative). Gaps through stops fill at the open."""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from .models import BrokerProfile, Scenario, SCENARIOS, bars_per_year
from .data import apply_scenario
from .strategy import compute_signals


def value_per_unit(b: BrokerProfile, price: float) -> float:
    """Account-currency P&L per 1.0 price move per 1.0 lot (account ccy assumed USD)."""
    return b.contract_size / price if b.usd_base else b.contract_size


def margin_per_lot(b: BrokerProfile, price: float) -> float:
    return b.contract_size / b.leverage * (1.0 if b.usd_base else price)


def size_position(b, equity, price, sl_dist, risk_pct, fixed_lots=None, free_margin=None):
    """Shared by backtest AND live so sizing logic is identical. Returns (lots, status)."""
    vpu = value_per_unit(b, price)
    lots = fixed_lots if fixed_lots else equity * risk_pct / 100.0 / max(sl_dist * vpu, 1e-12)
    lots = math.floor(lots / b.lot_step + 1e-9) * b.lot_step
    lots = min(lots, b.max_lot)
    if lots < b.min_lot - 1e-12:
        return 0.0, "below_min_lot"
    if free_margin is not None:
        cap = math.floor(free_margin * 0.9 / margin_per_lot(b, price) / b.lot_step) * b.lot_step
        if cap < b.min_lot - 1e-12:
            return 0.0, "insufficient_margin"
        lots = min(lots, cap)
    return round(lots, 6), "ok"


def run_backtest(df: pd.DataFrame, spec: dict, broker: BrokerProfile, scenario: Scenario | None = None,
                 initial_balance: float = 10000.0, start_idx: int = 0, events=None,
                 event_spread_mult: float = 3.0, event_slip_mult: float = 3.0) -> dict:
    sc = scenario or SCENARIOS["baseline"]
    b = broker.for_symbol(spec["symbol"])
    data = apply_scenario(df, sc)
    sig = compute_signals(data, spec, events)
    n = len(data)
    O, H, L, C = (data[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    atr = sig["atr"].to_numpy(float)
    LE, SE = sig["long_entry"].to_numpy(bool), sig["short_entry"].to_numpy(bool)
    LX, SX = np.asarray(sig["long_exit"], bool), np.asarray(sig["short_exit"], bool)
    idx = data.index
    sp0 = b.spread_points * b.point * sc.spread_mult
    slip0 = b.slippage_points * b.point * sc.slippage_mult
    ev_in = np.nan_to_num(sig["ev_in_bar"].to_numpy(float)) > 0      # bar contains a scheduled high-impact release
    ev_since = sig["ev_mins_since"].to_numpy(float); ev_next = sig["ev_mins_to_next"].to_numpy(float)
    sp_arr = np.where(ev_in, sp0 * event_spread_mult, sp0); slip_arr = np.where(ev_in, slip0 * event_slip_mult, slip0)
    sp, slip = sp0, slip0
    flat_before = risk.get("flatten_before_event_min") if (risk := spec["risk"]) else None
    def ev_ctx(t):
        if np.isnan(ev_since[t]) and np.isnan(ev_next[t]): return "no_events"
        if not np.isnan(ev_since[t]) and ev_since[t] <= 240: return "post_event"
        if not np.isnan(ev_next[t]) and ev_next[t] <= 240: return "pre_event"
        return "quiet"
    risk = spec["risk"]; max_bars = risk.get("max_bars"); reverse = risk.get("allow_reverse", True)
    sl_k, tp_k = risk.get("sl_atr"), risk.get("tp_atr")
    min_dist = b.stops_level_points * b.point

    ap = (atr / C)[~np.isnan(atr)]
    lo_q, hi_q = (np.quantile(ap, [1 / 3, 2 / 3]) if len(ap) else (0, 0))

    def regime(t):
        if np.isnan(atr[t]): return "unknown"
        r = atr[t] / C[t]
        return "low_vol" if r <= lo_q else ("high_vol" if r > hi_q else "mid_vol")

    balance = initial_balance
    equity = np.full(n, np.nan)
    trades, skipped = [], {"below_min_lot": 0, "insufficient_margin": 0}
    pos = None; pend_entry = None; pend_exit = False

    def close(t, price, reason):
        nonlocal balance, pos
        d = pos["dir"]
        gross = (price - pos["entry"]) * d * pos["lots"] * value_per_unit(b, price)
        comm = pos["lots"] * b.commission_per_lot
        days = (idx[t].normalize() - pos["time"].normalize()).days
        swap = pos["lots"] * b.swap_per_lot_day * days
        pnl = gross - comm + swap
        spread_cost = sp * pos["lots"] * value_per_unit(b, price)
        risk_amt = pos["sl_dist"] * pos["lots"] * value_per_unit(b, price) if pos["sl_dist"] else None
        balance += pnl
        trades.append({"entry_time": str(pos["time"]), "exit_time": str(idx[t]), "side": "long" if d > 0 else "short",
                       "lots": pos["lots"], "entry": round(pos["entry"], 6), "exit": round(price, 6),
                       "pnl": round(pnl, 2), "gross": round(gross, 2), "commission": round(comm, 2),
                       "swap": round(swap, 2), "spread_cost": round(spread_cost, 2),
                       "r": round(pnl / risk_amt, 3) if risk_amt else None, "bars": t - pos["bar"],
                       "reason": reason, "regime": pos["regime"], "ev_ctx": pos["ev"], "hour": pos["time"].hour,
                       "weekday": pos["time"].dayofweek})
        pos = None

    for t in range(n):
        sp, slip = sp_arr[t], slip_arr[t]
        # 1. pending signal-exit at this open
        if pos and pend_exit:
            px = O[t] - slip if pos["dir"] > 0 else O[t] + sp + slip
            close(t, px, "signal"); pend_exit = False
        # 2. pending entry at this open
        if pend_entry and pos is None and t >= start_idx:
            d, a_sig, reg = pend_entry["dir"], pend_entry["atr"], pend_entry["regime"]; evc = pend_entry["ev"]
            entry = O[t] + sp + slip if d > 0 else O[t] - slip
            sl_dist = tp_dist = None
            if sl_k:
                sl_dist = max(sl_k * a_sig, min_dist)
                tp_dist = max(tp_k * a_sig, min_dist) if tp_k else None
            used = 0.0
            lots, status = size_position(b, balance, entry, sl_dist or 0.0, risk.get("risk_pct", 1.0),
                                         risk.get("fixed_lots"), free_margin=balance)
            if status != "ok": skipped[status] += 1
            else:
                pos = {"ev": evc, "dir": d, "entry": entry, "lots": lots, "time": idx[t], "bar": t, "regime": reg,
                       "sl_dist": sl_dist,
                       "sl": (entry - d * sl_dist) if sl_dist else None,
                       "tp": (entry + d * tp_dist) if tp_dist else None}
        pend_entry = None
        # 3. intrabar SL / TP
        if pos:
            d = pos["dir"]
            if d > 0:
                if pos["sl"] is not None and O[t] <= pos["sl"]: close(t, O[t] - slip, "sl_gap")
                elif pos["sl"] is not None and L[t] <= pos["sl"]: close(t, pos["sl"] - slip, "sl")
                elif pos["tp"] is not None and H[t] >= pos["tp"]: close(t, max(pos["tp"], min(O[t], H[t])) if O[t] >= pos["tp"] else pos["tp"], "tp")
            else:
                if pos["sl"] is not None and O[t] + sp >= pos["sl"]: close(t, O[t] + sp + slip, "sl_gap")
                elif pos["sl"] is not None and H[t] + sp >= pos["sl"]: close(t, pos["sl"] + slip, "sl")
                elif pos["tp"] is not None and L[t] + sp <= pos["tp"]: close(t, pos["tp"], "tp")
        # 4. mark to market, stop-out, time stop
        if pos:
            unreal = (C[t] - pos["entry"]) * pos["lots"] * value_per_unit(b, C[t]) if pos["dir"] > 0 else \
                     (pos["entry"] - (C[t] + sp)) * pos["lots"] * value_per_unit(b, C[t])
            eq = balance + unreal
            m = pos["lots"] * margin_per_lot(b, C[t])
            if eq / m * 100 < b.stop_out_level:
                close(t, C[t] if pos["dir"] > 0 else C[t] + sp, "stop_out"); eq = balance
            elif flat_before and not np.isnan(ev_next[t]) and ev_next[t] <= flat_before:
                close(t, C[t] if pos["dir"] > 0 else C[t] + sp, "pre_event"); eq = balance
            elif max_bars and t - pos["bar"] >= max_bars:
                close(t, C[t] if pos["dir"] > 0 else C[t] + sp, "time"); eq = balance
            equity[t] = eq
        else:
            equity[t] = balance
        # 5. signals at this bar's close -> act next bar
        if t < n - 1 and t >= start_idx and not np.isnan(atr[t]):
            if pos is None:
                if LE[t] and not SE[t]: pend_entry = {"dir": 1, "atr": atr[t], "regime": regime(t), "ev": ev_ctx(t)}
                elif SE[t] and not LE[t]: pend_entry = {"dir": -1, "atr": atr[t], "regime": regime(t), "ev": ev_ctx(t)}
            else:
                opp = SE[t] if pos["dir"] > 0 else LE[t]
                ex = LX[t] if pos["dir"] > 0 else SX[t]
                if opp or ex:
                    pend_exit = True
                    if opp and reverse: pend_entry = {"dir": -pos["dir"], "atr": atr[t], "regime": regime(t), "ev": ev_ctx(t)}
    if pos:  # close any open trade at final bar
        close(n - 1, C[-1] if pos["dir"] > 0 else C[-1] + sp, "end_of_data"); equity[-1] = balance

    eq = pd.Series(equity, index=idx).iloc[start_idx:]
    metrics = compute_metrics(eq, trades, initial_balance, bars_per_year(spec["timeframe"]))
    metrics["skipped_trades"] = skipped
    return {"metrics": metrics, "trades": trades, "equity": eq, "broker": b.name, "scenario": sc.name}


def compute_metrics(eq: pd.Series, trades: list, initial: float, bpy: float) -> dict:
    m = {"trades": len(trades), "initial_balance": initial}
    if len(eq) < 3:
        return m
    final = float(eq.iloc[-1]); ret = eq.pct_change().dropna()
    dd = eq / eq.cummax() - 1
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    m.update(final_equity=round(final, 2), net_profit=round(final - initial, 2),
             return_pct=round((final / initial - 1) * 100, 2),
             cagr_pct=round(((final / initial) ** (1 / years) - 1) * 100, 2) if final > 0 else -100.0,
             max_drawdown_pct=round(float(dd.min()) * 100, 2),
             sharpe=round(float(ret.mean() / ret.std() * math.sqrt(bpy)), 3) if ret.std() > 0 else 0.0)
    neg = ret[ret < 0]
    m["sortino"] = round(float(ret.mean() / neg.std() * math.sqrt(bpy)), 3) if len(neg) > 1 and neg.std() > 0 else 0.0
    m["calmar"] = round(m["cagr_pct"] / abs(m["max_drawdown_pct"]), 3) if m["max_drawdown_pct"] < 0 else 0.0
    if trades:
        pnl = np.array([t["pnl"] for t in trades]); w, l = pnl[pnl > 0], pnl[pnl <= 0]
        rs = [t["r"] for t in trades if t["r"] is not None]
        streak = best = 0
        for p in pnl:
            streak = streak + 1 if p <= 0 else 0; best = max(best, streak)
        m.update(win_rate_pct=round(len(w) / len(pnl) * 100, 1),
                 profit_factor=float(round(min(w.sum() / abs(l.sum()), 99.0), 3)) if l.sum() != 0 else 99.0,
                 expectancy=round(float(pnl.mean()), 2), expectancy_r=round(float(np.mean(rs)), 3) if rs else None,
                 avg_win=round(float(w.mean()), 2) if len(w) else 0.0, avg_loss=round(float(l.mean()), 2) if len(l) else 0.0,
                 max_consec_losses=best, avg_bars_in_trade=round(float(np.mean([t["bars"] for t in trades])), 1),
                 total_costs=round(sum(t["commission"] + t["spread_cost"] - min(t["swap"], 0) for t in trades), 2),
                 exposure_pct=round(sum(t["bars"] for t in trades) / len(eq) * 100, 1))
    return m
