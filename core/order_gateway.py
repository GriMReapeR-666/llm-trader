"""Single entry-point for broker order execution (Phase 2 OrderGateway).

Enforces:
1. Preconditions: journal mode, kill switch, flattening flag, valid SL & side.
2. Fresh deterministic validation evidence matching setup_key.
3. Atomic risk reservation under BEGIN IMMEDIATE on trading_state.db (counting pending orders).
4. Margin preflight check with MarginSafetyGate (fail-closed, correct kwargs).
5. Thread-safe execution under process-wide MetaTrader5 module RLock.
6. Execution report journaling and intent lifecycle resolution.
7. Risk-reducing exits allowed under kill-switch; only-tighten protective SL updates.

Single-door rule: ONLY this module may call ``mt5.order_send``.
All other modules must use :class:`OrderGateway` (via
:func:`get_default_gateway`) and never import the broker adapters directly.
Enforcement is two-fold: a runtime :class:`DirectOrderExecutionForbidden`
guard keyed on a private ContextVar, and an AST test
(``tests/safety/test_single_order_door.py``) that forbids ``order_send``
and adapter imports outside this file.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import sys
import threading
from typing import Any, Dict, Iterator, List, Optional, Set

log = logging.getLogger("order_gateway")


class DirectOrderExecutionForbidden(RuntimeError):
    """Raised when mt5.order_send is invoked outside the OrderGateway send context."""
    pass


# Private send-context flag. Only code inside this module may set it (via
# _gateway_send_context). Any other module calling mt5.order_send directly
# — including via a smuggled LiveMT5Adapter — is rejected by the guard.
_GATEWAY_SEND_CTX: ContextVar[bool] = ContextVar("_GATEWAY_SEND_CTX", default=False)

# Broker retcodes worth retrying with the same intent (requote, reject,
# timeout, price changed, market closed). The intent stays CREATED so the
# reservation is held across retries.
TRANSIENT_RETCODES = frozenset({10004, 10006, 10012, 10015, 10018})


@contextmanager
def _gateway_send_context() -> Iterator[None]:
    token = _GATEWAY_SEND_CTX.set(True)
    try:
        yield
    finally:
        _GATEWAY_SEND_CTX.reset(token)


def _mt5_send(mt5_mod: Any, request: Dict[str, Any]) -> Any:
    """Send one raw MT5 request from inside the gateway send context."""
    with _gateway_send_context():
        return mt5_mod.order_send(request)


def install_order_send_guard(mt5_mod: Any = None) -> None:
    """Install a runtime interceptor requiring the gateway send context."""
    if mt5_mod is None:
        mt5_mod = sys.modules.get("MetaTrader5")
    if mt5_mod is None:
        return
    if getattr(mt5_mod, "_llm_trader_order_send_guarded", False) is True:
        return

    orig = getattr(mt5_mod, "order_send", None)
    if orig is None or not callable(orig):
        return

    class GuardedOrderSend:
        def __init__(self, orig_fn):
            self._orig = orig_fn

        def __call__(self, *args, **kwargs):
            if not _GATEWAY_SEND_CTX.get():
                import inspect
                frame = inspect.currentframe()
                caller = "unknown"
                try:
                    outer = frame.f_back if frame else None
                    if outer is not None:
                        caller = f"{outer.f_code.co_filename}:{outer.f_lineno}"
                finally:
                    del frame
                raise DirectOrderExecutionForbidden(
                    "Direct order_send forbidden outside OrderGateway "
                    f"(called from {caller}). Use get_default_gateway()."
                )
            return self._orig(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._orig, name)

    guarded = GuardedOrderSend(orig)
    setattr(mt5_mod, "order_send", guarded)
    setattr(mt5_mod, "_llm_trader_order_send_orig", orig)
    setattr(mt5_mod, "_llm_trader_order_send_guarded", True)


def uninstall_order_send_guard(mt5_mod: Any = None) -> None:
    """Remove the runtime interceptor on mt5.order_send."""
    if mt5_mod is None:
        mt5_mod = sys.modules.get("MetaTrader5")
    if mt5_mod is None:
        return
    guarded = getattr(mt5_mod, "order_send", None)
    orig = getattr(mt5_mod, "_llm_trader_order_send_orig", None) or getattr(guarded, "_orig", None)
    if orig is not None:
        try:
            setattr(mt5_mod, "order_send", orig)
        except Exception:
            pass
    for attr in ("_llm_trader_order_send_guarded", "_llm_trader_order_send_orig"):
        try:
            if hasattr(mt5_mod, attr):
                delattr(mt5_mod, attr)
        except Exception:
            pass


def get_mt5_lock() -> threading.RLock:
    """Process-wide MT5 lock stored on the MetaTrader5 module object.

    Because the module object is unique across double-imports of our own code,
    attaching the lock directly to MetaTrader5 ensures process-wide serialization.
    """
    mt5_mod = sys.modules.get("MetaTrader5")
    if mt5_mod is not None:
        lock = getattr(mt5_mod, "_llm_trader_lock", None)
        if lock is None:
            lock = threading.RLock()
            setattr(mt5_mod, "_llm_trader_lock", lock)
        return lock
    if not hasattr(get_mt5_lock, "_fallback_lock"):
        get_mt5_lock._fallback_lock = threading.RLock()
    return get_mt5_lock._fallback_lock


@dataclass
class OrderIntent:
    intent_key: str
    symbol: str
    side: str  # "BUY" or "SELL"
    volume: float
    price: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    risk_amount: float = 0.0
    setup_key: str = ""
    order_type: str = "MARKET"  # "MARKET" or "PENDING" or buy_limit/sell_limit/buy_stop/sell_stop
    magic: int = 460001
    comment: str = "OrderGateway"
    validation_result: Optional[Any] = None
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Equity the risk was sized on. When absent the gateway reads the live
    # account (LIVE/DEMO) or the store's "equity" state; with none it refuses.
    account_equity: Optional[float] = None


@dataclass
class GatewayResult:
    success: bool
    status: str  # "FILLED", "PENDING", "REJECTED", "ERROR", "CANCELLED"
    ticket: Optional[int] = None
    action: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class BaseBrokerAdapter:
    """Gateway-internal transport. Do NOT import from other modules."""

    def send_order(self, request: Dict[str, Any], mt5_client: Any = None) -> Dict[str, Any]:
        raise NotImplementedError

    def modify_protection(self, ticket: int, symbol: str, sl: float, tp: float, mt5_client: Any = None) -> Dict[str, Any]:
        raise NotImplementedError

    def close_position(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "", mt5_client: Any = None, deviation: int = 20) -> Dict[str, Any]:
        raise NotImplementedError

    def position_protection(self, ticket: int, mt5_client: Any = None) -> Optional[tuple]:
        """(sl, tp) of an open position, or None when it cannot be read."""
        return None

    def cancel_order(self, ticket: int, mt5_client: Any = None) -> Dict[str, Any]:
        raise NotImplementedError


class LiveMT5Adapter(BaseBrokerAdapter):
    """Gateway-internal live transport. Other modules must NOT import this."""

    def __init__(self, client: Any = None):
        self.client = client

    def _get_mt5(self, override: Any = None):
        mod = override if override is not None else self.client
        if mod is None:
            try:
                import MetaTrader5 as mt5  # type: ignore
                mod = mt5
            except ImportError:
                mod = sys.modules.get("MetaTrader5")
        if mod is not None:
            install_order_send_guard(mod)
        return mod

    def send_order(self, request: Dict[str, Any], mt5_client: Any = None) -> Dict[str, Any]:
        mt5 = self._get_mt5(mt5_client)
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 module unavailable", "result": None}
        with get_mt5_lock():
            res = _mt5_send(mt5, request)
            if res is None:
                err = mt5.last_error() if hasattr(mt5, "last_error") else (-1, "order_send returned None")
                return {"retcode": err[0] if isinstance(err, tuple) else -1, "error": str(err), "result": None}
            return {"retcode": getattr(res, "retcode", 10009), "order": getattr(res, "order", 0), "result": res}

    def modify_protection(self, ticket: int, symbol: str, sl: float, tp: float, mt5_client: Any = None) -> Dict[str, Any]:
        mt5 = self._get_mt5(mt5_client)
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 unavailable"}
        req = {
            "action": getattr(mt5, "TRADE_ACTION_SLTP", 6),
            "position": ticket,
            "symbol": symbol,
            "sl": float(sl),
            "tp": float(tp),
        }
        with get_mt5_lock():
            res = _mt5_send(mt5, req)
            return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}

    @staticmethod
    def _find_position(mt5: Any, ticket: int) -> Any:
        """The position with exactly this ticket, or None (never another one: review X2)."""
        if not hasattr(mt5, "positions_get"):
            return None
        try:
            positions = mt5.positions_get(ticket=ticket)
        except TypeError:
            positions = mt5.positions_get()
        matched = [p for p in (positions or []) if getattr(p, "ticket", None) == ticket]
        return matched[0] if matched else None

    @staticmethod
    def _filling_mode(mt5: Any, symbol: str) -> Optional[int]:
        """Broker-advertised market filling mode, as order_execution uses for entries (review X3)."""
        try:
            flags = int(getattr(mt5.symbol_info(symbol), "filling_mode", -1))
        except Exception:
            return None
        if flags > 0 and flags & 2:
            return int(getattr(mt5, "ORDER_FILLING_IOC", 1))
        if flags > 0 and flags & 1:
            return int(getattr(mt5, "ORDER_FILLING_FOK", 0))
        return None

    def position_protection(self, ticket: int, mt5_client: Any = None) -> Optional[tuple]:
        mt5 = self._get_mt5(mt5_client)
        if mt5 is None:
            return None
        with get_mt5_lock():
            pos = self._find_position(mt5, int(ticket))
        if pos is None:
            return None
        return float(getattr(pos, "sl", 0.0) or 0.0), float(getattr(pos, "tp", 0.0) or 0.0)

    def close_position(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "", mt5_client: Any = None, deviation: int = 20) -> Dict[str, Any]:
        mt5 = self._get_mt5(mt5_client)
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 unavailable"}
        with get_mt5_lock():
            pos = self._find_position(mt5, int(ticket))
            if pos is None:
                return {"retcode": -1, "error": f"Position {ticket} not found"}
            symbol = str(getattr(pos, "symbol", "") or symbol)
            tick = mt5.symbol_info_tick(symbol) if hasattr(mt5, "symbol_info_tick") else None
            is_buy = getattr(pos, "type", 0) == getattr(mt5, "ORDER_TYPE_BUY", 0)
            price = getattr(tick, "bid" if is_buy else "ask", None) if tick is not None else None
            if not price:
                price = getattr(pos, "price_current", 0.0)
            pos_volume = float(getattr(pos, "volume", 0.0) or 0.0)
            close_volume = float(volume) if volume else pos_volume
            if not 0 < close_volume <= pos_volume + 1e-9:
                return {"retcode": -1, "error": f"Close volume {close_volume} outside position volume {pos_volume}"}
            req = {
                "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
                "position": ticket,
                "symbol": symbol,
                "volume": close_volume,
                "type": getattr(mt5, "ORDER_TYPE_SELL" if is_buy else "ORDER_TYPE_BUY", 1),
                "price": price,
                "deviation": int(deviation),
                "magic": int(getattr(pos, "magic", 0) or 0),
                "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
                "comment": comment or "gateway_close",
            }
            filling = self._filling_mode(mt5, symbol)
            if filling is not None:
                req["type_filling"] = filling
            res = _mt5_send(mt5, req)
            return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}

    def cancel_order(self, ticket: int, mt5_client: Any = None) -> Dict[str, Any]:
        mt5 = self._get_mt5(mt5_client)
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 unavailable"}
        # Prefer the high-level cancel when the SDK exposes it; otherwise
        # fall back to a REMOVE request through the guarded send path.
        if hasattr(mt5, "order_cancel"):
            with get_mt5_lock():
                try:
                    with _gateway_send_context():
                        res = mt5.order_cancel(ticket)
                except DirectOrderExecutionForbidden:
                    raise
                except Exception as exc:
                    return {"retcode": -1, "error": str(exc)}
                return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}
        req = {
            "action": getattr(mt5, "TRADE_ACTION_REMOVE", 8),
            "order": ticket,
        }
        with get_mt5_lock():
            res = _mt5_send(mt5, req)
            return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}


class PaperAdapter(BaseBrokerAdapter):
    """Gateway-internal paper transport. Other modules must NOT import this."""

    def __init__(self, paper_broker: Any = None):
        if paper_broker is None:
            from core.order_execution import PaperBroker
            paper_broker = PaperBroker()
        self.paper = paper_broker

    @staticmethod
    def _pending_otype(request: Dict[str, Any]) -> Optional[str]:
        otype = request.get("otype") or request.get("order_type")
        if isinstance(otype, str) and otype in {"buy_limit", "sell_limit", "buy_stop", "sell_stop"}:
            return otype
        mt5_type = request.get("type")
        mapping = {2: "buy_limit", 3: "sell_limit", 4: "buy_stop", 5: "sell_stop"}
        if mt5_type in mapping:
            return mapping[mt5_type]
        action = request.get("action")
        # TRADE_ACTION_PENDING == 5
        if action == 5 and isinstance(mt5_type, int):
            return mapping.get(mt5_type)
        return None

    def send_order(self, request: Dict[str, Any], mt5_client: Any = None) -> Dict[str, Any]:
        symbol = request.get("symbol", "EURUSD")
        vol = float(request.get("volume", 0.01))
        price = float(request.get("price", 1.0))
        sl = float(request.get("sl") or 0.0)
        tp = float(request.get("tp") or 0.0)
        magic = int(request.get("magic", 460001))
        comment = request.get("comment", "")
        otype = self._pending_otype(request)
        if otype is not None:
            expires_at = request.get("expires_at")
            return self.paper.pending_order(
                symbol=symbol, otype=otype, price=price, volume=vol,
                sl=sl, tp=tp, comment=comment, magic=magic, expires_at=expires_at,
            )
        raw_type = request.get("type", 0)
        if isinstance(raw_type, str):
            side = "BUY" if raw_type.upper().startswith("BUY") or raw_type == "0" else "SELL"
            if raw_type.lower() in ("buy", "sell"):
                side = raw_type.upper()
        else:
            side = "BUY" if int(raw_type or 0) == 0 else "SELL"
        return self.paper.market_order(
            symbol=symbol, side=side, volume=vol, price=price,
            sl=sl, tp=tp, comment=comment, magic=magic
        )

    def modify_protection(self, ticket: int, symbol: str, sl: float, tp: float, mt5_client: Any = None) -> Dict[str, Any]:
        pos = getattr(self.paper, "positions", {}).get(int(ticket), None)
        magic = int((pos or {}).get("magic", 0) or 0)
        ok = self.paper.modify_position_sltp(symbol=symbol, sl=sl, tp=tp, magic=magic) if hasattr(self.paper, "modify_position_sltp") else self.paper.modify_position(ticket, sl=sl, tp=tp)
        if isinstance(ok, int):
            return {"retcode": 10009 if ok > 0 else -1}
        return {"retcode": 10009 if ok else -1}

    def position_protection(self, ticket: int, mt5_client: Any = None) -> Optional[tuple]:
        pos = getattr(self.paper, "positions", {}).get(int(ticket))
        if not pos:
            return None
        return float(pos.get("sl", 0.0) or 0.0), float(pos.get("tp", 0.0) or 0.0)

    def close_position(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "", mt5_client: Any = None, deviation: int = 20) -> Dict[str, Any]:
        pos = getattr(self.paper, "positions", {}).get(int(ticket), {})
        price = float(pos.get("current_price") or pos.get("price") or pos.get("price_open") or 1.0)
        magic = int(pos.get("magic", 0) or 0)
        if hasattr(self.paper, "close_position"):
            try:
                ok = self.paper.close_position(ticket, price=price, volume=volume, magic=magic)
            except TypeError:
                ok = self.paper.close_position(ticket, price=price, volume=volume)
        else:
            ok = {"retcode": -1}
        if isinstance(ok, dict):
            return {"retcode": int(ok.get("retcode", -1)), "order": ticket}
        return {"retcode": 10009 if ok else -1, "order": ticket if ok else None}

    def cancel_order(self, ticket: int, mt5_client: Any = None) -> Dict[str, Any]:
        ok = self.paper.cancel_order(ticket)
        return {"retcode": 10009 if ok else -1}


def _journal_mode(store: Any) -> str:
    scope = getattr(store, "scope", None)
    if isinstance(scope, dict) and scope.get("mode"):
        return str(scope["mode"]).upper()
    mode = getattr(store, "mode", None)
    if mode:
        return str(mode).upper()
    import os
    return str(os.getenv("TRADING_JOURNAL_MODE", "PAPER") or "PAPER").upper()


class OrderGateway:
    """The authoritative single door to broker execution."""

    def __init__(
        self,
        store: Any,
        mode: str = "PAPER",
        adapter: Optional[BaseBrokerAdapter] = None,
        arbiter: Optional[Any] = None,
        max_evidence_age_s: float = 120.0,
    ):
        self.store = store
        if mode in (None, "", "AUTO"):
            mode = _journal_mode(store) if store is not None else "PAPER"
        self.mode = str(mode or "PAPER").upper()
        if self.mode not in ("PAPER", "DEMO", "LIVE"):
            self.mode = "PAPER"
        if adapter is not None:
            self.adapter = adapter
        elif self.mode in ("LIVE", "DEMO"):
            self.adapter = LiveMT5Adapter()
        else:
            paper_broker = getattr(store, "paper_broker", None)
            self.adapter = PaperAdapter(paper_broker) if paper_broker is not None else PaperAdapter()
            # Keep the store-linked broker and the adapter broker identical so
            # paper exits/modifies hit the same book that holds the positions.
            try:
                if paper_broker is not None:
                    self.adapter.paper = paper_broker
            except Exception:
                pass
        self.arbiter = arbiter
        self.max_evidence_age_s = max_evidence_age_s
        self.flattening = False
        self._reservation_lock = threading.RLock()

    # -- adapter client plumbing -------------------------------------------
    @property
    def is_paper(self) -> bool:
        return isinstance(self.adapter, PaperAdapter)

    def bind_paper_broker(self, paper_broker: Any) -> None:
        if isinstance(self.adapter, PaperAdapter) and paper_broker is not None:
            self.adapter.paper = paper_broker

    def bind_live_client(self, client: Any) -> None:
        if isinstance(self.adapter, LiveMT5Adapter) and client is not None:
            self.adapter.client = client

    # -- entries ------------------------------------------------------------
    def submit_entry(
        self,
        intent: OrderIntent,
        mt5_request: Optional[Dict[str, Any]] = None,
        mt5_client: Any = None,
    ) -> GatewayResult:
        """Submit a new entry order through preconditions, evidence, reservation, and lock."""
        # 1. Preconditions
        if self.flattening:
            return GatewayResult(False, "REJECTED", error="Refused: flattening in progress")
        from core.kill_switch import entries_blocked
        blocked, block_reason = entries_blocked(self.store)
        if blocked:
            if block_reason == "kill switch active":
                return GatewayResult(False, "REJECTED", error="Refused: kill switch is active")
            return GatewayResult(False, "REJECTED", error=f"Refused: {block_reason}")

        side = str(intent.side or "").upper()
        if side not in ("BUY", "SELL"):
            return GatewayResult(False, "REJECTED", error=f"Invalid side: '{intent.side}'")
        try:
            sl_val = float(intent.sl) if intent.sl is not None else 0.0
        except (TypeError, ValueError):
            sl_val = 0.0
        if not sl_val or sl_val <= 0:
            return GatewayResult(False, "REJECTED", error="PH1-N3: entry requires explicit non-zero stop-loss")
        try:
            if float(intent.volume) <= 0:
                return GatewayResult(False, "REJECTED", error="Invalid volume: must be > 0")
        except (TypeError, ValueError):
            return GatewayResult(False, "REJECTED", error="Invalid volume: must be > 0")

        # 2. Evidence
        vr = intent.validation_result
        if vr is None or not getattr(vr, "approved", False):
            return GatewayResult(False, "REJECTED", error="Missing or unapproved validation evidence")
        strict = self.mode in ("LIVE", "DEMO")
        vr_setup = getattr(vr, "setup_key", None)
        if strict and not (vr_setup and intent.setup_key):
            return GatewayResult(False, "REJECTED", error="Validation evidence is not bound to a setup_key")
        if intent.setup_key and vr_setup and vr_setup != intent.setup_key:
            return GatewayResult(False, "REJECTED", error=f"Validation evidence setup_key mismatch: {vr_setup} != {intent.setup_key}")

        created_ts = getattr(vr, "created_at", None)
        if strict and not created_ts:
            return GatewayResult(False, "REJECTED", error="Validation evidence has no timestamp")
        if created_ts:
            try:
                ts_dt = datetime.fromisoformat(created_ts.replace("Z", "+00:00")) if isinstance(created_ts, str) else created_ts
                age = (datetime.now(timezone.utc) - ts_dt).total_seconds()
            except Exception as e:
                return GatewayResult(False, "REJECTED", error=f"Validation evidence timestamp unreadable: {e}")
            if age > self.max_evidence_age_s:
                return GatewayResult(False, "REJECTED", error=f"Validation evidence expired ({age:.1f}s > {self.max_evidence_age_s}s)")

        equity = self._equity(intent, mt5_client)
        if equity is None:
            return GatewayResult(False, "REJECTED", error="Refused: account equity unavailable for the risk cap")

        # 3. Atomic Risk Reservation (BEGIN IMMEDIATE with mutex)
        now_iso = datetime.now(timezone.utc).isoformat()
        db_path = getattr(self.store, "path", "trading_state.db")
        with self._reservation_lock:
            with sqlite3.connect(db_path, timeout=10.0, isolation_level=None) as con:
                con.execute("PRAGMA busy_timeout = 10000")
                con.execute("BEGIN IMMEDIATE")
                try:
                    con.execute("""CREATE TABLE IF NOT EXISTS order_intents (
                      intent_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                      run_id TEXT, proposal_id TEXT, status TEXT NOT NULL, mt5_ticket INTEGER,
                      payload TEXT NOT NULL, result TEXT, error TEXT
                    )""")

                    existing = con.execute(
                        "SELECT status FROM order_intents WHERE intent_key=?", (intent.intent_key,)
                    ).fetchone()
                    if existing is not None and existing[0] != "CREATED":
                        con.execute("ROLLBACK")
                        return GatewayResult(False, "REJECTED", error=f"Duplicate intent {intent.intent_key} already {existing[0]}")

                    # Open risk = open trades + intents still reserving, this
                    # intent excluded so a pre-created or retried CREATED row
                    # is counted once, as the new risk below (review R3).
                    from core.trading_safety import reserving_intent_payloads
                    trades_exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'").fetchone()
                    current_open_risk = 0.0
                    if trades_exists:
                        current_open_risk = float(con.execute(
                            "SELECT COALESCE(SUM(risk_amount),0) FROM trades WHERE closed_at IS NULL").fetchone()[0])
                    current_open_risk += sum(float(pl.get("risk_amount", 0.0) or 0.0)
                                             for pl in reserving_intent_payloads(con, exclude_key=intent.intent_key))

                    max_open_fraction = 0.03
                    if self.arbiter:
                        max_open_fraction = getattr(self.arbiter, "max_open_risk_fraction", 0.03)
                    cap = equity * max_open_fraction
                    new_total = current_open_risk + float(intent.risk_amount or 0.0)
                    if new_total > cap + 1e-6:
                        con.execute("ROLLBACK")
                        return GatewayResult(False, "REJECTED", error=f"Portfolio open-risk limit breached: {new_total:.2f} > {cap:.2f}")

                    if existing is None:
                        payload_json = json.dumps({
                            "symbol": intent.symbol,
                            "side": intent.side,
                            "volume": intent.volume,
                            "price": intent.price,
                            "sl": intent.sl,
                            "tp": intent.tp,
                            "risk_amount": intent.risk_amount,
                            "setup_key": intent.setup_key,
                            "order_type": intent.order_type,
                        })
                        con.execute(
                            "INSERT INTO order_intents(intent_key, created_at, updated_at, status, payload) VALUES(?,?,?,?,?)",
                            (intent.intent_key, now_iso, now_iso, "CREATED", payload_json),
                        )
                    con.execute("COMMIT")
                except Exception as exc:
                    try:
                        con.execute("ROLLBACK")
                    except Exception:
                        pass
                    return GatewayResult(False, "ERROR", error=f"Risk reservation failed: {exc}")

        # 4. Margin Preflight (LIVE/DEMO only; PAPER has no broker margin).
        # Fail CLOSED: any margin error rejects the entry.
        if self.mode in ("LIVE", "DEMO"):
            try:
                from core.margin_safety import MarginSafetyGate
                gate = MarginSafetyGate()
                mt5_for_margin = mt5_client or (self.adapter.client if isinstance(self.adapter, LiveMT5Adapter) else None)
                if mt5_for_margin is None:
                    try:
                        import MetaTrader5 as _mt5mod  # type: ignore
                        mt5_for_margin = _mt5mod
                    except ImportError:
                        mt5_for_margin = sys.modules.get("MetaTrader5")
                assessment = gate.assess_from_mt5(
                    mt5_module=mt5_for_margin,
                    symbol=intent.symbol,
                    side=side,
                    volume=float(intent.volume),
                    price=float(intent.price),
                    new_risk=float(intent.risk_amount or 0.0),
                    driver_buckets=self._buckets(intent.symbol, side),
                    position_margin_estimates=[],
                )
                if not assessment.approved:
                    self._resolve_intent(intent.intent_key, "REJECTED", error=str(assessment.reasons))
                    return GatewayResult(False, "REJECTED", error=f"Margin safety refusal: {assessment.reasons}")
            except Exception as e:
                try:
                    self._resolve_intent(intent.intent_key, "REJECTED", error=f"Margin preflight failed closed: {e}")
                except Exception:
                    pass
                return GatewayResult(False, "REJECTED", error=f"Margin preflight failed closed: {e}")

        # 5. Send under MT5 Lock (via the gateway adapters only).
        if mt5_request is not None:
            send_req = dict(mt5_request)
        else:
            send_req = self._build_entry_request(intent, side)
            if send_req is None:
                self._resolve_intent(intent.intent_key, "REJECTED", error="Unsupported order_type")
                return GatewayResult(False, "REJECTED", error="Unsupported order_type for gateway dispatch")

        try:
            send_res = self.adapter.send_order(send_req, mt5_client=mt5_client)
        except Exception as exc:
            # The request may or may not have reached the broker: live -> UNRESOLVED
            # (keeps reserving, reconciler/operator decides); paper -> REJECTED.
            status = "REJECTED" if self.mode == "PAPER" else "UNRESOLVED"
            self._resolve_intent(intent.intent_key, status, error=f"send raised: {exc}")
            return GatewayResult(False, "ERROR", error=f"send raised: {exc}")
        is_market = str(intent.order_type or "MARKET").upper() in ("MARKET", "BUY", "SELL")

        retcode = send_res.get("retcode", -1)
        if retcode in (10009, 10008, 10010):  # DONE / PLACED / DONE_PARTIAL
            ticket = send_res.get("order") or send_res.get("ticket")
            self._resolve_intent(intent.intent_key, "PENDING", ticket=ticket)
            if retcode == 10010:
                self._resolve_intent(intent.intent_key, "PARTIAL", ticket=ticket)
            # PENDING -> FILLED is owned by the reconciler, which also writes the
            # trades row that takes over the reservation (paper and live alike).
            filled = self.mode == "PAPER" and is_market and retcode != 10010
            return GatewayResult(True, "FILLED" if filled else "PENDING", ticket=ticket, action=side, details=send_res)
        err = send_res.get("error", f"retcode {retcode}")
        if retcode in TRANSIENT_RETCODES:
            # Leave the intent CREATED (reservation held) for the caller's retry.
            return GatewayResult(False, "REJECTED", error=f"transient retcode {retcode}: {err}", details=send_res)
        self._resolve_intent(intent.intent_key, "REJECTED", error=str(err))
        return GatewayResult(False, "REJECTED", error=str(err), details=send_res)

    def sweep_stale_created(self, max_age_s: float = 300.0, mt5_client: Any = None) -> Dict[str, str]:
        """Resolve CREATED intents older than ``max_age_s`` (review D4).

        A CREATED intent that old means the process died (or raised) between
        the reservation and the send, so it reserves risk forever otherwise.
        - PAPER: the in-memory book did not survive; nothing reached a broker -> REJECTED.
        - LIVE/DEMO: look for the order at the broker by symbol, volume and magic.
          An open order/position no other intent claims -> PENDING with that
          ticket; a matching order in recent history -> UNRESOLVED (sent,
          outcome unknown); nothing anywhere -> REJECTED.
        A broker read failure leaves the intent CREATED for the next sweep.
        Returns {intent_key: new_status}.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - float(max_age_s)
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_intents'").fetchone():
                    return {}
                rows = con.execute("SELECT intent_key, created_at, payload FROM order_intents WHERE status='CREATED'").fetchall()
                claimed = {r[0] for r in con.execute("SELECT mt5_ticket FROM order_intents WHERE mt5_ticket IS NOT NULL")}
        except Exception as exc:
            log.warning("stale-intent sweep could not read intents: %s", exc)
            return {}
        stale = []
        for key, created_at, payload in rows:
            try:
                created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except ValueError:
                created = None
            if created is None or created.timestamp() <= cutoff:
                try:
                    pl = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
                except Exception:
                    pl = {}
                stale.append((key, created, pl))
        if not stale:
            return {}
        outcomes: Dict[str, str] = {}
        if self.mode == "PAPER":
            for key, _, _ in stale:
                if self._resolve_intent(key, "REJECTED", error="stale reservation: never sent (paper)"):
                    outcomes[key] = "REJECTED"
            return outcomes
        client = mt5_client or getattr(self.adapter, "client", None) or sys.modules.get("MetaTrader5")
        if client is None:
            return {}

        def matches(obj: Any, pl: Dict[str, Any]) -> bool:
            if str(getattr(obj, "symbol", "")) != str(pl.get("symbol", "")):
                return False
            vol = getattr(obj, "volume_initial", None) or getattr(obj, "volume", None)
            try:
                if abs(float(vol) - float(pl.get("volume"))) > 1e-9:
                    return False
            except (TypeError, ValueError):
                return False
            if pl.get("magic") not in (None, "", 0) and int(getattr(obj, "magic", 0) or 0) != int(pl["magic"]):
                return False
            return True

        try:
            with get_mt5_lock():
                live = list(client.orders_get() or []) + list(client.positions_get() or [])
                since = min((c for _, c, _ in stale if c is not None), default=datetime.now(timezone.utc))
                # MT5 history is in broker server time (often hours off UTC): pad by a day.
                history = list(client.history_orders_get(since.replace(tzinfo=None) - timedelta(days=1),
                                                         datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)) or [])
        except Exception as exc:
            log.warning("stale-intent sweep could not read the broker; leaving intents CREATED: %s", exc)
            return {}
        for key, _, pl in stale:
            found = [o for o in live if matches(o, pl) and getattr(o, "ticket", None) not in claimed]
            if found:
                ticket = int(getattr(found[0], "ticket", 0))
                if self._resolve_intent(key, "PENDING", ticket=ticket):
                    claimed.add(ticket)
                    outcomes[key] = "PENDING"
            elif any(matches(o, pl) for o in history):
                if self._resolve_intent(key, "UNRESOLVED", error="stale reservation: a matching order reached the broker; outcome unknown"):
                    outcomes[key] = "UNRESOLVED"
            elif self._resolve_intent(key, "REJECTED", error="stale reservation: no matching broker order"):
                outcomes[key] = "REJECTED"
        return outcomes

    def abandon_entry(self, intent_key: str, error: str) -> bool:
        """Release a CREATED reservation the caller will not retry again.

        Only CREATED (never sent, or a transient broker refusal) is released;
        an intent already PENDING/FILLED at the broker is left untouched.
        """
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                row = con.execute("SELECT status FROM order_intents WHERE intent_key=?", (intent_key,)).fetchone()
        except Exception as exc:
            log.warning("abandon_entry lookup failed: %s", exc)
            return False
        if row is None or row[0] != "CREATED":
            return False
        return self._resolve_intent(intent_key, "REJECTED", error=error)

    def _equity(self, intent: OrderIntent, mt5_client: Any = None) -> Optional[float]:
        """Equity for the open-risk cap; None (refuse) when nothing trustworthy is known (review R2)."""
        import math
        candidates = [intent.account_equity]
        if self.mode in ("LIVE", "DEMO"):
            client = mt5_client or getattr(self.adapter, "client", None) or sys.modules.get("MetaTrader5")
            try:
                with get_mt5_lock():
                    info = client.account_info() if client is not None else None
                candidates.append(getattr(info, "equity", None))
            except Exception as exc:
                log.warning("account_info unavailable for the risk cap: %s", exc)
        if hasattr(self.store, "get_state"):
            try:
                candidates.append(self.store.get_state("equity"))
            except Exception:
                pass
        for value in candidates:
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v) and v > 0:
                return v
        return None

    def _buckets(self, symbol: str, side: str) -> List[str]:
        try:
            from core.trading_safety import exposure_buckets
            return sorted(exposure_buckets(symbol, side))
        except Exception:
            return [f"{symbol}:{side}"]

    def _build_entry_request(self, intent: OrderIntent, side: str) -> Optional[Dict[str, Any]]:
        otype = str(intent.order_type or "MARKET").upper()
        base: Dict[str, Any] = {
            "symbol": intent.symbol,
            "volume": float(intent.volume),
            "price": float(intent.price),
            "sl": float(intent.sl or 0.0),
            "tp": float(intent.tp or 0.0),
            "magic": int(intent.magic),
            "comment": intent.comment,
        }
        if otype in ("MARKET", "BUY", "SELL"):
            base.update({"action": 1, "type": 0 if side == "BUY" else 1})
            return base
        lower = str(intent.order_type or "").lower()
        if lower in ("buy_limit", "sell_limit", "buy_stop", "sell_stop"):
            base.update({"action": 5, "otype": lower})
            if intent.expires_at:
                base["expires_at"] = intent.expires_at
            return base
        return None

    # -- exits / modifies / cancels (risk-reducing; allowed under kill switch)
    def submit_exit(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "exit", mt5_client: Any = None, owned_magics: Optional[Set[int]] = None, deviation: int = 20) -> GatewayResult:
        """Submit position exit. Allowed under kill switch (risk-reducing)."""
        if owned_magics is not None and mt5_client is not None and hasattr(mt5_client, "positions_get"):
            try:
                positions = mt5_client.positions_get(ticket=int(ticket)) or []
                if positions and int(getattr(positions[0], "magic", 0) or 0) not in set(owned_magics):
                    return GatewayResult(False, "REJECTED", ticket=int(ticket), action="EXIT", error="Refused: position magic not owned")
            except Exception:
                pass
        res = self.adapter.close_position(int(ticket), symbol, volume=volume, comment=comment, mt5_client=mt5_client, deviation=deviation)
        retcode = res.get("retcode", -1)
        ok = retcode in (10009, 10008, 10010)
        # No intent transition here: a filled intent's risk is released by its
        # trades row (see trading_safety.reserving_intent_payloads).
        return GatewayResult(ok, "FILLED" if ok else "ERROR", ticket=int(ticket), action="EXIT", error=res.get("error"), details=res)

    def modify_protection(self, ticket: int, symbol: str, new_sl: Optional[float] = None, new_tp: Optional[float] = None, current_sl: Optional[float] = None, side: str = "BUY", mt5_client: Any = None, current_tp: Optional[float] = None) -> GatewayResult:
        """Modify stop-loss and take-profit enforcing monotonic only-tighten protection.

        A value left as None keeps the position's current one; MT5 treats 0
        as "remove", so an SL-only change must not clear the TP and a TP-only
        change must never strip the protective SL (review X1).
        """
        if new_sl is not None and current_sl is not None and current_sl > 0:
            try:
                if side.upper() == "BUY" and float(new_sl) < float(current_sl) - 1e-12:
                    return GatewayResult(False, "REJECTED", ticket=int(ticket), action="MODIFY", error=f"Protective SL cannot be widened on BUY ({new_sl} < {current_sl})")
                if side.upper() == "SELL" and float(new_sl) > float(current_sl) + 1e-12:
                    return GatewayResult(False, "REJECTED", ticket=int(ticket), action="MODIFY", error=f"Protective SL cannot be widened on SELL ({new_sl} > {current_sl})")
            except (TypeError, ValueError):
                return GatewayResult(False, "REJECTED", ticket=int(ticket), action="MODIFY", error="Invalid SL values")
        if new_sl is None:
            new_sl = current_sl
        if new_tp is None:
            new_tp = current_tp
        if new_sl is None or new_tp is None:
            known = self.adapter.position_protection(int(ticket), mt5_client=mt5_client)
            if known is None:
                return GatewayResult(False, "REJECTED", ticket=int(ticket), action="MODIFY",
                                     error="Current SL/TP unknown; refusing a modify that could clear them")
            new_sl = known[0] if new_sl is None else new_sl
            new_tp = known[1] if new_tp is None else new_tp
        try:
            if not float(new_sl) > 0:
                return GatewayResult(False, "REJECTED", ticket=int(ticket), action="MODIFY",
                                     error="Refused: modify would remove the protective stop-loss")
        except (TypeError, ValueError):
            return GatewayResult(False, "REJECTED", ticket=int(ticket), action="MODIFY", error="Invalid SL values")
        res = self.adapter.modify_protection(int(ticket), symbol, sl=float(new_sl), tp=float(new_tp or 0.0), mt5_client=mt5_client)
        retcode = res.get("retcode", -1)
        ok = retcode in (10009, 10008, 10010)
        return GatewayResult(ok, "FILLED" if ok else "ERROR", ticket=int(ticket), action="MODIFY", error=res.get("error"), details=res)

    def cancel_pending(self, ticket: int, symbol: str = "", mt5_client: Any = None) -> GatewayResult:
        """Cancel a pending order and release its reservation."""
        res = self.adapter.cancel_order(int(ticket), mt5_client=mt5_client)
        retcode = res.get("retcode", -1)
        ok = retcode in (10009, 10008, 10010)
        # Release only a confirmed cancel; otherwise the order may still be
        # working at the broker, so keep reserving and flag it (review R4).
        if ok:
            self._resolve_by_ticket(int(ticket), "CANCELLED")
        else:
            self._resolve_by_ticket(int(ticket), "UNRESOLVED", error=f"cancel not confirmed: {res.get('error') or retcode}")
        return GatewayResult(ok, "CANCELLED" if ok else "ERROR", ticket=int(ticket), action="CANCEL", error=res.get("error"), details=res)

    def execute_flatten(self, owned_magics: Optional[Set[int]] = None, mt5_client: Any = None) -> List[GatewayResult]:
        """Execute guardian flatten while setting flattening flag (CC-F10)."""
        self.flattening = True
        results: List[GatewayResult] = []
        try:
            # If paper adapter, close all paper positions with their own symbols.
            if isinstance(self.adapter, PaperAdapter):
                for ticket, pos in list(getattr(self.adapter.paper, "positions", {}).items()):
                    symbol = str((pos or {}).get("symbol", "") or "EURUSD")
                    if owned_magics:
                        try:
                            if int((pos or {}).get("magic", 0) or 0) not in set(owned_magics):
                                continue
                        except (TypeError, ValueError):
                            continue
                    res = self.submit_exit(int(ticket), symbol, comment="guardian_flatten")
                    results.append(res)
            else:
                # MT5 adapter
                client = mt5_client or getattr(self.adapter, "client", None) or getattr(self.adapter, "_get_mt5", lambda: None)()
                if client is not None and hasattr(client, "positions_get"):
                    with get_mt5_lock():
                        with _gateway_send_context():
                            try:
                                positions = client.positions_get() or []
                            except TypeError:
                                positions = []
                    for pos in positions:
                        try:
                            if owned_magics and int(getattr(pos, "magic", 0) or 0) not in set(owned_magics):
                                continue
                            res = self.submit_exit(int(getattr(pos, "ticket", 0) or 0), str(getattr(pos, "symbol", "")), comment="guardian_flatten", mt5_client=client, owned_magics=owned_magics)
                            results.append(res)
                        except Exception as exc:
                            results.append(GatewayResult(False, "ERROR", error=str(exc)))
                # Cancel pending orders owned by us.
                if client is not None and hasattr(client, "orders_get"):
                    with get_mt5_lock():
                        with _gateway_send_context():
                            try:
                                orders = client.orders_get() or []
                            except Exception:
                                orders = []
                    for order in orders or []:
                        try:
                            if owned_magics and int(getattr(order, "magic", 0) or 0) not in set(owned_magics):
                                continue
                            results.append(self.cancel_pending(int(getattr(order, "ticket", 0) or 0), mt5_client=client))
                        except Exception as exc:
                            results.append(GatewayResult(False, "ERROR", error=str(exc)))
        finally:
            self.flattening = False
        return results

    def _resolve_intent(self, intent_key: str, status: str, ticket: Optional[int] = None, error: Optional[str] = None) -> bool:
        """Apply one legal intent transition (review R5: never a free-form overwrite)."""
        try:
            from operations.agent_operations import AgentOperations
            transitions = AgentOperations.ORDER_INTENT_TRANSITIONS
        except Exception as exc:  # pragma: no cover - import layout guard
            log.warning("intent state machine unavailable: %s", exc)
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                row = con.execute("SELECT status FROM order_intents WHERE intent_key=?", (intent_key,)).fetchone()
                if row is None:
                    return False
                if status not in transitions.get(row[0], frozenset()):
                    log.warning("Illegal intent transition %s -> %s for %s", row[0], status, intent_key)
                    return False
                con.execute(
                    "UPDATE order_intents SET status=?, updated_at=?, mt5_ticket=COALESCE(?, mt5_ticket), error=COALESCE(?, error) WHERE intent_key=?",
                    (status, now_iso, ticket, error, intent_key),
                )
            return True
        except Exception as e:
            log.warning("Intent resolution update failed: %s", e)
            return False

    def _resolve_by_ticket(self, ticket: int, status: str, error: Optional[str] = None) -> None:
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                keys = [r[0] for r in con.execute("SELECT intent_key FROM order_intents WHERE mt5_ticket=?", (int(ticket),))]
        except Exception as e:
            log.warning("Intent lookup by ticket failed: %s", e)
            return
        for key in keys:
            self._resolve_intent(key, status, error=error)


