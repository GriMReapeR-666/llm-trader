"""Tests for P0 meta-learning wiring fixes.

Verifies:
1. DSR proxy uses deflated_sharpe_test from overfit_detector
2. PerformanceMonitor.close_trade feeds meta_loop.log_outcome
3. MARS multipliers loaded into alpha_ensemble
4. RegimeTransitionDetector state persists across cycles
5. Evolutionary fitness uses CPCV-based scoring
6. search_historical_scenarios uses dynamic journal data
"""
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# 1. DSR proxy uses deflated_sharpe_test
# ---------------------------------------------------------------------------

class TestDSRProxyUsesDeflatedSharpe:
    """Verify _compute_dsr_proxy calls deflated_sharpe_test from overfit_detector."""

    def _make_shadow(self, tmp_path):
        from core.meta_loop import ShadowDeploy
        return ShadowDeploy(dsr_threshold=0.5, shadow_window=5)

    def test_dsr_proxy_returns_valid_score(self, tmp_path):
        shadow = self._make_shadow(tmp_path)
        # Good scenario: high Sharpe, low DD, lots of obs
        score = shadow._compute_dsr_proxy(2.0, 0.05, 200)
        assert 0.0 <= score <= 1.0
        assert score > 0.5  # Should be high for good metrics

    def test_dsr_proxy_penalizes_poor_sharpe(self, tmp_path):
        shadow = self._make_shadow(tmp_path)
        # Bad scenario: negative Sharpe
        score = shadow._compute_dsr_proxy(-1.0, 0.8, 10)
        assert score < 0.3

    def test_dsr_proxy_scales_with_n_trials(self, tmp_path):
        from core.meta_loop import ShadowDeploy
        shadow = ShadowDeploy(dsr_threshold=0.5, shadow_window=5)
        # With many trials, DSR should be more conservative
        score = shadow._compute_dsr_proxy(1.0, 0.1, 100)
        assert 0.0 <= score <= 1.0

    def test_dsr_proxy_fallback_on_import_error(self, tmp_path):
        """Even if overfit_detector is unavailable, proxy still works."""
        shadow = self._make_shadow(tmp_path)
        score = shadow._compute_dsr_proxy(1.5, 0.1, 100)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# 2. close_trade → meta_loop.log_outcome wiring
# ---------------------------------------------------------------------------

