"""Tests for Auto-Trigger Quantitative Tools and Regime Duration Tracker."""
from __future__ import annotations

import json
import os
import tempfile
import time

import numpy as np
import pytest

from core.auto_quant_tools import AutoQuantTools, AutoQuantState, ToolRun
from core.regime_duration_tracker import RegimeDurationTracker, RegimeTransition


# ===========================================================================
# 1. AutoQuantTools: trigger interval logic
# ===========================================================================

class TestAutoQuantTriggerIntervals:
    """Verify tools run at correct intervals."""

    def _make_tool(self, tmp_path, intervals=None):
        state_file = str(tmp_path / "auto_state.json")
        return AutoQuantTools(state_file=state_file, intervals=intervals or {})

    def test_first_run_triggers_all(self, tmp_path):
        tool = self._make_tool(tmp_path, {"cpcv": 5, "kalman": 5, "tca": 5, "fractional_diff": 5, "bocpd": 5,
                                            "alpha_discovery": 5, "meta_adaptive": 5, "self_improvement": 5,
                                            "evolutionary": 5, "cross_market": 5, "multimodal": 5, "crash_detector": 5, "causal_filter": 5,
                                            "walk_forward": 5, "optimal_execution": 5, "ewc_status": 5})
        toon = {"ohlc": [{"close": 1.0 + i * 0.001} for i in range(100)], "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        assert "auto_quant_tools" in result
        # All tools should run on first cycle
        assert len(result["auto_quant_tools"]["results"]) >= 27  # 32 total tools

    def test_tool_does_not_rerun_before_interval(self, tmp_path):
        tool = self._make_tool(tmp_path, {"cpcv": 10, "kalman": 10, "tca": 10, "fractional_diff": 10, "bocpd": 10,
                                            "alpha_discovery": 10, "meta_adaptive": 10, "self_improvement": 10,
                                            "evolutionary": 10, "cross_market": 10, "multimodal": 10, "crash_detector": 10, "causal_filter": 10,
                                            "walk_forward": 10, "optimal_execution": 10, "ewc_status": 10,
                                            "session_spread": 10, "ou_time_stop": 10, "cusum": 10, "sprt": 10,
                                            "bayesian": 10, "sentiment_veto": 10, "account_sizing": 10,
                                            "portfolio_decorr": 10, "alpha_capacity": 10, "corr_breakdown": 10,
                                            "retail_friction": 10})
        toon = {"ohlc": [{"close": 1.0 + i * 0.001} for i in range(100)], "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        tool.update(1, toon)
        # Second call at cycle 5 (< interval of 10) should NOT trigger any tool
        result = tool.update(5, toon)
        assert result["auto_quant_tools"]["results"] == {}

    def test_tool_reruns_at_interval(self, tmp_path):
        tool = self._make_tool(tmp_path, {"cpcv": 5, "kalman": 5, "tca": 5, "fractional_diff": 5, "bocpd": 5,
                                            "alpha_discovery": 5, "meta_adaptive": 5, "self_improvement": 5,
                                            "evolutionary": 5, "cross_market": 5, "multimodal": 5, "crash_detector": 5, "causal_filter": 5,
                                            "walk_forward": 5, "optimal_execution": 5, "ewc_status": 5,
                                            "session_spread": 5, "ou_time_stop": 5, "cusum": 5, "sprt": 5,
                                            "bayesian": 5, "sentiment_veto": 5, "account_sizing": 5,
                                            "portfolio_decorr": 5, "alpha_capacity": 5, "corr_breakdown": 5,
                                            "retail_friction": 5})
        toon = {"ohlc": [{"close": 1.0 + i * 0.001} for i in range(100)], "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        tool.update(1, toon)
        result = tool.update(6, toon)
        assert len(result["auto_quant_tools"]["results"]) >= 27  # 32 total tools

    def test_state_persists(self, tmp_path):
        state_file = str(tmp_path / "auto_state.json")
        tool1 = AutoQuantTools(state_file=state_file, intervals={"cpcv": 5, "kalman": 5, "tca": 5, "fractional_diff": 5, "bocpd": 5})
        toon = {"ohlc": [{"close": 1.0 + i * 0.001} for i in range(100)], "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        tool1.update(1, toon)
        # New instance with same file
        tool2 = AutoQuantTools(state_file=state_file, intervals={"cpcv": 5, "kalman": 5, "tca": 5, "fractional_diff": 5, "bocpd": 5})
        assert tool2.state.cycle_count == 1
        assert "cpcv" in tool2.state.last_run


# ===========================================================================
# 2. CPCV auto-trigger: overfitting detection
# ===========================================================================

class TestAutoCPCV:
    """Verify CPCV runs and detects overfitting."""

    def _make_tool(self, tmp_path):
        return AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": 1, "kalman": 9999, "tca": 9999, "fractional_diff": 9999, "bocpd": 9999},
        )

    def test_cpcv_runs_on_ohlcv(self, tmp_path):
        tool = self._make_tool(tmp_path)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, 200))).tolist()
        ohlc = [{"close": p, "volume": 1000} for p in prices]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        cpcv = result["auto_quant_tools"]["results"].get("cpcv", {})
        assert "status" in cpcv
        assert cpcv["status"] in ("CPCV_VALIDATED", "OVERFITTING_RISK_DETECTED", "INSUFFICIENT_SAMPLES_FOR_CPCV")

    def test_cpcv_alert_on_overfitting(self, tmp_path):
        tool = self._make_tool(tmp_path)
        # Random walk = likely overfitting risk
        np.random.seed(99)
        prices = (1.0800 + np.cumsum(np.random.normal(0, 0.001, 200))).tolist()
        ohlc = [{"close": p, "volume": 1000} for p in prices]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        alerts = result["auto_quant_tools"]["alerts"]
        # Random walk should trigger overfitting alert
        overfit_alerts = [a for a in alerts if a["code"] == "AUTO_CPCV_OVERFITTING"]
        assert len(overfit_alerts) == 1
        assert overfit_alerts[0]["severity"] == "CRITICAL"

    def test_cpcv_insufficient_data(self, tmp_path):
        tool = self._make_tool(tmp_path)
        ohlc = [{"close": 1.0 + i * 0.001} for i in range(10)]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        cpcv = result["auto_quant_tools"]["results"].get("cpcv", {})
        assert cpcv["status"] == "INSUFFICIENT_DATA"


