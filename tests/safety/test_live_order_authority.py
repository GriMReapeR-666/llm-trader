"""Live entries need an ORDER_WRITE token from the launcher's live sandbox (PH2-N1).

Before: the orchestrator never passed a token, so every live planned entry
raised PermissionError, and the live urgency path had no capability gate at all.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from core.harness.sandbox import CapabilitySandbox, PermissionLevel


def _orch(mode="LIVE", sandbox_level=PermissionLevel.ORDER_WRITE, wired=True):
    from llm_orchestrator import LLMOrchestrator
    orch = LLMOrchestrator.__new__(LLMOrchestrator)
    orch.store = NS(scope={"mode": mode})
    if wired:
        orch.order_write_sandbox = CapabilitySandbox(default_level=sandbox_level)
    return orch


def test_live_token_is_short_lived_and_broker_scoped():
    token = _orch()._live_order_token()
    assert token.can_access(PermissionLevel.ORDER_WRITE, scope="broker")
    assert token.expires_at - datetime.now(timezone.utc) <= timedelta(minutes=2)


@pytest.mark.parametrize("kwargs", [
    {"wired": False},                                    # launcher never handed authority over
    {"sandbox_level": PermissionLevel.SIMULATION},       # paper/demo profile sandbox
    {"mode": "DEMO"}, {"mode": "PAPER"},                  # journal is not LIVE
], ids=["no-sandbox", "simulation-sandbox", "demo-journal", "paper-journal"])
def test_no_token_without_live_authority(kwargs):
    assert _orch(**kwargs)._live_order_token() is None


def test_fallback_harness_context_grants_nothing():
    orch = _orch(wired=False)
    orch.harness_ctx = NS(has=lambda k: True, get=lambda k: CapabilitySandbox(PermissionLevel.ORDER_WRITE))
    assert orch._live_order_token() is None


def test_live_urgency_order_requires_the_token(monkeypatch):
    from core import order_execution
    monkeypatch.setattr(order_execution, "_get_mt5", lambda: pytest.fail("broker touched before the gate"))
    with pytest.raises(PermissionError):
        order_execution.place_urgency_routed_order("EURUSD", "buy", 0.1, 1.09, 1.12, "x", paper=None)
    sim = CapabilitySandbox(PermissionLevel.SIMULATION).issue_token(PermissionLevel.SIMULATION, scopes=["broker"])
    with pytest.raises(PermissionError):
        order_execution.place_urgency_routed_order("EURUSD", "buy", 0.1, 1.09, 1.12, "x", paper=None, token=sim)


def _pending(token):
    from core.order_execution import place_pending_order
    return place_pending_order("EURUSD", "buy_limit", 1.09, 0.1, 1.08, 1.11, "x", None, magic=460001,
                               expires_at=datetime.now(timezone.utc) + timedelta(hours=1), token=token)


def test_live_pending_order_gate_accepts_a_live_token(monkeypatch):
    from core import order_execution
    with pytest.raises(PermissionError):
        _pending(None)
    expired = _orch()._live_order_token()
    expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(PermissionError):
        _pending(expired)
    # A valid token passes the capability gate; the next stop is the broker.
    monkeypatch.setattr(order_execution, "_get_mt5", lambda: (_ for _ in ()).throw(RuntimeError("broker reached")))
    with pytest.raises(RuntimeError, match="broker reached"):
        _pending(_orch()._live_order_token())


def test_launcher_wires_authority_only_for_live():
    import ast, pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "mt5_trader.py").read_text(encoding="utf-8")
    assignments = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Attribute) and t.attr == "order_write_sandbox" for t in n.targets)]
    assert len(assignments) == 1
    seg = src[src.rfind("\n", 0, src.find("orch.order_write_sandbox")) - 200: src.find("orch.order_write_sandbox")]
    assert 'getattr(args, "live", False)' in seg