# -- process-wide default gateway -------------------------------------------
_DEFAULT_GATEWAY: Optional[OrderGateway] = None
_DEFAULT_GATEWAY_LOCK = threading.RLock()


def configure_default_gateway(
    store: Any,
    mode: Optional[str] = None,
    paper_broker: Any = None,
    arbiter: Any = None,
    max_evidence_age_s: float = 120.0,
) -> OrderGateway:
    """Configure the process-wide gateway. Call once at boot after store wiring."""
    global _DEFAULT_GATEWAY
    resolved_mode = mode or (_journal_mode(store) if store is not None else "PAPER")
    gateway = OrderGateway(store=store, mode=resolved_mode, arbiter=arbiter, max_evidence_age_s=max_evidence_age_s)
    if paper_broker is not None:
        gateway.bind_paper_broker(paper_broker)
    elif store is not None and getattr(store, "paper_broker", None) is not None:
        gateway.bind_paper_broker(getattr(store, "paper_broker"))
    with _DEFAULT_GATEWAY_LOCK:
        _DEFAULT_GATEWAY = gateway
    return gateway


def get_default_gateway() -> OrderGateway:
    """Return the configured gateway, or raise fail-closed if not configured."""
    with _DEFAULT_GATEWAY_LOCK:
        gateway = _DEFAULT_GATEWAY
    if gateway is None:
        raise RuntimeError("OrderGateway not configured: call configure_default_gateway(store, ...) at boot")
    return gateway


def reset_default_gateway() -> None:
    global _DEFAULT_GATEWAY
    with _DEFAULT_GATEWAY_LOCK:
        _DEFAULT_GATEWAY = None


try:
    install_order_send_guard()
except Exception:
    pass
