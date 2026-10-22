"""P02 FLUEY (Jha 2026) - thresholds, predicates, registry, detector hardening."""
from core import flue_thresholds as flue
from core.flue_thresholds import FlueConfig, scan
from core.multimodal_analysis import (
    FLUEY_BIAS_DIRECTION,
    FLUEY_PATTERN_TYPES,
    CandlestickDetector,
    MultimodalAnalysisEngine,
    PatternType,
    SignalDirection,
)


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


# --- Full paper catalogue (Sec. 4, 5.1.6, 5.2.4, 5.3) -------------------

def test_single_candle_extensions():
    assert flue.spinning_top(100.0, 101.0, 99.0, 100.2)
    assert not flue.spinning_top(100.0, 102.0, 99.0, 101.8)
    assert flue.long_white(100.0, 102.0, 99.0, 101.9)
    assert not flue.long_white(100.0, 102.0, 99.0, 101.0)
    assert flue.long_black(101.9, 102.0, 99.0, 100.0)
    assert flue.belt_hold_bull(100.0, 100.9, 100.0, 100.8)
    assert not flue.belt_hold_bull(100.0, 101.5, 99.5, 100.8)
    assert flue.belt_hold_bear(100.8, 100.8, 99.9, 100.0)
    assert flue.rickshaw_man(100.0, 102.0, 98.0, 100.04)


def test_gap_helpers_are_scale_invariant():
    assert flue.gap_up(111.0, 111.2)
    assert not flue.gap_up(111.0, 111.05)       # inside GAP_TOL
    assert flue.gap_down(99.0, 98.8)
    assert flue.near(100.0, 100.05)
    assert not flue.near(100.0, 100.5)


def test_harami_cross_and_counterattack():
    cross = [(110.0, 111.0, 99.0, 100.0), (100.02, 100.5, 99.5, 100.04)]
    assert flue.harami_cross(cross)
    not_cross = [cross[0], (100.02, 100.5, 99.5, 100.24)]  # rho = 0.22
    assert not flue.harami_cross(not_cross)
    assert flue.counterattack_bull(
        [(110.0, 111.0, 99.0, 100.0), (98.0, 101.0, 97.0, 100.02)])
    assert flue.counterattack_bear(
        [(100.0, 101.0, 99.0, 110.0), (112.0, 113.0, 109.0, 109.98)])
    assert not flue.counterattack_bull(
        [(110.0, 111.0, 99.0, 100.0), (98.0, 101.0, 97.0, 101.0)])


def test_kicking_separating_lines_matching_high():
    assert flue.kicking_bull(
        [(110.0, 111.0, 99.0, 100.0), (111.5, 115.0, 111.2, 114.0)])
    assert flue.kicking_bear(
        [(100.0, 101.0, 99.0, 110.0), (89.0, 89.8, 86.0, 87.0)])
    assert not flue.kicking_bull(
        [(110.0, 111.0, 99.0, 100.0), (110.5, 115.0, 110.2, 114.0)])
    assert flue.separating_lines_bull(
        [(110.0, 111.0, 99.0, 100.0), (100.0, 104.0, 99.5, 103.0)])
    assert flue.separating_lines_bear(
        [(100.0, 101.0, 99.0, 110.0), (110.0, 111.0, 107.0, 108.0)])
    assert not flue.separating_lines_bull(
        [(110.0, 111.0, 99.0, 100.0), (100.5, 104.0, 99.5, 103.0)])
    assert flue.matching_high(
        [(100.0, 105.0, 99.0, 101.0), (101.0, 105.0, 100.0, 103.0)])
    assert not flue.matching_high(
        [(100.0, 105.0, 99.0, 101.0), (101.0, 106.0, 100.0, 103.0)])


SOLDIERS = [
    (100.0, 101.0, 99.5, 100.8),
    (100.8, 102.0, 100.6, 101.9),
    (101.9, 103.0, 101.7, 102.9),
]
CROWS = [
    (110.0, 110.5, 109.0, 109.2),
    (109.2, 109.4, 108.0, 108.1),
    (108.1, 108.3, 106.0, 106.7),
]


