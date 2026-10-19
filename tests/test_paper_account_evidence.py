"""Regression: paper account-evidence contract (session-8 deadlock fix).

Paper sessions perform no authenticated MT5 login, so ``get_account_info()``
legitimately returns nothing and the decision protocol deadlocks every cycle
on "missing account evidence -> HOLD" (session-8 evidence: the model produced
a valid BUY plan but was forced to HOLD on the empty account object).

Contract pinned here:
  1. PAPER journal sessions get an honestly-labeled PAPER_SIMULATED account
     snapshot sourced from the same seeded RiskManager state the sizing/heat
     math already uses — the model can never mistake it for broker evidence.
  2. Without the paper flag (DEMO/LIVE), empty broker account info stays
     empty: missing account evidence must keep failing closed.
  3. A zero seed invents nothing: the backstop yields an empty object too.
  4. Wiring: LLMOrchestrator enables the backstop only for journal_mode
     == "PAPER".
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from operations.agent_operations import AgentOperations
from data.market_data_provider import MarketDataProvider
import data.market_data_provider as mdp
from mt5_trader import LLMOrchestrator
from core.trading_safety import TradingStore


def _orchestrator(journal_mode, tmp_path):
    """Build a real orchestrator on a temp-db store (holiday-fixture pattern)."""
    risk = MagicMock()
    risk.risk_per_trade = 0.01
    risk.open_positions = []
    db = str(tmp_path / f"orch_{journal_mode}.db")
    with _patch_store(db):
        # DEMO/LIVE journals refuse to open without an account fingerprint
        # (operations/journal_scope.py), so give them one.
        fingerprint = "test-account" if journal_mode in ("LIVE", "DEMO") else ""
        orch = LLMOrchestrator(
            MagicMock(), risk, MagicMock(), "api_key", enable_rl=False,
            journal_mode=journal_mode, account_fingerprint=fingerprint,
        )
    orch.store = TradingStore(path=db, mode=journal_mode or "PAPER", account_fingerprint=fingerprint)
    orch.operations = AgentOperations(db)
    return orch


def _patch_store(db):
    from unittest.mock import patch
    return patch(
        "mt5_trader.TradingStore",
        lambda **kwargs: TradingStore(
            path=db, **{k: v for k, v in kwargs.items() if k != "path"}))


def _paper_df(n: int = 100) -> pd.DataFrame:
    return pd.DataFrame({
        "open": np.linspace(1.08, 1.09, n),
        "high": np.linspace(1.085, 1.095, n),
        "low": np.linspace(1.075, 1.085, n),
        "close": np.linspace(1.08, 1.09, n),
        "tick_volume": [1000] * n,
        "spread": [1] * n,
        "real_volume": [0] * n,
    }, index=pd.date_range("2026-01-01", periods=n, freq="15min"))


def _provider(risk) -> MarketDataProvider:
    # Paper-authentic client: terminal-only session yields no account info.
    client = MagicMock()
    # Must be a real empty dict, not an unstubbed MagicMock: a Mock is truthy,
    # so the provider treated it as genuine broker account evidence and returned
    # a populated account dict, breaking the fail-closed assertions. These tests
    # exist precisely to prove that empty broker evidence stays empty.
    client.get_account_info.return_value = {}
    # Stub the api_manager: fetch_data otherwise performs live NewsAPI/FRED
    # calls that hang the suite on network latency. Those branches are
    # fail-soft, so empty returns are the correct hermetic shape - these tests
    # cover paper account evidence, not macro data.
    api = SimpleNamespace(
        get_news=lambda *a, **k: [],
        get_hot_topics=lambda *a, **k: [],
        get_fred_data=lambda *a, **k: {},
        get_status=lambda *a, **k: {},
    )
    provider = MarketDataProvider(client=client, risk_manager=risk, api_manager=api)
    # Guards against a regression where the ctor builds its own APIManager
    # and silently re-introduces live NewsAPI/FRED calls into the test.
    assert provider.api_manager is api
    return provider


def _risk(balance: float = 10000.0, equity: float = 10045.5) -> MagicMock:
    risk = MagicMock()
    risk.balance = balance
    risk.equity = equity
    # fetch_data() seeds paper account evidence from risk.current_equity()
    # (market_data_provider.py:1492). An unstubbed MagicMock returns a Mock,
    # and float(Mock) == 1.0, which passed the "> 0.0" guard and invented an
    # account dict even when the risk state was seeded with zero. Returning the
    # seeded equity keeps the test asserting what it means to assert: that a
    # zeroed risk state yields an EMPTY account, not a fabricated 1.0.
    risk.current_equity.return_value = equity
    risk.risk_per_trade = 0.01
    risk.max_drawdown = 0.05
    risk.open_positions = []
    return risk


def _fetch_toon(monkeypatch, provider: MarketDataProvider) -> dict:
    monkeypatch.setattr(
        mdp, "_get_fetch_ohlc", lambda: (lambda s, tf, count=100: _paper_df()))
    return provider.fetch_data("EURUSD", "M15")


def test_paper_flag_surfaces_labeled_simulated_account(monkeypatch):
    provider = _provider(_risk())
    provider.enable_paper_account_evidence()

    toon = _fetch_toon(monkeypatch, provider)

    acc = toon["account"]
    assert acc, "paper session must not deadlock on an empty account object"
    assert acc["mode"] == "PAPER_SIMULATED"
    assert acc["simulated"] is True
    assert acc["server"] == "PAPER_SIMULATED"
    # risk_manager_equity: paper equity comes from risk.current_equity()
    # (open floating P&L), not the raw balance seed it used to echo.
    assert acc["source"] == "risk_manager_equity"
    assert acc["balance"] == 10000.0
    assert acc["equity"] == 10045.5
    assert acc["margin_free"] == acc["equity"]


def test_no_flag_keeps_fail_closed_empty_account(monkeypatch):
    """DEMO/LIVE default: empty broker evidence must stay empty."""
    provider = _provider(_risk())

    toon = _fetch_toon(monkeypatch, provider)

    assert toon["account"] == {}


def test_zero_seed_invents_nothing(monkeypatch):
    """Nothing seeded: keep the empty object so decisions still fail closed."""
    provider = _provider(_risk(balance=0.0, equity=0.0))
    provider.enable_paper_account_evidence()

    toon = _fetch_toon(monkeypatch, provider)

    assert toon["account"] == {}


def test_orchestrator_enables_backstop_only_for_paper(tmp_path):
    paper = _orchestrator("PAPER", tmp_path)
    assert paper.data_provider.paper_account_evidence is True

    for mode in ("LIVE", "DEMO", None):
        orch = _orchestrator(mode, tmp_path)
        assert orch.data_provider.paper_account_evidence is False, mode
