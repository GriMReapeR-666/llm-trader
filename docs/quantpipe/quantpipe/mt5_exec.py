"""MT5 + paper execution with a hard RiskGuard. NOTE: MetaTrader5 python package is Windows-only and
needs the MT5 terminal installed/logged in. This module is written against the official API but must be
validated on YOUR demo account before any live use."""
from __future__ import annotations
import json, os, time
import pandas as pd
from .models import ROOT, HOME, load_brokers
from . import store
from .backtest import size_position

RISK_FILE = HOME / "risk.json" if (HOME / "risk.json").exists() else ROOT / "config" / "risk.json"


def risk_limits() -> dict:
    return json.loads(RISK_FILE.read_text())


class MT5Client:
    kind = "mt5"

    def __init__(self):
        import MetaTrader5 as mt5
        self.mt5 = mt5

    def connect(self):
        kw = {k: v for k, v in dict(path=os.getenv("MT5_PATH"), server=os.getenv("MT5_SERVER"), password=os.getenv("MT5_PASSWORD")).items() if v}
        if os.getenv("MT5_LOGIN"): kw["login"] = int(os.getenv("MT5_LOGIN"))
        if not self.mt5.initialize(**kw): raise RuntimeError(f"MT5 initialize failed: {self.mt5.last_error()}")
        return self

    def account(self) -> dict:
        a = self.mt5.account_info()._asdict()
        a["is_demo"] = a["trade_mode"] == self.mt5.ACCOUNT_TRADE_MODE_DEMO
        return a

    def positions(self, symbol=None, magic=None) -> list[dict]:
        p = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        out = [x._asdict() for x in (p or [])]
        return [x for x in out if magic is None or x["magic"] == magic]

    def sym(self, symbol):
        self.mt5.symbol_select(symbol, True)
        return self.mt5.symbol_info(symbol)

    def quote(self, symbol) -> tuple[float, float]:
        t = self.mt5.symbol_info_tick(symbol); return t.bid, t.ask

    def rates(self, symbol, tf, count=600) -> pd.DataFrame:
        self.mt5.symbol_select(symbol, True)
        r = self.mt5.copy_rates_from_pos(symbol, getattr(self.mt5, f"TIMEFRAME_{tf}"), 0, count)
        df = pd.DataFrame(r); df["time"] = pd.to_datetime(df["time"], unit="s")
        return df.set_index("time")  # last row = forming bar; caller drops it

    def _fill(self, info):
        fm = info.filling_mode
        return self.mt5.ORDER_FILLING_IOC if fm & 2 else (self.mt5.ORDER_FILLING_FOK if fm & 1 else self.mt5.ORDER_FILLING_RETURN)

    def market_order(self, symbol, side, lots, sl=None, tp=None, magic=0, comment="qp", deviation=20) -> dict:
        info = self.sym(symbol); bid, ask = self.quote(symbol)
        px = ask if side == "buy" else bid
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(lots),
               "type": self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL, "price": px,
               "deviation": deviation, "magic": magic, "comment": comment[:30],
               "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._fill(info)}
        if sl: req["sl"] = round(sl, info.digits)
        if tp: req["tp"] = round(tp, info.digits)
        r = self.mt5.order_send(req)
        ok = r is not None and r.retcode == self.mt5.TRADE_RETCODE_DONE
        return {"ok": ok, "retcode": getattr(r, "retcode", None), "comment": getattr(r, "comment", None),
                "ticket": getattr(r, "order", None), "price": getattr(r, "price", px)}

    def close_position(self, pos: dict) -> dict:
        info = self.sym(pos["symbol"]); bid, ask = self.quote(pos["symbol"])
        buy = pos["type"] == self.mt5.POSITION_TYPE_BUY
        r = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos["symbol"], "volume": pos["volume"],
                                 "type": self.mt5.ORDER_TYPE_SELL if buy else self.mt5.ORDER_TYPE_BUY,
                                 "position": pos["ticket"], "price": bid if buy else ask, "deviation": 20,
                                 "magic": pos["magic"], "comment": "qp close", "type_time": self.mt5.ORDER_TIME_GTC,
                                 "type_filling": self._fill(info)})
        return {"ok": r is not None and r.retcode == self.mt5.TRADE_RETCODE_DONE, "retcode": getattr(r, "retcode", None)}


