"""TradingView webhook bridge (plan Phase 2, item 1).

Contract under test:
- parse_alert rejects malformed payloads (schema validation)
- check_auth rejects bad/missing tokens and accepts the correct one
- journaling via the live HTTP endpoint creates and dedupes ADVISORY
  order_intent rows through the real journal
- advisory rows are invisible to both PENDING reconciler and killswitch
  consumers (never executed)
- no MT5 import anywhere in the bridge module (structural guarantee)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from core._import_root import install as install_import_root
install_import_root()
for _pkg in ("core", "intelligence", "operations", "research", "data", "ui"):
    _p = os.path.join(ROOT, _pkg)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import data.tradingview_bridge as tvb  # noqa: E402
from data.tradingview_bridge import (  # noqa: E402
    TradingViewBridge, parse_alert, check_auth, compute_alert_key,
    AlertAuthError, AlertValidationError,
)


class TestParseValidation:
    def test_valid_alert_parses(self):
        alert = parse_alert('{"symbol": "EURUSD", "action": "BUY", "price": 1.0855,'
                            ' "timeframe": "M15", "alert_id": "A1"}')
        assert alert["symbol"] == "EURUSD"
        assert alert["action"] == "BUY"
        assert alert["price"] == pytest.approx(1.0855)
        assert "token" not in alert and "secret" not in alert

    def test_alternate_tf_key_map_to_timeframe(self):
        alert = parse_alert('{"symbol": "XAUUSD", "action": "SELL", "tf": "H1"}')
        assert alert["timeframe"] == "H1"

    def test_rejects_bad_json(self):
        with pytest.raises(AlertValidationError, match="invalid JSON"):
            parse_alert("not json at all")

    def test_rejects_non_object(self):
        with pytest.raises(AlertValidationError, match="JSON object"):
            parse_alert("[1,2,3]")

    def test_rejects_missing_symbol(self):
        with pytest.raises(AlertValidationError, match="symbol is required"):
            parse_alert('{"action": "BUY", "price": 1.0}')

    def test_rejects_unknown_action(self):
        with pytest.raises(AlertValidationError, match="action must be"):
            parse_alert('{"symbol": "EURUSD", "action": "YELL", "price": 1.0}')

    def test_rejects_non_numeric_price(self):
        with pytest.raises(AlertValidationError, match="price must be numeric"):
            parse_alert('{"symbol": "EURUSD", "action": "BUY", "price": "expensive"}')

    def test_rejects_negative_or_inf_price(self):
        with pytest.raises(AlertValidationError):
            parse_alert('{"symbol": "EURUSD", "action": "BUY", "price": -1}')
        with pytest.raises(AlertValidationError):
            parse_alert('{"symbol": "EURUSD", "action": "BUY", "price": 1e999}')

    def test_rejects_non_numeric_optional_fields(self):
        with pytest.raises(AlertValidationError, match="volume"):
            parse_alert('{"symbol":"EURUSD","action":"BUY","price":1.0,"volume":"lots"}')


class TestAuth:
    def test_bad_token_rejected(self):
        with pytest.raises(AlertAuthError):
            check_auth("wrong", "s3cret")

    def test_missing_token_rejected(self):
        with pytest.raises(AlertAuthError):
            check_auth(None, "s3cret")

    def test_correct_token_accepted(self):
        check_auth("s3cret", "s3cret")

    def test_no_secret_configured_allows_through(self):
        # explicit operator choice: when no secret set, auth is disabled.
        check_auth(None, None)


# ---------------------------------------------------------------------------
# End-to-end HTTP + journal integration (real AgentOperations journal, real
# ThreadingHTTPServer on an ephemeral port)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def bridge_and_journal(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tvbridge")
    journal_path = str(tmp / "tv_journal.db")
    from operations.agent_operations import AgentOperations
    ops = AgentOperations(journal_path)

    bridge = TradingViewBridge(secret="test-secret", journal_path=journal_path)
    bridge._operations = lambda: ops
    started = bridge.start(host="127.0.0.1", port=0)   # ephemeral port
    port = started["port"]
    base = f"http://127.0.0.1:{port}"
    try:
        yield base, bridge, ops, journal_path
    finally:
        bridge.stop()


def _post(base: str, path: str, body: bytes, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


GOOD_BODY = json.dumps({
    "symbol": "EURUSD", "action": "BUY", "price": 1.0855,
    "timeframe": "M15", "alert_id": "test-1",
}).encode("utf-8")


class TestHttpEndpoint:
    def test_health_endpoint_reports_advisory_only(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        code, payload = _get(base, "/health")
        assert code == 200
        assert payload["ok"] is True
        assert payload["advisory_only"] is True

    def test_unknown_path_404s(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        code, _ = _get(base, "/example")
        assert code == 404

    def test_missing_token_is_401_before_any_journal_write(self, bridge_and_journal):
        base, bridge, ops, journal_path = bridge_and_journal
        before = int(ops.event if False else 0)  # ops has no count; use table count
        con = sqlite3.connect(journal_path)
        before_rows = con.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0]
        con.close()
        code, payload = _post(base, "/alert", GOOD_BODY, {})
        assert code == 401
        assert payload["ok"] is False
        con = sqlite3.connect(journal_path)
        after_rows = con.execute("SELECT COUNT(*) FROM order_intents").fetchone()[0]
        con.close()
        assert after_rows == before_rows, "rejected request must not journal"

    def test_bad_token_is_401(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        code, payload = _post(base, "/alert", GOOD_BODY, {"X-TV-Token": "wrong"})
        assert code == 401
        assert "invalid or missing" in payload["error"]

    def test_malformed_body_is_400(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        code, payload = _post(base, "/alert", b"{not json}",
                              {"X-TV-Token": "test-secret"})
        assert code == 400
        assert payload["ok"] is False

    def test_valid_alert_journals_advisory_row(self, bridge_and_journal):
        base, bridge, ops, journal_path = bridge_and_journal
        code, payload = _post(base, "/alert", GOOD_BODY,
                              {"X-TV-Token": "test-secret"})
        assert code == 200
        assert payload["ok"] is True
        assert payload["advisory_only"] is True
        assert payload["execution_authority"] is False

        con = sqlite3.connect(journal_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM order_intents WHERE intent_key=?",
            (payload["intent_key"],)).fetchall()
        con.close()
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "ADVISORY"
        body = json.loads(row["payload"])
        assert body["advisory"] is True
        assert body["source"] == "tradingview_webhook"
        assert body["execution_authority"] is False
        assert body["alert"]["symbol"] == "EURUSD"

    def test_replayed_alert_deduplicates(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        headers = {"X-TV-Token": "test-secret"}
        # Self-contained alert id: immune to cross-test dedupe of prior posts.
        replay_body = json.dumps({
            "symbol": "XAUUSD", "action": "SELL", "price": 2312.5,
            "timeframe": "H1", "alert_id": "replay-unique-1",
        }).encode("utf-8")
        first_code, first = _post(base, "/alert", replay_body, headers)
        assert first_code == 200
        con_path = bridge_and_journal[3]
        con = sqlite3.connect(con_path)
        rows_after_first = con.execute(
            "SELECT COUNT(*) FROM order_intents WHERE intent_key=?",
            (first["intent_key"],)).fetchone()[0]
        con.close()
        assert rows_after_first == 1

        second_code, second = _post(base, "/alert", replay_body, headers)
        assert second_code == 200
        assert second["inserted"] is False
        con = sqlite3.connect(con_path)
        rows_after_second = con.execute(
            "SELECT COUNT(*) FROM order_intents WHERE intent_key=?",
            (first["intent_key"],)).fetchone()[0]
        con.close()
        assert rows_after_second == 1

    def test_oversized_body_is_413(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        big = json.dumps({"symbol": "EURUSD", "action": "BUY", "price": 1.0,
                          "comment": "x" * (16 * 1024)}).encode("utf-8")
        code, _ = _post(base, "/alert", big, {"X-TV-Token": "test-secret"})
        assert code == 413

    def test_empty_body_is_413(self, bridge_and_journal):
        base, *_ = bridge_and_journal
        code, _ = _post(base, "/alert", b"", {"X-TV-Token": "test-secret"})
        assert code == 413


class TestNeverExecutedContract:
    def test_advisory_row_invisible_to_pending_reconciler_and_killswitch(self, bridge_and_journal):
        """The two execution-time consumers of order_intents must not see
        advisory rows: reconcile_pending_orders iterates statuses
        (``PENDING``, ``UNRESOLVED``), and operations-autonomy killswitch
        cancel_pending_intents fetches ``(PENDING,)``. Both invisible.

        Self-sufficient: inserts its own advisory row via journal_alert
        (the module-scoped fixture is per-xdist-worker, so no test may
        depend on rows written by tests on another worker)."""
        base, bridge, ops, journal_path = bridge_and_journal
        key, inserted, record = bridge.journal_alert({
            "symbol": "USDJPY", "action": "SELL", "price": 149.50,
            "timeframe": "M30", "alert_id": "contract-probe-1",
        })
        assert inserted is True
        assert record.get("status") == "ADVISORY"
        # Both consumers' status filters:
        pending_view = ops.order_intents(("PENDING",)) + ops.order_intents(("UNRESOLVED",))
        assert all(item["status"] != "ADVISORY" for item in pending_view)

    def test_resolve_order_intent_rejects_advisory_state(self, bridge_and_journal):
        """A state outside ORDER_INTENT_TRANSITIONS must be unresolvable: no
        execution engine should be able to move an advisory row into a
        fill/cancel/expire path."""
        base, bridge, ops, _ = bridge_and_journal
        key, _inserted, _record = bridge.journal_alert({
            "symbol": "GBPUSD", "action": "BUY", "price": 1.2700,
            "timeframe": "M15", "alert_id": "contract-probe-2",
        })
        with pytest.raises(ValueError):
            ops.resolve_order_intent(key, "FILLED")

    def test_bridge_module_has_no_mt5_import(self):
        """Structural guarantee: the bridge never touches the broker.
        Checks real code shapes (import lines, attribute access) and stops
        ignoring mt5_* prefixes, because the bridge legitimately has none."""
        import data.tradingview_bridge as mod
        source = open(mod.__file__, "r", encoding="utf-8").read()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("#", '"', "'")):
                continue
            assert "import MetaTrader5" not in stripped
            assert "mt5." not in stripped
            assert "MetaTrader5" not in stripped


class TestAlertKeyDedup:
    def test_same_alert_same_key(self):
        a = {"symbol": "EURUSD", "action": "BUY", "price": 1.0855,
             "timeframe": "M15", "alert_id": "same", "ts": "123"}
        b = {"symbol": "EURUSD", "action": "BUY", "price": 1.0855,
             "timeframe": "M15", "alert_id": "same", "ts": "123"}
        assert compute_alert_key(a) == compute_alert_key(b)

    def test_different_alerts_different_keys(self):
        a = {"symbol": "EURUSD", "action": "BUY", "price": 1.0855,
             "timeframe": "M15", "alert_id": "a"}
        b = {"symbol": "EURUSD", "action": "SELL", "price": 1.0855,
             "timeframe": "M15", "alert_id": "a"}
        assert compute_alert_key(a) != compute_alert_key(b)