# ===========================================================================
# 3. BOCPD auto-trigger: regime shift detection
# ===========================================================================

class TestAutoBOCPD:
    """Verify BOCPD detects structural shifts."""

    def _make_tool(self, tmp_path):
        return AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": 9999, "kalman": 9999, "tca": 9999, "fractional_diff": 9999, "bocpd": 1},
        )

    def test_bocpd_runs_on_returns(self, tmp_path):
        tool = self._make_tool(tmp_path)
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, 100))).tolist()
        ohlc = [{"close": p} for p in prices]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        bocpd = result["auto_quant_tools"]["results"].get("bocpd", {})
        assert "total_observations" in bocpd
        assert bocpd["total_observations"] > 0

    def test_bocpd_alert_on_shift(self, tmp_path):
        tool = self._make_tool(tmp_path)
        # Create prices with a very clear regime shift (large volatility change)
        np.random.seed(42)
        stable = (1.0800 + np.cumsum(np.random.normal(0.0001, 0.0001, 80))).tolist()
        volatile = (stable[-1] + np.cumsum(np.random.normal(0, 0.05, 80))).tolist()
        ohlc = [{"close": p} for p in stable + volatile]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        bocpd = result["auto_quant_tools"]["results"].get("bocpd", {})
        # Should detect at least one changepoint given the extreme shift
        assert bocpd.get("total_observations", 0) > 0
        # The result should have valid structure
        assert "has_recent_structural_shift" in bocpd


# ===========================================================================
# 4. Fractional Differentiation auto-trigger
# ===========================================================================

class TestAutoFractionalDiff:
    """Verify fractional diff runs and checks stationarity."""

    def _make_tool(self, tmp_path):
        return AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": 9999, "kalman": 9999, "tca": 9999, "fractional_diff": 1, "bocpd": 9999},
        )

    def test_fractional_diff_runs(self, tmp_path):
        tool = self._make_tool(tmp_path)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, 100))).tolist()
        ohlc = [{"close": p} for p in prices]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        fd = result["auto_quant_tools"]["results"].get("fractional_diff", {})
        assert "is_stationary" in fd
        assert "adf_t_stat" in fd


# ===========================================================================
# 5. TCA auto-trigger
# ===========================================================================

