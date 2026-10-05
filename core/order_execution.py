"""Order routing, execution, emergency exit, and paper broker."""
from __future__ import annotations

import logging
import math
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np

log = logging.getLogger(__name__)
logger = log

# ── Locked broker reads (Phase 4 PH4-N1) ────────────────────────────────────
# Raw mt5.* reads bypass the gateway's MT5 lock and race order_send's
# last_error attribution. All reads below go through core.broker_reads.
from core.broker_reads import (
    broker_orders_get,
    broker_positions_get,
    broker_symbol_info,
    broker_symbol_info_tick,
)


# ── Single-door gateway dispatch (Phase 2 One Door) ──────────────────────────
# ONLY core/order_gateway.py may call mt5.order_send (runtime guard +
# AST test enforce this). All live paths below delegate to the default
# OrderGateway; paper paths stay on the in-memory PaperBroker.
def _live_gateway():
    from core.order_gateway import get_default_gateway
    return get_default_gateway()


def _gateway_intent(*, symbol, side, volume, price, sl, tp, risk_amount,
                    setup_key="", order_type="MARKET", magic=0, comment="",
                    validation_result=None, intent_key=None, expires_at=None, account_equity=None):
    from core.order_gateway import OrderIntent
    import uuid
    return OrderIntent(
        intent_key=intent_key or f"{setup_key or symbol}:{magic}:{uuid.uuid4().hex[:8]}",
        symbol=symbol, side=str(side).upper(), volume=float(volume), price=float(price),
        sl=sl, tp=tp, risk_amount=float(risk_amount or 0.0),
        setup_key=setup_key or "", order_type=order_type,
        magic=int(magic or 0), comment=comment or "order_execution",
        validation_result=validation_result, expires_at=expires_at,
        account_equity=account_equity,
    )


def _require_order_write(token, what: str) -> None:
    """Live entries need an unexpired ORDER_WRITE token for the broker scope (PH2-N1)."""
    from core.harness.sandbox import PermissionLevel
    if token is None or not hasattr(token, "can_access") or not token.can_access(PermissionLevel.ORDER_WRITE, scope="broker"):
        raise PermissionError(
            f"Capability authorization failed: {what} requires an ORDER_WRITE token "
            "issued by the harness sandbox."
        )


def _paper_gateway(paper):
    """Gateway for a paper entry: the shared one when it holds this book, else a
    private paper gateway on the same journal (so the reservation ledger is shared).
    Raises when no gateway is configured: no evidence-backed entry without a ledger."""
    from core.order_gateway import OrderGateway, get_default_gateway
    shared = get_default_gateway()
    if shared.is_paper and shared.adapter.paper is paper:
        return shared
    private = OrderGateway(shared.store, mode="PAPER", arbiter=shared.arbiter,
                           max_evidence_age_s=shared.max_evidence_age_s)
    private.bind_paper_broker(paper)
    return private


def _paper_entry_via_gateway(paper, *, symbol, side, order_type, price, volume, sl, tp, comment, magic,
                             validation_result, setup_key, risk_amount, intent_key,
                             expires_at=None, account_equity=None) -> Dict[str, Any]:
    """Paper entries with validation evidence take the same door as live ones
    (reservation, intent lifecycle, evidence checks; review D2)."""
    if not risk_amount or float(risk_amount) <= 0:
        raise RuntimeError("paper entry with validation evidence requires risk_amount for reservation")
    gw = _paper_gateway(paper)
    intent = _gateway_intent(
        symbol=symbol, side=side, volume=volume, price=price, sl=sl, tp=tp, risk_amount=risk_amount,
        setup_key=setup_key, order_type=order_type, magic=magic, comment=comment,
        validation_result=validation_result, intent_key=intent_key,
        expires_at=expires_at.isoformat() if expires_at else None, account_equity=account_equity,
    )
    request = {"symbol": symbol, "volume": float(volume), "price": float(price), "sl": sl or 0.0,
               "tp": tp or 0.0, "magic": int(magic or 0), "comment": comment}
    if str(order_type).upper() == "MARKET":
        request["type"] = 0 if str(side).upper() == "BUY" else 1
    else:
        request.update({"otype": order_type, "expires_at": expires_at})
    res = gw.submit_entry(intent, mt5_request=request)
    if not res.success:
        gw.abandon_entry(intent.intent_key, str(res.error))
        raise RuntimeError(f"Paper entry rejected by gateway: {res.error}")
    return dict(res.details)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    from core.execution_planning import validate_emergency_exit_request
except ImportError:
    from core.execution_planning import validate_emergency_exit_request


def _get_mt5():
    mt5_mod = sys.modules.get("mt5_trader")
    if mt5_mod is not None and hasattr(mt5_mod, "mt5"):
        return mt5_mod.mt5
    return mt5


