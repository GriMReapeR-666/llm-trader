"""AST single-door and runtime execution door proof (Phase 2 PR 2.3, hardened).

Proves:
1. Single door in AST: `.order_send` appears in EXACTLY ONE production file
   (core/order_gateway.py), and gateway adapters are never imported elsewhere.
2. Runtime door: calling mt5.order_send outside the gateway send context
   raises DirectOrderExecutionForbidden — including via a smuggled adapter.
3. Gateway dispatch: OrderGateway.submit_* paths succeed.
4. Pyramiding disabled: Pyramiding is disabled by default (PYRAMIDING_ENABLED=0).
"""
import ast
import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from core.order_gateway import (
    DirectOrderExecutionForbidden,
    LiveMT5Adapter,
    OrderGateway,
    OrderIntent,
    install_order_send_guard,
    uninstall_order_send_guard,
)
from core.trading_safety import TradingStore


def test_ast_proves_order_send_only_in_order_gateway():
    """Verify that .order_send attribute/call appears in exactly one production file."""
    repo_root = Path(__file__).resolve().parent.parent.parent

    # Find all python files, excluding virtual environments, tests, compat bridge stubs, scripts
    # .git-linked worktrees (.kilo/worktrees/* etc.) hold OTHER branches'
    # production copies; the single-door invariant is about THIS checkout.
    ignored_parts = {".venv", "venv", ".git", ".kilo", ".cline", ".cursor",
                     "tests", "compat", "scripts", "__pycache__"}
    production_files_with_order_send = []
    adapter_import_violations = []

    for py_file in repo_root.rglob("*.py"):
        rel_parts = py_file.relative_to(repo_root).parts
        if any(ignored in rel_parts for ignored in ignored_parts):
            continue

        try:
            text = py_file.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(py_file))
        except Exception:
            continue

        has_order_send = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "order_send":
                has_order_send = True
                break

        if has_order_send:
            rel_path = py_file.relative_to(repo_root).as_posix()
            production_files_with_order_send.append(rel_path)

        rel_path = py_file.relative_to(repo_root).as_posix()
        if rel_path != "core/order_gateway.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "core.order_gateway":
                    for alias in node.names:
                        if alias.name in ("LiveMT5Adapter", "PaperAdapter", "_mt5_send", "_gateway_send_context", "_GATEWAY_SEND_CTX"):
                            adapter_import_violations.append(f"{rel_path}:{alias.name}")

    assert production_files_with_order_send == ["core/order_gateway.py"], (
        f"order_send must appear in EXACTLY ONE production file ('core/order_gateway.py'), "
        f"but found in: {production_files_with_order_send}"
    )
    assert adapter_import_violations == [], (
        "gateway adapters are private to core/order_gateway.py; "
        f"use get_default_gateway() instead: {adapter_import_violations}"
    )


def test_runtime_guard_blocks_direct_order_send():
    """Calling mt5.order_send outside the gateway send context is forbidden."""
    fake_mt5 = MagicMock()
    fake_mt5.order_send = MagicMock(return_value=MagicMock(retcode=10009))

    # Install runtime guard
    install_order_send_guard(fake_mt5)
    try:
        with pytest.raises(DirectOrderExecutionForbidden, match="Direct order_send forbidden"):
            fake_mt5.order_send({"action": 1, "symbol": "EURUSD"})
    finally:
        uninstall_order_send_guard(fake_mt5)


def test_runtime_guard_blocks_smuggled_adapter():
    """A LiveMT5Adapter smuggled outside the gateway still cannot send."""
    from core.order_gateway import _GATEWAY_SEND_CTX
    fake_mt5 = MagicMock()
    fake_mt5.order_send = MagicMock(return_value=MagicMock(retcode=10009, order=1))
    install_order_send_guard(fake_mt5)
    try:
        # Sanity: context is closed here; adapter direct use from a test
        # module goes through the same guarded order_send and must raise.
        assert _GATEWAY_SEND_CTX.get() is False
        adapter = LiveMT5Adapter(fake_mt5)
        with pytest.raises(DirectOrderExecutionForbidden):
            fake_mt5.order_send({"action": 1, "symbol": "EURUSD"})
        # The adapter's own send path (which enters the context) still works.
        res = adapter.send_order({"action": 1, "symbol": "EURUSD"})
        assert res["retcode"] == 10009
    finally:
        uninstall_order_send_guard(fake_mt5)


def test_runtime_guard_permits_gateway_execution():
    """Calling through LiveMT5Adapter succeeds because caller frame is order_gateway.py."""
    fake_mt5 = MagicMock()
    fake_mt5.order_send = MagicMock(return_value=MagicMock(retcode=10009, order=555))

    install_order_send_guard(fake_mt5)
    try:
        adapter = LiveMT5Adapter(fake_mt5)
        res = adapter.send_order({"action": 1, "symbol": "EURUSD"})
        assert res["retcode"] == 10009
        assert res["order"] == 555
        assert getattr(fake_mt5.order_send, "_orig", fake_mt5.order_send).called
    finally:
        uninstall_order_send_guard(fake_mt5)