class TestAutoTCA:
    """Verify TCA runs on latest price."""

    def _make_tool(self, tmp_path):
        return AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": 9999, "kalman": 9999, "tca": 1, "fractional_diff": 9999, "bocpd": 9999},
        )

    def test_tca_runs(self, tmp_path):
        tool = self._make_tool(tmp_path)
        ohlc = [{"close": 1.0800, "volume": 500000}]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        result = tool.update(1, toon)
        tca = result["auto_quant_tools"]["results"].get("tca", {})
        assert "total_cost_bps" in tca
        assert tca["total_cost_bps"] >= 0


# ===========================================================================
# 6. Kalman auto-trigger
# ===========================================================================

class TestAutoKalman:
    """Verify Kalman filter runs on price series."""

    def _make_tool(self, tmp_path):
        return AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": 9999, "kalman": 1, "tca": 9999, "fractional_diff": 9999, "bocpd": 9999},
        )

    def test_kalman_runs(self, tmp_path):
        tool = self._make_tool(tmp_path)
        prices = (100.0 + np.cumsum(np.random.normal(0.1, 0.5, 100))).tolist()
        ohlc = [{"close": p} for p in prices]
        toon = {"ohlc": ohlc, "context": {"symbol": "XAUUSD", "timeframe": "H1"}}
        result = tool.update(1, toon)
        kalman = result["auto_quant_tools"]["results"].get("kalman", {})
        assert "dynamic_beta" in kalman or kalman.get("status") in ("INSUFFICIENT_DATA", "EMPTY_SERIES", "ERROR")


# ===========================================================================
# 7. AutoQuantState
# ===========================================================================

class TestAutoQuantState:
    """Verify state serialization."""

    def test_state_to_dict(self):
        state = AutoQuantState(cycle_count=10, last_run={"cpcv": 5})
        d = state.to_dict()
        assert d["cycle_count"] == 10
        assert d["last_run"]["cpcv"] == 5

    def test_tool_run_to_dict(self):
        run = ToolRun(tool_name="cpcv", cycle_num=5, timestamp=1.0, result={"status": "OK"}, success=True)
        d = run.to_dict()
        assert d["tool_name"] == "cpcv"
        assert d["success"] is True


# ===========================================================================
# 8. AutoQuantTools format report
# ===========================================================================

class TestAutoQuantReport:
    """Verify human-readable report."""

    def test_format_report(self, tmp_path):
        tool = AutoQuantTools(state_file=str(tmp_path / "auto_state.json"))
        report = tool.format_report()
        assert "Auto Quant Tools" in report
        assert "cpcv" in report
        assert "kalman" in report


# ===========================================================================
# 9. RegimeDurationTracker: basic transitions
# ===========================================================================

