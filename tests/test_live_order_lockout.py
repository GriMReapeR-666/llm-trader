"""Pin tests for the env-level live-trading lockout.

Absence of --paper is not an operator decision, so live order placement
additionally requires REQUIRE_LIVE_CONFIRMATION enabled AND
LIVE_CONFIRMATION_TOKEN set to the exact acknowledgment sentence. The gate
fires at the start of ``_build_execution_plan``'s live branch — before ANY
live-broker call — and raises the same ValueError the pre-submit caller
already converts into an auditable HOLD + REJECTED lifecycle row.

Pinned here:
- gate semantics: fail-closed on unset/partial config; both-envs authorization;
  case/whitespace tolerance; explicit "0" stays locked;
- plan-builder conversion: live branch with lockout raises ValueError and
  journals a WARNING event + REJECTED lifecycle row; authorized env proceeds
  into the live branch (quote fetch attempted); paper branch never gates;
- the audit payload never leaks the token value.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "intelligence"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from mt5_trader import LLMOrchestrator

FLAG = "REQUIRE_LIVE_CONFIRMATION"
TOKEN_ENV = "LIVE_CONFIRMATION_TOKEN"
TOKEN = "I_UNDERSTAND_LIVE_ORDERS_WILL_BE_PLACED"


class _Ops:
    def __init__(self):
        self.events = []

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))


class _Store:
    def __init__(self):
        self.lifecycle = []

    def log_lifecycle_event(self, *args, **kwargs):
        self.lifecycle.append((args, kwargs))


class _Proposal:
    def __init__(self):
        self.symbol = "EURUSD"
        self.side = "BUY"
        self.entry = 1.1000
        self.stop_loss = 1.0950
        self.take_profit = 1.1100

    def setup_key(self):
        return "test_setup"


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    return monkeypatch


def _orchestrator():
    o = LLMOrchestrator.__new__(LLMOrchestrator)
    o.operations = _Ops()
    o.store = _Store()
    o.run_id = "run"
    return o


def _gate():
    """Bare skeleton: _live_orders_authorized only reads class attrs + env."""
    return LLMOrchestrator.__new__(LLMOrchestrator)


def _proposal_stub():
    return _Proposal()


def test_gate_fail_closed_when_unset(clean_env):
    ok, why = LLMOrchestrator._live_orders_authorized(_gate())
    assert ok is False
    assert FLAG in why


def test_gate_fail_closed_on_flag_without_token(clean_env):
    clean_env.setenv(FLAG, "1")
    ok, why = LLMOrchestrator._live_orders_authorized(_gate())
    assert ok is False
    assert TOKEN_ENV in why


def test_gate_fail_closed_on_wrong_token(clean_env):
    clean_env.setenv(FLAG, "true")
    clean_env.setenv(TOKEN_ENV, "yes i am sure")
    ok, why = LLMOrchestrator._live_orders_authorized(_gate())
    assert ok is False
    assert "missing or incorrect" in why


def test_gate_authorized_with_both_envs(clean_env):
    clean_env.setenv(FLAG, "true")
    clean_env.setenv(TOKEN_ENV, TOKEN)
    ok, why = LLMOrchestrator._live_orders_authorized(_gate())
    assert ok is True and why == ""


def test_gate_accepts_aliases_and_tolerates_whitespace(clean_env):
    for flag in ("1", "yes", "on", "TRUE"):
        clean_env.setenv(FLAG, flag)
        clean_env.setenv(TOKEN_ENV, f"  {TOKEN}  ")
        ok, _ = LLMOrchestrator._live_orders_authorized(_gate())
        assert ok is True, flag


def test_gate_stays_locked_on_explicit_zero(clean_env):
    clean_env.setenv(FLAG, "0")
    clean_env.setenv(TOKEN_ENV, TOKEN)
    ok, _ = LLMOrchestrator._live_orders_authorized(_gate())
    assert ok is False


def test_lockout_notice_never_leaks_token():
    notice = LLMOrchestrator._live_order_lockout_notice("some reason")
    assert TOKEN not in str(notice)
    assert notice["execution_authority"] is False
    assert notice["paper_mode"] is True
    assert notice["env_flag"] == FLAG and notice["token_env"] == TOKEN_ENV


def test_build_execution_plan_lockout_raises_and_journals(clean_env):
    o = _orchestrator()
    clean_env.setenv(FLAG, "1")  # flag without token: still locked
    with pytest.raises(ValueError) as excinfo:
        o._build_execution_plan(_proposal_stub(), {}, paper_mode=False, volume=0.01)
    assert "live order lockout" in str(excinfo.value)
    assert any(e[0][1] == "live_order_lockout" for e in o.operations.events), \
        "lockout must emit an operational event"
    assert o.store.lifecycle and o.store.lifecycle[0][0][2] == "REJECTED", \
        "lockout must append a REJECTED lifecycle row"
    assert o.store.lifecycle[0][0][3] == "live_order_lockout"
    assert TOKEN not in str(o.operations.events) + str(o.store.lifecycle)


def test_build_execution_plan_authorized_env_reaches_live_branch(clean_env):
    """With both envs set, the gate must NOT fire: the live branch proceeds
    and fails on live-broker plumbing (quote/policy/mt5 availability) —
    any exception EXCEPT the lockout is acceptable here."""
    o = _orchestrator()
    clean_env.setenv(FLAG, "1")
    clean_env.setenv(TOKEN_ENV, TOKEN)
    with pytest.raises(Exception) as excinfo:
        o._build_execution_plan(_proposal_stub(), {}, paper_mode=False, volume=0.01)
    assert "live order lockout" not in str(excinfo.value)
    assert not o.store.lifecycle


def test_build_execution_plan_paper_mode_never_gates(clean_env):
    """The paper branch (paper quote from toon DOM) must run regardless of env.
    Entry sits AWAY from the quote (a valid pending limit), not at/through it."""
    o = _orchestrator()
    toon = {"dom": {"best_bid": 1.0999, "best_ask": 1.1001}}
    proposal = _proposal_stub()
    proposal.entry = 1.0900        # below the quote: valid BUY limit zone
    proposal.stop_loss = 1.0850
    proposal.take_profit = 1.1050
    plan, preflight = o._build_execution_plan(
        proposal, toon, paper_mode=True, volume=0.01)
    assert preflight["mode"] == "paper_simulation"
    assert plan is not None