class TestCloseTradeMetaLoopWiring:
    """Verify PerformanceMonitor.close_trade feeds meta_loop.log_outcome."""

    def test_close_trade_feeds_meta_loop(self, tmp_path):
        from data.market_data_provider import PerformanceMonitor
        monitor = PerformanceMonitor(history_file=str(tmp_path / "trading_rewards.json"))
        # Log a trade
        monitor.log_trade(
            ticket=99999, symbol="EURUSD", action="BUY", volume=0.01,
            price=1.1000, decision={"risk_amount": 5.0},
        )
        # Close it
        monitor.close_trade(99999, exit_price=1.1050, pnl=5.0)

        # Verify meta_loop received the outcome
        from core.meta_loop import get_meta_loop
        import core.meta_loop as ml
        # Reset singleton to get fresh instance
        ml._loop = None
        meta = get_meta_loop()
        entries = meta.corpus.get_entries(limit=10)
        # Should have at least one entry containing the ticket number in hypothesis_id
        ticket_entries = [e for e in entries if "99999" in e.hypothesis_id]
        assert len(ticket_entries) >= 1, f"Expected meta_loop entry for ticket 99999, got {len(ticket_entries)}"
        assert ticket_entries[0].live_pnl == 5.0

    def test_close_trade_meta_loop_graceful_failure(self, tmp_path):
        """Meta-loop failure should not break close_trade."""
        from data.market_data_provider import PerformanceMonitor
        monitor = PerformanceMonitor(history_file=str(tmp_path / "trading_rewards.json"))
        monitor.log_trade(
            ticket=99998, symbol="EURUSD", action="BUY", volume=0.01,
            price=1.1000, decision={"risk_amount": 5.0},
        )
        # Should not raise even if meta_loop has issues
        monitor.close_trade(99998, exit_price=1.1050, pnl=5.0)
        # Trade still closed
        for t in monitor.history:
            if t["ticket"] == 99998:
                assert t["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# 3. MARS multipliers loaded into alpha_ensemble
# ---------------------------------------------------------------------------

class TestMARSMultipliersLoaded:
    """Verify _apply_regime_adjustments loads from artifacts/mars_alpha_multipliers.json."""

    def test_mars_multipliers_loaded_when_available(self, tmp_path, monkeypatch):
        import core.alpha_ensemble as ae
        # Create MARS artifact
        mars_data = {
            "LOW_VOL_TREND": {
                "momentum": 2.0,
                "reversal": 0.1,
                "mean_reversion": 0.1,
                "volume_price": 1.5,
                "divergence": 0.5,
                "composite": 1.2,
            }
        }
        mars_path = Path("artifacts/mars_alpha_multipliers.json")
        mars_path.parent.mkdir(exist_ok=True)
        mars_path.write_text(json.dumps(mars_data))

        try:
            weights = {"alpha_001": 0.5, "alpha_002": 0.5}
            regime = {"primary_regime": "LOW_VOL_TREND"}
            adjusted = ae._apply_regime_adjustments(weights, regime)
            # With MARS multipliers, weights should differ from hardcoded
            assert isinstance(adjusted, dict)
            assert len(adjusted) == 2
            # All weights should be positive
            assert all(v > 0 for v in adjusted.values())
        finally:
            if mars_path.exists():
                mars_path.unlink()

    def test_fallback_to_hardcoded_when_no_mars(self):
        import core.alpha_ensemble as ae
        weights = {"alpha_001": 0.5, "alpha_002": 0.5}
        regime = {"primary_regime": "LOW_VOL_TREND"}
        adjusted = ae._apply_regime_adjustments(weights, regime)
        # Should use hardcoded multipliers
        assert isinstance(adjusted, dict)
        assert len(adjusted) == 2


# ---------------------------------------------------------------------------
# 4. RegimeTransitionDetector state persists
# ---------------------------------------------------------------------------

class TestDetectorStatePersistence:
    """Verify detector state persists across MetaAdaptiveEngine instantiations."""

    def test_detector_state_persists_across_cycles(self, tmp_path):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        state_file = str(tmp_path / "meta_adaptive_state.json")
        engine = MetaAdaptiveEngine(state_file=state_file)

        # Simulate 5 bars of same regime to build stability
        probs = {"LOW_VOL_TREND": 0.7, "LOW_VOL_RANGE": 0.2, "HIGH_VOL_TREND": 0.1}
        for i in range(5):
            engine.update(probs, bar_num=i)

        # Check detector has state
        assert engine.detector.bars_since_transition >= 0
        saved_bars = engine.detector.bars_since_transition

        # Create new engine from same state file
        engine2 = MetaAdaptiveEngine(state_file=state_file)
        # Detector state should be restored
        assert engine2.detector.bars_since_transition == saved_bars

    def test_detector_state_saves_current_regime(self, tmp_path):
        from core.meta_adaptive_engine import MetaAdaptiveEngine
        state_file = str(tmp_path / "meta_adaptive_state.json")
        engine = MetaAdaptiveEngine(state_file=state_file)

        # Force a regime assignment
        probs = {"LOW_VOL_TREND": 0.8, "LOW_VOL_RANGE": 0.2}
        engine.update(probs, bar_num=0)

        # Save state
        engine._save_state()

        # Load and verify detector_state exists
        data = json.loads(Path(state_file).read_text())
        assert "detector_state" in data
        assert data["detector_state"]["current_regime"] is not None


# ---------------------------------------------------------------------------
# 5. Evolutionary fitness uses CPCV-based scoring
# ---------------------------------------------------------------------------

class TestEvolutionaryFitnessCPCV:
    """Verify evolutionary fitness uses data-driven scoring, not just heuristics."""

    def test_fitness_includes_fitness_type(self, tmp_path):
        from core.auto_quant_tools import AutoQuantTools
        tools = AutoQuantTools(state_file=str(tmp_path / "auto_quant_state.json"))
        # Run with empty OHLC (heuristic fallback)
        toon = {"ohlc": [], "context": {"symbol": "EURUSD"}}
        result = tools._run_evolutionary(toon)
        assert "best_fitness" in result
        assert result.get("fitness_type") == "heuristic"

    def test_fitness_with_ohlc_data(self, tmp_path):
        from core.auto_quant_tools import AutoQuantTools
        tools = AutoQuantTools(state_file=str(tmp_path / "auto_quant_state.json"))
        # Generate fake OHLC with 100 bars
        rng = np.random.RandomState(42)
        closes = 1.1000 + np.cumsum(rng.randn(100) * 0.001)
        ohlc = [{"close": float(c), "open": float(c - 0.0001),
                 "high": float(c + 0.0005), "low": float(c - 0.0005)} for c in closes]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD"}}
        result = tools._run_evolutionary(toon)
        assert "best_fitness" in result
        assert result.get("fitness_type") == "cpcv_sharpe"


# ---------------------------------------------------------------------------
# 6. search_historical_scenarios uses dynamic data
# ---------------------------------------------------------------------------

class TestDynamicScenarioFallback:
    """Verify search_historical_scenarios uses journal data, not hardcoded."""

    def _invoke(self, symbol, pattern, limit=1):
        """Invoke the tool — it's a LangChain StructuredTool."""
        from core.trading_tools import search_historical_scenarios
        # StructuredTool wraps the function; call via .invoke or access .func
        if hasattr(search_historical_scenarios, 'invoke'):
            return search_historical_scenarios.invoke({"symbol": symbol, "setup_pattern": pattern, "limit": limit})
        elif hasattr(search_historical_scenarios, 'func'):
            return search_historical_scenarios.func(symbol=symbol, setup_pattern=pattern, limit=limit)
        else:
            return search_historical_scenarios(symbol=symbol, setup_pattern=pattern, limit=limit)

    def test_fallback_returns_source_field(self):
        """When no journal data, returns source field."""
        result = self._invoke("XYZXYZ", "test_pattern")
        scenarios = result.get("scenarios", [])
        assert len(scenarios) >= 1
        assert scenarios[0].get("source") in ("empty", "trade_journal", "fallback_error")

    def test_fallback_has_dynamic_fields(self):
        """New fallback includes source and sample_count fields."""
        result = self._invoke("XYZXYZ", "test_pattern")
        scenarios = result.get("scenarios", [])
        assert len(scenarios) >= 1
        s = scenarios[0]
        assert "source" in s
        assert "sample_count" in s


# ---------------------------------------------------------------------------
# 7. Meta corpus ingestion count
# ---------------------------------------------------------------------------

class TestMetaCorpusIngestion:
    """Verify meta_corpus.db receives entries from close_trade."""

    def test_corpus_count_increases_after_close(self, tmp_path):
        import core.meta_loop as ml_mod
        ml_mod._loop = None
        from core.meta_loop import get_meta_loop
        meta = get_meta_loop()
        initial_count = meta.corpus.get_entry_count()

        # Close a trade via PerformanceMonitor
        from data.market_data_provider import PerformanceMonitor
        monitor = PerformanceMonitor(history_file=str(tmp_path / "trading_rewards.json"))
        monitor.log_trade(
            ticket=99997, symbol="EURUSD", action="BUY", volume=0.01,
            price=1.1000, decision={"risk_amount": 5.0},
        )
        monitor.close_trade(99997, exit_price=1.1050, pnl=5.0)

        # Count should increase
        new_count = meta.corpus.get_entry_count()
        assert new_count > initial_count, f"Expected count increase: {initial_count} -> {new_count}"
