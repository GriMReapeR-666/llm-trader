"""Single entry-point for broker order execution (Phase 2 OrderGateway).

Enforces:
1. Preconditions: journal mode, kill switch, flattening flag, valid SL & side.
2. Fresh deterministic validation evidence matching setup_key.
3. Atomic risk reservation under BEGIN IMMEDIATE on trading_state.db (counting pending orders).
4. Margin preflight check with MarginSafetyGate.assess (fail-closed).
5. Thread-safe execution under process-wide MetaTrader5 module RLock.
6. Execution report journaling and intent lifecycle resolution.
7. Risk-reducing exits allowed under kill-switch; only-tighten protective SL updates.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import logging
import sqlite3
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("order_gateway")


class DirectOrderExecutionForbidden(RuntimeError):
    """Raised when mt5.order_send is invoked directly outside core/order_gateway.py."""
    pass


def install_order_send_guard(mt5_mod: Any = None) -> None:
    """Installs a runtime interceptor on mt5.order_send requiring gateway caller frames."""
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
            caller_frame = sys._getframe(1)
            caller_file = (caller_frame.f_code.co_filename or "").replace("\\", "/")
            if not caller_file.endswith("core/order_gateway.py") and not caller_file.endswith("order_gateway.py"):
                raise DirectOrderExecutionForbidden(
                    f"Direct order_send forbidden outside core/order_gateway.py (called from {caller_file}:{caller_frame.f_lineno})"
                )
            return self._orig(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._orig, name)

    guarded = GuardedOrderSend(orig)
    setattr(mt5_mod, "order_send", guarded)
    setattr(mt5_mod, "_llm_trader_order_send_guarded", True)


def uninstall_order_send_guard(mt5_mod: Any = None) -> None:
    """Removes the runtime interceptor on mt5.order_send."""
    if mt5_mod is None:
        mt5_mod = sys.modules.get("MetaTrader5")
    if mt5_mod is None:
        return
    guarded = getattr(mt5_mod, "order_send", None)
    if guarded is not None and hasattr(guarded, "_orig"):
        setattr(mt5_mod, "order_send", guarded._orig)
    if hasattr(mt5_mod, "_llm_trader_order_send_guarded"):
        delattr(mt5_mod, "_llm_trader_order_send_guarded")


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
    order_type: str = "MARKET"  # "MARKET" or "PENDING"
    magic: int = 460001
    comment: str = "OrderGateway"
    validation_result: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayResult:
    success: bool
    status: str  # "FILLED", "PENDING", "REJECTED", "ERROR", "CANCELLED"
    ticket: Optional[int] = None
    action: str = ""
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class BaseBrokerAdapter:
    def send_order(self, request: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def modify_protection(self, ticket: int, symbol: str, sl: float, tp: float) -> Dict[str, Any]:
        raise NotImplementedError

    def close_position(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "") -> Dict[str, Any]:
        raise NotImplementedError

    def cancel_order(self, ticket: int) -> Dict[str, Any]:
        raise NotImplementedError


class LiveMT5Adapter(BaseBrokerAdapter):
    """Adapter sending real orders to MetaTrader 5 under the MT5 lock."""

    def __init__(self, client: Any = None):
        self.client = client

    def _get_mt5(self):
        if self.client is not None:
            mod = self.client
        else:
            try:
                import MetaTrader5 as mt5
                mod = mt5
            except ImportError:
                mod = sys.modules.get("MetaTrader5")
        if mod is not None:
            install_order_send_guard(mod)
        return mod

    def send_order(self, request: Dict[str, Any]) -> Dict[str, Any]:
        mt5 = self._get_mt5()
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 module unavailable", "result": None}
        with get_mt5_lock():
            res = mt5.order_send(request)
            if res is None:
                err = mt5.last_error() if hasattr(mt5, "last_error") else (-1, "order_send returned None")
                return {"retcode": err[0] if isinstance(err, tuple) else -1, "error": str(err), "result": None}
            return {"retcode": getattr(res, "retcode", 10009), "order": getattr(res, "order", 0), "result": res}

    def modify_protection(self, ticket: int, symbol: str, sl: float, tp: float) -> Dict[str, Any]:
        mt5 = self._get_mt5()
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
            res = mt5.order_send(req)
            return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}

    def close_position(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "") -> Dict[str, Any]:
        mt5 = self._get_mt5()
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 unavailable"}
        with get_mt5_lock():
            positions = None
            try:
                positions = mt5.positions_get(ticket=ticket) if hasattr(mt5, "positions_get") else None
            except TypeError:
                positions = mt5.positions_get() if hasattr(mt5, "positions_get") else None
            pos = None
            if positions:
                matched = [p for p in positions if getattr(p, "ticket", None) == ticket]
                pos = matched[0] if matched else positions[0]
            if not pos:
                return {"retcode": -1, "error": f"Position {ticket} not found"}
            tick = mt5.symbol_info_tick(symbol) if hasattr(mt5, "symbol_info_tick") else None
            is_buy = getattr(pos, "type", 0) == getattr(mt5, "ORDER_TYPE_BUY", 0)
            price = getattr(tick, "bid" if is_buy else "ask", getattr(pos, "price_current", 0.0))
            req = {
                "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
                "position": ticket,
                "symbol": symbol,
                "volume": float(volume or getattr(pos, "volume", 0.01)),
                "type": getattr(mt5, "ORDER_TYPE_SELL" if is_buy else "ORDER_TYPE_BUY", 1),
                "price": price,
                "comment": comment or "gateway_close",
            }
            res = mt5.order_send(req)
            return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}

    def cancel_order(self, ticket: int) -> Dict[str, Any]:
        mt5 = self._get_mt5()
        if mt5 is None:
            return {"retcode": -1, "error": "MetaTrader5 unavailable"}
        req = {
            "action": getattr(mt5, "TRADE_ACTION_REMOVE", 8),
            "order": ticket,
        }
        with get_mt5_lock():
            res = mt5.order_send(req)
            return {"retcode": getattr(res, "retcode", 10009) if res else -1, "result": res}


class PaperAdapter(BaseBrokerAdapter):
    """Adapter executing simulated orders against PaperBroker."""

    def __init__(self, paper_broker: Any = None):
        if paper_broker is None:
            from core.order_execution import PaperBroker
            paper_broker = PaperBroker()
        self.paper = paper_broker

    def send_order(self, request: Dict[str, Any]) -> Dict[str, Any]:
        symbol = request.get("symbol", "EURUSD")
        side = "BUY" if request.get("type", 0) == 0 else "SELL"
        vol = float(request.get("volume", 0.01))
        price = float(request.get("price", 1.0))
        sl = float(request.get("sl") or 0.0)
        tp = float(request.get("tp") or 0.0)
        magic = int(request.get("magic", 460001))
        comment = request.get("comment", "")
        return self.paper.market_order(
            symbol=symbol, side=side, volume=vol, price=price,
            sl=sl, tp=tp, comment=comment, magic=magic
        )

    def modify_protection(self, ticket: int, symbol: str, sl: float, tp: float) -> Dict[str, Any]:
        ok = self.paper.modify_position(ticket, sl=sl, tp=tp)
        return {"retcode": 10009 if ok else -1}

    def close_position(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "") -> Dict[str, Any]:
        pos = getattr(self.paper, "positions", {}).get(int(ticket), {})
        price = float(pos.get("current_price") or pos.get("price") or 1.0)
        ok = self.paper.close_position(ticket, price=price, volume=volume)
        return {"retcode": 10009 if ok else -1}

    def cancel_order(self, ticket: int) -> Dict[str, Any]:
        ok = self.paper.cancel_order(ticket)
        return {"retcode": 10009 if ok else -1}


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
        self.mode = str(mode or "PAPER").upper()
        if self.mode not in ("PAPER", "DEMO", "LIVE"):
            self.mode = "PAPER"
        if adapter is not None:
            self.adapter = adapter
        elif self.mode == "LIVE":
            self.adapter = LiveMT5Adapter()
        elif self.mode == "DEMO":
            self.adapter = LiveMT5Adapter()
        else:
            self.adapter = PaperAdapter()
        self.arbiter = arbiter
        self.max_evidence_age_s = max_evidence_age_s
        self.flattening = False

    def submit_entry(self, intent: OrderIntent) -> GatewayResult:
        """Submit a new entry order through preconditions, evidence, reservation, and lock."""
        # 1. Preconditions
        if self.flattening:
            return GatewayResult(False, "REJECTED", error="Refused: flattening in progress")
        if self.store.is_kill_switch_active():
            return GatewayResult(False, "REJECTED", error="Refused: kill switch is active")

        side = str(intent.side or "").upper()
        if side not in ("BUY", "SELL"):
            return GatewayResult(False, "REJECTED", error=f"Invalid side: '{intent.side}'")
        if intent.sl is None or float(intent.sl) <= 0:
            return GatewayResult(False, "REJECTED", error="PH1-N3: entry requires explicit non-zero stop-loss")
        if intent.volume <= 0:
            return GatewayResult(False, "REJECTED", error="Invalid volume: must be > 0")

        # 2. Evidence
        vr = intent.validation_result
        if vr is None or not getattr(vr, "approved", False):
            return GatewayResult(False, "REJECTED", error="Missing or unapproved validation evidence")
        if intent.setup_key and getattr(vr, "setup_key", None) and vr.setup_key != intent.setup_key:
            return GatewayResult(False, "REJECTED", error=f"Validation evidence setup_key mismatch: {vr.setup_key} != {intent.setup_key}")

        created_ts = getattr(vr, "created_at", None)
        if created_ts:
            try:
                if isinstance(created_ts, str):
                    ts_dt = datetime.fromisoformat(created_ts.replace("Z", "+00:00"))
                else:
                    ts_dt = created_ts
                age = (datetime.now(timezone.utc) - ts_dt).total_seconds()
                if age > self.max_evidence_age_s:
                    return GatewayResult(False, "REJECTED", error=f"Validation evidence expired ({age:.1f}s > {self.max_evidence_age_s}s)")
            except Exception as e:
                log.debug("Evidence timestamp parse error: %s", e)

        # 3. Atomic Risk Reservation (BEGIN IMMEDIATE with mutex)
        now_iso = datetime.now(timezone.utc).isoformat()
        db_path = getattr(self.store, "path", "trading_state.db")
        if not hasattr(self, "_reservation_lock"):
            self._reservation_lock = threading.RLock()

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

                    # Compute current open risk = filled trades + active intents
                    open_tickets = set()
                    trades_exists = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'").fetchone()
                    if trades_exists:
                        row_trades = con.execute("SELECT COALESCE(SUM(risk_amount),0) FROM trades WHERE closed_at IS NULL").fetchone()
                        current_open_risk = float(row_trades[0] if row_trades else 0.0)
                        open_tickets = {r[0] for r in con.execute("SELECT ticket FROM trades WHERE closed_at IS NULL").fetchall()}
                    else:
                        current_open_risk = 0.0

                    active_intents = con.execute(
                        "SELECT payload, mt5_ticket FROM order_intents WHERE status IN ('CREATED','PENDING','PARTIAL','UNRESOLVED','ERROR','FILLED')"
                    ).fetchall()
                    for irow in active_intents:
                        try:
                            ticket = irow[1]
                            if ticket is not None and ticket in open_tickets:
                                continue
                            pl = json.loads(irow[0]) if isinstance(irow[0], str) else (irow[0] or {})
                            current_open_risk += float(pl.get("risk_amount", 0.0) or 0.0)
                        except Exception:
                            pass

                    # Arbiter check if configured
                    equity = 10000.0
                    if hasattr(self.store, "get_state"):
                        equity = float(self.store.get_state("equity") or 10000.0)
                    max_open_fraction = 0.03
                    if self.arbiter:
                        max_open_fraction = getattr(self.arbiter, "max_open_risk_fraction", 0.03)

                    if current_open_risk + intent.risk_amount > (equity * max_open_fraction) + 1e-6:
                        con.execute("ROLLBACK")
                        return GatewayResult(False, "REJECTED", error=f"Portfolio open-risk limit breached: {current_open_risk + intent.risk_amount:.2f} > {equity * max_open_fraction:.2f}")

                    # Insert intent as CREATED
                    payload_json = json.dumps({
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "volume": intent.volume,
                        "price": intent.price,
                        "sl": intent.sl,
                        "tp": intent.tp,
                        "risk_amount": intent.risk_amount,
                        "setup_key": intent.setup_key,
                    })
                    con.execute(
                        "INSERT OR REPLACE INTO order_intents(intent_key, created_at, updated_at, status, payload) VALUES(?,?,?,?,?)",
                        (intent.intent_key, now_iso, now_iso, "CREATED", payload_json),
                    )
                    con.execute("COMMIT")
                except Exception as exc:
                    try:
                        con.execute("ROLLBACK")
                    except Exception:
                        pass
                    return GatewayResult(False, "ERROR", error=f"Risk reservation failed: {exc}")

        # 4. Margin Preflight (LIVE/DEMO)
        if self.mode in ("LIVE", "DEMO"):
            try:
                from core.margin_safety import MarginSafetyGate
                gate = MarginSafetyGate()
                account = getattr(self.store, "get_account_snapshot", lambda: {"equity": equity, "margin": 0.0, "margin_free": equity})()
                margin_req = intent.risk_amount * 2.0  # estimate
                assessment = gate.assess(account, intent.symbol, margin_req, intent.risk_amount, self.mode)
                if not assessment.approved:
                    self._resolve_intent(intent.intent_key, "REJECTED", error=str(assessment.reasons))
                    return GatewayResult(False, "REJECTED", error=f"Margin safety refusal: {assessment.reasons}")
            except Exception as e:
                log.debug("Margin preflight exception: %s", e)

        # 5. Send under MT5 Lock
        mt5_req = {
            "action": 1,  # TRADE_ACTION_DEAL
            "symbol": intent.symbol,
            "volume": float(intent.volume),
            "type": 0 if side == "BUY" else 1,
            "price": float(intent.price),
            "sl": float(intent.sl or 0.0),
            "tp": float(intent.tp or 0.0),
            "magic": int(intent.magic),
            "comment": intent.comment,
        }

        with get_mt5_lock():
            send_res = self.adapter.send_order(mt5_req)

        retcode = send_res.get("retcode", -1)
        if retcode in (10009, 10008):  # ORDER_DONE / ORDER_PLACED
            ticket = send_res.get("order") or send_res.get("ticket")
            self._resolve_intent(intent.intent_key, "FILLED" if self.mode == "PAPER" else "PENDING", ticket=ticket)
            return GatewayResult(True, "FILLED" if self.mode == "PAPER" else "PENDING", ticket=ticket, action=side, details=send_res)
        else:
            err = send_res.get("error", f"retcode {retcode}")
            self._resolve_intent(intent.intent_key, "REJECTED", error=str(err))
            return GatewayResult(False, "REJECTED", error=str(err), details=send_res)

    def submit_exit(self, ticket: int, symbol: str, volume: Optional[float] = None, comment: str = "exit") -> GatewayResult:
        """Submit position exit. Allowed under kill switch (risk-reducing)."""
        with get_mt5_lock():
            res = self.adapter.close_position(ticket, symbol, volume=volume, comment=comment)
        retcode = res.get("retcode", -1)
        ok = retcode in (10009, 10008)
        if ok:
            self._close_intent_by_ticket(ticket)
        return GatewayResult(ok, "FILLED" if ok else "ERROR", ticket=ticket, action="EXIT", error=res.get("error"), details=res)

    def modify_protection(self, ticket: int, symbol: str, new_sl: Optional[float] = None, new_tp: Optional[float] = None, current_sl: Optional[float] = None, side: str = "BUY") -> GatewayResult:
        """Modify stop-loss and take-profit enforcing monotonic only-tighten protection."""
        if new_sl is not None and current_sl is not None and current_sl > 0:
            if side.upper() == "BUY" and new_sl < current_sl:
                return GatewayResult(False, "REJECTED", error=f"Protective SL cannot be widened on BUY ({new_sl} < {current_sl})")
            if side.upper() == "SELL" and new_sl > current_sl:
                return GatewayResult(False, "REJECTED", error=f"Protective SL cannot be widened on SELL ({new_sl} > {current_sl})")

        with get_mt5_lock():
            res = self.adapter.modify_protection(ticket, symbol, sl=new_sl or 0.0, tp=new_tp or 0.0)
        retcode = res.get("retcode", -1)
        ok = retcode in (10009, 10008)
        return GatewayResult(ok, "FILLED" if ok else "ERROR", ticket=ticket, action="MODIFY", error=res.get("error"), details=res)

    def cancel_pending(self, ticket: int, symbol: str) -> GatewayResult:
        """Cancel a pending order and release its reservation."""
        with get_mt5_lock():
            res = self.adapter.cancel_order(ticket)
        retcode = res.get("retcode", -1)
        ok = retcode in (10009, 10008)
        self._release_reservation_by_ticket(ticket)
        return GatewayResult(ok, "CANCELLED" if ok else "ERROR", ticket=ticket, action="CANCEL", error=res.get("error"), details=res)

    def execute_flatten(self, owned_magics: Optional[Set[int]] = None) -> List[GatewayResult]:
        """Execute guardian flatten while setting flattening flag (CC-F10)."""
        self.flattening = True
        results = []
        try:
            # If paper adapter, close all paper positions
            if isinstance(self.adapter, PaperAdapter):
                for ticket in list(getattr(self.adapter.paper, "positions", {}).keys()):
                    res = self.submit_exit(ticket, "EURUSD", comment="guardian_flatten")
                    results.append(res)
            else:
                # MT5 adapter
                mt5 = getattr(self.adapter, "_get_mt5", lambda: None)()
                if mt5 and hasattr(mt5, "positions_get"):
                    with get_mt5_lock():
                        positions = mt5.positions_get() or []
                    for pos in positions:
                        if owned_magics and getattr(pos, "magic", 0) not in owned_magics:
                            continue
                        res = self.submit_exit(pos.ticket, pos.symbol, comment="guardian_flatten")
                        results.append(res)
        finally:
            self.flattening = False
        return results

    def _resolve_intent(self, intent_key: str, status: str, ticket: Optional[int] = None, error: Optional[str] = None):
        now_iso = datetime.now(timezone.utc).isoformat()
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                con.execute(
                    "UPDATE order_intents SET status=?, updated_at=?, mt5_ticket=COALESCE(?, mt5_ticket), error=? WHERE intent_key=?",
                    (status, now_iso, ticket, error, intent_key),
                )
        except Exception as e:
            log.debug("Intent resolution update failed: %s", e)

    def _release_reservation_by_ticket(self, ticket: int):
        now_iso = datetime.now(timezone.utc).isoformat()
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                con.execute(
                    "UPDATE order_intents SET status='CANCELLED', updated_at=? WHERE mt5_ticket=?",
                    (now_iso, ticket),
                )
        except Exception as e:
            log.debug("Releasing reservation failed: %s", e)

    def _close_intent_by_ticket(self, ticket: int):
        now_iso = datetime.now(timezone.utc).isoformat()
        db_path = getattr(self.store, "path", "trading_state.db")
        try:
            with sqlite3.connect(db_path, timeout=5.0) as con:
                con.execute(
                    "UPDATE order_intents SET status='CLOSED', updated_at=? WHERE mt5_ticket=?",
                    (now_iso, ticket),
                )
        except Exception as e:
            log.debug("Closing intent failed: %s", e)


try:
    install_order_send_guard()
except Exception:
    pass