class PaperBroker:
    """In-memory paper broker for simulation mode.

    When a journal_callback is set, every close_position() call will
    automatically persist the closed trade to the TradingStore journal,
    enabling research gates and reconciliation to consume paper trade data.
    """

    def __init__(self, journal_callback=None):
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.positions: Dict[int, Dict[str, Any]] = {}
        self.history: List[Dict[str, Any]] = []
        self.next_ticket = 100000
        self._journal_callback = journal_callback

    def _ticket(self) -> int:
        t = self.next_ticket
        self.next_ticket += 1
        return t

    def market_order(self, symbol: str, side: str, volume: float, price: float,
                     sl: Optional[float], tp: Optional[float], comment: str,
                     magic: int = 0) -> Dict[str, Any]:
        if not math.isfinite(float(price)) or float(price) <= 0 or volume <= 0:
            return {"retcode": 10013, "order": None,
                    "error": "paper quote/volume unavailable; no synthetic position created"}
        ticket = self._ticket()
        self.positions[ticket] = {
            "symbol": symbol, "side": side, "volume": volume, "price_open": price,
            "sl": sl, "tp": tp, "comment": comment, "magic": int(magic), "profit": 0.0,
        }
        return {"retcode": 10009, "order": ticket}

    def pending_order(self, symbol: str, otype: str, price: float, volume: float,
                      sl: Optional[float], tp: Optional[float], comment: str,
                      magic: int = 0, expires_at: Optional[datetime] = None) -> Dict[str, Any]:
        if otype not in {"buy_limit", "sell_limit", "buy_stop", "sell_stop"}:
            return {"retcode": 10013, "order": None, "error": "invalid paper pending order type"}
        if not symbol or not math.isfinite(float(price)) or float(price) <= 0 or volume <= 0:
            return {"retcode": 10013, "order": None,
                    "error": "paper pending order requires finite positive symbol/price/volume"}
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            return {"retcode": 10013, "order": None, "error": "paper pending order is already expired"}
        ticket = self._ticket()
        self.orders[ticket] = {
            "symbol": symbol, "type": otype, "price": price, "volume": volume,
            "sl": sl, "tp": tp, "comment": comment, "magic": magic,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        return {"retcode": 10009, "order": ticket, "status": "PENDING", "requested_price": price,
                "requested_volume": volume, "comment": "paper pending order placed"}

    def modify_position_sltp(self, symbol: Optional[str], sl: Optional[float], tp: Optional[float],
                             magic: int = 0, ticket: Optional[int] = None) -> int:
        if isinstance(magic, bool) or not isinstance(magic, int) or magic <= 0:
            return 0
        count = 0
        for pos_ticket, pos in list(self.positions.items()):
            if ticket is not None and int(pos_ticket) != int(ticket):
                continue
            if (symbol is None or pos["symbol"] == symbol) and int(pos.get("magic", 0) or 0) == magic:
                if sl is not None:
                    pos["sl"] = sl
                if tp is not None:
                    pos["tp"] = tp
                count += 1
        return count

    def cancel_order(self, order_id: int) -> bool:
        return self.orders.pop(int(order_id), None) is not None

    def close_position(self, ticket: int, price: float, volume: Optional[float] = None,
                       magic: int = 0) -> Dict[str, Any]:
        position = self.positions.get(int(ticket))
        if not position:
            return {"retcode": 10036, "order": None, "error": "paper position not found"}
        try:
            numeric_price = float(price)
            close_volume = float(position["volume"] if volume is None else volume)
        except (TypeError, ValueError, OverflowError):
            return {"retcode": 10013, "order": None, "error": "invalid emergency exit price/volume"}
        if not math.isfinite(numeric_price) or numeric_price <= 0 or not math.isfinite(close_volume) or close_volume <= 0:
            return {"retcode": 10013, "order": None, "error": "invalid emergency exit price/volume"}
        if int(position.get("magic", 0) or 0) != int(magic) or int(magic) <= 0:
            return {"retcode": 10013, "order": None, "error": "paper position identity/magic ownership mismatch"}
        if close_volume > float(position["volume"]) + 1e-12:
            return {"retcode": 10013, "order": None, "error": "emergency exit volume exceeds position"}
        sign = 1.0 if str(position.get("side", "")).lower() == "buy" else -1.0
        position["profit"] = (numeric_price - float(position["price_open"])) * close_volume * sign
        if close_volume >= float(position["volume"]) - 1e-12:
            self.positions.pop(int(ticket), None)
            status = "CLOSED"
        else:
            position["volume"] = float(position["volume"]) - close_volume
            status = "PARTIAL"
        deal_info = {
            "ticket": int(ticket),
            "symbol": position.get("symbol"),
            "side": position.get("side"),
            "volume": close_volume,
            "price_open": float(position.get("price_open", 0.0)),
            "price_close": numeric_price,
            "profit": float(position.get("profit", 0.0)),
            "magic": int(position.get("magic", 0) or 0),
            "status": status,
            "close_time": time.time(),
        }
        self.history.append(deal_info)
        if len(self.history) > 2000:
            self.history = self.history[-1000:]

        # ── Journal Bridge: persist paper close to SQLite ────────────────────
        if self._journal_callback is not None:
            try:
                self._journal_callback(deal_info)
            except Exception as _jb_exc:
                log.debug("PaperBroker journal callback failed: %s", _jb_exc)

        return {"retcode": 10009, "order": int(ticket), "status": status,
                "filled_price": numeric_price, "filled_volume": close_volume,
                "profit": position.get("profit", 0.0)}


def _symbol_tick(symbol: str) -> Any:
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        raise RuntimeError("MetaTrader5 module unavailable")
    info, _info_err = broker_symbol_info(symbol, mt5_inst=mt5_inst)
    if not info or not getattr(info, "visible", True):
        mt5_inst.symbol_select(symbol, True)
    tick, _tick_err = broker_symbol_info_tick(symbol, mt5_inst=mt5_inst)
    if not tick:
        raise RuntimeError(f"Failed to get tick for {symbol}")
    return tick


_SPREAD_HISTORY: Dict[str, List[float]] = {}


def _get_rolling_spread_avg_points(symbol: str, current_points: float, point: float, max_window: int = 20) -> float:
    """Calculate rolling spread average in points (1a-F5, TL-T2)."""
    try:
        from core.order_gateway import get_default_gateway
        gw = get_default_gateway()
        store = getattr(gw, "store", None)
        if store is not None and hasattr(store, "get_state"):
            ms = store.get_state(f"microstructure:{symbol}", {}) or {}
            hist = ms.get("spread_history", [])
            if hist:
                return (sum(hist) / len(hist)) / point if point > 0 else (sum(hist) / len(hist))
    except Exception:
        pass
    h = _SPREAD_HISTORY.setdefault(symbol, [])
    if not h:
        baseline_points = None
        try:
            mt5_inst = _get_mt5()
            if mt5_inst:
                info, _ = broker_symbol_info(symbol, mt5_inst=mt5_inst)
                if info and getattr(info, "spread", None) is not None:
                    s_val = float(info.spread)
                    if s_val > 0:
                        baseline_points = s_val
        except Exception:
            pass
        h.append(current_points)
        return baseline_points if baseline_points is not None else current_points
    avg = sum(h) / len(h)
    h.append(current_points)
    if len(h) > max_window:
        _SPREAD_HISTORY[symbol] = h[-max_window:]
    return avg


def _broker_market_filling_mode(symbol: str) -> int:
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        raise RuntimeError("MetaTrader5 module unavailable")
    info, _info_err = broker_symbol_info(symbol, mt5_inst=mt5_inst)
    if not info:
        raise RuntimeError(f"Broker symbol specification unavailable for {symbol}")
    try:
        flags = int(getattr(info, "filling_mode", -1))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Malformed broker filling mode for {symbol}") from exc
    if flags & 2:
        return int(getattr(mt5_inst, "ORDER_FILLING_IOC", 1))
    if flags & 1:
        return int(getattr(mt5_inst, "ORDER_FILLING_FOK", 0))
    raise RuntimeError(f"Broker does not advertise a supported market filling mode for {symbol}")


def validate_order_params(side: str, price: float, sl: Optional[float], tp: Optional[float], atr: Optional[float] = None) -> None:
    if not isinstance(side, str) or side.lower() not in ("buy", "sell"):
        raise ValueError(f"Invalid side '{side}': must be 'buy' or 'sell'")

    if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError(f"Invalid price {price}: must be a finite positive number")

    if sl is None or not isinstance(sl, (int, float)) or not math.isfinite(sl) or sl <= 0:
        raise ValueError(f"Stop-loss is mandatory and must be a finite positive number: {sl}")

    if tp is not None:
        if not isinstance(tp, (int, float)) or not math.isfinite(tp) or tp <= 0:
            raise ValueError(f"Take-profit must be a finite positive number if provided: {tp}")

    if atr is not None:
        if not isinstance(atr, (int, float)) or not math.isfinite(atr) or atr <= 0:
            raise ValueError(f"ATR must be a finite positive number if provided: {atr}")

    side_lower = side.lower()
    if side_lower == "buy":
        if sl >= price:
            raise ValueError(f"BUY Order Invalid: SL ({sl}) must be BELOW Price ({price})")
        if tp and tp <= price:
            raise ValueError(f"BUY Order Invalid: TP ({tp}) must be ABOVE Price ({price})")
    elif side_lower == "sell":
        if sl <= price:
            raise ValueError(f"SELL Order Invalid: SL ({sl}) must be ABOVE Price ({price})")
        if tp and tp >= price:
            raise ValueError(f"SELL Order Invalid: TP ({tp}) must be BELOW Price ({price})")

    dist = abs(price - sl)
    dist_pct = dist / price
    if dist_pct > 0.05:
        raise ValueError(f"SL Distance {dist_pct:.1%} exceeds max safety (5%). Trade Rejected.")

    if atr and atr > 0:
        dist_atr = dist / atr
        if dist_atr < 0.5:
            raise ValueError(f"SL Distance {dist_atr:.2f} ATR is too tight (Min 0.5 ATR). Rejected.")

    if tp and tp > 0:
        risk_dist = abs(price - sl)
        reward_dist = abs(tp - price)
        if risk_dist > 0:
            rr = reward_dist / risk_dist
            if rr < 2.0:
                raise ValueError(f"Risk:Reward {rr:.2f} is below minimum 2.0. Trade Rejected.")


def validate_pending_entry_geometry(otype: str, entry: float, bid: float, ask: float) -> None:
    if otype not in {"buy_limit", "sell_limit", "buy_stop", "sell_stop"}:
        raise ValueError("invalid pending order type")
    try:
        numeric_entry = float(entry)
        numeric_bid = float(bid)
        numeric_ask = float(ask)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("pending entry geometry requires numeric quote values") from exc
    if any(not math.isfinite(value) or value <= 0 for value in (numeric_entry, numeric_bid, numeric_ask)):
        raise ValueError("pending entry geometry requires finite positive quote values")
    if numeric_bid > numeric_ask:
        raise ValueError("pending entry geometry received inverted bid/ask")
    valid = {
        "buy_limit": numeric_entry < numeric_ask,
        "buy_stop": numeric_entry > numeric_ask,
        "sell_limit": numeric_entry > numeric_bid,
        "sell_stop": numeric_entry < numeric_bid,
    }[otype]
    if not valid:
        raise ValueError(f"{otype} entry is marketable or crossed by the current quote")


def calculate_dynamic_slippage_deviation(
    symbol: str,
    urgency_score: float = 0.5,
    spread_points: Optional[float] = None,
    base_points: int = 20,
) -> int:
    """Calibrates broker deviation in points dynamically.

    Widens deviation under high urgency (IOC taker) to prevent requotes,
    tightens deviation under low urgency (maker fills) to protect against slippage.
    Scales with observed spread points when available.
    """
    try:
        u = float(urgency_score) if math.isfinite(float(urgency_score)) else 0.5
    except (TypeError, ValueError):
        u = 0.5

    if spread_points is not None:
        try:
            sp = float(spread_points)
            if math.isfinite(sp) and sp > 0:
                if u >= 0.70:
                    dev = int(round(sp * 1.5))
                elif u <= 0.30:
                    dev = int(round(sp * 0.8))
                else:
                    dev = int(round(sp * 1.0))
                return max(5, min(150, dev))
        except (TypeError, ValueError):
            pass

    if u >= 0.70:
        dev = int(round(base_points * 1.5))
    elif u <= 0.30:
        dev = max(5, int(round(base_points * 0.7)))
    else:
        dev = base_points
    return max(5, min(150, dev))


def place_market_order(symbol: str, side: str, volume: float,
                       sl: Optional[float], tp: Optional[float], comment: str, paper: Optional[PaperBroker], atr: Optional[float] = None,
                       magic: int = 0, validation_result: Optional[Any] = None, setup_key: str = "",
                       risk_amount: float = 0.0, intent_key: Optional[str] = None) -> Dict[str, Any]:
    # Evidence kwargs are accepted for signature parity with the other entry
    # points (MarketDataProvider forwards them); the live path stays disabled.
    if paper is None:
        raise RuntimeError(
            "direct market entries are disabled; use a validated pending plan "
            "or emergency_market_exit for an owned position"
        )
    if paper:
        # Get real tick price for paper fills (not NaN which PaperBroker rejects)
        try:
            tick = _symbol_tick(symbol)
            price = (tick.ask if side.lower() == "buy" else tick.bid) if tick and tick.bid and tick.ask else 1.0
        except Exception:
            price = 1.0  # absolute fallback
        return paper.market_order(symbol, side, volume, price, sl, tp, comment, magic=magic)

    # Live path is intentionally disabled (single-door rule): validated
    # entries must go through place_pending_order → OrderGateway.
    raise RuntimeError(
        "direct market entries are disabled; use a validated pending plan "
        "or emergency_market_exit for an owned position"
    )


def place_urgency_routed_order(symbol: str, side: str, volume: float,
                                sl: Optional[float], tp: Optional[float], comment: str,
                                paper: Optional[PaperBroker], atr: Optional[float] = None,
                                magic: int = 0, validation_result: Optional[Any] = None,
                                setup_key: str = "", risk_amount: float = 0.0,
                                intent_key: Optional[str] = None,
                                account_equity: Optional[float] = None,
                                token: Optional[Any] = None) -> Dict[str, Any]:
    """Place order with UrgencySOR pre-execution routing.

    Routes to:
      - MARKET_IOC (high urgency >= 0.70): aggressive taker, immediate fill
      - LIMIT_MAKER (low urgency <= 0.30): passive limit at micro-price
      - MARKET (neutral): standard market order
    """
    if paper is not None:
        # Pre-execution UrgencySOR computation for paper mode
        _urgency = 0.5
        _routing = None
        try:
            from core.urgency_sor import UrgencySmartOrderRouter, calculate_alpha_decay_urgency_score
            _sor = UrgencySmartOrderRouter()
            try:
                tick = _symbol_tick(symbol)
                _bid, _ask = float(getattr(tick, "bid", 1.0) or 1.0), float(getattr(tick, "ask", 1.0) or 1.0)
            except Exception:
                _bid, _ask = 1.0, 1.0
            _mid = (_bid + _ask) / 2.0
            _spread_pct = abs(_ask - _bid) / _mid if _mid else 0.001
            _urgency = calculate_alpha_decay_urgency_score(
                instantaneous_volatility=_spread_pct,
                order_flow_imbalance=1.0,
                order_book_depth=100.0,
            )
            _routing = _sor.route_execution_order(
                symbol=symbol, side=side, volume=volume,
                price=_mid, urgency_score=_urgency,
                best_bid=_bid, best_ask=_ask,
            )
        except Exception as _sor_exc:
            log.warning("SOR routing failed for %s: %s; falling back to MARKET order", symbol, _sor_exc)
            _routing = None

        _order_type = _routing["order_type"] if _routing else "MARKET"
        _target_price = _routing["target_price"] if _routing else (1.0)

        if validation_result is not None:
            if _order_type == "LIMIT_MAKER":
                _p_otype, _p_price, _p_comment = ("buy_limit" if side.lower() == "buy" else "sell_limit"), round(_target_price, 5), f"SOR_MKR:{comment}"
            else:
                _p_otype, _p_comment = "MARKET", comment
                _q = _symbol_tick(symbol)   # raises without a quote: no synthetic 1.0 fills
                _p_price = float(_q.ask if side.lower() == "buy" else _q.bid)
            return _paper_entry_via_gateway(
                paper, symbol=symbol, side=side, order_type=_p_otype, price=_p_price, volume=volume,
                sl=sl, tp=tp, comment=_p_comment, magic=magic, validation_result=validation_result,
                setup_key=setup_key, risk_amount=risk_amount, intent_key=intent_key,
                account_equity=account_equity,
            )
        if _order_type == "LIMIT_MAKER":
            otype = "buy_limit" if side.lower() == "buy" else "sell_limit"
            return paper.pending_order(
                symbol=symbol,
                otype=otype,
                price=round(_target_price, 5),
                volume=volume,
                sl=sl,
                tp=tp,
                comment=f"SOR_MKR:{comment}",
                magic=magic,
            )
        return place_market_order(symbol, side, volume, sl, tp, comment, paper, atr, magic)

    _require_order_write(token, "live urgency order")
    mt5_inst = _get_mt5()
    tick = _symbol_tick(symbol)
    if tick is None or not tick.bid or not tick.ask:
        raise RuntimeError(f"No tick data for {symbol}")
    if validation_result is None or not getattr(validation_result, "approved", False):
        raise RuntimeError(
            "live urgency order requires validation evidence (single-door rule): "
            "pass validation_result/setup_key/risk_amount from DeterministicValidator"
        )
    if not risk_amount or float(risk_amount) <= 0:
        raise RuntimeError("live urgency order requires explicit risk_amount for reservation")

    # --- Pre-execution UrgencySOR computation ---
    _urgency = 0.5  # default neutral
    _routing = None
    try:
        from core.urgency_sor import UrgencySmartOrderRouter, calculate_alpha_decay_urgency_score
        _sor = UrgencySmartOrderRouter()
        _mid = (tick.bid + tick.ask) / 2.0
        _spread_pct = abs(tick.ask - tick.bid) / _mid if _mid else 0.001
        _urgency = calculate_alpha_decay_urgency_score(
            instantaneous_volatility=_spread_pct,
            order_flow_imbalance=float(getattr(tick, "volume", 1) or 1),
            order_book_depth=100.0,
        )
        _routing = _sor.route_execution_order(
            symbol=symbol, side=side, volume=volume,
            price=_mid, urgency_score=_urgency,
            best_bid=tick.bid, best_ask=tick.ask,
        )
    except Exception as _sor_exc:
        logging.getLogger("OrderExec").debug("UrgencySOR pre-route failed: %s", _sor_exc)

    _order_type = _routing["order_type"] if _routing else "MARKET"
    _target_price = _routing["target_price"] if _routing else (tick.ask if side.lower() == "buy" else tick.bid)

    validate_order_params(side, _target_price, sl, tp, atr=atr)

    tick_point = getattr(tick, "point", None) or 0.00001
    spread_pts = abs(tick.ask - tick.bid) / tick_point if tick.bid and tick.ask and tick_point else None
    deviation = calculate_dynamic_slippage_deviation(
        symbol=symbol,
        urgency_score=_urgency,
        spread_points=spread_pts,
        base_points=30 if _order_type == "MARKET_IOC" else 20,
    )

    if _order_type == "MARKET_IOC":
        # High urgency → aggressive IOC fill
        request = {
            "action": mt5_inst.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5_inst.ORDER_TYPE_BUY if side.lower() == "buy" else mt5_inst.ORDER_TYPE_SELL,
            "price": _target_price,
            "sl": sl or 0.0, "tp": tp or 0.0,
            "deviation": deviation,
            "type_filling": getattr(mt5_inst, "ORDER_FILLING_IOC",
                                    getattr(mt5_inst, "ORDER_FILLING_RETURN", 0)),
            "type_time": mt5_inst.ORDER_TIME_GTC,
            "magic": magic,
            "comment": f"SOR_IOC:{comment}",
        }
    elif _order_type == "LIMIT_MAKER":
        # Low urgency → passive limit at micro-price
        _limit_type = (mt5_inst.ORDER_TYPE_BUY_LIMIT if side.lower() == "buy"
                       else mt5_inst.ORDER_TYPE_SELL_LIMIT)
        request = {
            "action": mt5_inst.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": _limit_type,
            "price": round(_target_price, 5),
            "sl": sl or 0.0, "tp": tp or 0.0,
            "type_filling": _broker_market_filling_mode(symbol),
            "type_time": mt5_inst.ORDER_TIME_GTC,
            "magic": magic,
            "comment": f"SOR_MKR:{comment}",
        }
    else:
        # Neutral → standard market order
        request = {
            "action": mt5_inst.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5_inst.ORDER_TYPE_BUY if side.lower() == "buy" else mt5_inst.ORDER_TYPE_SELL,
            "price": _target_price,
            "sl": sl or 0.0, "tp": tp or 0.0,
            "deviation": deviation,
            "type_filling": _broker_market_filling_mode(symbol),
            "type_time": mt5_inst.ORDER_TIME_GTC,
            "magic": magic,
            "comment": f"SOR_STD:{comment}",
        }

    # Single-door dispatch: validated request goes through OrderGateway
    # (reservation → margin → journal → send). The pre-built SOR request is
    # passed through so routing/deviation semantics are preserved.
    _gateway_otype = "MARKET"
    if _order_type == "LIMIT_MAKER":
        _gateway_otype = "buy_limit" if side.lower() == "buy" else "sell_limit"
    _intent = _gateway_intent(
        symbol=symbol, side=side, volume=volume, price=_target_price,
        sl=sl, tp=tp, risk_amount=risk_amount, setup_key=setup_key,
        order_type=_gateway_otype, magic=magic, comment=f"SOR:{comment}",
        validation_result=validation_result, intent_key=intent_key, account_equity=account_equity,
    )
    _gw = _live_gateway()
    _gw_res = _gw.submit_entry(_intent, mt5_request=request, mt5_client=mt5_inst)
    if not _gw_res.success:
        _gw.abandon_entry(_intent.intent_key, str(_gw_res.error))
        raise RuntimeError(f"Urgency order rejected by gateway: {_gw_res.error}")
    res = _gw_res.details.get("result")
    if res is None:
        # Paper-mode gateway fills resolve without a raw MT5 result object.
        _ticket = _gw_res.ticket
        return {"retcode": 10009, "order": _ticket,
                "urgency_score": round(_urgency, 4),
                "execution_routing": _routing}

    # κ Audit
    try:
        from core.compliance_audit import get_audit, EventType
        get_audit().log(
            EventType.ORDER_FILLED if res.retcode == mt5_inst.TRADE_RETCODE_DONE else EventType.ORDER_REJECTED,
            symbol=symbol, agent="ε",
            payload={"type": "urgency_routed", "order_type": _order_type,
                     "urgency_score": round(_urgency, 4),
                     "side": side, "volume": volume, "price": _target_price,
                     "sl": sl, "tp": tp, "retcode": res.retcode},
        )
    except Exception as exc:
        logger.warning("Compliance audit log failed on urgency order: %s", exc)

    if res.retcode != mt5_inst.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Urgency order failed: retcode={res.retcode}, comment={res.comment}")

    return {"retcode": res.retcode, "order": res.order,
            "urgency_score": round(_urgency, 4),
            "execution_routing": _routing}


def emergency_market_exit(*, symbol: str, position_ticket: int, side: str, volume: float,
                          magic: int, reason: str, paper: Optional[PaperBroker] = None,
                          price: Optional[float] = None) -> Dict[str, Any]:
    check = validate_emergency_exit_request(position_ticket=position_ticket, symbol=symbol,
                                            side=side, volume=volume, magic=magic, reason=reason)
    if not check["valid"]:
        raise ValueError("invalid emergency exit: " + "; ".join(check["reasons"]))
    if paper is not None:
        position = paper.positions.get(int(position_ticket))
        if not position or str(position.get("symbol", "")) != check["symbol"] or str(position.get("side", "")).upper() != check["side"]:
            raise RuntimeError("paper emergency exit position identity mismatch")
        exit_price = float(price) if price is not None else float(position.get("price_open", 0.0))
        result = paper.close_position(int(position_ticket), exit_price, check["volume"], magic=check["magic"])
        if int(result.get("retcode", 0) or 0) != 10009:
            raise RuntimeError(str(result.get("error", "paper emergency exit failed")))
        return result

    mt5_inst = _get_mt5()
    if mt5_inst is None:
        raise RuntimeError("MetaTrader5 module unavailable for emergency exit")
    positions, _pos_err = broker_positions_get(ticket=int(position_ticket), mt5_inst=mt5_inst)
    positions = positions or []
    if len(positions) != 1:
        raise RuntimeError("emergency exit requires exactly one broker position")
    position = positions[0]
    broker_symbol = str(getattr(position, "symbol", "") or "")
    broker_magic = int(getattr(position, "magic", 0) or 0)
    position_type = int(getattr(position, "type", -1) or -1)
    buy_type = int(getattr(mt5_inst, "POSITION_TYPE_BUY", 0))
    broker_side = "BUY" if position_type == buy_type else "SELL"
    broker_volume = float(getattr(position, "volume", 0) or 0)
    if broker_symbol != check["symbol"] or broker_side != check["side"] or broker_magic != check["magic"]:
        raise RuntimeError("broker emergency exit position identity/magic mismatch")
    if check["volume"] > broker_volume + 1e-12:
        raise RuntimeError("emergency exit volume exceeds broker position")
    tick = _symbol_tick(check["symbol"])
    exit_price = float(price) if price is not None else float(getattr(tick, "bid" if broker_side == "BUY" else "ask"))
    if not math.isfinite(exit_price) or exit_price <= 0:
        raise RuntimeError("emergency exit quote unavailable")
    # Single-door dispatch: risk-reducing exits go through OrderGateway
    # (allowed under kill switch, journaled, under the MT5 lock).
    _gw = _live_gateway()
    _gw_res = _gw.submit_exit(
        int(position_ticket), check["symbol"], volume=check["volume"],
        comment=f"EMERGENCY_EXIT:{check['reason'][:180]}",
        mt5_client=mt5_inst, owned_magics={check["magic"]},
    )
    if not _gw_res.success:
        raise RuntimeError(f"emergency exit rejected by gateway: {_gw_res.error}")
    result = _gw_res.details.get("result")
    if result is None:
        # Gateway resolved without a raw MT5 result (should not happen live).
        return {"retcode": 10009, "order": _gw_res.ticket,
                "position": int(position_ticket), "filled_price": exit_price,
                "filled_volume": check["volume"], "reason": check["reason"]}
    accepted = {getattr(mt5_inst, "TRADE_RETCODE_DONE", 10009), getattr(mt5_inst, "TRADE_RETCODE_DONE_PARTIAL", 10010)}
    if int(getattr(result, "retcode", 0) or 0) not in accepted:
        raise RuntimeError(f"emergency exit failed: retcode={getattr(result, 'retcode', None)}, comment={getattr(result, 'comment', '')}")
    # κ Audit: log emergency exit
    try:
        from core.compliance_audit import get_audit, EventType
        get_audit().log(
            EventType.ORDER_FILLED,
            symbol=symbol,
            agent="ε",
            payload={
                "type": "emergency_exit", "side": side, "volume": check["volume"],
                "position_ticket": position_ticket, "exit_price": exit_price,
                "reason": check["reason"], "retcode": int(result.retcode),
            },
        )
    except Exception as exc:
        logger.warning("Compliance audit log failed on emergency exit: %s", exc)
    return {"retcode": int(result.retcode), "order": getattr(result, "order", None),
            "position": int(position_ticket), "filled_price": exit_price,
            "filled_volume": check["volume"], "reason": check["reason"]}


def place_pending_order(symbol: str, otype: str, price: float, volume: float,
                        sl: Optional[float], tp: Optional[float], comment: str, paper: Optional[PaperBroker],
                        magic: int = 0, expires_at: Optional[datetime] = None,
                        token: Optional[Any] = None, validation_result: Optional[Any] = None,
                        setup_key: str = "", risk_amount: float = 0.0,
                        intent_key: Optional[str] = None,
                        account_equity: Optional[float] = None) -> Dict[str, Any]:
    # --- Surgical sandbox guard: live ORDER_WRITE requires token, paper SIMULATION allowed ---
    if token is not None and hasattr(token, "can_access"):
        from core.harness.sandbox import PermissionLevel
        if not token.can_access(PermissionLevel.ORDER_WRITE, scope="broker"):
            raise PermissionError("Capability authorization failed: Live pending order requires ORDER_WRITE token.")
    elif paper is None:
        # Live broker dispatch with no capability token: refuse. There is no
        # implicit authorization path.
        #
        # The SIMULATION-mode probe that used to live here called
        # core.harness.kernel.get_active_context() and read ctx.mode. Neither
        # exists anywhere in the repository: kernel.py defines no
        # get_active_context, no module-level context singleton, and
        # TradingContext exposes only get/provide/get_state/set_state. The
        # hasattr() guard therefore always evaluated False, the body never ran,
        # and every live pending order reached broker dispatch with zero capability
        # verification despite the "fail-closed" comment.
        #
        # Scoped to the live path only: a paper order returns below, before this
        # gate, and `token=` keeps working unchanged.
        raise PermissionError(
            "Capability authorization failed: live pending order requires an "
            "ORDER_WRITE token issued by the harness sandbox."
        )

    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("pending order requires a symbol")
    if isinstance(magic, bool) or not isinstance(magic, int) or magic <= 0:
        raise ValueError("pending order requires a positive bot magic number")
    if otype not in {"buy_limit", "sell_limit", "buy_stop", "sell_stop"}:
        raise ValueError("otype must be one of buy_limit/sell_limit/buy_stop/sell_stop")
    try:
        numeric_price = float(price)
        numeric_volume = float(volume)
        numeric_sl = float(sl) if sl is not None else math.nan
        numeric_tp = float(tp) if tp is not None else math.nan
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("pending order prices and volume must be numeric") from exc
    if (not math.isfinite(numeric_price) or numeric_price <= 0 or
            not math.isfinite(numeric_volume) or numeric_volume <= 0 or
            not math.isfinite(numeric_sl) or numeric_sl <= 0 or
            not math.isfinite(numeric_tp) or numeric_tp <= 0):
        raise ValueError("pending order requires finite positive price, volume, stop, and target")
    if expires_at is None or expires_at.tzinfo is None:
        raise ValueError("pending order requires a timezone-aware expiry")
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("pending order expiry must be in the future")
    side = "buy" if otype.startswith("buy_") else "sell"
    validate_order_params(side, numeric_price, numeric_sl, numeric_tp)
    if paper:
        if validation_result is not None:
            return _paper_entry_via_gateway(
                paper, symbol=symbol, side=side, order_type=otype, price=numeric_price, volume=numeric_volume,
                sl=numeric_sl, tp=numeric_tp, comment=comment, magic=magic, validation_result=validation_result,
                setup_key=setup_key, risk_amount=risk_amount, intent_key=intent_key, expires_at=expires_at,
                account_equity=account_equity,
            )
        # Evidence-less calls (tools, tests) stay on the in-memory simulator.
        return paper.pending_order(symbol, otype, numeric_price, numeric_volume, numeric_sl, numeric_tp,
                                   comment, magic=magic, expires_at=expires_at)

    # Capability security verification for live execution
    if token is not None and hasattr(token, "can_access"):
        from core.harness.sandbox import PermissionLevel
        if not token.can_access(PermissionLevel.ORDER_WRITE, scope="broker"):
            raise PermissionError("Capability authorization failed: Live pending order requires ORDER_WRITE permission token.")

    mt5_inst = _get_mt5()
    if mt5_inst is None:
        raise RuntimeError("MetaTrader5 module unavailable for live pending order")
    # --- Pre-trade SafetySupervisor gate (250ms / 2.5x spread) ---
    try:
        from core.execution_safety_supervisor import get_execution_safety_supervisor
        # The singleton, NOT a fresh LiveExecutionSafetySupervisor(). A
        # throwaway instance carries default state (kill_switch_active=False,
        # daily_peak_equity=0.0, permanent_halt=False), so it can never observe
        # the equity / kill-switch state the singleton accumulates via
        # update_equity() — the gate would approve unconditionally.
        sup = get_execution_safety_supervisor()
        # The tick fetch is a real broker round-trip; it is the only honest
        # latency signal available before dispatch.
        _t0 = time.perf_counter()
        try:
            _tick, _tick_err = broker_symbol_info_tick(symbol, mt5_inst=mt5_inst)
        except Exception:
            _tick = None
        _roundtrip_ms = (time.perf_counter() - _t0) * 1000.0
        if _tick is None:
            raise RuntimeError("LiveExecutionSafetySupervisor: tick unavailable for live order")
        _bid = float(getattr(_tick, "bid", 0.0) or 0.0)
        _ask = float(getattr(_tick, "ask", 0.0) or 0.0)
        if not (_bid > 0 and _ask > 0 and _ask >= _bid):
            raise RuntimeError(f"LiveExecutionSafetySupervisor: invalid live prices bid={_bid}, ask={_ask}")
        info, _info_err = broker_symbol_info(symbol, mt5_inst=mt5_inst) if hasattr(mt5_inst, "symbol_info") else (None, None)
        point = float(getattr(info, "point", 0.00001) or 0.00001) if info else 0.00001
        _current_spread = (_ask - _bid) / point if point > 0 else (_ask - _bid)
        _avg_spread = _get_rolling_spread_avg_points(symbol, _current_spread, point)
        sup_check = sup.validate_pre_trade_safety(
            symbol=symbol,
            requested_volume=numeric_volume,
            current_spread_pips=_current_spread,
            historical_avg_spread_pips=_avg_spread,
            roundtrip_latency_ms=_roundtrip_ms,
        )
        # The gate reports "is_approved"; "allowed" is never a key it returns.
        if not sup_check.get("is_approved", False):
            raise RuntimeError(
                f"SafetySupervisor blocked: {sup_check.get('rejection_reason', 'spread/latency')}"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("LiveExecutionSafetySupervisor evaluation failed: %s; failing closed", exc)
        raise RuntimeError(f"LiveExecutionSafetySupervisor failed closed: {exc}")

    # --- TradingConstraintEngine secondary fence ---
    try:
        from core.trading_constraints import TradingConstraintEngine, ConstraintVerdict
        _cte = TradingConstraintEngine()
        _cte_eval = _cte.evaluate(
            {"action": side, "entry": numeric_price, "stop_loss": numeric_sl,
             "take_profit": numeric_tp, "lots": numeric_volume, "symbol": symbol},
            {},
        )
        if _cte_eval.overall_verdict == ConstraintVerdict.REJECT:
            raise RuntimeError(f"TradingConstraintEngine {_cte_eval.overall_verdict.value}: {_cte_eval.reject_reasons}")
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("TradingConstraintEngine evaluation failed: %s; failing closed", exc)
        raise RuntimeError(f"TradingConstraintEngine failed closed: {exc}")
    # Refresh tick and re-check margin (TOCTOU close — re-assess at send time).
    # 1a-F19 fix: correct assess_from_mt5 kwargs, fail CLOSED (was wrong
    # signature swallowed as "non-fatal", so the re-check never ran).
    live_tick = _symbol_tick(symbol)
    validate_pending_entry_geometry(
        otype, numeric_price, getattr(live_tick, "bid", None), getattr(live_tick, "ask", None)
    )
    try:
        from core.margin_safety import MarginSafetyGate
        gate = MarginSafetyGate()
        # Re-estimate margin at current quote (seconds after initial assess)
        quote_price = float(getattr(live_tick, "ask" if otype.startswith("buy_") else "bid", numeric_price))
        margin_check = gate.assess_from_mt5(
            mt5_module=mt5_inst, symbol=symbol, side=side,
            volume=numeric_volume, price=quote_price, new_risk=float(risk_amount or 0.0),
            driver_buckets=[f"{symbol}:{side}"],
        )
        if not margin_check.approved:
            raise RuntimeError(f"Margin TOCTOU re-check failed: {margin_check.reasons}")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Margin TOCTOU re-check failed closed: {exc}")
    type_map = {
        "buy_limit": mt5_inst.ORDER_TYPE_BUY_LIMIT,
        "sell_limit": mt5_inst.ORDER_TYPE_SELL_LIMIT,
        "buy_stop": mt5_inst.ORDER_TYPE_BUY_STOP,
        "sell_stop": mt5_inst.ORDER_TYPE_SELL_STOP,
    }
    # Dynamic deviation: 20 pipettes for FX, scale for metals/indices via point
    try:
        info, _dev_info_err = broker_symbol_info(symbol, mt5_inst=mt5_inst)
        point = float(getattr(info, "point", 0.00001) or 0.00001)
        # ~15 points for FX majors, ~30 for XAU (point 0.01 vs 0.00001)
        dev = 30 if point >= 0.01 else 20
    except Exception:
        dev = 20
    request = {
        "action": mt5_inst.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": numeric_volume,
        "type": type_map[otype],
        "price": numeric_price,
        "sl": numeric_sl,
        "tp": numeric_tp,
        "deviation": dev,
        "type_time": mt5_inst.ORDER_TIME_SPECIFIED if expires_at else mt5_inst.ORDER_TIME_GTC,
        "expiration": int(expires_at.timestamp()),
        "type_filling": getattr(mt5_inst, "ORDER_FILLING_RETURN", mt5_inst.ORDER_FILLING_FOK),
        "magic": magic,
        "comment": comment,
    }
    # Retry for transient retcodes: REQUOTE 10004, REJECT 10006, TIMEOUT 10012, PRICE_CHANGED 10015
    RETRYABLE = {10004, 10006, 10012, 10015, 10018}
    ACCEPTED = {getattr(mt5_inst, "TRADE_RETCODE_DONE", 10009), getattr(mt5_inst, "TRADE_RETCODE_PLACED", 10008), getattr(mt5_inst, "TRADE_RETCODE_DONE_PARTIAL", 10010)}
    if validation_result is None or not getattr(validation_result, "approved", False):
        raise RuntimeError(
            "live pending order requires validation evidence (single-door rule): "
            "pass validation_result/setup_key/risk_amount from DeterministicValidator"
        )
    if not risk_amount or float(risk_amount) <= 0:
        raise RuntimeError("live pending order requires explicit risk_amount for reservation")
    _intent = _gateway_intent(
        symbol=symbol, side=side, volume=numeric_volume, price=numeric_price,
        sl=numeric_sl, tp=numeric_tp, risk_amount=risk_amount, setup_key=setup_key,
        order_type=otype, magic=magic, comment=comment,
        validation_result=validation_result, intent_key=intent_key,
        expires_at=expires_at.isoformat() if expires_at else None, account_equity=account_equity,
    )
    _gw = _live_gateway()
    last_err = None
    for attempt in range(3):
        _gw_res = _gw.submit_entry(_intent, mt5_request=dict(request), mt5_client=mt5_inst)
        if not _gw_res.success:
            # Retry only broker-transient rejections; gateway policy rejections fail fast.
            last_err = f"gateway rejected: {_gw_res.error}"
            _rc_hint = str(_gw_res.error or "")
            if any(code in _rc_hint for code in ("10004", "10006", "10012", "10015", "10018")) and attempt < 2:
                import time as _t; _t.sleep(0.25 * (attempt + 1))
                try:
                    live_tick = _symbol_tick(symbol)
                except Exception as tick_exc:
                    log.debug("Tick refresh failed during retry: %s", tick_exc)
                continue
            _gw.abandon_entry(_intent.intent_key, str(_gw_res.error))
            raise RuntimeError(f"Pending order rejected by gateway: {_gw_res.error}")
        res = _gw_res.details.get("result")
        if res is None:
            raise RuntimeError(f"gateway send returned no broker result: {_gw_res.error}")
        rc = int(getattr(res, "retcode", -1) or -1)
        if rc in ACCEPTED:
            # Handle DONE_PARTIAL: accepted_volume may be < requested; surface correctly
            acc_vol = getattr(res, "volume", None)
            try:
                acc_vol_f = float(acc_vol) if acc_vol is not None else numeric_volume
            except Exception:
                acc_vol_f = numeric_volume
            status = "PENDING_PARTIAL" if rc == getattr(mt5_inst, "TRADE_RETCODE_DONE_PARTIAL", 10010) else "PENDING"
            # κ Audit: log pending order placed
            try:
                from core.compliance_audit import get_audit, EventType
                get_audit().log(
                    EventType.ORDER_SUBMITTED,
                    symbol=symbol,
                    agent="ε",
                    payload={
                        "type": otype, "price": numeric_price, "volume": numeric_volume,
                        "sl": numeric_sl, "tp": numeric_tp, "retcode": rc,
                        "ticket": int(res.order) if hasattr(res, 'order') else 0,
                        "status": status, "expires_at": str(expires_at) if expires_at else None,
                    },
                )
            except Exception as exc:
                logger.warning("Compliance audit log failed on pending order: %s", exc)
            return {"retcode": rc, "order": res.order, "status": status,
                    "requested_price": numeric_price, "requested_volume": numeric_volume,
                    "accepted_price": getattr(res, "price", numeric_price),
                    "accepted_volume": acc_vol_f, "comment": getattr(res, "comment", ""),
                    "attempt": attempt + 1}
        if rc in RETRYABLE and attempt < 2:
            last_err = f"retcode={rc}, comment={getattr(res, 'comment','')}"
            import time as _t; _t.sleep(0.25 * (attempt + 1))
            continue
        raise RuntimeError(f"Pending order failed: retcode={rc}, comment={getattr(res, 'comment','')}")
    raise RuntimeError(f"Pending order failed after retries: {last_err}")


def modify_order_sltp(symbol: Optional[str], sl: Optional[float], tp: Optional[float],
                      paper: Optional[PaperBroker], magic: int = 0) -> int:
    if isinstance(magic, bool) or not isinstance(magic, int) or magic <= 0:
        return 0
    if paper:
        return paper.modify_position_sltp(symbol, sl, tp, magic=magic)
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        return 0
    positions, _mod_pos_err = broker_positions_get(symbol=symbol, mt5_inst=mt5_inst) if symbol else broker_positions_get(mt5_inst=mt5_inst)
    count = 0
    _gw = _live_gateway()
    for p in positions or []:
        if int(getattr(p, "magic", 0) or 0) != magic:
            continue
        _cur_sl = float(getattr(p, "sl", 0) or 0)
        _side = "BUY" if int(getattr(p, "type", 0) or 0) == int(getattr(mt5_inst, "POSITION_TYPE_BUY", getattr(mt5_inst, "ORDER_TYPE_BUY", 0))) else "SELL"
        _res = _gw.modify_protection(
            int(getattr(p, "ticket", 0) or 0), getattr(p, "symbol", symbol or ""),
            new_sl=sl if sl is not None else _cur_sl,
            new_tp=tp if tp is not None else float(getattr(p, "tp", 0) or 0),
            current_sl=_cur_sl, side=_side, mt5_client=mt5_inst,
        )
        if _res.success:
            count += 1
    return count


def cancel_pending_order(order_id: int, paper: Optional[PaperBroker], magic: int) -> bool:
    if isinstance(magic, bool) or not isinstance(magic, int) or magic <= 0:
        return False
    if paper:
        order = paper.orders.get(int(order_id))
        if not order or int(order.get("magic", 0) or 0) != magic:
            return False
        return paper.cancel_order(order_id)
    mt5_inst = _get_mt5()
    if mt5_inst is None:
        return False
    order, _order_err = broker_orders_get(ticket=order_id, mt5_inst=mt5_inst)
    if not order or len(order) != 1:
        return False
    if int(getattr(order[0], "magic", 0) or 0) != magic:
        return False
    _gw = _live_gateway()
    _res = _gw.cancel_pending(int(order_id), mt5_client=mt5_inst)
    return bool(_res.success)


def calculate_alpha_urgency(
    instantaneous_volatility: float,
    order_flow_imbalance: float,
    depth: float,
    lambda_decay: float = 0.05,
    elapsed_seconds: float = 0.0,
) -> float:
    """Calculate Alpha Decay Urgency score — delegates to canonical urgency_sor."""
    from core.urgency_sor import calculate_alpha_decay_urgency_score
    return calculate_alpha_decay_urgency_score(
        instantaneous_volatility=instantaneous_volatility,
        order_flow_imbalance=order_flow_imbalance,
        order_book_depth=depth,
        elapsed_time_seconds=elapsed_seconds,
        decay_lambda=lambda_decay,
    )


def determine_smart_order_routing(urgency_score: float) -> Dict[str, Any]:
    """Route order based on Alpha Decay Urgency score U.
    
    - U > 0.70: AGGRESSIVE_TAKER (IOC / Market) to capture fast alpha
    - U < 0.30: PASSIVE_MAKER (Limit with Quote Fading) to minimize spread
    - 0.30 <= U <= 0.70: HYBRID_ADAPTIVE
    """
    u = float(min(1.0, max(0.0, urgency_score)))
    if u > 0.70:
        return {
            "urgency_score": round(u, 3),
            "execution_style": "AGGRESSIVE_TAKER",
            "filling_type": "IOC",
            "fade_quote": False,
            "rationale": "High alpha urgency: expected alpha decay exceeds half-spread friction.",
        }
    elif u < 0.30:
        return {
            "urgency_score": round(u, 3),
            "execution_style": "PASSIVE_MAKER",
            "filling_type": "LIMIT_POST_ONLY",
            "fade_quote": True,
            "rationale": "Low alpha urgency: prioritize maker spread capture with toxic flow fading.",
        }
    else:
        return {
            "urgency_score": round(u, 3),
            "execution_style": "HYBRID_ADAPTIVE",
            "filling_type": "IOC_OR_PASSIVE",
            "fade_quote": False,
            "rationale": "Moderate alpha urgency: adaptive routing based on book depth.",
        }

