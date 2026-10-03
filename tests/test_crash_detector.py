"""Tests for Logic-Q inspired CrashDetector module.

Covers:
- Indicator calculations (volatility, downside risk, growth rate)
- Program sketch evaluation (5 trends)
- Tuning parameters (risk, position, confidence, entry gate)
- State persistence
- Crash streak tracking
- Edge cases
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest

from core.crash_detector import (
    CrashDetector,
    CrashDetectorState,
    MarketTrend,
    ProgramSketchEvaluator,
    ProgramSketchParameters,
    TrendSignal,
    calculate_downside_risk,
    calculate_growth_rate,
    calculate_volatility,
)


# ---------------------------------------------------------------------------
# Indicator Tests
# ---------------------------------------------------------------------------

class TestVolatility:
    def test_constant_prices(self):
        closes = np.array([100.0] * 20)
        assert calculate_volatility(closes) == 0.0

    def test_volatile_prices(self):
        closes = np.array([100.0, 110.0, 90.0, 115.0, 85.0] * 4)
        vol = calculate_volatility(closes)
        assert vol > 0

    def test_short_window(self):
        closes = np.array([100.0, 101.0, 102.0])
        vol = calculate_volatility(closes, window=3)
        assert vol > 0

    def test_empty(self):
        assert calculate_volatility(np.array([])) == 0.0

    def test_single_price(self):
        assert calculate_volatility(np.array([100.0])) == 0.0


class TestDownsideRisk:
    def test_no_downside(self):
        # All prices equal -> no downside below mean
        closes = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        dr = calculate_downside_risk(closes)
        assert dr == 0.0

    def test_all_downside(self):
        # All prices below mean
        closes = np.array([100.0, 90.0, 80.0, 70.0, 60.0])
        dr = calculate_downside_risk(closes)
        assert dr > 0

    def test_mixed(self):
        closes = np.array([100.0, 110.0, 90.0, 105.0, 95.0])
        dr = calculate_downside_risk(closes)
        assert dr > 0

    def test_empty(self):
        assert calculate_downside_risk(np.array([])) == 0.0


class TestGrowthRate:
    def test_positive_growth(self):
        closes = np.array([100.0, 105.0, 110.0])
        gr = calculate_growth_rate(closes)
        assert gr == pytest.approx(10.0, abs=0.01)

    def test_negative_growth(self):
        closes = np.array([100.0, 95.0, 90.0])
        gr = calculate_growth_rate(closes)
        assert gr == 0.0  # Clamped to 0

    def test_zero_start(self):
        closes = np.array([0.0, 10.0])
        gr = calculate_growth_rate(closes)
        assert gr == 0.0

    def test_empty(self):
        assert calculate_growth_rate(np.array([])) == 0.0


# ---------------------------------------------------------------------------
# Program Sketch Tests
# ---------------------------------------------------------------------------

class TestProgramSketchEvaluator:
    def _make_evaluator(self, **overrides) -> ProgramSketchEvaluator:
        params = ProgramSketchParameters(**overrides)
        return ProgramSketchEvaluator(params)

    def test_rapid_descend(self):
        # High volatility + high downside risk
        closes = np.array([100, 110, 80, 115, 70, 120, 60, 125, 50, 130,
                           45, 135, 40, 140, 35, 145, 30, 150, 25, 155],
                          dtype=float)
        ev = self._make_evaluator(
            rapid_descend_vol_threshold=0.01,
            rapid_descend_dr_threshold=0.01,
        )
        signal = ev.evaluate(closes)
        assert signal.trend == MarketTrend.RAPID_DESCEND
        assert signal.entries_allowed is False
        assert signal.risk_multiplier < 1.0
        assert signal.position_scale < 1.0

    def test_steady_descend(self):
        # Low volatility + high downside risk (steady decline)
        # vol=33.25, dr=5.77, gr=0.0
        closes = np.array([100, 99, 98, 97, 96, 95, 94, 93, 92, 91,
                           90, 89, 88, 87, 86, 85, 84, 83, 82, 81],
                          dtype=float)
        ev = self._make_evaluator(
            rapid_descend_vol_threshold=50.0,  # Above vol=33.25
            rapid_descend_dr_threshold=10.0,   # Above dr=5.77
            steady_descend_vol_threshold=50.0,  # Above vol=33.25
            steady_descend_dr_threshold=1.0,    # Below dr=5.77
        )
        signal = ev.evaluate(closes)
        assert signal.trend == MarketTrend.STEADY_DESCEND
        assert signal.risk_multiplier < 1.0
        assert signal.position_scale < 1.0

    def test_rapid_ascend(self):
        # High volatility + high growth: last price >> first price with big swings
        # Pattern: low,high,low,high... with highs increasing, last=highest
        # vol=100631, gr=900
        closes = np.array([100, 150, 90, 200, 80, 300, 70, 400, 60, 500,
                           50, 600, 45, 700, 40, 800, 35, 900, 30, 1000],
                          dtype=float)
        ev = self._make_evaluator(
            rapid_descend_vol_threshold=1e9,    # Disable rapid descend
            rapid_descend_dr_threshold=1e9,
            rapid_ascend_vol_threshold=100.0,   # Below vol=100631
            rapid_ascend_gr_threshold=1.0,      # Below gr=900
        )
        signal = ev.evaluate(closes)
        assert signal.trend == MarketTrend.RAPID_ASCEND
        assert signal.entries_allowed is True
        assert signal.risk_multiplier >= 1.0

    def test_steady_ascend(self):
        # Low volatility + positive growth
        # vol=33.25, dr=5.77, gr=19.0
        closes = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                           110, 111, 112, 113, 114, 115, 116, 117, 118, 119],
                          dtype=float)
        ev = self._make_evaluator(
            rapid_descend_vol_threshold=50.0,   # Above vol=33.25
            rapid_descend_dr_threshold=10.0,    # Above dr=5.77
            rapid_ascend_vol_threshold=50.0,    # Above vol=33.25
            rapid_ascend_gr_threshold=20.0,     # Above gr=19.0
            steady_ascend_vol_threshold=50.0,   # Above vol=33.25
            steady_ascend_gr_threshold=1.0,     # Below gr=19.0
        )
        signal = ev.evaluate(closes)
        assert signal.trend == MarketTrend.STEADY_ASCEND
        assert signal.entries_allowed is True
        assert signal.risk_multiplier == 1.0

    def test_oscillation_default(self):
        # Flat prices -> oscillation
        closes = np.array([100.0] * 20)
        ev = self._make_evaluator()
        signal = ev.evaluate(closes)
        assert signal.trend == MarketTrend.OSCILLATION
        assert signal.entries_allowed is True

    def test_short_data_returns_oscillation(self):
        ev = self._make_evaluator(
            rapid_descend_vol_threshold=1000.0,  # Disable rapid descend
            rapid_descend_dr_threshold=1000.0,
        )
        signal = ev.evaluate(np.array([100.0, 101.0, 102.0]))
        assert signal.trend == MarketTrend.OSCILLATION

    def test_signal_to_dict(self):
        closes = np.array([100.0] * 20)
        ev = self._make_evaluator()
        signal = ev.evaluate(closes)
        d = signal.to_dict()
        assert "trend" in d
        assert "risk_multiplier" in d
        assert "entries_allowed" in d
        assert isinstance(d["trend"], str)


# ---------------------------------------------------------------------------
# CrashDetector Tests
# ---------------------------------------------------------------------------

class TestCrashDetector:
    def _make_detector(self, tmp_path: Path) -> CrashDetector:
        state_file = str(tmp_path / "crash_state.json")
        return CrashDetector(state_file=state_file)

    def test_basic_evaluation(self, tmp_path):
        det = self._make_detector(tmp_path)
        closes = [100.0] * 20
        signal = det.evaluate(closes)
        assert isinstance(signal, TrendSignal)
        assert isinstance(signal.trend, MarketTrend)

    def test_state_persists(self, tmp_path):
        det = self._make_detector(tmp_path)
        closes = [100.0] * 20
        det.evaluate(closes)
        
        # Load new instance
        det2 = self._make_detector(tmp_path)
        assert det2.state.total_evaluations == 1

    def test_consecutive_rapid_descends(self, tmp_path):
        det = self._make_detector(tmp_path)
        # Evaluate with rapid descend conditions multiple times
        volatile_closes = [100, 110, 80, 115, 70, 120, 60, 125, 50, 130,
                           45, 135, 40, 140, 35, 145, 30, 150, 25, 155]
        for _ in range(3):
            signal = det.evaluate(volatile_closes)
        
        assert det.state.consecutive_rapid_descends == 3
        assert det.state.max_consecutive_rapid_descends == 3

    def test_crash_streak_resets(self, tmp_path):
        det = self._make_detector(tmp_path)
        volatile_closes = [100, 110, 80, 115, 70, 120, 60, 125, 50, 130,
                           45, 135, 40, 140, 35, 145, 30, 150, 25, 155]
        det.evaluate(volatile_closes)
        det.evaluate(volatile_closes)
        
        # Now evaluate with calm data
        calm_closes = [100.0] * 20
        det.evaluate(calm_closes)
        
        assert det.state.consecutive_rapid_descends == 0
        assert det.state.max_consecutive_rapid_descends == 2

    def test_get_status(self, tmp_path):
        det = self._make_detector(tmp_path)
        det.evaluate([100.0] * 20)
        status = det.get_status()
        assert "last_trend" in status
        assert "consecutive_rapid_descends" in status
        assert "total_evaluations" in status
        assert "crash_streak_active" in status
        assert "params" in status

    def test_get_recent_trends(self, tmp_path):
        det = self._make_detector(tmp_path)
        for _ in range(5):
            det.evaluate([100.0] * 20)
        trends = det.get_recent_trends(3)
        assert len(trends) == 3

    def test_empty_data(self, tmp_path):
        det = self._make_detector(tmp_path)
        signal = det.evaluate([])
        assert signal.trend == MarketTrend.OSCILLATION
        assert signal.entries_allowed is True

    def test_minimum_data(self, tmp_path):
        det = self._make_detector(tmp_path)
        signal = det.evaluate([100.0, 101.0])
        assert isinstance(signal, TrendSignal)


# ---------------------------------------------------------------------------
# ProgramSketchParameters Tests
# ---------------------------------------------------------------------------

class TestProgramSketchParameters:
    def test_default_params(self):
        p = ProgramSketchParameters()
        assert p.rapid_descend_vol_threshold > 0
        assert p.phi_rapid_descend_risk < 1.0
        assert p.phi_rapid_descend_entry == 0.0

    def test_to_dict(self):
        p = ProgramSketchParameters()
        d = p.to_dict()
        assert isinstance(d, dict)
        assert "rapid_descend_vol_threshold" in d

    def test_from_dict(self):
        d = ProgramSketchParameters().to_dict()
        d["rapid_descend_vol_threshold"] = 0.005
        p = ProgramSketchParameters.from_dict(d)
        assert p.rapid_descend_vol_threshold == 0.005

    def test_param_count(self):
        p = ProgramSketchParameters()
        assert p.param_count() == 28  # 10 thresholds + 18 tuning params


# ---------------------------------------------------------------------------
# CrashDetectorState Tests
# ---------------------------------------------------------------------------

class TestCrashDetectorState:
    def test_default_state(self):
        s = CrashDetectorState()
        assert s.last_trend == MarketTrend.OSCILLATION.value
        assert s.consecutive_rapid_descends == 0

    def test_to_dict(self):
        s = CrashDetectorState()
        d = s.to_dict()
        assert "last_trend" in d
        assert "trend_history" in d

    def test_from_dict(self):
        d = CrashDetectorState().to_dict()
        d["consecutive_rapid_descends"] = 5
        s = CrashDetectorState.from_dict(d)
        assert s.consecutive_rapid_descends == 5

    def test_history_capped(self):
        s = CrashDetectorState()
        s.trend_history = [{"trend": "OSCILLATION"} for _ in range(100)]
        d = s.to_dict()
        assert len(d["trend_history"]) <= 50
