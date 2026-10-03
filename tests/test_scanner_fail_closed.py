from scanner import MarketScanner


def test_offline_scanner_returns_no_synthetic_opportunity():
    scanner = MarketScanner(client=None)
    assert scanner.scan() == []
    assert scanner._fallback_scan() == []


def test_authenticated_scanner_uses_stable_watchlist_order(monkeypatch):
    scanner = MarketScanner(client=object(), fetch_func=lambda *args, **kwargs: None)
    seen = []

    def fake_analyze(symbol, timeframe):
        seen.append(symbol)
        return None

    monkeypatch.setattr(scanner, "_analyze_symbol", fake_analyze)
    scanner.scan(timeframe="H1")
    first = list(seen)
    seen.clear()
    scanner.scan(timeframe="H1")
    assert first == seen
    assert first == ["XAUUSD", "EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "AUDUSD"]


def test_scanner_provider_score_uses_fred_and_openbb():
    class _FakeAPI:
        def get_fred_data(self, series_ids):
            return {
                "VIXCLS": {"value": 28.0, "date": "2026-08-10"},
                "DFF": {"value": 5.25, "date": "2026-08-10"},
            }

        def get_currency_strength(self):
            return {"EUR": 0.8, "USD": 0.2}

        def get_market_sentiment(self):
            return {"bias": "RISK_OFF"}

        openbb = type("OB", (), {"summary": staticmethod(lambda: {
            "openbb_installed": True,
            "active_providers": 3,
            "provider_health": {},
        })})()

    scanner = MarketScanner(client=object(), api_manager=_FakeAPI())
    scanner._provider_cache = scanner._collect_provider_snapshot()
    result = scanner._provider_score("EURUSD")
    assert result["score"] > 0
    assert "FRED" in result["providers_used"]
    assert "OpenBB" in result["providers_used"]
    assert any(
        ("VIX" in r) or ("FX strength" in r) or ("OpenBB" in r) or ("FRED" in r)
        for r in result["reasons"]
    )
