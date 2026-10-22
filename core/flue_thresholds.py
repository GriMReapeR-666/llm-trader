"""FLUEY thresholds + deterministic pattern predicates (P02 Sec. 6.1-6.6).

Ports the Formal Logical Unified Engine for Candlestick Analysis (Jha, 2026)
configuration block and helper/pattern functions into reusable predicates so
existing detectors share one scale-invariant threshold source instead of
ad-hoc magic numbers.

Config (paper Sec. 6.1): DOJI_BODY=0.05, LARGE_BODY=0.6, SMALL_BODY=0.3,
LONG_SHADOW_MULT k=2.0, GAP_TOL=0.001, EPS=1e-9.

Core maths (paper Sec. 2): B=|C-O|, R=max(H-L,EPS), rho=B/R,
U=H-max(O,C), D=min(O,C)-L, M=(O+C)/2.

Registry (paper Sec. 6.6): (n_candles, name, predicate), evaluated
longest-first, first match wins. Thresholds stay tunable per asset/
timeframe/volatility regime (paper Sec. 6.7) via `FlueConfig`.

Coverage: every Sec. 4 single-candle rule, the Sec. 5.1-5.3 patterns named
in the paper, and the Sec. 6.2 gap helpers. Sec. 5.1.6/5.2.4/5.3 give prose
only (no formulas), so those predicates are conventional chart definitions
built from the same metrics and are marked in their docstrings.

References:
    Jha, A. (2026). "FLUEY: Formal Logical Unified Engine for Candlestick
    Analysis."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple


@dataclass(frozen=True)
class FlueConfig:
    """Paper Sec. 6.1 configuration block (tunable per regime)."""

    doji_body: float = 0.05
    large_body: float = 0.6
    small_body: float = 0.3
    long_shadow_mult: float = 2.0
    gap_tol: float = 0.001
    eps: float = 1e-9


DEFAULT = FlueConfig()

Bar = Tuple[float, float, float, float]  # (open, high, low, close)


def body(o: float, c: float) -> float:
    return abs(c - o)


def rng(h: float, l: float, cfg: FlueConfig = DEFAULT) -> float:
    return max(h - l, cfg.eps)


def ratio(o: float, c: float, h: float, l: float, cfg: FlueConfig = DEFAULT) -> float:
    return body(o, c) / rng(h, l, cfg)


def upper(o: float, c: float, h: float) -> float:
    return h - max(o, c)


def lower(o: float, c: float, l: float) -> float:
    return min(o, c) - l


def midpoint(o: float, c: float) -> float:
    return (o + c) / 2.0


def bullish(o: float, c: float) -> bool:
    return c > o


def bearish(o: float, c: float) -> bool:
    return c < o


def _tol(ref: float, cfg: FlueConfig) -> float:
    """Relative GAP_TOL (paper Sec. 6.1): scale-invariant across assets."""
    return cfg.gap_tol * max(abs(ref), cfg.eps)


def near(a: float, b: float, cfg: FlueConfig = DEFAULT) -> bool:
    return abs(a - b) <= _tol(max(abs(a), abs(b)), cfg)


def gap_up(prev_h: float, curr_l: float, cfg: FlueConfig = DEFAULT) -> bool:
    """Search gap up (paper Sec. 6.2): current low clears the prior high."""
    return curr_l - prev_h > _tol(prev_h, cfg)


def gap_down(prev_l: float, curr_h: float, cfg: FlueConfig = DEFAULT) -> bool:
    """Search gap down (paper Sec. 6.2): current high clears the prior low."""
    return prev_l - curr_h > _tol(prev_l, cfg)


def is_doji(o: float, c: float, h: float, l: float, cfg: FlueConfig = DEFAULT) -> bool:
    return ratio(o, c, h, l, cfg) <= cfg.doji_body


def is_large(o: float, c: float, h: float, l: float, cfg: FlueConfig = DEFAULT) -> bool:
    return ratio(o, c, h, l, cfg) >= cfg.large_body


def is_small(o: float, c: float, h: float, l: float, cfg: FlueConfig = DEFAULT) -> bool:
    return ratio(o, c, h, l, cfg) <= cfg.small_body


def long_upper(o: float, c: float, h: float, cfg: FlueConfig = DEFAULT) -> bool:
    b = body(o, c)
    return b > 0 and upper(o, c, h) > cfg.long_shadow_mult * b


def long_lower(o: float, c: float, l: float, cfg: FlueConfig = DEFAULT) -> bool:
    b = body(o, c)
    return b > 0 and lower(o, c, l) > cfg.long_shadow_mult * b


def hammer(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return c > o and long_lower(o, c, l, cfg)


def hanging_man(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return c < o and long_lower(o, c, l, cfg)


def inverted_hammer(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return c > o and long_upper(o, c, h, cfg) and lower(o, c, l) < body(o, c)


def shooting_star(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return c < o and long_upper(o, c, h, cfg)


def dragonfly_doji(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return is_doji(o, c, h, l, cfg) and lower(o, c, l) > 2 * upper(o, c, h)


def gravestone_doji(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return is_doji(o, c, h, l, cfg) and upper(o, c, h) > 2 * lower(o, c, l)


def long_legged_doji(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    b = body(o, c)
    return is_doji(o, c, h, l, cfg) and upper(o, c, h) > 2 * b and lower(o, c, l) > 2 * b


def marubozu_bull(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return c > o and upper(o, c, h) < cfg.eps and lower(o, c, l) < cfg.eps


def marubozu_bear(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    return c < o and upper(o, c, h) < cfg.eps and lower(o, c, l) < cfg.eps


def high_wave(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    b = body(o, c)
    return is_small(o, c, h, l, cfg) and upper(o, c, h) > b and lower(o, c, l) > b


def spinning_top(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    """Small body, balanced shadows (paper Sec. 4.7): rho <= SMALL_BODY."""
    return is_small(o, c, h, l, cfg)


def long_white(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    """Bullish close + large body (paper Sec. 4.8)."""
    return bullish(o, c) and is_large(o, c, h, l, cfg)


def long_black(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    """Bearish close + large body (paper Sec. 4.9)."""
    return bearish(o, c) and is_large(o, c, h, l, cfg)


def belt_hold_bull(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    """C>O, open at the low, upper shadow < 0.2*B (paper Sec. 4.11)."""
    b = body(o, c)
    return c > o and abs(o - l) < cfg.eps and upper(o, c, h) < 0.2 * b


def belt_hold_bear(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    """C<O, open at the high, lower shadow < 0.2*B (paper Sec. 4.11)."""
    b = body(o, c)
    return c < o and abs(o - h) < cfg.eps and lower(o, c, l) < 0.2 * b


def rickshaw_man(o: float, h: float, l: float, c: float, cfg: FlueConfig = DEFAULT) -> bool:
    """Doji with body centred in the range (paper Sec. 4.13).

    Kept for paper fidelity: at rho <= 0.05 the centring test is implied
    by the doji bound, so this never narrows `is_doji`.
    """
    return is_doji(o, c, h, l, cfg) and abs(midpoint(o, c) - o) < 0.1 * rng(h, l, cfg)


def _ok(bars: Sequence[Bar], n: int) -> bool:
    return len(bars) >= n and all(b[1] > b[2] and b[0] > 0 for b in bars[-n:])


def bullish_engulfing(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """C2>O2, O2<C1, C2>O1 (paper Sec. 5.1.1): exact body containment."""
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 < o1 and c2 > o2 and o2 < c1 and c2 > o1


def bearish_engulfing(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 > o1 and c2 < o2 and o2 > c1 and c2 < o1


def bullish_harami(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 < o1 and c2 > o2 and o2 > c1 and c2 < o1


def bearish_harami(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 > o1 and c2 < o2 and o2 < c1 and c2 > o1


def morning_star(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """C1<O1, rho2<=0.3, C3>O3, O3>(O1+C1)/2 (paper Sec. 5.2.1)."""
    if not _ok(bars, 3):
        return False
    o1, h1, l1, c1 = bars[-3]
    o2, h2, l2, c2 = bars[-2]
    o3, h3, l3, c3 = bars[-1]
    return (
        c1 < o1
        and ratio(o2, c2, h2, l2, cfg) <= cfg.small_body
        and c3 > o3
        and o3 > midpoint(o1, c1)
    )


def evening_star(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 3):
        return False
    o1, h1, l1, c1 = bars[-3]
    o2, h2, l2, c2 = bars[-2]
    o3, h3, l3, c3 = bars[-1]
    return (
        c1 > o1
        and ratio(o2, c2, h2, l2, cfg) <= cfg.small_body
        and c3 < o3
        and o3 < midpoint(o1, c1)
    )


def harami_cross(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Doji contained in the prior body (paper Sec. 5.1.4)."""
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, h2, l2, c2 = bars[-1]
    contained = min(o2, c2) > min(o1, c1) and max(o2, c2) < max(o1, c1)
    return contained and is_doji(o2, c2, h2, l2, cfg)


