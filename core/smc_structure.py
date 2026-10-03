"""SMC structural features: swings, BOS/CHoCH, order blocks, liquidity pools.

Implements Paper P06 (TwinAlgo, Thophet 2026) Layer-1 SMC Feature Engine
deterministic rules on OHLCV bars:

  - swing highs/lows: pivot highs/lows over a lookback window
  - BOS: break of structure validated by TWO consecutive closes beyond the
    swing (single-close breaks rejected as ranging noise)
  - CHoCH: same two-close validation against the prevailing direction;
    primary reversal signal
  - Order Block: last opposing candle before an impulsive displacement move,
    with mitigation flag (invalid once price closes through its boundary)
  - EQH/EQL: equal highs/lows within tolerance = stop-cluster liquidity pools
  - premium/discount: Fibonacci position (0.382/0.5/0.618) of price inside
    the dealing range; premium entries discouraged in bullish regimes

FVG detection is NOT duplicated here: reuse `research.alpha_miner.fvg_detector`.

References:
    Thophet, N. (2026). "TwinAlgo: Expectancy-Optimised Algorithmic Trading
    via Dynamic Regime-Conditioned LLM Prompting and SMC Integration on Gold."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SwingEvent:
    kind: str          # "BOS_BULL", "BOS_BEAR", "CHOCH_BULL", "CHOCH_BEAR"
    index: int
    level: float
    closes: Tuple[float, float]


@dataclass
class OrderBlock:
    index: int
    top: float
    bottom: float
    direction: str     # "bullish" | "bearish"
    mitigated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "top": self.top, "bottom": self.bottom,
                "direction": self.direction, "mitigated": self.mitigated}


def swing_points(highs: List[float], lows: List[float],
                 window: int = 3) -> Tuple[List[int], List[int]]:
    """Pivot swing highs/lows: extreme of `window` bars each side."""
    shi, slo = [], []
    n = len(highs)
    for i in range(window, n - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            shi.append(i)
        if lows[i] == min(lows[i - window:i + window + 1]):
            slo.append(i)
    return shi, slo


def _closes_beyond(closes: List[float], level: float, direction: int,
                   end: int) -> Optional[Tuple[float, float]]:
    """Two consecutive closes beyond `level` ending at `end` (P06: no single-close)."""
    if end < 1:
        return None
    c1, c2 = closes[end - 1], closes[end]
    if direction > 0 and c1 > level and c2 > level:
        return (c1, c2)
    if direction < 0 and c1 < level and c2 < level:
        return (c1, c2)
    return None


def detect_structure(highs: List[float], lows: List[float], closes: List[float],
                     window: int = 3) -> List[SwingEvent]:
    """BOS/CHoCH scan. Trend state advances on BOS; CHoCH flips it (reversal)."""
    shi, slo = swing_points(highs, lows, window)
    events: List[SwingEvent] = []
    trend = 0  # +1 bull, -1 bear, 0 unknown
    for i in range(1, len(closes)):
        fired: Optional[SwingEvent] = None
        for s in [s for s in shi if s < i - 1]:
            lvl = highs[s]
            pair = _closes_beyond(closes, lvl, +1, i)
            if pair and (not events or i > events[-1].index):
                fired = SwingEvent("BOS_BULL" if trend >= 0 else "CHOCH_BULL",
                                   i, lvl, pair)
                trend = +1
                break
        if fired is None:
            for s in [s for s in slo if s < i - 1]:
                lvl = lows[s]
                pair = _closes_beyond(closes, lvl, -1, i)
                if pair and (not events or i > events[-1].index):
                    fired = SwingEvent("BOS_BEAR" if trend <= 0 else "CHOCH_BEAR",
                                       i, lvl, pair)
                    trend = -1
                    break
        if fired is not None:
            events.append(fired)
    return events


def find_order_blocks(opens: List[float], highs: List[float], lows: List[float],
                      closes: List[float], disp_atr_mult: float = 1.5,
                      atr: Optional[List[float]] = None) -> List[OrderBlock]:
    """Last opposing candle before an impulsive displacement (P06 Layer 1).

    Displacement = body > disp_atr_mult * ATR (ATR series optional; falls
    back to rolling mean range). Mitigated when a later close crosses the OB.
    """
    n = len(closes)
    if atr is None:
        atr = [(sum(highs[max(0, i - 13):i + 1]) - sum(lows[max(0, i - 13):i + 1]))
               / max(1, min(14, i + 1)) for i in range(n)]
    blocks: List[OrderBlock] = []
    for i in range(1, n):
        body = abs(closes[i] - opens[i])
        if body <= disp_atr_mult * (atr[i] or 1e-9):
            continue
        bull = closes[i] > opens[i]
        j = i - 1
        if (bull and closes[j] < opens[j]) or (not bull and closes[j] > opens[j]):
            top, bottom = max(opens[j], closes[j]), min(opens[j], closes[j])
            direction = "bullish" if bull else "bearish"
            mitigated = any((closes[k] < bottom if bull else closes[k] > top)
                            for k in range(i + 1, n))
            blocks.append(OrderBlock(index=j, top=top, bottom=bottom,
                                     direction=direction, mitigated=mitigated))
    return blocks


def liquidity_pools(highs: List[float], lows: List[float],
                    tol_frac: float = 0.0005,
                    lookback: int = 20) -> Dict[str, List[Tuple[int, float]]]:
    """EQH/EQL stop-cluster zones: highs/lows equal within tolerance."""
    eqh, eql = [], []
    for i in range(max(0, len(highs) - lookback), len(highs)):
        if any(abs(highs[i] - highs[j]) / max(highs[i], 1e-9) <= tol_frac
               for j in range(max(0, i - lookback), i)):
            eqh.append((i, highs[i]))
        if any(abs(lows[i] - lows[j]) / max(lows[i], 1e-9) <= tol_frac
               for j in range(max(0, i - lookback), i)):
            eql.append((i, lows[i]))
    return {"EQH": eqh, "EQL": eql}


def range_position(price: float, range_low: float, range_high: float) -> Optional[float]:
    """Fib position of price in the dealing range (0=low, 1=high)."""
    if range_high <= range_low:
        return None
    return (price - range_low) / (range_high - range_low)


def is_premium(price: float, range_low: float, range_high: float,
               thresh: float = 0.618) -> Optional[bool]:
    """Above 61.8% retracement = premium (P06: entries discouraged in bull regimes)."""
    pos = range_position(price, range_low, range_high)
    return None if pos is None else pos >= thresh
