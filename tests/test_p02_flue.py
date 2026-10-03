"""P02 FLUEY (Jha 2026) - thresholds, predicates, registry, detector hardening."""
import pytest

from core import flue_thresholds as flue
from core.flue_thresholds import FlueConfig, scan
from core.multimodal_analysis import CandlestickDetector, PatternType


def test_config_matches_paper_sec_6_1():
    cfg = FlueConfig()
    assert (cfg.doji_body, cfg.large_body, cfg.small_body) == (0.05, 0.6, 0.3)
    assert cfg.long_shadow_mult == 2.0
    assert cfg.gap_tol == 0.001
    assert cfg.eps == 1e-9


def test_doji_boundary():
    assert flue.is_doji(100.0, 100.05, 101.0, 99.0)      # rho = 0.025
    assert flue.is_doji(100.0, 100.10, 101.0, 99.0)      # rho = 0.05 exact
    assert not flue.is_doji(100.0, 100.12, 101.0, 99.0)  # rho = 0.06


def test_hammer_needs_bullish_close_and_long_lower():
    assert flue.hammer(100.0, 102.0, 94.0, 100.8)        # D=6 > 2*0.8
    assert not flue.hammer(100.8, 102.0, 94.0, 100.0)    # bearish -> hanging man
    assert flue.hanging_man(100.8, 102.0, 94.0, 100.0)
    assert not flue.hammer(100.0, 101.0, 99.5, 100.8)    # D=0.5 < 2*0.8


def test_engulfing_requires_exact_containment():
    contained = [(100.0, 101.0, 99.0, 100.5), (100.6, 101.0, 97.0, 98.0)]
    assert flue.bearish_engulfing(contained)
    # Big but NOT containing (o2 inside prior body): old 1.2x rule fired, FLUE must not
    big_not_containing = [(100.0, 101.0, 99.0, 100.5), (100.4, 101.0, 97.0, 98.0)]
    assert not flue.bearish_engulfing(big_not_containing)


def test_morning_star_midpoint_rule():
    bars = [(110.0, 111.0, 99.0, 100.0),   # long bearish
            (100.0, 101.0, 99.0, 100.5),   # small: rho = 0.25
            (106.0, 109.0, 105.0, 108.0)]  # O3=106 > midpoint 105
    assert flue.morning_star(bars)
    weak = [(110.0, 111.0, 99.0, 100.0),
            (100.0, 101.0, 99.0, 100.5),
            (104.0, 109.0, 103.0, 108.0)]  # O3=104 < midpoint 105
    assert not flue.morning_star(weak)


def test_scan_longest_first():
    # Bar2 is both a hammer (single) and completes a bullish engulfing (2-candle)
    bars = [(101.0, 101.5, 99.5, 100.0),
            (99.9, 101.2, 97.0, 101.1)]
    assert flue.hammer(*bars[1])
    assert flue.bullish_engulfing(bars)
    labels = scan(bars)
    assert labels == [("bullish_engulfing", "bullish_reversal", 1)]


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 1000}


def _neutral(n=5):
    return [_bar(100.0, 102.0, 98.0, 101.0) for _ in range(n)]


def test_detector_catches_flue_hammer_with_big_upper_wick():
    # Old rule (upper < 0.3B) missed this; FLUE hammer = C>O + D>2B
    bars = _neutral() + [_bar(100.0, 102.0, 94.0, 100.8)]
    types = {p.pattern_type for p in CandlestickDetector().detect(bars)}
    assert PatternType.HAMMER in types


def test_detector_rejects_non_containing_engulfing():
    bars = _neutral() + [_bar(100.0, 101.0, 99.5, 100.5),
                         _bar(100.4, 101.0, 97.0, 98.0)]
    types = {p.pattern_type for p in CandlestickDetector().detect(bars)}
    assert PatternType.ENGULFING_BEARISH not in types
    assert PatternType.ENGULFING_BULLISH not in types