def counterattack_bull(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Prior bearish, open below prior close, close back at it (Sec. 5.1.5)."""
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 < o1 and o2 < c1 and near(c2, c1, cfg)


def counterattack_bear(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 > o1 and o2 > c1 and near(c2, c1, cfg)


def kicking_bull(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Bearish then bullish candle with a gap up (paper Sec. 5.1.6)."""
    if not _ok(bars, 2):
        return False
    o1, h1, _, c1 = bars[-2]
    o2, _, l2, c2 = bars[-1]
    return c1 < o1 and c2 > o2 and gap_up(h1, l2, cfg)


def kicking_bear(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 2):
        return False
    o1, _, l1, c1 = bars[-2]
    o2, h2, _, c2 = bars[-1]
    return c1 > o1 and c2 < o2 and gap_down(l1, h2, cfg)


def separating_lines_bull(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Bearish then bullish candle opening at the prior close (Sec. 5.1.6)."""
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 < o1 and c2 > o2 and near(o2, c1, cfg)


def separating_lines_bear(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 2):
        return False
    o1, _, _, c1 = bars[-2]
    o2, _, _, c2 = bars[-1]
    return c1 > o1 and c2 < o2 and near(o2, c1, cfg)


def matching_high(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Two consecutive equal highs within GAP_TOL (paper Sec. 5.1.6)."""
    if not _ok(bars, 2):
        return False
    return near(bars[-2][1], bars[-1][1], cfg)


def three_white_soldiers(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Three bullish candles, rising closes, large bodies 2-3 (Sec. 5.2.3)."""
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, h2, l2, c2), (o3, h3, l3, c3) = bars[-3:]
    return (
        c1 > o1
        and c2 > o2
        and c3 > o3
        and c2 > c1
        and c3 > c2
        and ratio(o2, c2, h2, l2, cfg) >= cfg.large_body
        and ratio(o3, c3, h3, l3, cfg) >= cfg.large_body
    )


def three_black_crows(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, h2, l2, c2), (o3, h3, l3, c3) = bars[-3:]
    return (
        c1 < o1
        and c2 < o2
        and c3 < o3
        and c2 < c1
        and c3 < c2
        and ratio(o2, c2, h2, l2, cfg) >= cfg.large_body
        and ratio(o3, c3, h3, l3, cfg) >= cfg.large_body
    )


def three_inside_up(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Bullish harami + close above the first open (paper Sec. 5.2.4)."""
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3) = bars[-3:]
    return c1 < o1 and c2 > o2 and o2 > c1 and c2 < o1 and c3 > o3 and c3 > o1


def three_inside_down(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3) = bars[-3:]
    return c1 > o1 and c2 < o2 and o2 < c1 and c2 > o1 and c3 < o3 and c3 < o1


def three_outside_up(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Bullish engulfing + higher close (paper Sec. 5.2.4)."""
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3) = bars[-3:]
    return c1 < o1 and c2 > o2 and o2 < c1 and c2 > o1 and c3 > o3 and c3 > c2


def three_outside_down(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3) = bars[-3:]
    return c1 > o1 and c2 < o2 and o2 > c1 and c2 < o1 and c3 < o3 and c3 < c2


def tri_star(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Three consecutive dojis (paper Sec. 5.2.4)."""
    if not _ok(bars, 3):
        return False
    return all(is_doji(o, c, h, l, cfg) for o, h, l, c in bars[-3:])


def upside_gap_two_crows(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """White, black gapping up, lower black still above close 1 (Sec. 5.2.4)."""
    if not _ok(bars, 3):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3) = bars[-3:]
    return (
        c1 > o1
        and c2 < o2
        and o2 > c1
        and c3 < o3
        and o3 > o2
        and c3 < c2
        and c3 > c1
    )


def unique_three_river_bottom(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Long black, small black at a new low, small white inside it (Sec. 5.2.4)."""
    if not _ok(bars, 3):
        return False
    (o1, h1, l1, c1), (o2, h2, l2, c2), (o3, _, _, c3) = bars[-3:]
    return (
        c1 < o1
        and c2 < o2
        and l2 < l1
        and h2 < h1
        and is_small(o2, c2, h2, l2, cfg)
        and c3 > o3
        and o3 > c2
        and c3 < o2
    )


def deliberation(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Large white, smaller white, then a small white at the top (Sec. 5.2.4)."""
    if not _ok(bars, 3):
        return False
    (o1, h1, l1, c1), (o2, _, _, c2), (o3, h3, l3, c3) = bars[-3:]
    return (
        c1 > o1
        and is_large(o1, c1, h1, l1, cfg)
        and c2 > o2
        and c2 > c1
        and body(o2, c2) < body(o1, c1)
        and c3 > o3
        and is_small(o3, c3, h3, l3, cfg)
        and c3 > c2
    )


def tasuki_gap_up(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Two gapping whites, then a black closing inside the gap (Sec. 5.1.6)."""
    if not _ok(bars, 3):
        return False
    (o1, h1, _, c1), (o2, _, l2, c2), (o3, _, _, c3) = bars[-3:]
    return (
        c1 > o1
        and c2 > o2
        and gap_up(h1, l2, cfg)
        and o2 < o3 < c2
        and c1 < c3 < o2
    )


def concealing_baby_swallow(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Three black candles gapping down, then a black engulfing them (Sec. 5.3)."""
    if not _ok(bars, 4):
        return False
    (o1, _, l1, c1), (o2, h2, _, c2), (o3, h3, _, c3), (o4, _, _, c4) = bars[-4:]
    return (
        c1 < o1
        and c2 < o2
        and gap_down(l1, h2, cfg)
        and c3 < o3
        and upper(o3, c3, h3) > cfg.eps
        and bars[-2][2] < bars[-3][2]
        and c4 < o4
        and o4 > o3
        and c4 < min(c2, c3)
    )


def three_line_strike_bull(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Three rising whites erased by a black engulfing all three (Sec. 5.3)."""
    if not _ok(bars, 4):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3), (o4, _, _, c4) = bars[-4:]
    return (
        c1 > o1
        and c2 > o2
        and c3 > o3
        and c2 > c1
        and c3 > c2
        and c4 < o4
        and o4 > c3
        and c4 < o1
    )


def three_line_strike_bear(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    if not _ok(bars, 4):
        return False
    (o1, _, _, c1), (o2, _, _, c2), (o3, _, _, c3), (o4, _, _, c4) = bars[-4:]
    return (
        c1 < o1
        and c2 < o2
        and c3 < o3
        and c2 < c1
        and c3 < c2
        and c4 > o4
        and o4 < c3
        and c4 > o1
    )


def rising_three_methods(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Large white, three small blacks inside it, then a new high (Sec. 5.3)."""
    if not _ok(bars, 5):
        return False
    (o1, h1, l1, c1), b2, b3, b4, (o5, _, _, c5) = bars[-5:]
    pullback = (
        o1 < b[3] < b[0] < c1 and is_small(b[0], b[3], b[1], b[2], cfg)
        for b in (b2, b3, b4)
    )
    return (
        c1 > o1
        and is_large(o1, c1, h1, l1, cfg)
        and all(pullback)
        and c5 > o5
        and c5 > c1
    )


def ladder_bottom(bars: Sequence[Bar], cfg: FlueConfig = DEFAULT) -> bool:
    """Long black, three falling small blacks, then a white reversal (Sec. 5.3)."""
    if not _ok(bars, 5):
        return False
    (o1, h1, l1, c1), b2, b3, b4, (o5, _, _, c5) = bars[-5:]
    rungs = all(
        b[3] < b[0] and is_small(b[0], b[3], b[1], b[2], cfg) and upper(b[0], b[3], b[1]) > cfg.eps
        for b in (b2, b3, b4)
    )
    return (
        c1 < o1
        and is_large(o1, c1, h1, l1, cfg)
        and rungs
        and b2[0] < o1
        and b3[0] < b2[0]
        and b4[0] < b3[0]
        and b2[3] < c1
        and b3[3] < b2[3]
        and b4[3] < b3[3]
        and c5 > o5
        and c5 > b4[1]
    )


Predicate = Callable[..., bool]

REGISTRY: List[Tuple[int, str, str]] = [
    (1, "hammer", "bullish_reversal"),
    (1, "hanging_man", "bearish_reversal"),
    (1, "inverted_hammer", "bullish_reversal"),
    (1, "shooting_star", "bearish_reversal"),
    (1, "dragonfly_doji", "bullish_reversal"),
    (1, "gravestone_doji", "bearish_reversal"),
    (1, "long_legged_doji", "indecision"),
    (1, "rickshaw_man", "indecision"),
    (1, "marubozu_bull", "bullish_continuation"),
    (1, "marubozu_bear", "bearish_continuation"),
    (1, "belt_hold_bull", "bullish_continuation"),
    (1, "belt_hold_bear", "bearish_continuation"),
    (1, "high_wave", "indecision"),
    (1, "long_white", "bullish_continuation"),
    (1, "long_black", "bearish_continuation"),
    (1, "spinning_top", "indecision"),
    (2, "bullish_engulfing", "bullish_reversal"),
    (2, "bearish_engulfing", "bearish_reversal"),
    (2, "bullish_harami", "bullish_reversal"),
    (2, "bearish_harami", "bearish_reversal"),
    (2, "harami_cross", "indecision"),
    (2, "counterattack_bull", "bullish_reversal"),
    (2, "counterattack_bear", "bearish_reversal"),
    (2, "kicking_bull", "bullish_reversal"),
    (2, "kicking_bear", "bearish_reversal"),
    (2, "separating_lines_bull", "bullish_continuation"),
    (2, "separating_lines_bear", "bearish_continuation"),
    (2, "matching_high", "indecision"),
    (3, "morning_star", "bullish_reversal"),
    (3, "evening_star", "bearish_reversal"),
    (3, "three_white_soldiers", "bullish_continuation"),
    (3, "three_black_crows", "bearish_continuation"),
    (3, "three_inside_up", "bullish_reversal"),
    (3, "three_inside_down", "bearish_reversal"),
    (3, "three_outside_up", "bullish_reversal"),
    (3, "three_outside_down", "bearish_reversal"),
    (3, "tri_star", "indecision"),
    (3, "upside_gap_two_crows", "bearish_reversal"),
    (3, "unique_three_river_bottom", "bullish_reversal"),
    (3, "deliberation", "bearish_reversal"),
    (3, "tasuki_gap_up", "bullish_continuation"),
    (4, "concealing_baby_swallow", "bullish_reversal"),
    (4, "three_line_strike_bull", "bearish_reversal"),
    (4, "three_line_strike_bear", "bullish_reversal"),
    (5, "rising_three_methods", "bullish_continuation"),
    (5, "ladder_bottom", "bullish_reversal"),
]

_SINGLE: Dict[str, Predicate] = {
    "hammer": hammer,
    "hanging_man": hanging_man,
    "inverted_hammer": inverted_hammer,
    "shooting_star": shooting_star,
    "dragonfly_doji": dragonfly_doji,
    "gravestone_doji": gravestone_doji,
    "long_legged_doji": long_legged_doji,
    "rickshaw_man": rickshaw_man,
    "marubozu_bull": marubozu_bull,
    "marubozu_bear": marubozu_bear,
    "belt_hold_bull": belt_hold_bull,
    "belt_hold_bear": belt_hold_bear,
    "high_wave": high_wave,
    "long_white": long_white,
    "long_black": long_black,
    "spinning_top": spinning_top,
}

_MULTI: Dict[str, Predicate] = {
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "bullish_harami": bullish_harami,
    "bearish_harami": bearish_harami,
    "harami_cross": harami_cross,
    "counterattack_bull": counterattack_bull,
    "counterattack_bear": counterattack_bear,
    "kicking_bull": kicking_bull,
    "kicking_bear": kicking_bear,
    "separating_lines_bull": separating_lines_bull,
    "separating_lines_bear": separating_lines_bear,
    "matching_high": matching_high,
    "morning_star": morning_star,
    "evening_star": evening_star,
    "three_white_soldiers": three_white_soldiers,
    "three_black_crows": three_black_crows,
    "three_inside_up": three_inside_up,
    "three_inside_down": three_inside_down,
    "three_outside_up": three_outside_up,
    "three_outside_down": three_outside_down,
    "tri_star": tri_star,
    "upside_gap_two_crows": upside_gap_two_crows,
    "unique_three_river_bottom": unique_three_river_bottom,
    "deliberation": deliberation,
    "tasuki_gap_up": tasuki_gap_up,
    "concealing_baby_swallow": concealing_baby_swallow,
    "three_line_strike_bull": three_line_strike_bull,
    "three_line_strike_bear": three_line_strike_bear,
    "rising_three_methods": rising_three_methods,
    "ladder_bottom": ladder_bottom,
}


def scan(
    bars: Sequence[Bar], cfg: FlueConfig = DEFAULT
) -> List[Tuple[str, str, int]]:
    """Label each bar index with the first (longest-first) matching pattern.

    Returns [(pattern_name, bias, end_index)] in scan order.
    """
    out = []
    ordered = sorted(REGISTRY, key=lambda r: -r[0])
    for i in range(len(bars)):
        for n, name, bias in ordered:
            if i + 1 < n:
                continue
            window = bars[i + 1 - n : i + 1]
            fn = _MULTI.get(name) or _SINGLE.get(name)
            if fn is None:
                continue
            if n > 1:
                hit = fn(window, cfg)
            else:
                hit = fn(*(window[0]), cfg)
            if hit:
                out.append((name, bias, i))
                break
    return out
