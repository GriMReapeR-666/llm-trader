"""Tests for 12 quant models wired into auto_quant_tools."""
import pytest
import numpy as np


class TestQuantModelRunners:
    """Test each quant model runner in auto_quant_tools."""

    def _make_tools(self):
        from core.auto_quant_tools import AutoQuantTools
        return AutoQuantTools(state_file=":memory:")

    def _make_toh(self):
        """Create a minimal toon dict with OHLC data."""
        np.random.seed(42)
        closes = 100 + np.cumsum(np.random.randn(200) * 0.5)
        ohlc = []
        for i, c in enumerate(closes):
            ohlc.append({
                "open": c - 0.1, "high": c + 0.2, "low": c - 0.2,
                "close": c, "volume": 1000, "time": i,
            })
        return {
            "ohlc": ohlc,
            "account": {"equity": 10000.0},
            "current_price": float(closes[-1]),
            "proposed_sl": float(closes[-1]) * 0.995,
            "trade_history": [{"pnl": np.random.randn() * 10} for _ in range(50)],
        }

    def test_cusum_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_cusum(toon)
        assert result["status"] == "OK"
        assert "n_breaches" in result
        assert "allocation_multiplier" in result

    def test_sprt_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_sprt(toon)
        assert result["status"] == "OK"
        assert result["decision"] in ("CONTINUE", "KILL", "INCONCLUSIVE")

    def test_bayesian_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_bayesian(toon)
        assert result["status"] == "OK"
        assert "posterior_mean" in result
        assert "confidence_level" in result

    def test_sentiment_veto_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_sentiment_veto(toon)
        assert result["status"] == "OK"
        assert "veto_trade" in result
        assert "recommended_action" in result

    def test_account_sizing_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_account_sizing(toon)
        assert result["status"] == "OK"
        assert "one_r_dollar" in result
        assert "is_overbetting" in result

    def test_session_spread_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_session_spread(toon)
        assert result["status"] == "OK"
        assert "session" in result
        assert "is_high_cost_session" in result

    def test_ou_time_stop_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_ou_time_stop(toon)
        assert result["status"] == "OK"
        assert "half_life" in result
        assert "is_stagnant" in result

    def test_portfolio_decorr_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_portfolio_decorr(toon)
        assert result["status"] == "OK"
        assert "portfolio_volatility" in result
        assert "is_well_diversified" in result

    def test_alpha_capacity_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_alpha_capacity(toon)
        assert result["status"] == "OK"
        assert "capacity_utilization" in result
        assert "is_viable" in result

    def test_corr_breakdown_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_corr_breakdown(toon)
        assert result["status"] == "OK"
        assert "is_breakdown" in result
        assert "correlation_regime" in result

    def test_retail_friction_runner(self):
        tools = self._make_tools()
        toon = self._make_toh()
        result = tools._run_retail_friction(toon)
        assert result["status"] == "OK"
        assert "ev_retail_bps" in result
        assert "is_profitable_after_costs" in result

    def test_all_intervals_registered(self):
        """All 12 quant models should have intervals in DEFAULT_INTERVALS."""
        from core.auto_quant_tools import DEFAULT_INTERVALS
        expected = ["cusum", "sprt", "bayesian", "sentiment_veto", "account_sizing",
                     "session_spread", "ou_time_stop", "portfolio_decorr",
                     "alpha_capacity", "corr_breakdown", "retail_friction"]
        for name in expected:
            assert name in DEFAULT_INTERVALS, f"Missing interval: {name}"

    def test_update_includes_quant_models(self):
        """update() should include quant model results in output."""
        tools = self._make_tools()
        toon = self._make_toh()
        # Force all to run by resetting last_run
        for key in ["cusum", "sprt", "bayesian", "sentiment_veto", "account_sizing",
                     "session_spread", "ou_time_stop", "portfolio_decorr",
                     "alpha_capacity", "corr_breakdown", "retail_friction"]:
            tools.state.last_run[key] = -9999
        result = tools.update(100, toon)
        aqt = result.get("auto_quant_tools", {})
        results = aqt.get("results", {})
        # At least some should have run
        quant_keys = ["cusum", "sprt", "bayesian", "sentiment_veto", "account_sizing",
                       "session_spread", "ou_time_stop", "portfolio_decorr",
                       "alpha_capacity", "corr_breakdown", "retail_friction"]
        ran = [k for k in quant_keys if k in results]
        assert len(ran) > 0, f"No quant models ran. Available: {list(results.keys())}"
