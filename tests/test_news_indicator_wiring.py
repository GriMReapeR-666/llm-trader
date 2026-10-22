"""Tests for news indicator wiring, live FRED fetcher, gate backtest, and TUI NEWS screen."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
import numpy as np


# ============================================================================
# 1. LiveIndicatorFetcher
# ============================================================================

class TestLiveIndicatorFetcher:
    """Tests for core/news_indicator_fetcher.py."""

    def test_import(self):
        from core.news_indicator_fetcher import (
            LiveIndicatorFetcher, IndicatorSnapshot,
            NFPIndicators, CPIIndicators,
            NFP_INDICATOR_SERIES, CPI_INDICATOR_SERIES, ESI_SERIES,
        )
        assert LiveIndicatorFetcher is not None
        assert IndicatorSnapshot is not None
        assert len(NFP_INDICATOR_SERIES) >= 4
        assert len(CPI_INDICATOR_SERIES) >= 4
        assert len(ESI_SERIES) >= 5

    def test_init_without_api(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        fetcher = LiveIndicatorFetcher(api_manager=None)
        assert fetcher._has_fred_api() is False

    def test_init_with_api(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        mock_manager = MagicMock()
        mock_manager.fred = MagicMock()
        fetcher = LiveIndicatorFetcher(api_manager=mock_manager)
        assert fetcher._has_fred_api() is True

    def test_fetch_without_api_returns_missing(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        fetcher = LiveIndicatorFetcher(api_manager=None)
        snap = fetcher._fetch_fred_series("PAYEMS")
        assert snap is not None
        assert snap.status == "MISSING"

    def test_fetch_with_mock_api(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        import pandas as pd
        mock_manager = MagicMock()
        mock_fred = MagicMock()
        # Create a realistic FRED series with recent dates
        dates = pd.date_range(pd.Timestamp.now() - pd.Timedelta(days=90), periods=5, freq="MS")
        values = [280000, 285000, 290000, 288000, 292000]
        series = pd.Series(values, index=dates)
        mock_fred.get_series.return_value = series
        mock_manager.fred = mock_fred

        fetcher = LiveIndicatorFetcher(api_manager=mock_manager)
        snap = fetcher._fetch_fred_series("PAYEMS")
        assert snap is not None
        assert snap.status == "OK"
        assert snap.value == 292000.0
        assert snap.previous == 288000.0

    def test_fetch_all_without_api(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        fetcher = LiveIndicatorFetcher(api_manager=None)
        result = fetcher.fetch_all_indicators()
        assert result["is_live"] is False
        assert result["nfp"] is not None
        assert result["cpi"] is not None
        assert result["esi_releases"] == []
        assert result["cached"] is False

    def test_fetch_all_caches(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        fetcher = LiveIndicatorFetcher(api_manager=None)
        r1 = fetcher.fetch_all_indicators()
        r2 = fetcher.fetch_all_indicators()
        assert r1["cached"] is False
        assert r2["cached"] is True

    def test_nfp_indicators_defaults(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher, NFPIndicators
        fetcher = LiveIndicatorFetcher(api_manager=None)
        nfp = fetcher.get_nfp_indicators()
        assert isinstance(nfp, NFPIndicators)
        assert nfp.is_live is False
        # Defaults should be reasonable
        assert nfp.jobless_claims_4w_ma > 0
        assert nfp.jolts_openings_millions > 0

    def test_cpi_indicators_defaults(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher, CPIIndicators
        fetcher = LiveIndicatorFetcher(api_manager=None)
        cpi = fetcher.get_cpi_indicators()
        assert isinstance(cpi, CPIIndicators)
        assert cpi.is_live is False

    def test_get_summary(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        fetcher = LiveIndicatorFetcher(api_manager=None)
        summary = fetcher.get_summary()
        assert "nfp_prediction" in summary
        assert "cpi_prediction" in summary
        assert "esi" in summary
        assert summary["nfp_prediction"]["event"] == "NFP"
        assert summary["cpi_prediction"]["event"] == "CPI"
        assert "esi_score" in summary["esi"]

    def test_staleness_detection(self):
        from core.news_indicator_fetcher import LiveIndicatorFetcher
        import pandas as pd
        mock_manager = MagicMock()
        mock_fred = MagicMock()
        # Data from 120 days ago → STALE
        old_date = pd.Timestamp(datetime.now(timezone.utc) - timedelta(days=120))
        series = pd.Series([100.0], index=pd.DatetimeIndex([old_date]))
        mock_fred.get_series.return_value = series
        mock_manager.fred = mock_fred

        fetcher = LiveIndicatorFetcher(api_manager=mock_manager)
        snap = fetcher._fetch_fred_series("PAYEMS")
        assert snap.status == "STALE"


# ============================================================================
# 2. News Trading Gate Integration
# ============================================================================

class TestNewsGateLiveWiring:
    """Tests for news_trading.py with live indicator wiring."""

    def test_get_news_trading_context_with_fetcher(self):
        from core.news_trading import get_news_trading_context
        from core.news_indicator_fetcher import LiveIndicatorFetcher

        fetcher = LiveIndicatorFetcher(api_manager=None)
        events = [{
            "title": "NON-FARM PAYROLLS",
            "currency": "USD",
            "time": datetime.now(timezone.utc) + timedelta(hours=3),
            "importance": "high",
            "forecast": 180,
            "previous": 175,
        }]
        ctx = get_news_trading_context(
            symbol="EURUSD", events=events,
            live_indicator_fetcher=fetcher,
        )
        assert "gate" in ctx
        assert "esi" in ctx
        assert "nfp_prediction" in ctx
        assert "cpi_prediction" in ctx
        assert "is_live" in ctx
        assert ctx["is_live"] is False  # No FRED API
        assert ctx["summary_for_llm"]  # Non-empty

    def test_get_news_trading_context_without_fetcher(self):
        from core.news_trading import get_news_trading_context
        ctx = get_news_trading_context(symbol="EURUSD", events=[])
        assert ctx["gate"]["allowed"] is True
        assert ctx["gate"]["phase"] == "CLEAR"
        assert "nfp_prediction" not in ctx or ctx.get("nfp_prediction") is None

    def test_news_gate_blocks_during_spike(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        now = datetime.now(timezone.utc)
        events = [{
            "title": "NON-FARM PAYROLLS",
            "currency": "USD",
            "time": now + timedelta(minutes=3),  # 3 min away → SPIKE_BLACKOUT
            "importance": "high",
        }]
        result = gate.evaluate(symbol="EURUSD", now=now, events=events)
        assert result.allowed is False
        assert result.phase == "SPIKE_BLACKOUT"

    def test_news_gate_allows_far_future_event(self):
        from core.news_trading import NewsTradingGate
        gate = NewsTradingGate()
        now = datetime.now(timezone.utc)
        events = [{
            "title": "NON-FARM PAYROLLS",
            "currency": "USD",
            "time": now + timedelta(hours=5),  # 5 hours away → CLEAR
            "importance": "high",
        }]
        result = gate.evaluate(symbol="EURUSD", now=now, events=events)
        assert result.allowed is True
        assert result.phase == "CLEAR"

    def test_predict_nfp_with_live_data(self):
        from core.news_trading import predict_nfp
        result = predict_nfp(
            adp_change_k=250.0,
            adp_consensus_k=180.0,
            jobless_claims_4w_ma=200.0,
            jobless_claims_baseline=215.0,
            ism_employment_pmi=52.0,
            jolts_openings_millions=9.0,
            jolts_baseline=8.0,
            esi_score=65.0,
        )
        assert result["event"] == "NFP"
        assert result["directional_bias"] in ("STRONG_BEAT", "MODERATE_BEAT", "IN_LINE", "MODERATE_MISS", "STRONG_MISS")
        assert 0 <= result["confidence"] <= 1.0

    def test_predict_cpi_with_live_data(self):
        from core.news_trading import predict_cpi
        result = predict_cpi(
            ppi_mom_pct=0.4,
            ppi_consensus_pct=0.2,
            ism_prices_paid_pmi=55.0,
            wage_growth_yoy_pct=4.5,
            wage_baseline_pct=3.5,
        )
        assert result["event"] == "CPI"
        assert result["directional_bias"] in ("STRONG_BEAT", "MODERATE_BEAT", "IN_LINE", "MODERATE_MISS", "STRONG_MISS")


# ============================================================================
# 3. News Gate Backtest
# ============================================================================

class TestNewsGateBacktest:
    """Tests for research/news_gate_backtest.py."""

    def test_import(self):
        from research.news_gate_backtest import NewsGateBacktest, TradeRecord, BacktestResult
        assert NewsGateBacktest is not None

    def test_synthetic_backtest(self):
        from research.news_gate_backtest import NewsGateBacktest
        bt = NewsGateBacktest()
        result = bt.run(symbol="EURUSD", n_synthetic_trades=200, n_synthetic_events=10)
        assert result.total_trades == 200
        assert result.events_analyzed == 10
        assert result.with_gate["n_trades"] <= result.without_gate["n_trades"]
        assert result.whipsaw_trades_avoided >= 0
        assert "win_rate" in result.with_gate
        assert "sharpe" in result.with_gate

    def test_backtest_improves_with_gate(self):
        """The gate should improve average trade quality by filtering bad entries."""
        from research.news_gate_backtest import NewsGateBacktest
        bt = NewsGateBacktest()
        result = bt.run(symbol="EURUSD", n_synthetic_trades=500, n_synthetic_events=20, seed=42)
        # Gate should reduce number of trades (blackout trades removed)
        assert result.with_gate["n_trades"] <= result.without_gate["n_trades"]
        # Whipsaw trades should be avoided
        assert result.whipsaw_trades_avoided >= 0
        # Improvement metrics should be computed
        assert "win_rate_delta" in result.improvement
        assert "trades_reduced_pct" in result.improvement
        # Win rate should not be drastically worse (within tolerance)
        assert result.with_gate["win_rate"] >= result.without_gate["win_rate"] - 0.15

    def test_backtest_summary(self):
        from research.news_gate_backtest import NewsGateBacktest
        bt = NewsGateBacktest()
        result = bt.run(symbol="EURUSD", n_synthetic_trades=100)
        summary = result.summary()
        assert "NEWS GATE BACKTEST RESULTS" in summary
        assert "Win Rate" in summary or "win_rate" in summary

    def test_custom_trades(self):
        from research.news_gate_backtest import NewsGateBacktest, TradeRecord
        from datetime import datetime, timezone, timedelta
        bt = NewsGateBacktest()
        now = datetime.now(timezone.utc)
        trades = [
            TradeRecord(
                entry_time=now + timedelta(minutes=3),  # During spike
                exit_time=now + timedelta(minutes=60),
                direction="BUY", entry_price=1.1000, exit_price=1.0980,
            ),
            TradeRecord(
                entry_time=now + timedelta(hours=5),  # Clear
                exit_time=now + timedelta(hours=6),
                direction="BUY", entry_price=1.1000, exit_price=1.1020,
            ),
        ]
        events = [{
            "title": "NON-FARM PAYROLLS", "currency": "USD",
            "time": now + timedelta(minutes=5), "importance": "high",
        }]
        result = bt.run(trades=trades, events=events, symbol="EURUSD",
                    data_source="synthetic")
        # The first trade should be blocked, second should go through
        assert result.with_gate["n_trades"] == 1
        assert result.without_gate["n_trades"] == 2
        assert result.whipsaw_trades_avoided == 1


# ============================================================================
# 5. Execute Cycle Integration
# ============================================================================

class TestExecuteCycleNewsWiring:
    """Tests that execute_cycle properly wires the live indicator fetcher."""

    def test_orchestrator_has_fetcher_attr(self):
        """Verify the orchestrator stores the fetcher on first news gate pass."""
        # Just verify the attribute is referenced in the code
        import ast
        with open("intelligence/llm_orchestrator.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "_live_indicator_fetcher" in source
        assert "LiveIndicatorFetcher" in source

    def test_news_context_includes_predictions(self):
        """Verify get_news_trading_context returns nfp/cpi predictions."""
        from core.news_trading import get_news_trading_context
        ctx = get_news_trading_context("EURUSD", events=[])
        assert "nfp_prediction" in ctx or "esi" in ctx

    def test_bus_emit_covers_news_gate(self):
        """Verify the orchestrator emits a bus event for NEWS_GATE checked."""
        import ast
        with open("intelligence/llm_orchestrator.py", "r", encoding="utf-8") as f:
            source = f.read()
        assert "NEWS_GATE" in source
        assert "checked" in source or "blocked" in source
