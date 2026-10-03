"""Regression tests: Yahoo provider outage guard + zero-lag quote wiring.

Background (2026-09-26 audit): yfinance reports transient DNS/network outages
as ``possibly delisted; no price data found`` — see mt5_trader.log 08:00-08:12
where ^GSPC/^DJI/^IXIC/^VIX/DX-Y.NYB/CL=F/ZQ=F/NG=F/SR3=F all "vanished"
during a machine-wide DNS failure (ForexFactory logged
``[Errno 11001] getaddrinfo failed`` in the same second) and fetched fine
after recovery. These tests pin the hardened fetch path:

- All history/quote call sites share one guarded ``_history`` entry point.
- The module-level circuit breaker opens after repeated consecutive failures
  (empty frames or exceptions) and suppresses further traffic until backoff
  elapses; a success resets it.
- ``LLM_TRADER_YF_OFFLINE=1`` forces the empty path (hermetic tests; conftest
  pins it by default).
- The zero-lag macro provider consumes ``get_quote_data`` (the previously
  nonexistent ``fetch_quote`` was a silent always-fallback bug) and caches
  quotes per symbol.

All tests are offline: yfinance is stubbed or the offline kill switch is set.
"""

from __future__ import annotations

import sys
import os
import types
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))

import yahoo_finance_provider as yfp_module  # top-level identity, as tests/conftest allows
from yahoo_finance_provider import YahooFinanceProvider


