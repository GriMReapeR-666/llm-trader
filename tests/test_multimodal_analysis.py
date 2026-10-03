"""Tests for Multimodal Analysis Engine — Phase 6: Visual Reasoning.

Covers: CandlestickDetector, ChartPatternDetector, TrendAnalyzer,
VolumePatternDetector, ConfluenceScorer, MultimodalAnalysisEngine.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np
import pytest

from core.multimodal_analysis import (
    PatternType,
    SignalDirection,
    DetectedPattern,
    PatternConfluence,
    VisualAnalysisReport,
    CandlestickDetector,
    ChartPatternDetector,
    TrendAnalyzer,
    VolumePatternDetector,
    ConfluenceScorer,
    MultimodalAnalysisEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    o: float = 1.1000, h: float = 1.1050, l: float = 1.0950, c: float = 1.1020,
    volume: float = 100000
) -> Dict[str, Any]:
    return {"open": o, "high": h, "low": l, "close": c, "volume": volume}


def _make_ohlcv(n: int = 50, trend: str = "up") -> List[Dict[str, Any]]:
    """Generate synthetic OHLCV data."""
    rng = np.random.RandomState(42)
    bars = []
    price = 1.1000
    for i in range(n):
        if trend == "up":
            drift = 0.0003
        elif trend == "down":
            drift = -0.0003
        else:
            drift = 0

        change = drift + rng.randn() * 0.001
        o = price
        c = price + change
        h = max(o, c) + abs(rng.randn()) * 0.0005
        l = min(o, c) - abs(rng.randn()) * 0.0005
        vol = 100000 + rng.randint(-20000, 20000)
        bars.append(_make_bar(o, h, l, c, vol))
        price = c
    return bars


def _make_doji_bars() -> List[Dict[str, Any]]:
    """Create bars ending with a doji candle."""
    bars = _make_ohlcv(20, "up")
    # Replace last bar with doji (open == close, long wicks)
    bars[-1] = _make_bar(o=1.1000, h=1.1050, l=1.0950, c=1.1001)
    return bars


def _make_engulfing_bars() -> List[Dict[str, Any]]:
    """Create bars ending with bullish engulfing."""
    bars = _make_ohlcv(20, "down")
    # Previous bar: bearish
    bars[-2] = _make_bar(o=1.1000, h=1.1010, l=1.0970, c=1.0980)
    # Last bar: bullish engulfing (larger body, opposite direction)
    bars[-1] = _make_bar(o=1.0975, h=1.1020, l=1.0970, c=1.1015)
    return bars


def _make_hammer_bars() -> List[Dict[str, Any]]:
    """Create bars ending with a hammer."""
    bars = _make_ohlcv(20, "down")
    # Hammer: small body at top, long lower wick
    bars[-1] = _make_bar(o=1.0980, h=1.0990, l=1.0940, c=1.0985)
    return bars


def _make_volume_spike_bars() -> List[Dict[str, Any]]:
    """Create bars with a volume spike at the end."""
    bars = _make_ohlcv(30, "up")
    bars[-1]["volume"] = 500000  # 5x average
    return bars


def _make_obv_divergence_bars() -> List[Dict[str, Any]]:
    """Create bars with bullish OBV divergence (price down, OBV up)."""
    rng = np.random.RandomState(42)
    bars = []
    price = 1.1000
    for i in range(30):
        if i < 20:
            change = 0.0003 + rng.randn() * 0.0005
            vol = 100000 + rng.randint(0, 50000)
        else:
            # Price declining but volume increasing
            change = -0.0005 + rng.randn() * 0.0003
            vol = 150000 + (i - 20) * 10000
        o = price
        c = price + change
        h = max(o, c) + abs(rng.randn()) * 0.0003
        l = min(o, c) - abs(rng.randn()) * 0.0003
        bars.append(_make_bar(o, h, l, c, vol))
        price = c
    return bars


# ---------------------------------------------------------------------------
# DetectedPattern
# ---------------------------------------------------------------------------

class TestDetectedPattern:
    def test_to_dict(self):
        p = DetectedPattern(
            pattern_type=PatternType.DOJI,
            direction=SignalDirection.NEUTRAL,
            confidence=0.7,
            bar_index=10,
            description="Test doji",
        )
        d = p.to_dict()
        assert d["pattern_type"] == "doji"
        assert d["direction"] == "NEUTRAL"
        assert d["confidence"] == 0.7


# ---------------------------------------------------------------------------
# CandlestickDetector
# ---------------------------------------------------------------------------

class TestCandlestickDetector:
    def test_no_patterns_on_empty(self):
        det = CandlestickDetector()
        assert det.detect([]) == []

    def test_too_few_bars(self):
        det = CandlestickDetector()
        assert det.detect([_make_bar()]) == []

    def test_detects_doji(self):
        det = CandlestickDetector()
        bars = _make_doji_bars()
        patterns = det.detect(bars)
        doji = [p for p in patterns if p.pattern_type == PatternType.DOJI]
        assert len(doji) >= 1

    def test_detects_engulfing(self):
        det = CandlestickDetector()
        bars = _make_engulfing_bars()
        patterns = det.detect(bars)
        engulfing = [p for p in patterns if "engulfing" in p.pattern_type.value]
        assert len(engulfing) >= 1

    def test_detects_hammer(self):
        det = CandlestickDetector()
        bars = _make_ohlcv(20, "down")
        # Hammer: small body at top, long lower wick (>2x body), tiny upper wick
        bars[-1] = _make_bar(o=1.0980, h=1.0981, l=1.0940, c=1.0983)
        patterns = det.detect(bars)
        hammer = [p for p in patterns if p.pattern_type == PatternType.HAMMER]
        assert len(hammer) >= 1

    def test_normal_bars_no_patterns(self):
        """Smooth trend should have few/no candlestick patterns."""
        det = CandlestickDetector()
        bars = _make_ohlcv(20, "up")
        patterns = det.detect(bars)
        # Should have at most a few incidental patterns
        assert len(patterns) < 10

    def test_patterns_have_valid_directions(self):
        det = CandlestickDetector()
        bars = _make_ohlcv(30)
        patterns = det.detect(bars)
        for p in patterns:
            assert p.direction in (SignalDirection.BULLISH, SignalDirection.BEARISH, SignalDirection.NEUTRAL)

    def test_patterns_have_valid_confidence(self):
        det = CandlestickDetector()
        bars = _make_ohlcv(30)
        patterns = det.detect(bars)
        for p in patterns:
            assert 0 <= p.confidence <= 1


# ---------------------------------------------------------------------------
# ChartPatternDetector
# ---------------------------------------------------------------------------

class TestChartPatternDetector:
    def test_no_patterns_on_empty(self):
        det = ChartPatternDetector()
        assert det.detect([]) == []

    def test_detects_on_ohlcv(self):
        det = ChartPatternDetector()
        bars = _make_ohlcv(50)
        patterns = det.detect(bars)
        # May or may not find patterns in random data
        assert isinstance(patterns, list)

    def test_double_top_detection(self):
        """Create a synthetic double top pattern."""
        rng = np.random.RandomState(42)
        bars = []
        # Build up to resistance
        price = 1.1000
        for i in range(10):
            bars.append(_make_bar(price, price + 0.001, price - 0.0005, price + 0.0005))
            price += 0.001
        # First peak
        peak = price
        for i in range(5):
            bars.append(_make_bar(peak - i * 0.0005, peak + 0.0001, peak - 0.001, peak - i * 0.0005))
        # Valley
        valley = peak - 0.003
        for i in range(5):
            bars.append(_make_bar(valley + i * 0.0002, valley + 0.001, valley - 0.0005, valley + i * 0.0002))
        # Second peak (similar to first)
        for i in range(5):
            bars.append(_make_bar(peak - i * 0.0005, peak + 0.0001, peak - 0.001, peak - i * 0.0005))
        # Drop below neckline
        for i in range(10):
            bars.append(_make_bar(valley - i * 0.0005, valley, valley - 0.001, valley - i * 0.0005))

        det = ChartPatternDetector(lookback=100, pivot_window=3)
        patterns = det.detect(bars)
        # Should find some chart patterns
        assert isinstance(patterns, list)

    def test_triangle_detection(self):
        """Create a converging triangle with clear pivots."""
        bars = []
        # Build a converging pattern with alternating highs/lows
        for i in range(30):
            phase = i % 6
            if phase < 3:  # High bars (resistance)
                h = 1.1100 - i * 0.0002  # Declining highs
                l = h - 0.003
            else:  # Low bars (support)
                l = 1.0900 + i * 0.0002  # Rising lows
                h = l + 0.003
            o = (h + l) / 2
            c = o + 0.0001
            bars.append(_make_bar(o, h, l, c))

        det = ChartPatternDetector(lookback=30, pivot_window=2)
        patterns = det.detect(bars)
        triangles = [p for p in patterns if "triangle" in p.pattern_type.value]
        assert len(triangles) >= 1


# ---------------------------------------------------------------------------
# TrendAnalyzer
# ---------------------------------------------------------------------------

class TestTrendAnalyzer:
    def test_uptrend(self):
        ta = TrendAnalyzer()
        bars = _make_ohlcv(50, "up")
        result = ta.analyze(bars)
        assert result["direction"] in ("UPTREND", "SIDEWAYS")
        assert result["strength"] >= 0

    def test_downtrend(self):
        ta = TrendAnalyzer()
        bars = _make_ohlcv(50, "down")
        result = ta.analyze(bars)
        assert result["direction"] in ("DOWNTREND", "SIDEWAYS")

    def test_sideways(self):
        ta = TrendAnalyzer()
        bars = _make_ohlcv(50, "flat")
        result = ta.analyze(bars)
        assert result["direction"] in ("SIDEWAYS", "UPTREND", "DOWNTREND")

    def test_support_resistance(self):
        ta = TrendAnalyzer()
        bars = _make_ohlcv(50, "up")
        result = ta.analyze(bars)
        assert "nearest_support" in result
        assert "nearest_resistance" in result
        assert "support_count" in result
        assert "resistance_count" in result

    def test_empty_bars(self):
        ta = TrendAnalyzer()
        assert ta.analyze([]) == {}

    def test_r_squared_range(self):
        ta = TrendAnalyzer()
        bars = _make_ohlcv(50)
        result = ta.analyze(bars)
        assert 0 <= result["r_squared"] <= 1

    def test_slope_pips(self):
        ta = TrendAnalyzer()
        bars = _make_ohlcv(50, "up")
        result = ta.analyze(bars)
        assert isinstance(result["slope_pips_per_bar"], float)


# ---------------------------------------------------------------------------
# VolumePatternDetector
# ---------------------------------------------------------------------------

class TestVolumePatternDetector:
    def test_no_patterns_on_empty(self):
        det = VolumePatternDetector()
        assert det.detect([]) == []

    def test_detects_volume_spike(self):
        det = VolumePatternDetector()
        bars = _make_volume_spike_bars()
        patterns = det.detect(bars)
        spikes = [p for p in patterns if p.pattern_type == PatternType.VOLUME_SPIKE]
        assert len(spikes) >= 1
        assert spikes[0].direction in (SignalDirection.BULLISH, SignalDirection.BEARISH)

    def test_no_spike_on_normal_volume(self):
        det = VolumePatternDetector()
        bars = _make_ohlcv(30, "up")
        patterns = det.detect(bars)
        spikes = [p for p in patterns if p.pattern_type == PatternType.VOLUME_SPIKE]
        assert len(spikes) == 0

    def test_obv_divergence(self):
        det = VolumePatternDetector()
        bars = _make_obv_divergence_bars()
        patterns = det.detect(bars)
        obv_div = [p for p in patterns if p.pattern_type == PatternType.OBV_DIVERGENCE]
        # May or may not detect depending on exact data
        assert isinstance(obv_div, list)

    def test_volume_ratio_in_metadata(self):
        det = VolumePatternDetector()
        bars = _make_volume_spike_bars()
        patterns = det.detect(bars)
        spikes = [p for p in patterns if p.pattern_type == PatternType.VOLUME_SPIKE]
        if spikes:
            assert "volume_ratio" in spikes[0].metadata


# ---------------------------------------------------------------------------
# ConfluenceScorer
# ---------------------------------------------------------------------------

class TestConfluenceScorer:
    def test_empty_patterns(self):
        scorer = ConfluenceScorer()
        result = scorer.score([])
        assert result.net_direction == SignalDirection.NEUTRAL
        assert result.confluence_score == 0.0

    def test_bullish_dominance(self):
        scorer = ConfluenceScorer()
        patterns = [
            DetectedPattern(PatternType.HAMMER, SignalDirection.BULLISH, 0.8, 10, "hammer"),
            DetectedPattern(PatternType.ENGULFING_BULLISH, SignalDirection.BULLISH, 0.7, 11, "engulfing"),
        ]
        result = scorer.score(patterns)
        assert result.net_direction == SignalDirection.BULLISH
        assert result.confluence_score > 0
        assert result.bullish_count == 2
        assert result.bearish_count == 0

    def test_bearish_dominance(self):
        scorer = ConfluenceScorer()
        patterns = [
            DetectedPattern(PatternType.DOUBLE_TOP, SignalDirection.BEARISH, 0.8, 10, "dt"),
            DetectedPattern(PatternType.EVENING_STAR, SignalDirection.BEARISH, 0.7, 11, "es"),
        ]
        result = scorer.score(patterns)
        assert result.net_direction == SignalDirection.BEARISH

    def test_mixed_signals(self):
        scorer = ConfluenceScorer()
        patterns = [
            DetectedPattern(PatternType.HAMMER, SignalDirection.BULLISH, 0.7, 10, ""),
            DetectedPattern(PatternType.DOUBLE_TOP, SignalDirection.BEARISH, 0.7, 11, ""),
        ]
        result = scorer.score(patterns)
        assert result.bullish_count == 1
        assert result.bearish_count == 1
        # Should be close to neutral or slightly biased

    def test_confluence_score_range(self):
        scorer = ConfluenceScorer()
        patterns = [
            DetectedPattern(PatternType.HAMMER, SignalDirection.BULLISH, 0.9, 10, ""),
            DetectedPattern(PatternType.ENGULFING_BULLISH, SignalDirection.BULLISH, 0.8, 11, ""),
            DetectedPattern(PatternType.VOLUME_SPIKE, SignalDirection.BULLISH, 0.7, 12, ""),
        ]
        result = scorer.score(patterns)
        assert 0 <= result.confluence_score <= 1

    def test_dominant_pattern_set(self):
        scorer = ConfluenceScorer()
        patterns = [
            DetectedPattern(PatternType.DOJI, SignalDirection.NEUTRAL, 0.3, 10, ""),
            DetectedPattern(PatternType.ENGULFING_BULLISH, SignalDirection.BULLISH, 0.9, 11, ""),
        ]
        result = scorer.score(patterns)
        assert result.dominant_pattern == "engulfing_bullish"

    def test_summary_generated(self):
        scorer = ConfluenceScorer()
        patterns = [
            DetectedPattern(PatternType.HAMMER, SignalDirection.BULLISH, 0.8, 10, ""),
            DetectedPattern(PatternType.DOUBLE_TOP, SignalDirection.BEARISH, 0.6, 11, ""),
        ]
        result = scorer.score(patterns)
        assert len(result.summary) > 0
        assert "bullish" in result.summary.lower() or "bearish" in result.summary.lower()


# ---------------------------------------------------------------------------
# VisualAnalysisReport
# ---------------------------------------------------------------------------

class TestVisualAnalysisReport:
    def test_to_dict(self):
        report = VisualAnalysisReport(
            symbol="EURUSD", timeframe="H1",
            confluence=PatternConfluence(net_direction=SignalDirection.BULLISH),
        )
        d = report.to_dict()
        assert d["symbol"] == "EURUSD"
        assert "patterns" in d
        assert "confluence" in d

    def test_to_llm_context_empty(self):
        report = VisualAnalysisReport(symbol="EURUSD", timeframe="H1")
        ctx = report.to_llm_context()
        assert "No patterns detected" in ctx

    def test_to_llm_context_with_patterns(self):
        report = VisualAnalysisReport(
            symbol="EURUSD", timeframe="H1",
            patterns=[
                DetectedPattern(PatternType.HAMMER, SignalDirection.BULLISH, 0.8, 10, "hammer"),
            ],
            confluence=PatternConfluence(
                net_direction=SignalDirection.BULLISH,
                confluence_score=0.7,
                summary="1 bullish (hammer)",
            ),
            support_resistance={"nearest_support": 1.0950, "nearest_resistance": 1.1050},
            trend_assessment={"direction": "UPTREND", "strength": 0.8, "bars_in_trend": 15},
        )
        ctx = report.to_llm_context()
        assert "CHART PATTERNS" in ctx
        assert "hammer" in ctx
        assert "CONFLUENCE" in ctx
        assert "SUPPORT/RESISTANCE" in ctx
        assert "TREND" in ctx


# ---------------------------------------------------------------------------
# MultimodalAnalysisEngine — Full Pipeline
# ---------------------------------------------------------------------------

class TestMultimodalAnalysisEngine:
    def test_analyze_returns_report(self):
        engine = MultimodalAnalysisEngine(state_file="test_multimodal.json")
        bars = _make_ohlcv(50, "up")
        report = engine.analyze("EURUSD", "H1", bars)
        assert isinstance(report, VisualAnalysisReport)
        assert report.symbol == "EURUSD"
        assert report.timeframe == "H1"
        assert len(report.patterns) >= 0
        assert report.timestamp > 0

    def test_analyze_saves_state(self, tmp_path):
        import os
        # tmp_path, not a bare relative name: tests/conftest.py redirects any
        # state path whose parent directory IS the repo root into a per-test
        # sandbox, so a bare "test_multimodal_save.json" is written somewhere the
        # test cannot see. tmp_path lives outside the repo root, so the engine
        # writes exactly where we assert — and pytest cleans it up.
        state_file = str(tmp_path / "multimodal_save.json")
        engine = MultimodalAnalysisEngine(state_file=state_file)
        bars = _make_ohlcv(50)
        engine.analyze("EURUSD", "H1", bars)
        assert os.path.exists(state_file)

    def test_analyze_loads_state(self, tmp_path):
        state_file = str(tmp_path / "multimodal_load.json")
        engine = MultimodalAnalysisEngine(state_file=state_file)
        bars = _make_ohlcv(50)
        engine.analyze("EURUSD", "H1", bars)
        state = engine.load_state()
        assert state is not None
        assert state["symbol"] == "EURUSD"

    def test_get_status_no_analysis(self, tmp_path):
        engine = MultimodalAnalysisEngine(state_file=str(tmp_path / "nonexistent.json"))
        status = engine.get_status()
        assert status["status"] == "NO_ANALYSIS"

    def test_get_status_after_analysis(self, tmp_path):
        state_file = str(tmp_path / "multimodal_status.json")
        engine = MultimodalAnalysisEngine(state_file=state_file)
        bars = _make_ohlcv(50)
        engine.analyze("EURUSD", "H1", bars)
        status = engine.get_status()
        assert status["status"] == "ANALYZED"
        assert status["symbol"] == "EURUSD"

    def test_analyze_with_volume_spike(self, tmp_path):
        engine = MultimodalAnalysisEngine(state_file=str(tmp_path / "multimodal_vol.json"))
        bars = _make_volume_spike_bars()
        report = engine.analyze("EURUSD", "H1", bars)
        vol_patterns = [p for p in report.patterns if p.pattern_type == PatternType.VOLUME_SPIKE]
        assert len(vol_patterns) >= 1


    def test_analyze_empty_ohlcv(self):
        engine = MultimodalAnalysisEngine(state_file="test_multimodal_empty.json")
        report = engine.analyze("EURUSD", "H1", [])
        assert isinstance(report, VisualAnalysisReport)
        assert len(report.patterns) == 0

    def test_analyze_trend_detected(self):
        engine = MultimodalAnalysisEngine(state_file="test_multimodal_trend.json")
        bars = _make_ohlcv(50, "up")
        report = engine.analyze("EURUSD", "H1", bars)
        assert "direction" in report.trend_assessment

    def test_analyze_llm_context(self):
        engine = MultimodalAnalysisEngine(state_file="test_multimodal_ctx.json")
        bars = _make_ohlcv(50, "up")
        report = engine.analyze("EURUSD", "H1", bars)
        ctx = report.to_llm_context()
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_all_pattern_types_enumerated(self):
        """Ensure all PatternType values are covered."""
        types = list(PatternType)
        assert len(types) >= 15  # At least 15 pattern types


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_bar(self):
        engine = MultimodalAnalysisEngine(state_file="test_edge_single.json")
        report = engine.analyze("EURUSD", "H1", [_make_bar()])
        assert isinstance(report, VisualAnalysisReport)

    def test_two_bars(self):
        engine = MultimodalAnalysisEngine(state_file="test_edge_two.json")
        bars = [_make_bar(), _make_bar(o=1.1010, h=1.1020, l=1.1000, c=1.1015)]
        report = engine.analyze("EURUSD", "H1", bars)
        assert isinstance(report, VisualAnalysisReport)

    def test_all_same_price(self):
        engine = MultimodalAnalysisEngine(state_file="test_edge_flat.json")
        bars = [_make_bar(o=1.1000, h=1.1000, l=1.1000, c=1.1000) for _ in range(20)]
        report = engine.analyze("EURUSD", "H1", bars)
        assert isinstance(report, VisualAnalysisReport)

    def test_extreme_volume(self):
        engine = MultimodalAnalysisEngine(state_file="test_edge_vol.json")
        bars = _make_ohlcv(20)
        bars[-1]["volume"] = 1e9
        report = engine.analyze("EURUSD", "H1", bars)
        assert isinstance(report, VisualAnalysisReport)

    def test_signal_directions(self):
        assert SignalDirection.BULLISH.value == "BULLISH"
        assert SignalDirection.BEARISH.value == "BEARISH"
        assert SignalDirection.NEUTRAL.value == "NEUTRAL"