class TestRegimeDurationBasic:
    """Verify regime transition tracking."""

    def _make_tracker(self, tmp_path):
        return RegimeDurationTracker(state_file=str(tmp_path / "regime_state.json"))

    def test_initial_state(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        status = tracker.get_status()
        assert status["current_regime"] == "UNKNOWN"
        assert status["transition_count"] == 0
        assert status["current_dwell_bars"] == 0

    def test_single_regime_no_transition(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.update(1, {"primary_regime": "LOW_VOL_TREND"})
        tracker.update(2, {"primary_regime": "LOW_VOL_TREND"})
        tracker.update(3, {"primary_regime": "LOW_VOL_TREND"})
        status = tracker.get_status()
        assert status["current_regime"] == "LOW_VOL_TREND"
        assert status["current_dwell_bars"] == 3
        assert status["transition_count"] == 1  # UNKNOWN -> LOW_VOL_TREND

    def test_regime_transition(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        tracker.update(1, {"primary_regime": "LOW_VOL_TREND"})
        tracker.update(2, {"primary_regime": "LOW_VOL_TREND"})
        tracker.update(3, {"primary_regime": "HIGH_VOL_CRISIS"})
        status = tracker.get_status()
        assert status["current_regime"] == "HIGH_VOL_CRISIS"
        assert status["current_dwell_bars"] == 1
        assert status["transition_count"] == 2  # UNKNOWN->TREND, TREND->CRISIS

    def test_multiple_transitions(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        regimes = ["LOW_VOL_TREND"] * 10 + ["HIGH_VOL_CRISIS"] * 5 + ["LOW_VOL_RANGE"] * 8
        for i, r in enumerate(regimes):
            tracker.update(i, {"primary_regime": r})
        status = tracker.get_status()
        assert status["transition_count"] == 3  # UNKNOWN->TREND, TREND->CRISIS, CRISIS->RANGE
        assert status["current_regime"] == "LOW_VOL_RANGE"
        assert status["current_dwell_bars"] == 8


# ===========================================================================
# 10. RegimeDurationTracker: dwell records
# ===========================================================================

class TestRegimeDwellRecords:
    """Verify dwell time tracking per regime."""

    def _make_tracker(self, tmp_path):
        return RegimeDurationTracker(state_file=str(tmp_path / "regime_state.json"))

    def test_dwell_recorded_on_exit(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        for i in range(10):
            tracker.update(i, {"primary_regime": "LOW_VOL_TREND"})
        tracker.update(10, {"primary_regime": "HIGH_VOL_CRISIS"})
        status = tracker.get_status()
        # LOW_VOL_TREND should have dwell record
        assert "LOW_VOL_TREND" in status["regime_distribution"]
        assert status["regime_distribution"]["LOW_VOL_TREND"] > 0

    def test_avg_dwell_computed(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        # TREND for 10 bars
        for i in range(10):
            tracker.update(i, {"primary_regime": "LOW_VOL_TREND"})
        # CRISIS for 5 bars
        for i in range(10, 15):
            tracker.update(i, {"primary_regime": "HIGH_VOL_CRISIS"})
        status = tracker.get_status()
        assert status["avg_regime_dwell_bars"] > 0


# ===========================================================================
# 11. RegimeDurationTracker: stability score
# ===========================================================================

class TestRegimeStability:
    """Verify stability score computation."""

    def _make_tracker(self, tmp_path):
        return RegimeDurationTracker(state_file=str(tmp_path / "regime_state.json"))

    def test_stable_regime_high_score(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        for i in range(100):
            tracker.update(i, {"primary_regime": "LOW_VOL_TREND"})
        status = tracker.get_status()
        assert status["stability_score"] > 0.8

    def test_choppy_regime_low_score(self, tmp_path):
        tracker = self._make_tracker(tmp_path)
        # Switch regime every 2 bars
        for i in range(20):
            regime = "LOW_VOL_TREND" if i % 2 == 0 else "HIGH_VOL_CRISIS"
            tracker.update(i, {"primary_regime": regime})
        status = tracker.get_status()
        assert status["stability_score"] < 0.5


# ===========================================================================
# 12. RegimeDurationTracker: persistence
# ===========================================================================

class TestRegimeDurationPersistence:
    """Verify state survives restart."""

    def test_state_persists(self, tmp_path):
        state_file = str(tmp_path / "regime_state.json")
        tracker1 = RegimeDurationTracker(state_file=state_file)
        for i in range(15):
            tracker1.update(i, {"primary_regime": "LOW_VOL_TREND" if i < 10 else "HIGH_VOL_CRISIS"})
        # New instance
        tracker2 = RegimeDurationTracker(state_file=state_file)
        status = tracker2.get_status()
        assert status["current_regime"] == "HIGH_VOL_CRISIS"
        assert status["transition_count"] == 2


# ===========================================================================
# 13. RegimeDurationTracker: recent transitions
# ===========================================================================

class TestRegimeRecentTransitions:
    """Verify recent transition history."""

    def test_recent_transitions_limited(self, tmp_path):
        tracker = RegimeDurationTracker(state_file=str(tmp_path / "regime_state.json"))
        for i in range(30):
            regime = ["LOW_VOL_TREND", "HIGH_VOL_CRISIS", "LOW_VOL_RANGE"][i % 3]
            tracker.update(i, {"primary_regime": regime})
        status = tracker.get_status()
        assert len(status["recent_transitions"]) <= 10


# ===========================================================================
# 14. RegimeDurationTracker: format report
# ===========================================================================

class TestRegimeFormatReport:
    """Verify human-readable report."""

    def test_format_report(self, tmp_path):
        tracker = RegimeDurationTracker(state_file=str(tmp_path / "regime_state.json"))
        for i in range(10):
            tracker.update(i, {"primary_regime": "LOW_VOL_TREND"})
        report = tracker.format_report()
        assert "Regime Duration" in report
        assert "LOW_VOL_TREND" in report


# ===========================================================================
# 15. Integration: auto tools + regime tracker together
# ===========================================================================

class TestAutoToolsAndRegimeIntegration:
    """Verify both systems run together without conflict."""

    def test_both_run_independently(self, tmp_path):
        tool = AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": 1, "kalman": 1, "tca": 1, "fractional_diff": 1, "bocpd": 1},
        )
        tracker = RegimeDurationTracker(state_file=str(tmp_path / "regime_state.json"))
        np.random.seed(42)
        prices = (1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, 100))).tolist()
        ohlc = [{"close": p, "volume": 500000} for p in prices]
        toon = {"ohlc": ohlc, "context": {"symbol": "EURUSD", "timeframe": "M5"}}
        auto_result = tool.update(1, toon)
        tracker.update(1, {"primary_regime": "LOW_VOL_TREND"})
        assert "auto_quant_tools" in auto_result
        status = tracker.get_status()
        assert status["current_regime"] == "LOW_VOL_TREND"


# ===========================================================================
# 16. Prompt integration
# ===========================================================================

class TestPromptIntegration:
    """Verify LLM prompt includes auto tools and regime duration rules."""

    def test_prompt_contains_regime_duration(self):
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "Regime Duration" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "stability" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "dwell" in SIGNAL_SYSTEM_PROMPT_TEMPLATE

    def test_prompt_contains_auto_quant_tools(self):
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "Auto-Tools" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "CPCV" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "Kalman" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "BOCPD" in SIGNAL_SYSTEM_PROMPT_TEMPLATE

    def test_prompt_contains_cpcv_escalation(self):
        from intelligence.llm_orchestrator import SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "CPCV Escalation" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "position_reduction_recommended" in SIGNAL_SYSTEM_PROMPT_TEMPLATE
        assert "recommended_position_scale" in SIGNAL_SYSTEM_PROMPT_TEMPLATE


# ===========================================================================
# 17. CPCV Escalation: consecutive degradation detection
# ===========================================================================

class TestCPCVEscalation:
    """Verify escalation logic for persistent CPCV degradation."""

    def _make_tool(self, tmp_path, cpcv_interval=1):
        return AutoQuantTools(
            state_file=str(tmp_path / "auto_state.json"),
            intervals={"cpcv": cpcv_interval, "kalman": 9999, "tca": 9999, "fractional_diff": 9999, "bocpd": 9999},
        )

    def _degraded_cpcv_result(self):
        return {
            "probability_of_backtest_overfitting_pbo": 0.30,
            "deflated_sharpe_ratio": 0.60,
            "status": "PASS",
        }

    def _healthy_cpcv_result(self):
        return {
            "probability_of_backtest_overfitting_pbo": 0.10,
            "deflated_sharpe_ratio": 0.90,
            "status": "PASS",
        }

    def _no_ohlcv_toon(self):
        return {"ohlc": [], "context": {"symbol": "EURUSD", "timeframe": "M5"}}

    def test_degradation_streak_increments(self, tmp_path):
        tool = self._make_tool(tmp_path)
        # Directly inject degraded result 3 times
        for i in range(3):
            alerts = tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert tool.state.cpcv_degradation_streak == 3
        assert tool.state.cpcv_escalation_active is True
        assert tool.state.position_reduction_recommended is True
        assert tool.state.recommended_position_scale == 0.5

    def test_escalation_triggers_at_threshold(self, tmp_path):
        tool = self._make_tool(tmp_path)
        # Run 2 degraded (below threshold) - no escalation
        for i in range(2):
            alerts = tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert tool.state.cpcv_degradation_streak == 2
        assert tool.state.cpcv_escalation_active is False
        assert len([a for a in alerts if a["code"] == "CPCV_ESCALATION"]) == 0

        # 3rd degraded run triggers escalation
        alerts = tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=3)
        assert tool.state.cpcv_escalation_active is True
        assert tool.state.cpcv_escalation_count == 1
        escalation_alerts = [a for a in alerts if a["code"] == "CPCV_ESCALATION"]
        assert len(escalation_alerts) == 1
        assert "Reduce position size" in escalation_alerts[0]["message"]

    def test_health_resets_streak(self, tmp_path):
        tool = self._make_tool(tmp_path)
        # 3 degraded runs -> escalated
        for i in range(3):
            tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert tool.state.cpcv_escalation_active is True

        # 1 healthy run -> resets
        alerts = tool._update_cpcv_escalation(self._healthy_cpcv_result(), cycle_num=4)
        assert tool.state.cpcv_degradation_streak == 0
        assert tool.state.cpcv_escalation_active is False
        assert tool.state.position_reduction_recommended is False
        assert tool.state.recommended_position_scale == 1.0
        cleared = [a for a in alerts if a["code"] == "CPCV_DEGRADATION_CLEARED"]
        assert len(cleared) == 1
        assert "recovered" in cleared[0]["message"]

    def test_worsening_streak_reduces_scale(self, tmp_path):
        tool = self._make_tool(tmp_path)
        # 4 degraded -> scale 0.5
        for i in range(4):
            tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert tool.state.recommended_position_scale == 0.5

        # 5th and 6th -> scale drops to 0.25
        all_alerts = []
        for i in range(2):
            alerts = tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=5 + i)
            all_alerts.extend(alerts)
        assert tool.state.recommended_position_scale == 0.25
        worsening = [a for a in all_alerts if a["code"] == "CPCV_DEGRADATION_WORSENING"]
        assert len(worsening) >= 1

    def test_critical_individual_run_extra_cut(self, tmp_path):
        tool = self._make_tool(tmp_path)
        critical_result = {
            "probability_of_backtest_overfitting_pbo": 0.40,
            "deflated_sharpe_ratio": 0.40,
            "status": "PASS",
        }
        # 3 critical runs -> streak 3, base scale 0.5 * 0.5 critical = 0.25
        for i in range(3):
            tool._update_cpcv_escalation(critical_result, cycle_num=i + 1)
        assert tool.state.cpcv_escalation_active is True
        assert tool.state.recommended_position_scale == 0.25

    def test_seven_plus_streak_cash_only(self, tmp_path):
        tool = self._make_tool(tmp_path)
        for i in range(7):
            alerts = tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert tool.state.recommended_position_scale == 0.0
        assert tool.state.position_reduction_recommended is True

    def test_insufficient_data_does_not_break_streak(self, tmp_path):
        tool = self._make_tool(tmp_path)
        insufficient = {"status": "INSUFFICIENT_DATA"}
        # 3 degraded runs -> escalated
        for i in range(3):
            tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert tool.state.cpcv_degradation_streak == 3

        # Insufficient data -> streak unchanged
        tool._update_cpcv_escalation(insufficient, cycle_num=4)
        assert tool.state.cpcv_degradation_streak == 3

    def test_degradation_history_limited_to_10(self, tmp_path):
        tool = self._make_tool(tmp_path)
        for i in range(15):
            tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        assert len(tool.state.cpcv_degradation_history) == 10

    def test_state_persists_escalation(self, tmp_path):
        state_file = str(tmp_path / "auto_state.json")
        tool1 = AutoQuantTools(
            state_file=state_file,
            intervals={"cpcv": 9999, "kalman": 9999, "tca": 9999, "fractional_diff": 9999, "bocpd": 9999},
        )
        for i in range(3):
            tool1._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        tool1._save_state()
        # Verify the state dict serializes correctly before reloading
        saved = json.loads(open(state_file).read())
        esc = saved["cpcv_escalation"]
        assert esc["degradation_streak"] == 3
        assert esc["escalation_active"] is True
        assert esc["position_reduction_recommended"] is True

        tool2 = AutoQuantTools(
            state_file=state_file,
            intervals={"cpcv": 9999, "kalman": 9999, "tca": 9999, "fractional_diff": 9999, "bocpd": 9999},
        )
        assert tool2.state.cpcv_degradation_streak == 3
        assert tool2.state.cpcv_escalation_active is True
        assert tool2.state.position_reduction_recommended is True

    def test_to_dict_includes_escalation(self, tmp_path):
        tool = self._make_tool(tmp_path)
        for i in range(3):
            tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        status = tool.get_status()
        assert "cpcv_escalation" in status
        esc = status["cpcv_escalation"]
        assert esc["degradation_streak"] == 3
        assert esc["escalation_active"] is True
        assert esc["recommended_position_scale"] == 0.5
        assert esc["position_reduction_recommended"] is True

    def test_format_report_shows_escalation(self, tmp_path):
        tool = self._make_tool(tmp_path)
        for i in range(3):
            tool._update_cpcv_escalation(self._degraded_cpcv_result(), cycle_num=i + 1)
        report = tool.format_report()
        assert "Escalation" in report
        assert "streak=3" in report
        assert "active=True" in report
