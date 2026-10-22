"""FLUEY pattern fitness test — event study with an in/out-of-sample split.

Carver (2015) treats a trading rule as something that must earn its place:
Chapter 2 asks "why are certain rules profitable" and what Sharpe ratio to
realistically expect, Chapter 3 demands an out-of-sample check. A FLUEY
candlestick pattern is exactly such a rule — a binary signal with a
directional bias — so the same test applies to it.

This harness answers one question per pattern: does the signal carry a
positive edge that survives a purged out-of-sample split, after costs, with
enough trades to mean anything?

Governance (docs/PROJECT_SUCCESS_CONTRACT.md, binding on this repository):
  * element 11 — every pattern tested is a trial. Testing 46 patterns is 46
    trials, so they must be registered in the hash-chained ledger
    (`core/trial_ledger.py`) before any verdict may be promoted. The gate
    refuses to approve a pattern whose hypotheses were not counted.
  * element 12 — the split is purged and embargoed: an in-sample signal
    whose forward window reaches into the out-of-sample block is dropped,
    and an out-of-sample signal formed from in-sample bars is dropped.
  * element 13 — cost before promise. Without an explicit cost assumption
    the harness reports no edge at all and refuses every verdict; with one,
    every reported number is net of that cost.
  * element 8 — a missing dependency reads as refusal, never as consent.

ponytail: single horizon, overlapping samples, no Newey-West correction —
ceiling: a full event backtest with the cost model; upgrade path: reuse
research/alpha_backtest_harness.py once a pattern is approved.

References:
    Carver, R. (2015). "Systematic Trading." Harriman House, Ch. 2-3.
    Jha, A. (2026). "FLUEY: Formal Logical Unified Engine for Candlestick
    Analysis." (core/flue_thresholds.py)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

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

    cost_r: Optional[float] = None
    """Per-trade cost in R units.

    None means no cost assumption was made, and the harness then reports no
    edge and refuses every verdict (contract element 13, fail-closed).
    """

    pattern_lookback: int = 5
    """Bars a FLUEY label looks back over. Used to embargo the split."""


@dataclass(frozen=True)
class Sample:
    """One signal observation, in R units (gross of cost)."""

    index: int
    pattern: str
    direction: int  # +1 long, -1 short
    r: float


@dataclass(frozen=True)
class PatternFitness:
    """Verdict for a single FLUEY pattern. All edge numbers are net of cost."""

    pattern: str
    n: int
    is_n: int
    oos_n: int
    is_mean_r: float
    oos_mean_r: float
    oos_std_r: float
    oos_t: float
    hit_rate: float
    approved: bool
    reason: str
    cost_r: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern,
            "n": self.n,
            "is_n": self.is_n,
            "oos_n": self.oos_n,
            "is_mean_r": round(self.is_mean_r, 4),
            "oos_mean_r": round(self.oos_mean_r, 4),
            "oos_std_r": round(self.oos_std_r, 4),
            "oos_t": round(self.oos_t, 2),
            "hit_rate": round(self.hit_rate, 4),
            "approved": self.approved,
            "reason": self.reason,
            "cost_r": self.cost_r,
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


def _std(values: Sequence[float]) -> float:
    return float(np.std(values, ddof=1)) if len(values) > 1 else 0.0


def _tstat(values: Sequence[float]) -> float:
    std = _std(values)
    if len(values) < 2 or std <= 0:
        return 0.0
    return _mean(values) / (std / math.sqrt(len(values)))


def split_samples(
    rows: Sequence[Sample],
    split_index: int,
    cfg: FitnessConfig = FitnessConfig(),
) -> Tuple[List[Sample], List[Sample]]:
    """Split samples into in-sample and out-of-sample, purged and embargoed.

    Purge: an in-sample signal whose forward window reaches into the
    out-of-sample block carries out-of-sample information back into the
    training half, so it is dropped.
    Embargo: an out-of-sample signal formed from in-sample bars was not
    fully out of sample, so it is dropped too.
    """
    in_sample: List[Sample] = []
    out_sample: List[Sample] = []
    for sample in rows:
        if sample.index < split_index:
            if sample.index + cfg.horizon < split_index:
                in_sample.append(sample)
        elif sample.index - (cfg.pattern_lookback - 1) >= split_index:
            out_sample.append(sample)
    return in_sample, out_sample


def evaluate(
    samples: Sequence[Sample],
    split_index: int,
    cfg: FitnessConfig = FitnessConfig(),
) -> Dict[str, PatternFitness]:
    """Score every pattern on a purged in-sample and out-of-sample block.

    Every reported edge number is net of `cfg.cost_r`. If no cost was
    supplied, no edge is reported and nothing is approved.
    """
    grouped: Dict[str, List[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.pattern, []).append(sample)

    results: Dict[str, PatternFitness] = {}
    for pattern, rows in grouped.items():
        in_rows, out_rows = split_samples(rows, split_index, cfg)
        is_n, oos_n = len(in_rows), len(out_rows)

        if cfg.cost_r is None:
            results[pattern] = PatternFitness(
                pattern=pattern,
                n=len(rows),
                is_n=is_n,
                oos_n=oos_n,
                is_mean_r=0.0,
                oos_mean_r=0.0,
                oos_std_r=0.0,
                oos_t=0.0,
                hit_rate=0.0,
                approved=False,
                reason="no cost assumption provided (fail-closed)",
            )
            continue

        cost = float(cfg.cost_r)
        is_values = [s.r - cost for s in in_rows]
        oos_values = [s.r - cost for s in out_rows]
        is_mean = _mean(is_values)
        oos_mean = _mean(oos_values)
        oos_std = _std(oos_values)
        oos_t = _tstat(oos_values)
        hit_rate = _mean([1.0 if v > 0 else 0.0 for v in oos_values])

        reasons: List[str] = []
        if oos_n < max(3, cfg.min_trades // 2):
            reasons.append(f"only {oos_n} out-of-sample signals")
        if oos_mean <= 0:
            reasons.append("no positive out-of-sample edge")
        if oos_t < cfg.min_oos_t:
            reasons.append(f"out-of-sample t={oos_t:.2f} below {cfg.min_oos_t}")
        if is_mean * oos_mean < 0:
            reasons.append("in-sample and out-of-sample edges disagree")

        results[pattern] = PatternFitness(
            pattern=pattern,
            n=len(rows),
            is_n=is_n,
            oos_n=oos_n,
            is_mean_r=is_mean,
            oos_mean_r=oos_mean,
            oos_std_r=oos_std,
            oos_t=oos_t,
            hit_rate=hit_rate,
            approved=not reasons,
            reason="eligible: positive, consistent out-of-sample edge" if not reasons
            else "; ".join(reasons),
            cost_r=cost,
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
# Trials
# ---------------------------------------------------------------------------

def record_pattern_trials(
    ledger: Any,
    results: Mapping[str, PatternFitness],
    *,
    strategy_class: str = "fluey_pattern",
) -> int:
    """Register one trial per pattern in the hash-chained ledger.

    Contract element 11: the ledger's trial count is monotonic and
    unresettable, and DSR is computed from the true count. Testing a
    pattern is a trial, so it must be recorded here or the multiple-testing
    accounting is a lie.

    `ledger` needs a `record_trial(strategy_id, sharpe, n_observations,
    metadata=..., strategy_class=...)` method — `core.trial_ledger.TrialLedger`
    has one. The recorded Sharpe is the per-trade Sharpe of the
    out-of-sample net returns, not an annualised figure.
    """
    recorded = 0
    for name, fitness in sorted(results.items()):
        sharpe = 0.0
        if fitness.oos_std_r > 0:
            sharpe = fitness.oos_mean_r / fitness.oos_std_r
        ledger.record_trial(
            strategy_id=f"fluey:{name}",
            sharpe=sharpe,
            n_observations=fitness.oos_n,
            metadata={
                "fluey_pattern": name,
                "oos_t": fitness.oos_t,
                "cost_r": fitness.cost_r,
                "approved": fitness.approved,
            },
            strategy_class=strategy_class,
        )
        recorded += 1
    return recorded


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

class FlueyFitnessGate:
    """Which FLUEY patterns are allowed to influence a position.

    Fail-closed on every dependency (contract element 8): a pattern is
    eligible only if it passed the fitness test, the test carried a cost
    assumption, and its hypotheses were counted in the trial ledger.
    """

    def __init__(
        self,
        results: Mapping[str, PatternFitness],
        trials_recorded: Optional[int] = None,
    ):
        self._results: Dict[str, PatternFitness] = dict(results)
        self._trials_recorded = trials_recorded
        self._blocked_reason = ""
        if trials_recorded is None:
            self._blocked_reason = "hypotheses not counted in the trial ledger"
        elif trials_recorded < len(self._results):
            self._blocked_reason = (
                f"only {trials_recorded} of {len(self._results)} hypotheses counted"
            )
        if self._blocked_reason:
            self._approved: set = set()
        else:
            self._approved = {name for name, f in self._results.items() if f.approved}

    def is_approved(self, pattern: str) -> bool:
        return pattern in self._approved

    def approved(self) -> List[str]:
        return sorted(self._approved)

    def rejected(self) -> List[str]:
        return sorted(set(self._results) - self._approved)

    def blocked_reason(self) -> str:
        return self._blocked_reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved(),
            "rejected": self.rejected(),
            "blocked_reason": self._blocked_reason,
            "trials_recorded": self._trials_recorded,
            "patterns": {name: f.to_dict() for name, f in sorted(self._results.items())},
        }
