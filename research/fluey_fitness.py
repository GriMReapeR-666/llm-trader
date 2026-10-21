"""FLUEY pattern fitness test — event study with an in/out-of-sample split.

Carver (2015) treats a trading rule as something that must earn its place:
Chapter 2 asks "why are certain rules profitable" and what Sharpe ratio to
realistically expect, Chapter 3 demands an out-of-sample check. A FLUEY
candlestick pattern is exactly such a rule — a binary signal with a
directional bias — so the same test applies to it.

This harness answers one question per pattern: does the signal carry a
positive edge that survives a temporal out-of-sample split, with enough
trades to mean anything? Patterns that fail are not tradable; patterns that
pass are *eligible*, not endorsed (no costs, no execution model here).

Data flow:
    OHLCV -> core.flue_thresholds.scan() -> labels -> forward returns in
    ATR units -> in-sample / out-of-sample split -> verdict per pattern.

ponytail: single horizon, overlapping samples, no Newey-West correction and
no trading costs — ceiling: a full event backtest with cost model; upgrade
path: reuse research/alpha_backtest_harness.py once a pattern is approved.

References:
    Carver, R. (2015). "Systematic Trading." Harriman House, Ch. 2-3.
    Jha, A. (2026). "FLUEY: Formal Logical Unified Engine for Candlestick
    Analysis." (core/flue_thresholds.py)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

from core import flue_thresholds as flue

Bar = Tuple[float, float, float, float]  # (open, high, low, close)
Label = Tuple[str, str, int]  # (pattern, bias, end_index) as flue.scan returns


@dataclass(frozen=True)
class FitnessConfig:
    """Thresholds for the fitness test."""

    horizon: int = 5
    """Bars ahead for the forward return."""

    atr_period: int = 14
    """Lookback for the ATR used to express returns in R units."""

    min_trades: int = 30
    """Minimum signal count for a verdict to be trusted."""

    min_oos_t: float = 2.0
    """Minimum out-of-sample t-statistic."""

    oos_split: float = 0.5
    """Fraction of the series reserved for the out-of-sample block."""


@dataclass(frozen=True)
class Sample:
    """One signal observation, in R units."""

    index: int
    pattern: str
    direction: int  # +1 long, -1 short
    r: float


@dataclass(frozen=True)
class PatternFitness:
    """Verdict for a single FLUEY pattern."""

    pattern: str
    n: int
    is_n: int
    oos_n: int
    is_mean_r: float
    oos_mean_r: float
    oos_t: float
    hit_rate: float
    approved: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern,
            "n": self.n,
            "is_n": self.is_n,
            "oos_n": self.oos_n,
            "is_mean_r": round(self.is_mean_r, 4),
            "oos_mean_r": round(self.oos_mean_r, 4),
            "oos_t": round(self.oos_t, 2),
            "hit_rate": round(self.hit_rate, 4),
            "approved": self.approved,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Signal collection
# ---------------------------------------------------------------------------

def direction_for(bias: str) -> int:
    """+1 for a bullish bias, -1 for a bearish one, 0 for indecision."""
    if bias.startswith("bullish"):
        return 1
    if bias.startswith("bearish"):
        return -1
    return 0


def atr_series(bars: Sequence[Bar], period: int = 14) -> List[float]:
    """Average true range at each bar (the R unit for the forward return)."""
    out: List[float] = []
    true_ranges: List[float] = []
    for i, (o, h, low, c) in enumerate(bars):
        if i == 0:
            true_range = h - low
        else:
            prev_close = bars[i - 1][3]
            true_range = max(h - low, abs(h - prev_close), abs(low - prev_close))
        true_ranges.append(true_range)
        window = true_ranges[-period:]
        out.append(sum(window) / len(window))
    return out


def collect_samples(
    bars: Sequence[Bar],
    labels: Sequence[Label],
    atr: Sequence[float],
    horizon: int = 5,
) -> List[Sample]:
    """Turn FLUEY labels into forward-return samples in R units.

    Indecision patterns are skipped: they carry no direction to trade.
    """
    samples: List[Sample] = []
    for pattern, bias, index in labels:
        direction = direction_for(bias)
        if direction == 0 or index < 0 or index + horizon >= len(bars):
            continue
        unit = atr[index] if index < len(atr) else 0.0
        if not unit or unit <= 0 or not math.isfinite(unit):
            continue
        entry = bars[index][3]
        exit_price = bars[index + horizon][3]
        if not entry:
            continue
        r = direction * (exit_price - entry) / unit
        if math.isfinite(r):
            samples.append(Sample(index=index, pattern=pattern, direction=direction, r=r))
    return samples


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if values else 0.0


def _tstat(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    std = float(np.std(values, ddof=1))
    if std <= 0:
        return 0.0
    return _mean(values) / (std / math.sqrt(len(values)))


def evaluate(
    samples: Sequence[Sample],
    split_index: int,
    cfg: FitnessConfig = FitnessConfig(),
) -> Dict[str, PatternFitness]:
    """Score every pattern on an in-sample block and an out-of-sample block."""
    grouped: Dict[str, List[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.pattern, []).append(sample)

    results: Dict[str, PatternFitness] = {}
    for pattern, rows in grouped.items():
        in_sample = [s.r for s in rows if s.index < split_index]
        out_sample = [s.r for s in rows if s.index >= split_index]
        is_mean = _mean(in_sample)
        oos_mean = _mean(out_sample)
        oos_t = _tstat(out_sample)
        hits = [1.0 if r > 0 else 0.0 for r in out_sample]
        hit_rate = _mean(hits)

        reasons: List[str] = []
        if len(out_sample) < max(3, cfg.min_trades // 2):
            reasons.append(f"only {len(out_sample)} out-of-sample signals")
        if oos_mean <= 0:
            reasons.append("no positive out-of-sample edge")
        if oos_t < cfg.min_oos_t:
            reasons.append(f"out-of-sample t={oos_t:.2f} below {cfg.min_oos_t}")
        if is_mean * oos_mean < 0:
            reasons.append("in-sample and out-of-sample edges disagree")

        results[pattern] = PatternFitness(
            pattern=pattern,
            n=len(rows),
            is_n=len(in_sample),
            oos_n=len(out_sample),
            is_mean_r=is_mean,
            oos_mean_r=oos_mean,
            oos_t=oos_t,
            hit_rate=hit_rate,
            approved=not reasons,
            reason="eligible: positive, consistent out-of-sample edge" if not reasons
            else "; ".join(reasons),
        )
    return results


def evaluate_patterns(
    ohlcv: pd.DataFrame,
    cfg: FitnessConfig = FitnessConfig(),
) -> Dict[str, PatternFitness]:
    """Run the full fitness test over an OHLCV frame.

    The frame needs open/high/low/close columns; volume is ignored.
    """
    required = ("open", "high", "low", "close")
    missing = [c for c in required if c not in ohlcv.columns]
    if missing:
        raise ValueError(f"missing OHLCV columns: {missing}")
    bars: List[Bar] = [
        (float(row.open), float(row.high), float(row.low), float(row.close))
        for row in ohlcv[list(required)].itertuples(index=False)
    ]
    if len(bars) < 10:
        return {}
    labels = flue.scan(bars)
    atr = atr_series(bars, cfg.atr_period)
    samples = collect_samples(bars, labels, atr, cfg.horizon)
    split_index = int(len(bars) * (1.0 - cfg.oos_split))
    return evaluate(samples, split_index, cfg)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

class FlueyFitnessGate:
    """Which FLUEY patterns are allowed to influence a position.

    Deliberately conservative: a pattern is tradable only if it passed the
    fitness test on the data it was measured on.
    """

    def __init__(self, results: Mapping[str, PatternFitness]):
        self._results: Dict[str, PatternFitness] = dict(results)
        self._approved = {name for name, f in self._results.items() if f.approved}

    def is_approved(self, pattern: str) -> bool:
        return pattern in self._approved

    def approved(self) -> List[str]:
        return sorted(self._approved)

    def rejected(self) -> List[str]:
        return sorted(set(self._results) - self._approved)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved(),
            "rejected": self.rejected(),
            "patterns": {name: f.to_dict() for name, f in sorted(self._results.items())},
        }