class PaperClient:
    """Same interface, virtual fills. Quotes come from MT5 if available, else set via set_quote()."""
    kind = "paper"

    def __init__(self, balance=10000.0, quote_src: MT5Client | None = None):
        self.balance = balance; self._pos = []; self._q = {}; self.src = quote_src; self._t = 1000

    def set_quote(self, symbol, bid, ask): self._q[symbol] = (bid, ask)
    def quote(self, symbol): return self.src.quote(symbol) if self.src else self._q[symbol]
    def rates(self, symbol, tf, count=600): return self.src.rates(symbol, tf, count)

    def account(self):
        return {"balance": self.balance, "equity": self.balance + sum(self._pnl(p) for p in self._pos),
                "margin_free": self.balance, "is_demo": True, "login": 0, "paper": True}

    def _pnl(self, p):
        bid, ask = self.quote(p["symbol"]); b = load_brokers()[p["broker"]].for_symbol(p["symbol"])
        from .backtest import value_per_unit
        d = 1 if p["type"] == 0 else -1; px = bid if d > 0 else ask
        return (px - p["price_open"]) * d * p["volume"] * value_per_unit(b, px)

    def positions(self, symbol=None, magic=None):
        return [p for p in self._pos if (symbol is None or p["symbol"] == symbol) and (magic is None or p["magic"] == magic)]

    def market_order(self, symbol, side, lots, sl=None, tp=None, magic=0, comment="qp", deviation=20, broker="ecn_raw"):
        bid, ask = self.quote(symbol); self._t += 1
        self._pos.append({"ticket": self._t, "symbol": symbol, "type": 0 if side == "buy" else 1, "volume": lots,
                          "price_open": ask if side == "buy" else bid, "sl": sl, "tp": tp, "magic": magic, "broker": broker})
        return {"ok": True, "retcode": 10009, "ticket": self._t, "price": self._pos[-1]["price_open"], "paper": True}

    def close_position(self, pos):
        self.balance += self._pnl(pos); self._pos = [p for p in self._pos if p["ticket"] != pos["ticket"]]
        return {"ok": True, "paper": True}


_client = None


def get_client():
    """Mode is controlled by env. Default = paper. Live needs QP_MODE=live AND QP_LIVE_ENABLED=1,
    and the account must be DEMO unless QP_ALLOW_REAL=1."""
    global _client
    if _client: return _client
    mode = os.getenv("QP_MODE", "paper")
    try:
        mt5c = MT5Client().connect()
    except Exception as e:
        if mode == "live": raise
        mt5c = None; store.log_event("mt5_unavailable", str(e), "warn")
    if mode == "live":
        if os.getenv("QP_LIVE_ENABLED") != "1": raise PermissionError("Set QP_LIVE_ENABLED=1 to allow live orders")
        if not mt5c.account()["is_demo"] and os.getenv("QP_ALLOW_REAL") != "1":
            raise PermissionError("Real account detected. Set QP_ALLOW_REAL=1 only after demo validation.")
        _client = mt5c
    else:
        _client = PaperClient(quote_src=mt5c)
    return _client


