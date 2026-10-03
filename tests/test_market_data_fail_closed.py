from types import SimpleNamespace

import pytest

import mt5_trader


def test_authenticated_terminal_missing_bars_does_not_use_external_or_synthetic(monkeypatch):
    terminal = SimpleNamespace()
    fake_mt5 = SimpleNamespace(
        terminal_info=lambda: terminal,
        symbol_info=lambda symbol: SimpleNamespace(visible=True),
        symbol_select=lambda symbol, visible: True,
        copy_rates_from_pos=lambda symbol, timeframe, start, count: None,
    )
    monkeypatch.setattr(mt5_trader, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_trader, "tf_const", lambda timeframe: 15)

    with pytest.raises(RuntimeError, match="refusing external or synthetic fallback"):
        mt5_trader.fetch_ohlc("EURUSD", "M15", count=10)


def test_unavailable_terminal_does_not_use_yahoo_or_dummy_data(monkeypatch):
    fake_mt5 = SimpleNamespace(
        terminal_info=lambda: None,
        symbol_info=lambda symbol: None,
        copy_rates_from_pos=lambda symbol, timeframe, start, count: None,
    )
    monkeypatch.setattr(mt5_trader, "mt5", fake_mt5)
    monkeypatch.setattr(mt5_trader, "tf_const", lambda timeframe: 15)

    with pytest.raises(RuntimeError, match="refusing external or synthetic fallback"):
        mt5_trader.fetch_ohlc("EURUSD", "M15", count=10)


def test_paper_provider_requires_explicit_fixture_instead_of_random_market_data(monkeypatch):
    # Force the "no data plane" condition explicitly, as the two tests above do.
    # Without this, _get_mt5() returns whatever the host has: on a machine with
    # the MetaTrader5 SDK importable, terminal_info() succeeds, the fail-closed
    # guard's premise is false, and fetch_data() runs on into unrelated
    # attributes this test never set (it builds the provider via __new__ and
    # only assigns .client), surfacing as a confusing AttributeError instead of
    # the RuntimeError this test is asserting.
    fake_mt5 = SimpleNamespace(
        terminal_info=lambda: None,
        symbol_info=lambda symbol: None,
        copy_rates_from_pos=lambda symbol, timeframe, start, count: None,
    )
    monkeypatch.setattr(mt5_trader, "mt5", fake_mt5)

    provider = mt5_trader.MarketDataProvider.__new__(mt5_trader.MarketDataProvider)
    provider.client = None
    with pytest.raises(RuntimeError, match="refusing synthetic candles/DOM/news"):
        provider.fetch_data("EURUSD", "M15")
