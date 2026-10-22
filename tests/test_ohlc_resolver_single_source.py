"""The OHLC fetcher must be resolved through ONE resolver, not two.

``intelligence/llm_orchestrator.py`` used to carry its own copy of the
``_get_fetch_ohlc`` resolver.  The copy preferred ``mt5_trader.fetch_ohlc``
whenever ``mt5_trader`` was present in ``sys.modules`` -- and whether it was
present depended on which test module had been *collected* first, not on
anything about the market data.

Measured failure (2026-10-22): ``tests/test_ablation_harness.py`` imports
``mt5_trader`` at module scope, so by the time
``tests/test_news_gate_integration.py`` ran, ``sys.modules["mt5_trader"]``
existed and the orchestrator's private resolver returned the *live* broker
fetcher instead of the test's stub.  The cycle could not confirm a completed
candle ("Unsupported timeframe M15"), the news gate never published, and
4/4 news-gate tests failed -- while the same file passed 4/4 standalone.

The provider owns the decision; every caller asks it.
"""
from __future__ import annotations

import sys

import pytest


def _orchestrator():
    import intelligence.llm_orchestrator as orch
    return orch


def _provider():
    import data.market_data_provider as mdp
    return mdp


def test_orchestrator_and_provider_agree_when_mt5_trader_is_imported(monkeypatch):
    """With mt5_trader in sys.modules both resolvers must return one answer."""
    import mt5_trader  # noqa: F401  (the import that used to flip the answer)
    orch = _orchestrator()
    provider = _provider()

    def _stub(symbol, timeframe, count=100):  # pragma: no cover - identity only
        return "STUB"

    monkeypatch.setattr(provider, "_get_fetch_ohlc", lambda: _stub)
    monkeypatch.setattr(provider, "fetch_ohlc", _stub)

    assert orch._get_fetch_ohlc() is _stub
    assert provider._get_fetch_ohlc() is _stub


def test_orchestrator_agrees_with_provider_without_mt5_trader(monkeypatch):
    """Same answer when mt5_trader is absent: the two cases must not differ."""
    orch = _orchestrator()
    provider = _provider()

    def _stub(symbol, timeframe, count=100):  # pragma: no cover - identity only
        return "STUB"

    monkeypatch.setattr(provider, "_get_fetch_ohlc", lambda: _stub)
    monkeypatch.setattr(provider, "fetch_ohlc", _stub)

    was_imported = sys.modules.get("mt5_trader")
    monkeypatch.delitem(sys.modules, "mt5_trader", raising=False)
    try:
        assert orch._get_fetch_ohlc() is _stub
    finally:
        if was_imported is not None:
            monkeypatch.setitem(sys.modules, "mt5_trader", was_imported)


def test_orchestrator_has_no_private_fetch_resolver_copy():
    """The orchestrator must not re-implement the resolution decision.

    A second copy of the resolver is the defect itself: it can drift, and it
    did.  The orchestrator may only delegate.
    """
    import inspect

    import data.market_data_provider as provider

    orch = _orchestrator()
    source = inspect.getsource(orch._get_fetch_ohlc)
    assert "market_data_provider" in source, (
        "the orchestrator resolver must delegate to the provider's resolver"
    )
    # No second opinion about which module owns the fetcher.
    assert "sys.modules.get(\"mt5_trader\")" not in source, (
        "the orchestrator must not decide the fetcher from sys.modules; "
        "that made the answer depend on import order"
    )
    assert provider._get_fetch_ohlc is not orch._get_fetch_ohlc

    # The terminal resolver is the same decision and must not be copied
    # either.
    mt5_source = inspect.getsource(orch._get_mt5)
    assert "market_data_provider" in mt5_source
    assert "sys.modules.get(\"mt5_trader\")" not in mt5_source
    assert provider._get_mt5 is not orch._get_mt5


def test_orchestrator_holds_no_import_time_fetcher():
    """The orchestrator must not capture a fetcher at import time.

    A module-level ``from ... import fetch_ohlc`` freezes the function
    object: every later override (a stub, a runtime switch) is invisible to
    the orchestrator, so the orchestrator and the provider answer
    differently -- and the difference shows up as a function of which test
    imported what first.
    """
    orch = _orchestrator()
    assert not hasattr(orch, "fetch_ohlc"), (
        "the orchestrator must resolve the fetcher at call time, not bind it "
        "at import time"
    )


def test_provider_patch_survives_mt5_trader_import(monkeypatch):
    """Import order must not change which fetcher the provider resolves.

    Regression (2026-10-22): the provider's resolver preferred
    ``mt5_trader.fetch_ohlc`` whenever that module was in ``sys.modules``,
    so a stub installed on the provider was silently ignored once an
    earlier test had imported ``mt5_trader`` -- the same test saw its stub
    in one order and the live broker fetcher in the other.  That is what
    broke ``tests/test_news_gate_integration.py`` after
    ``tests/test_ablation_harness.py``.
    """
    import mt5_trader  # noqa: F401  (the import that used to flip the answer)
    provider = _provider()

    def _stub(symbol, timeframe, count=100):  # pragma: no cover - identity only
        return "STUB"

    monkeypatch.setattr(provider, "fetch_ohlc", _stub)

    assert provider._get_fetch_ohlc() is _stub


def test_mt5_trader_name_is_a_live_view_of_the_provider(monkeypatch):
    """The two names must never disagree: one owner, one answer."""
    import mt5_trader
    provider = _provider()

    def _stub(symbol, timeframe, count=100):  # pragma: no cover - identity only
        return "STUB"

    monkeypatch.setattr(provider, "fetch_ohlc", _stub)

    assert mt5_trader.fetch_ohlc("EURUSD", "M15", count=1) == "STUB"
    assert provider._get_fetch_ohlc() is _stub


def test_deliberate_mt5_trader_override_still_wins(monkeypatch):
    """Replacing the re-export is an explicit override, not drift.

    ``tests/test_candle_claim_order.py`` stubs the fetcher on
    ``mt5_trader``; that seam must keep working now that the resolver
    stopped guessing from ``sys.modules``.
    """
    import mt5_trader
    provider = _provider()

    def _override(symbol, timeframe, count=100):  # pragma: no cover - identity only
        return "OVERRIDE"

    monkeypatch.setattr(mt5_trader, "fetch_ohlc", _override)

    assert provider._get_fetch_ohlc() is _override


@pytest.mark.parametrize("module_name", ["intelligence.llm_orchestrator", "data.market_data_provider"])
def test_resolver_is_callable_and_returns_a_fetcher(module_name):
    mod = sys.modules.get(module_name)
    if mod is None:
        import importlib
        mod = importlib.import_module(module_name)
    assert callable(mod._get_fetch_ohlc())