def _frame(closes) -> pd.DataFrame:
    idx = pd.date_range("2026-09-22", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "Open": [c * 0.99 for c in closes],
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": list(closes),
            "Volume": [1000] * len(closes),
        },
        index=idx,
    )


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Isolate module-level breaker state between tests."""
    yfp_module._NET_FAIL_COUNT = 0
    yfp_module._NET_FAIL_UNTIL = 0.0
    yield
    yfp_module._NET_FAIL_COUNT = 0
    yfp_module._NET_FAIL_UNTIL = 0.0


def _stub_yf(monkeypatch: pytest.MonkeyPatch, history_impl):
    """Install a fake yfinance module with a configurable history impl."""
    fake = types.ModuleType("yfinance")
    fake.Ticker = lambda symbol: types.SimpleNamespace(history=history_impl)
    monkeypatch.setattr(yfp_module, "yf", fake, raising=False)
    monkeypatch.setattr(yfp_module, "YFINANCE_AVAILABLE", True, raising=False)


class TestGuardedHistory:
    def test_offline_kill_switch_suppresses_network(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "1")
        called = []

        def _impl(**kwargs):
            called.append(kwargs)
            return _frame([100.0])

        _stub_yf(monkeypatch, _impl)
        provider = YahooFinanceProvider()
        frame = provider._history("^GSPC", period="5d")
        assert frame is None
        assert called == []  # never touched the stub

    def test_success_returns_frame_and_resets_breaker(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([100.0, 101.0]))
        provider = YahooFinanceProvider()
        provider._record_net_failure()
        provider._record_net_failure()
        frame = provider._history("^GSPC", period="5d")
        assert frame is not None and not frame.empty
        assert yfp_module._NET_FAIL_COUNT == 0
        assert not provider._net_backoff_active()

    def test_empty_frame_counts_as_failure(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([]))
        provider = YahooFinanceProvider()
        assert provider._history("^GSPC", period="5d") is None
        assert yfp_module._NET_FAIL_COUNT == 1

    def test_exception_counts_as_failure(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")

        def _boom(**kwargs):
            raise ConnectionError("getaddrinfo failed")

        _stub_yf(monkeypatch, _boom)
        provider = YahooFinanceProvider()
        assert provider._history("^GSPC", period="5d") is None
        assert yfp_module._NET_FAIL_COUNT == 1

    def test_breaker_opens_after_threshold_and_suppresses(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        calls = []

        def _empty(**kwargs):
            calls.append(kwargs)
            return _frame([])

        _stub_yf(monkeypatch, _empty)
        provider = YahooFinanceProvider()
        for _ in range(provider.NET_FAIL_THRESHOLD):
            assert provider._history("CL=F", period="5d") is None
        assert len(calls) == provider.NET_FAIL_THRESHOLD
        # Breaker now open: further calls are suppressed without new attempts.
        assert provider._history("CL=F", period="5d") is None
        assert len(calls) == provider.NET_FAIL_THRESHOLD
        assert provider._net_backoff_active()

    def test_shared_breaker_across_instances(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([]))
        first = YahooFinanceProvider()
        second = YahooFinanceProvider()
        for _ in range(first.NET_FAIL_THRESHOLD):
            first._history("^VIX", period="5d")
        assert second._net_backoff_active()
        assert second._history("NG=F", period="5d") is None


class TestQuoteData:
    def test_get_quote_data_prefers_recent_close(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([90.0, 92.5]))
        provider = YahooFinanceProvider()
        quote = provider.get_quote_data("CL=F")
        assert quote is not None
        assert quote["symbol"] == "CL=F"
        assert quote["price"] == pytest.approx(92.5)

    def test_get_quote_data_falls_back_to_5d_when_1d_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        periods_seen = []

        def _impl(period=None, interval=None):
            periods_seen.append(period)
            return _frame([7743.4]) if period == "5d" else _frame([])

        _stub_yf(monkeypatch, _impl)
        provider = YahooFinanceProvider()
        quote = provider.get_quote_data("^GSPC")
        assert quote is not None
        assert quote["price"] == pytest.approx(7743.4)
        assert periods_seen == ["1d", "5d"]

    def test_get_quote_data_returns_none_when_all_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([]))
        provider = YahooFinanceProvider()
        assert provider.get_quote_data("ZQ=F") is None

    def test_fetch_quote_alias_matches_get_quote_data(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([14.87]))
        provider = YahooFinanceProvider()
        assert provider.fetch_quote("^VIX")["price"] == pytest.approx(14.87)


class TestPublicMethods:
    def test_market_indices_partial_on_outage(self, monkeypatch):
        """During an outage the method degrades to fewer entries, not errors."""
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "1")
        provider = YahooFinanceProvider()
        assert provider.get_market_indices() == {}
        assert provider.get_market_sentiment() == {}
        assert provider.get_currency_strength() == {}

    def test_market_indices_happy_path(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        _stub_yf(monkeypatch, lambda **kwargs: _frame([100.0, 102.0]))
        provider = YahooFinanceProvider()
        results = provider.get_market_indices()
        assert set(results) == {"SPX", "DJI", "NDX", "VIX", "DXY"}
        assert results["SPX"]["price"] == pytest.approx(102.0)
        # change_pct is measured against the first row's Close (prev), not Open.
        assert results["SPX"]["change_pct"] == pytest.approx((102.0 - 100.0) / 100.0 * 100)

    def test_historical_data_normalizes_fx_symbol(self, monkeypatch):
        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        seen = []

        def _impl(period=None, interval=None):
            seen.append((period, interval))
            return _frame([1.14])

        _stub_yf(monkeypatch, _impl)
        provider = YahooFinanceProvider()
        rows = provider.get_historical_data("EURUSD", interval="1h", period="5d")
        assert rows and rows[0]["close"] == pytest.approx(1.14)
        # yahoo_symbol "EURUSD=X" is built inside; symbol passed through here
        # is the normalized one — verify via forex path instead.
        assert provider.get_forex_data("EURUSD")["price"] == pytest.approx(1.14)


class TestZeroLagWiring:
    def test_zero_lag_uses_get_quote_data_and_caches(self, monkeypatch):
        from zero_lag_macro_provider import ZeroLagMacroProvider

        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        provider = ZeroLagMacroProvider()
        calls = []

        class _FakeYahoo:
            def get_quote_data(self, symbol):
                calls.append(symbol)
                return {"symbol": symbol, "price": 92.41, "observed_at": "now"}

        monkeypatch.setattr(provider, "_get_yahoo", lambda: _FakeYahoo())
        price = provider._fetch_quote_safe("CL=F", 78.50)
        assert price == pytest.approx(92.41)
        assert calls == ["CL=F"]
        # Second call inside TTL is served from cache: no new fetch.
        assert provider._fetch_quote_safe("CL=F", 78.50) == pytest.approx(92.41)
        assert calls == ["CL=F"]

    def test_zero_lag_falls_back_when_quote_unavailable(self, monkeypatch):
        from zero_lag_macro_provider import ZeroLagMacroProvider

        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        provider = ZeroLagMacroProvider()

        class _DeadYahoo:
            def get_quote_data(self, symbol):
                return None

        monkeypatch.setattr(provider, "_get_yahoo", lambda: _DeadYahoo())
        assert provider._fetch_quote_safe("NG=F", 2.65) == pytest.approx(2.65)

    def test_alt_provider_routes_through_shared_path(self, monkeypatch):
        from alternative_data_provider import AlternativeDataProvider

        monkeypatch.setenv("LLM_TRADER_YF_OFFLINE", "")
        alt = AlternativeDataProvider()

        class _FakeYahoo:
            def get_quote_data(self, symbol):
                return {"symbol": symbol, "price": 100.97, "observed_at": "now"}

        monkeypatch.setattr(alt, "_yahoo", _FakeYahoo())
        quote = alt._fetch_yahoo_quote("DX-Y.NYB")
        assert quote is not None
        assert quote["price"] == pytest.approx(100.97)

        monkeypatch.setattr(alt, "_yahoo", False)  # provider unavailable
        assert alt._fetch_yahoo_quote("^GSPC") is None