def test_three_soldiers_and_crows_require_large_bodies():
    assert flue.three_white_soldiers(SOLDIERS)
    assert not flue.three_white_soldiers(
        SOLDIERS[:2] + [(101.9, 103.5, 101.8, 102.9)])  # rho = 0.59
    assert flue.three_black_crows(CROWS)
    assert not flue.three_black_crows(
        CROWS[:2] + [(108.1, 108.6, 106.0, 106.7)])      # rho = 0.56


def test_three_inside_and_outside():
    assert flue.three_inside_up(
        [(110.0, 111.0, 99.0, 100.0), (100.5, 102.0, 100.0, 101.5),
         (110.5, 114.0, 110.0, 113.0)])
    assert not flue.three_inside_up(
        [(110.0, 111.0, 99.0, 100.0), (100.5, 102.0, 100.0, 101.5),
         (109.0, 109.5, 108.5, 109.2)])
    assert flue.three_inside_down(
        [(100.0, 111.0, 99.0, 110.0), (109.5, 110.0, 108.0, 108.5),
         (99.5, 100.0, 96.0, 97.0)])
    assert flue.three_outside_up(
        [(110.0, 111.0, 99.0, 100.0), (99.0, 112.0, 98.0, 111.0),
         (111.5, 114.0, 111.0, 113.0)])
    assert not flue.three_outside_up(
        [(110.0, 111.0, 99.0, 100.0), (99.0, 112.0, 98.0, 111.0),
         (110.5, 111.0, 109.0, 110.5)])
    assert flue.three_outside_down(
        [(100.0, 111.0, 99.0, 110.0), (111.0, 112.0, 98.0, 99.0),
         (98.5, 99.0, 96.0, 97.0)])


def test_three_candle_named_patterns():
    assert flue.tri_star(
        [(100.0, 101.0, 99.0, 100.02), (100.5, 101.5, 99.5, 100.52),
         (101.0, 102.0, 100.0, 101.02)])
    assert flue.upside_gap_two_crows(
        [(100.0, 101.0, 99.0, 100.8), (101.5, 102.0, 101.2, 101.3),
         (101.6, 102.1, 101.0, 101.2)])
    assert not flue.upside_gap_two_crows(
        [(100.0, 101.0, 99.0, 100.8), (101.5, 102.0, 101.2, 101.3),
         (101.6, 102.1, 100.4, 100.6)])
    assert flue.unique_three_river_bottom(
        [(110.0, 111.0, 99.0, 100.0), (98.5, 98.6, 96.9, 98.0),
         (98.2, 98.5, 98.1, 98.4)])
    assert flue.deliberation(
        [(100.0, 102.0, 99.0, 101.9), (101.9, 103.0, 101.7, 102.7),
         (102.8, 103.3, 102.6, 102.9)])
    assert flue.tasuki_gap_up(
        [(100.0, 101.0, 99.0, 100.8), (102.0, 104.0, 101.5, 103.5),
         (103.2, 103.3, 101.0, 101.2)])
    assert not flue.tasuki_gap_up(
        [(100.0, 101.0, 99.0, 100.8), (102.0, 104.0, 101.5, 103.5),
         (103.2, 103.3, 100.4, 100.6)])


