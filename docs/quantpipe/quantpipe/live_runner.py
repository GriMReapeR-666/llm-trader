"""Autonomous loop: runs deployed strategy specs on closed bars, sized with the same function as the
backtester, all orders through RiskGuard. Start:  python -m quantpipe.live_runner"""
from __future__ import annotations
import time
from . import store, mt5_exec as ex
from .models import load_brokers
import os
from .strategy import compute_signals, uses_events
from . import events as E

_last_bar: dict = {}


def tick_once(dep: dict):
    spec, _ = store.get_strategy(dep["strategy"], dep["version"])
    client = ex.get_client(); sym = spec["symbol"]
    df = client.rates(sym, spec["timeframe"], 600).iloc[:-1]       # drop forming bar
    if len(df) < 100 or _last_bar.get(dep["name"]) == df.index[-1]: return None
    _last_bar[dep["name"]] = df.index[-1]
    ev = None
    if uses_events(spec):
        name = os.getenv("QP_LIVE_EVENTS")
        if not name: return {"action": "blocked", "why": "spec uses events; set QP_LIVE_EVENTS=<event set> and keep it refreshed (ExportCalendar.mq5 + import_events)"}
        ev = E.load_events(name)              # re-read each bar so refreshed calendars are picked up
    sig = compute_signals(df, spec, ev).iloc[-1]; r = spec["risk"]
    magic = store.magic_for(dep["name"])
    pos = client.positions(sym, magic)
    if pos:
        d_long = pos[0]["type"] == 0
        opp = sig.short_entry if d_long else sig.long_entry; exit_ = sig.long_exit if d_long else sig.short_exit
        if opp or exit_: ex.close_all(sym, magic)
        if not (opp and r.get("allow_reverse", True)): return {"action": "closed" if (opp or exit_) else "hold"}
    if sig.long_entry == sig.short_entry: return {"action": "none"}
    side = "buy" if sig.long_entry else "sell"; a = float(sig.atr)
    bid, ask = client.quote(sym); px = ask if side == "buy" else bid; d = 1 if side == "buy" else -1
    sl = px - d * r["sl_atr"] * a if r.get("sl_atr") else None
    tp = px + d * r["tp_atr"] * a if r.get("tp_atr") else None
    return ex.place(sym, side, sl, tp, r.get("risk_pct", 1.0), r.get("fixed_lots"), dep["broker"], dep["name"], magic)


def main(poll=15):
    store.log_event("runner_start", {"mode": ex.get_client().kind})
    while True:
        for dep in store.get_deployments():
            try:
                res = tick_once(dep)
                if res: store.log_event("runner_tick", {"dep": dep["name"], "res": res})
            except Exception as e:
                store.log_event("runner_error", {"dep": dep["name"], "err": repr(e)}, "error")
        time.sleep(poll)


if __name__ == "__main__":
    main()