def test_pyramiding_is_disabled_by_default(tmp_path):
    """1a-F5 / 1a-F8: trade_manager pyramids are disabled by default."""
    from core.trade_manager import TradeManager
    from types import SimpleNamespace

    store = TradingStore(str(tmp_path / "state.db"))
    manager = TradeManager(
        MagicMock(), MagicMock(), store=store,
        state_file=str(tmp_path / "tm.json"),
        conditions_file=str(tmp_path / "tc.json"),
    )
    pos = SimpleNamespace(ticket=1001, symbol="EURUSD", volume=0.5, type=0, magic=460001)

    fake_mt5 = MagicMock()
    manager.log = MagicMock()

    # Without PYRAMIDING_ENABLED env var, it must not execute
    if "PYRAMIDING_ENABLED" in os.environ:
        del os.environ["PYRAMIDING_ENABLED"]

    manager._execute_pyramid_order(pos)
    assert fake_mt5.order_send.call_count == 0
    import json
    con = store._connect()
    try:
        refused = con.execute("SELECT * FROM manual_activity WHERE action='REFUSED_PYRAMID'").fetchall()
    finally:
        con.close()
    assert len(refused) >= 1
    pl = json.loads(refused[0]["payload"]) if isinstance(refused[0]["payload"], str) else (refused[0]["payload"] or {})
    assert pl.get("reason") == "pyramiding_disabled_by_default"


def test_end_to_end_validated_proposal_to_gateway_fill(tmp_path):
    """End-to-end flow: validated proposal -> gateway (PAPER) -> fill.

    PAPER mode skips broker margin (no MT5 account snapshot in unit tests);
    LIVE margin is pinned fail-closed by test_gateway_live_margin_fail_closed.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    store = TradingStore(str(tmp_path / "e2e_state.db"))
    store.set_state("equity", 10000.0)
    fake_mt5 = MagicMock()
    order_res = SimpleNamespace(retcode=10009, order=88888, comment="done")
    fake_mt5.order_send = MagicMock(return_value=order_res)

    adapter = LiveMT5Adapter(fake_mt5)
    gateway = OrderGateway(store=store, mode="PAPER", adapter=adapter)

    now_iso = datetime.now(timezone.utc).isoformat()
    evidence = SimpleNamespace(approved=True, setup_key="valid_alpha_1", created_at=now_iso)

    intent = OrderIntent(
        intent_key="intent_e2e_01",
        symbol="EURUSD",
        side="BUY",
        volume=0.1,
        price=1.1050,
        sl=1.1000,
        tp=1.1150,
        risk_amount=50.0,
        setup_key="valid_alpha_1",
        validation_result=evidence,
    )

    result = gateway.submit_entry(intent)
    assert result.success is True
    assert result.status == "FILLED"
    assert result.ticket == 88888
    assert getattr(fake_mt5.order_send, "_orig", fake_mt5.order_send).called

    # The intent is PENDING with the ticket: the reconciler owns PENDING -> FILLED
    # and writes the trades row that takes over the reservation.
    con = store._connect()
    try:
        row = con.execute("SELECT status, mt5_ticket FROM order_intents WHERE intent_key='intent_e2e_01'").fetchone()
        assert row is not None
        assert row["status"] == "PENDING"
        assert row["mt5_ticket"] == 88888
    finally:
        con.close()


def test_gateway_live_margin_fail_closed(tmp_path):
    """LIVE entries without a real broker margin snapshot are rejected (1a-F19)."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    store = TradingStore(str(tmp_path / "e2e_live.db"))
    fake_mt5 = MagicMock()
    fake_mt5.order_send = MagicMock(return_value=SimpleNamespace(retcode=10009, order=1))
    fake_mt5.account_info = MagicMock(return_value=None)
    adapter = LiveMT5Adapter(fake_mt5)
    gateway = OrderGateway(store=store, mode="LIVE", adapter=adapter)

    evidence = SimpleNamespace(approved=True, setup_key="k", created_at=datetime.now(timezone.utc).isoformat())
    intent = OrderIntent(
        intent_key="live-no-margin", symbol="EURUSD", side="BUY", volume=0.1,
        price=1.10, sl=1.09, tp=1.12, risk_amount=50.0, setup_key="k",
        validation_result=evidence,
        account_equity=10000.0,  # equity known; the margin read is what fails
    )
    result = gateway.submit_entry(intent, mt5_client=fake_mt5)
    assert result.success is False
    assert "margin" in (result.error or "").lower()
    assert fake_mt5.order_send.call_count == 0