def test_four_and_five_candle_patterns():
    assert flue.concealing_baby_swallow(
        [(110.0, 111.0, 99.0, 100.0), (97.0, 97.5, 94.0, 95.0),
         (94.5, 95.5, 92.0, 93.5), (95.0, 95.2, 90.0, 91.5)])
    assert flue.three_line_strike_bull(
        SOLDIERS + [(103.0, 103.2, 99.5, 99.8)])
    assert not flue.three_line_strike_bull(
        SOLDIERS + [(103.0, 103.2, 100.2, 100.5)])
    assert flue.three_line_strike_bear(
        CROWS + [(106.0, 110.8, 105.8, 110.5)])
    assert not flue.three_line_strike_bear(
        CROWS + [(106.0, 110.8, 105.8, 109.5)])
    assert flue.rising_three_methods(
        [(100.0, 103.0, 99.0, 102.8), (102.4, 102.6, 101.0, 102.0),
         (102.3, 102.5, 101.0, 102.0), (102.1, 102.4, 101.0, 101.7),
         (101.8, 104.0, 101.6, 103.5)])
    assert flue.ladder_bottom(
        [(110.0, 111.0, 99.0, 100.0), (99.0, 99.1, 95.0, 98.9),
         (98.8, 98.9, 94.0, 98.7), (98.6, 98.7, 93.0, 98.5),
         (98.6, 99.5, 98.4, 99.3)])


def test_registry_resolves_every_name():
    names = [name for _, name, _ in flue.REGISTRY]
    assert len(names) == len(set(names))
    assert set(names) == set(flue._SINGLE) | set(flue._MULTI)
    assert len(names) == 46


def test_scan_labels_longest_pattern_first():
    labels = scan(SOLDIERS + [(103.0, 103.2, 99.5, 99.8)])
    assert ("three_line_strike_bull", "bearish_reversal", 3) in labels


# --- Detector wiring -------------------------------------------------------

def test_fluey_mapping_covers_the_registry():
    names = {name for _, name, _ in flue.REGISTRY}
    assert set(FLUEY_PATTERN_TYPES) == names
    assert len(set(FLUEY_PATTERN_TYPES.values())) == len(names)
    biases = {bias for _, _, bias in flue.REGISTRY}
    assert biases <= set(FLUEY_BIAS_DIRECTION)


def test_detect_fluey_labels_candles():
    bars = [_bar(*b) for b in SOLDIERS
            + [(103.0, 103.2, 99.5, 99.8), (100.0, 101.0, 99.0, 100.5)]]
    patterns = CandlestickDetector().detect_fluey(bars)
    types = {p.pattern_type for p in patterns}
    assert PatternType.THREE_LINE_STRIKE_BULL in types
    strike = [p for p in patterns if p.pattern_type is PatternType.THREE_LINE_STRIKE_BULL]
    assert strike[0].direction is SignalDirection.BEARISH
    assert strike[0].metadata["fluey_pattern"] == "three_line_strike_bull"
    assert strike[0].metadata["fluey_candles"] == 4
    assert 0 < strike[0].confidence <= 1


def test_engine_fluey_feed_is_opt_in(tmp_path):
    bars = [_bar(*b) for b in SOLDIERS
            + [(103.0, 103.2, 99.5, 99.8), (100.0, 101.0, 99.0, 100.5)]]
    engine = MultimodalAnalysisEngine(state_file=str(tmp_path / "state.json"))
    default_report = engine.analyze("EURUSD", "H1", bars)
    assert not any("fluey_pattern" in p.metadata for p in default_report.patterns)
    fluey_report = engine.analyze("EURUSD", "H1", bars, include_fluey=True)
    assert any("fluey_pattern" in p.metadata for p in fluey_report.patterns)




def test_detect_fluey_excludes_continuous_market_noise():
    bars = _neutral() + [
        _bar(110.0, 111.0, 99.0, 100.0),
        _bar(100.0, 104.0, 99.5, 103.0),
    ]
    filtered = CandlestickDetector().detect_fluey(bars, exclude_noisy=True)
    pattern_names = {p.metadata.get("fluey_pattern") for p in filtered}
    assert "separating_lines_bull" not in pattern_names
    assert "matching_high" not in pattern_names

    unfiltered = CandlestickDetector().detect_fluey(bars, exclude_noisy=False)
    unfiltered_names = {p.metadata.get("fluey_pattern") for p in unfiltered}
    assert "separating_lines_bull" in unfiltered_names
