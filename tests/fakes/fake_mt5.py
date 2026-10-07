"""A minimal in-process MetaTrader5 stand-in that records every order_send.

Used by characterization tests (Phase 1) and the order-gateway tests (Phase 2).
"""
import sys
from types import SimpleNamespace as NS


class FakeMT5:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
    ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING = 1, 5
    TRADE_ACTION_SLTP, TRADE_ACTION_REMOVE = 6, 8
    ORDER_TIME_GTC, ORDER_TIME_SPECIFIED = 0, 2
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE = 10008, 10009

    def __init__(self, *, bid=1.1000, ask=1.1002, equity=10_000.0):
        self.bid, self.ask, self.equity = bid, ask, equity
        self.sent = []          # every order_send request, in order
        self.positions = []
        self.orders = []        # pending orders (SimpleNamespace with ticket/symbol/magic)
        self.deals = []         # broker deal history (SimpleNamespace rows)

    # --- account / market -------------------------------------------------
    def initialize(self, *a, **k):
        return True

    def terminal_info(self):
        return NS(connected=True, trade_allowed=True)

    def account_info(self):
        return NS(equity=self.equity, balance=self.equity, margin=0.0, margin_free=self.equity,
                  margin_so_mode=0, margin_so_so=50.0, login=1, server="Fake", currency="USD",
                  leverage=100, trade_allowed=True, trade_expert=True)

    def symbol_info(self, symbol):
        return NS(name=symbol, filling_mode=2, volume_min=0.01, volume_max=100.0, volume_step=0.01,
                  point=0.00001, digits=5, trade_stops_level=0, trade_freeze_level=0,
                  trade_tick_value=1.0, trade_tick_size=0.00001, trade_contract_size=100_000)

    def symbol_info_tick(self, symbol):
        return NS(bid=self.bid, ask=self.ask, time_msc=0, last=self.bid)

    def positions_get(self, *a, **k):
        return list(self.positions)

    def orders_get(self, *a, **k):
        return list(self.orders)

    def history_deals_get(self, *a, **k):
        return list(self.deals)

    def order_calc_margin(self, *a, **k):
        return 100.0

    def last_error(self):
        return (1, "Success")

    # --- the one call that matters ---------------------------------------
    def order_send(self, request):
        self.sent.append(dict(request))
        # Honest REMOVE: dropping the pending order makes remaining snapshots truthful.
        if request.get("action") == self.TRADE_ACTION_REMOVE:
            ticket = request.get("order")
            self.orders = [o for o in self.orders if getattr(o, "ticket", None) != ticket]
            return NS(retcode=self.TRADE_RETCODE_DONE, order=ticket, deal=0,
                      volume=0.0, price=0.0, comment="fake-cancel", request=request)
        # Honest SLTP: update position SL/TP without dropping position
        if request.get("action") == self.TRADE_ACTION_SLTP:
            ticket = request.get("position")
            for p in self.positions:
                if getattr(p, "ticket", None) == ticket:
                    p.sl = request.get("sl")
                    p.tp = request.get("tp")
            return NS(retcode=self.TRADE_RETCODE_DONE, order=ticket, deal=0,
                      volume=0.0, price=0.0, comment="fake-sltp", request=request)
        # Honest DEAL close: dropping/reducing the position makes remaining snapshots truthful.
        if "position" in request and request.get("action") == self.TRADE_ACTION_DEAL:
            ticket = request.get("position")
            close_vol = float(request.get("volume", 0.0) or 0.0)
            remaining_positions = []
            for p in self.positions:
                if getattr(p, "ticket", None) == ticket:
                    p_vol = float(getattr(p, "volume", 0.0) or 0.0)
                    if close_vol >= p_vol or close_vol <= 0:
                        continue  # fully closed
                    else:
                        p.volume = round(p_vol - close_vol, 2)
                        remaining_positions.append(p)
                else:
                    remaining_positions.append(p)
            self.positions = remaining_positions
        return NS(retcode=self.TRADE_RETCODE_DONE, order=len(self.sent), deal=0,
                  volume=request.get("volume"), price=request.get("price"), comment="fake", request=request)


def patch_everywhere(monkeypatch, path_suffix: str, name: str, value) -> int:
    """Set ``name`` on every loaded copy of a module (they may be double-loaded)."""
    suffixes = (path_suffix, path_suffix.replace("/", "\\"))
    copies = [m for m in list(sys.modules.values())
              if (getattr(m, "__file__", "") or "").endswith(suffixes)]
    for module in copies:
        monkeypatch.setattr(module, name, value, raising=False)
    return len(copies)
