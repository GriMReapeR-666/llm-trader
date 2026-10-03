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


Predicate = Callable[..., bool]

REGISTRY: List[Tuple[int, str, str]] = [
    (3, "morning_star", "bullish_reversal"),
    (3, "evening_star", "bearish_reversal"),
    (2, "bullish_engulfing", "bullish_reversal"),
    (2, "bearish_engulfing", "bearish_reversal"),
    (2, "bullish_harami", "bullish_reversal"),
    (2, "bearish_harami", "bearish_reversal"),
    (1, "hammer", "bullish_reversal"),
    (1, "hanging_man", "bearish_reversal"),
    (1, "inverted_hammer", "bullish_reversal"),
    (1, "shooting_star", "bearish_reversal"),
    (1, "dragonfly_doji", "bullish_reversal"),
    (1, "gravestone_doji", "bearish_reversal"),
    (1, "long_legged_doji", "indecision"),
    (1, "marubozu_bull", "bullish_continuation"),
    (1, "marubozu_bear", "bearish_continuation"),
    (1, "high_wave", "indecision"),
]

_SINGLE: Dict[str, Predicate] = {
    "hammer": hammer,
    "hanging_man": hanging_man,
    "inverted_hammer": inverted_hammer,
    "shooting_star": shooting_star,
    "dragonfly_doji": dragonfly_doji,
    "gravestone_doji": gravestone_doji,
    "long_legged_doji": long_legged_doji,
    "marubozu_bull": marubozu_bull,
    "marubozu_bear": marubozu_bear,
    "high_wave": high_wave,
}

_MULTI: Dict[str, Predicate] = {
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "bullish_harami": bullish_harami,
    "bearish_harami": bearish_harami,
    "morning_star": morning_star,
    "evening_star": evening_star,
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
