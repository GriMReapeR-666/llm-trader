"""TradingView webhook bridge: receive TV alerts, journal them as advisory
order_intent rows, never execute.

Design contract (plan Phase 2, item 1):
- stdlib ``http.server`` only; no new dependencies.
- POST /alert with JSON body: symbol, action, price, timeframe, alert_id,
  plus optional tf, comment, volume, sl, tp.
- Auth: optional shared secret. When ``TV_WEBHOOK_SECRET`` is set, every
  request must carry ``X-TV-Token`` (or a JSON ``token`` field) matching it;
  mismatches are rejected with 401 BEFORE any journal write.
- Journaling: each accepted alert inserts an idempotent row in the durable
  journal's ``order_intents`` table directly (see ``_insert_advisory_row``)
  with ``status='ADVISORY'`` — a never-executed state, committed BEFORE the
  200 is written. Statuses outside the execution state machine are invisible to
  both the PENDING reconciler (``reconcile_pending_orders``) and the killswitch
  consumer (``operations.autonomy.cancel_pending_intents``), and the write
  deliberately bypasses the RiskController ``create_order_intent`` authority
  path (whose contract preconditions reject any other actor anyway).
- Ports: ``start(port=0)`` really means "ephemeral"; only ``port=None`` falls
  back to ``TV_WEBHOOK_PORT``/8055.  Two bridges must never share a socket
  address, because SO_REUSEADDR would let both bind it and each would answer
  alerts into the other's journal.
- The bridge itself never places orders: no broker SDK import, no order
  routing call sites.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs

log = logging.getLogger("TradingViewBridge")

DEFAULT_PORT = 8055
MAX_BODY_BYTES = 8 * 1024
_ALLOWED_ACTIONS = {"BUY", "SELL", "HOLD", "EXIT", "CLOSE"}

# advisory-only DB status: deliberately OUTSIDE ORDER_INTENT_TRANSITIONS
ADVISORY_STATUS = "ADVISORY"


class AlertValidationError(ValueError):
    """Raised for malformed alerts; maps to HTTP 400."""


class AlertAuthError(PermissionError):
    """Raised for bad/missing auth tokens; maps to HTTP 401."""


def _redact_token(body: Dict[str, Any]) -> Dict[str, Any]:
    safe = dict(body)
    safe.pop("token", None)
    safe.pop("secret", None)
    return safe


def compute_alert_key(body: Dict[str, Any]) -> str:
    """Deterministic intent key: dedupes replayed TV webhooks."""
    parts = [str(body.get("alert_id") or ""), str(body.get("symbol") or ""),
             str(body.get("action") or ""), str(body.get("timeframe") or ""),
             str(body.get("bar_time") or ""), str(body.get("ts") or "")]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"tv:{body.get('symbol','').upper() or 'UNKNOWN'}:{digest}"


def parse_alert(raw_json_text: str) -> Dict[str, Any]:
    """Parse and validate a raw webhook body. Raises AlertValidationError."""
    try:
        body = json.loads(raw_json_text)
    except json.JSONDecodeError as exc:
        raise AlertValidationError(f"invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise AlertValidationError("payload must be a JSON object")

    symbol = str(body.get("symbol") or "").strip().upper()
    action = str(body.get("action") or "").strip().upper()
    if not symbol:
        raise AlertValidationError("symbol is required")
    if action not in _ALLOWED_ACTIONS:
        raise AlertValidationError(f"action must be one of {sorted(_ALLOWED_ACTIONS)}")

    price = body.get("price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError) as exc:
        raise AlertValidationError(f"price must be numeric: {exc}") from exc
    if price is not None and (price <= 0 or price != price or price in (float("inf"), float("-inf"))):
        raise AlertValidationError("price must be a positive finite number")

    timeframe = str(body.get("timeframe") or body.get("tf") or "").strip()[:12]
    alert_id = str(body.get("alert_id") or "").strip()[:120]
    comment = str(body.get("comment") or "").strip()[:200]

    parsed: Dict[str, Any] = {
        "symbol": symbol,
        "action": action,
        "price": price,
        "timeframe": timeframe,
        "alert_id": alert_id,
        "comment": comment,
    }
    for optional_key in ("volume", "sl", "tp"):
        if body.get(optional_key) is not None:
            try:
                parsed[optional_key] = float(body[optional_key])
            except (TypeError, ValueError) as exc:
                raise AlertValidationError(f"{optional_key} must be numeric") from exc
    parsed = _redact_token(parsed)
    return parsed


def check_auth(token_from_request: Optional[str], expected_secret: Optional[str]) -> None:
    if not expected_secret:
        return  # auth disabled (explicit operator choice)
    if not token_from_request or token_from_request != expected_secret:
        raise AlertAuthError("invalid or missing X-TV-Token")


class TradingViewBridgeHandler(BaseHTTPRequestHandler):
    """HTTP surface. One class, one route; every failure mode maps to a
    non-executing response."""

    # The bridge that OWNS this socket, read off the server instance rather
    # than off the class.  It used to be one process-wide mutable class slot
    # overwritten by the most recent ``start()``; with two bridges in one
    # process the older server journalled every alert into the newer bridge's
    # database, answering 200 with an intent_key that existed in a file the
    # caller never pointed at.  ``self.server`` is set by socketserver before
    # any do_* method runs, so this resolves per request, per server.
    @property
    def bridge(self) -> "TradingViewBridge":
        return getattr(self.server, "bridge", None)  # type: ignore[return-value]

    # -- quiet logging: one short line per request, no BaseHTTPRequestHandler spam
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        log.debug("%s %s", self.address_string(), fmt % args)

    def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        # Health probe path only: /health returns "ok"; never touches journal.
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(200, {"ok": True, "advisory_only": True})
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/alert", "/alerts"):
            self._send_json(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "body too large or empty"})
            return
        raw = self.rfile.read(length).decode("utf-8", errors="replace")

        # 1) Auth BEFORE any parsing or journal write.
        token = self.headers.get("X-TV-Token")
        if not token:
            qs = parse_qs(parsed.query)
            token = (qs.get("token") or [None])[0]
        try:
            check_auth(token, self.bridge.secret)
        except AlertAuthError as exc:
            self._send_json(401, {"ok": False, "error": str(exc)})
            return

        # 2) Validate schema.
        try:
            alert = parse_alert(raw)
        except AlertValidationError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return

        # 3) Journal as ADVISORY (never executed).
        try:
            key, inserted, record = self.bridge.journal_alert(alert)
        except Exception as exc:
            log.exception("journal_alert failed")
            self._send_json(500, {"ok": False, "error": f"journal write failed: {exc}"})
            return

        self._send_json(200, {
            "ok": True, "intent_key": key, "inserted": inserted,
            "advisory_only": True, "execution_authority": False,
        })


class TradingViewBridge:
    """Owns the HTTP server + journal coupling per bridge instance."""

    def __init__(self, secret: Optional[str] = None, journal_path: str = "trading_state.db"):
        self.secret = (secret or os.getenv("TV_WEBHOOK_SECRET") or "").strip()
        env_path = os.getenv("LLM_TRADER_JOURNAL_PATH")
        self.journal_path = env_path or journal_path
        self._ops_factory = None  # injectable for tests
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- journaling via AgentOperations ---------------------------------
    def _operations(self):
        from operations.agent_operations import AgentOperations
        return AgentOperations(self.journal_path)

    def journal_alert(self, alert: Dict[str, Any]) -> Tuple[str, bool, Dict[str, Any]]:
        """Insert an advisory order_intent record. Idempotent, never executed.

        Uses the advisory INSERT INSTEAD OF create_order_intent (which is
        RiskController-gated: creating rows through the deterministic
        execution path would both violate the contract and make the row
        eligible for the PENDING reconciler)."
        """
        ops = self._operations()
        intent_key = compute_alert_key(alert)
        payload = {
            "advisory": True,
            "source": "tradingview_webhook",
            "execution_authority": False,
            "alert": alert,
        }
        # Unconditional: the direct ADVISORY INSERT below is the ONLY write
        # path this bridge may take.  This used to probe ORDER_INTENT_TRANSITIONS
        # for an "ADVISORY" key and, on a future hit, fall back to
        # create_order_intent(..., actor="RiskController") - forging the
        # deterministic risk principal for a row that originated from a
        # TradingView webhook, which is precisely the execution-authority leak
        # this module exists to prevent.  A future state-machine change must
        # add a real advisory-aware method, never reuse the RiskController one.
        inserted, record = self._insert_advisory_row(ops, intent_key, payload)
        return intent_key, bool(inserted), record

    @staticmethod
    def _insert_advisory_row(ops, intent_key: str, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """Direct INSERT (status='ADVISORY') when no advisory-aware primary
        method exists. Mirrors create_order_intent's INSERT OR IGNORE, but
        emits a non-executorial event and skips contract precondition calls
        (those raise for non RiskController actors)."""
        from datetime import datetime, timezone
        # ``_con()`` returns a plain ``sqlite3.Connection``, not a context
        # manager: ``with con:`` COMMITs on clean exit, which is what makes the
        # advisory row durable before the HTTP 200 leaves do_POST.  An earlier
        # draft also called ``ops._con().__enter__()`` here, opening a second
        # connection that was never used, never closed and never committed -
        # deleted.
        with ops._con() as con:
            now = datetime.now(timezone.utc).isoformat()
            inserted = con.execute(
                "INSERT OR IGNORE INTO order_intents"
                "(intent_key,created_at,updated_at,run_id,proposal_id,status,payload) "
                "VALUES(?,?,?,?,?,?,?)",
                (intent_key, now, now, "", "", ADVISORY_STATUS,
                 json.dumps(payload, default=str)),
            ).rowcount
            row = con.execute(
                "SELECT * FROM order_intents WHERE intent_key=?", (intent_key,)
            ).fetchone()
        try:
            ops.event(
                actor="TradingViewBridge",
                event_type="advisory_intent_created" if inserted else "advisory_intent_deduplicated",
                payload={"intent_key": intent_key, "source": "tradingview_webhook",
                         "advisory_only": True, "execution_authority": False},
                correlation_id=intent_key,
                severity="INFO",
            )
        except Exception as exc:
            # Non-fatal on purpose: the ADVISORY row is already committed and a
            # failed observability write must not undo it or 500 a valid alert.
            # But this repo has a documented history of bare-except silent
            # failures (AUDIT_ENGINE_E2E.md item 11), and swallowing this at
            # debug level leaves a journal row with no audit event at all - an
            # operator reading the event stream would never learn the alert
            # existed.  Warn loudly instead.
            log.warning("advisory journal event emission failed: %s", exc)
        inserted_bool = bool(inserted)
        record = dict(row) if row is not None else {}
        return inserted_bool, record

    # -- server lifecycle ------------------------------------------------
    def start(self, host: str = "127.0.0.1", port: Optional[int] = None) -> Dict[str, Any]:
        if self._server is not None:
            return {"ok": True, "note": "already running",
                    "port": self._server.server_address[1]}
        # ``port=0`` means "let the OS pick an ephemeral port" and must NOT be
        # folded into the default by a truthiness test - ``0 or 8055`` is 8055,
        # so every caller asking for an ephemeral port silently shared one fixed
        # port instead.  That is not merely inconvenient: HTTPServer sets
        # ``allow_reuse_address``, i.e. SO_REUSEADDR, which on Windows lets a
        # SECOND process bind a port that is already bound.  Two bridges then
        # listen on the same socket address and the stack delivers each request
        # to an arbitrary one of them, so the loser of the race still answers
        # 200 with an intent_key that was written to the other bridge's journal.
        # Only ``None`` falls back to the env/default.
        if port is None:
            port = int(os.getenv("TV_WEBHOOK_PORT") or DEFAULT_PORT)
        # Attach the bridge to THIS server instead of to the handler class: a
        # process-wide handler slot meant a second ``start()`` silently
        # re-pointed the first server's requests at the second bridge's journal
        # - the in-process twin of the port race above, and it breaks the
        # "exactly one ADVISORY row exists before the 200" guarantee either way.
        server = ThreadingHTTPServer((host, port), TradingViewBridgeHandler)
        server.bridge = self
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="TradingViewBridge")
        self._thread.start()
        log.info("TradingView bridge listening on http://%s:%s/alert (advisory only)",
                 host, server.server_address[1])
        return {"ok": True, "host": host, "port": server.server_address[1]}

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None


def start_bridge(host: str = "127.0.0.1", port: Optional[int] = None,
                 secret: Optional[str] = None) -> TradingViewBridge:
    bridge = TradingViewBridge(secret=secret)
    bridge.start(host=host, port=port)
    return bridge


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import argparse
    parser = argparse.ArgumentParser(description="TradingView webhook bridge (advisory journal only)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--secret", default=None,
                        help="shared secret; falls back to TV_WEBHOOK_SECRET env")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    start_bridge(host=args.host, port=args.port, secret=args.secret)
    print("TradingView bridge running; Ctrl+C to stop")
    import time as _t
    while True:
        _t.sleep(60)