class RiskGuard:
    def __init__(self, limits=None): self.l = limits or risk_limits()

    def kill_switch(self) -> bool: return bool(store.kv_get("kill_switch", False))

    def check(self, client, symbol, lots, sl, risk_pct=None) -> tuple[bool, str]:
        L = self.l
        if self.kill_switch(): return False, "kill_switch_on"
        if L.get("allowed_symbols") and symbol not in L["allowed_symbols"]: return False, "symbol_not_allowed"
        if L.get("require_sl") and not sl: return False, "stop_loss_required"
        if risk_pct and risk_pct > L["max_risk_per_trade_pct"]: return False, "risk_per_trade_exceeds_limit"
        acc = client.account(); pos = client.positions()
        if len(pos) >= L["max_open_positions"]: return False, "max_open_positions"
        if sum(p["volume"] for p in pos) + lots > L["max_total_lots"]: return False, "max_total_lots"
        today = time.strftime("%Y-%m-%d"); key = f"day_start_equity:{today}"
        start = store.kv_get(key)
        if start is None: store.kv_set(key, acc["equity"]); start = acc["equity"]
        if (acc["equity"] / start - 1) * 100 <= -L["max_daily_loss_pct"]:
            store.kv_set("kill_switch", True); store.log_event("kill_switch", "daily loss limit hit", "error")
            return False, "daily_loss_limit"
        peak = max(store.kv_get("peak_equity", acc["equity"]), acc["equity"]); store.kv_set("peak_equity", peak)
        if (acc["equity"] / peak - 1) * 100 <= -L["max_total_drawdown_pct"]:
            store.kv_set("kill_switch", True); store.log_event("kill_switch", "max drawdown hit", "error")
            return False, "max_drawdown_limit"
        try:
            bid, ask = client.quote(symbol)
            sp_pts = (ask - bid) / load_brokers()["ecn_raw"].for_symbol(symbol).point
            if sp_pts > L.get("max_spread_points", 1e9): return False, f"spread_too_wide({sp_pts:.0f})"
        except Exception: pass
        return True, "ok"


def place(symbol, side, sl_price, tp_price=None, risk_pct=None, lots=None, broker="ecn_raw",
          strategy="manual", magic=0) -> dict:
    """Single entry point for ALL order flow (MCP tool, TV webhook, live runner)."""
    client = get_client(); b = load_brokers()[broker].for_symbol(symbol)
    bid, ask = client.quote(symbol); px = ask if side == "buy" else bid
    L = risk_limits()
    if L.get("allowed_symbols") and symbol not in L["allowed_symbols"]:
        store.log_event("order_blocked", {"symbol": symbol, "side": side, "why": "symbol_not_allowed", "strategy": strategy}, "warn")
        return {"ok": False, "reason": "symbol_not_allowed"}
    if not sl_price and L.get("require_sl"):
        store.log_event("order_blocked", {"symbol": symbol, "side": side, "why": "stop_loss_required", "strategy": strategy}, "warn")
        return {"ok": False, "reason": "stop_loss_required"}
    acc = client.account()
    if lots is None:
        if not (risk_pct and sl_price): return {"ok": False, "reason": "need lots or (risk_pct and sl)"}
        lots, st = size_position(b, acc["equity"], px, abs(px - sl_price), risk_pct, None, acc.get("margin_free", acc["equity"]))
        if st != "ok": return {"ok": False, "reason": st}
    ok, why = RiskGuard().check(client, symbol, lots, sl_price, risk_pct)
    if not ok:
        store.log_event("order_blocked", {"symbol": symbol, "side": side, "why": why, "strategy": strategy}, "warn")
        return {"ok": False, "reason": why}
    kw = {"broker": broker} if client.kind == "paper" else {}
    res = client.market_order(symbol, side, lots, sl_price, tp_price, magic, f"qp:{strategy}", **kw)
    store.log_event("order", {"symbol": symbol, "side": side, "lots": lots, "sl": sl_price, "tp": tp_price,
                              "strategy": strategy, "mode": client.kind, "result": res})
    return {**res, "lots": lots, "mode": client.kind}


def close_all(symbol=None, magic=None) -> list:
    c = get_client(); res = [c.close_position(p) for p in c.positions(symbol, magic)]
    store.log_event("close_all", {"symbol": symbol, "magic": magic, "n": len(res)}); return res
