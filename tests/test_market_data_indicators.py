from types import SimpleNamespace
from unittest.mock import MagicMock
import numpy as np
import pandas as pd
import pytest

import mt5_trader
from data.market_data_provider import MarketDataProvider
import data.market_data_provider as mdp


def test_fetch_data_computes_indicators_and_alphas(monkeypatch):
    """Verify fetch_data populates rsi_14 and standard indicators without KeyError."""
    n = 100
    df = pd.DataFrame({
        "open": np.linspace(1.08, 1.09, n),
        "high": np.linspace(1.085, 1.095, n),
        "low": np.linspace(1.075, 1.085, n),
        "close": np.linspace(1.08, 1.09, n),
        "tick_volume": [1000] * n,
        "spread": [1] * n,
        "real_volume": [0] * n,
    }, index=pd.date_range("2026-01-01", periods=n, freq="15min"))

    risk = SimpleNamespace(
        balance=10000.0,
        risk_per_trade=0.01,
        max_drawdown=0.05,
        open_positions=[],
        update_account=lambda b, e: None,
    )

    client = SimpleNamespace(
        get_account_info=lambda: {"balance": 10000.0, "equity": 10000.0},
    )

    # Stub the whole api_manager surface: fetch_data otherwise performs live
    # NewsAPI/FRED calls, which hang the suite on network latency. Those
    # branches are fail-soft, so empty returns are the correct hermetic
    # shape - this test covers indicator/alpha computation only.
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
    monkeypatch.setattr(mdp, "_get_fetch_ohlc", lambda: (lambda s, tf, count=100: df.copy()))

    toon = provider.fetch_data("EURUSD", "M15")

    assert "indicators" in toon
    indicators = toon["indicators"]
    assert "rsi_14" in indicators
    assert len(indicators["rsi_14"]) == n
    assert "macd" in indicators
    assert "ema_50" in indicators
    assert "atr_14" in indicators
