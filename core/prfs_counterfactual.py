"""Post-Rejection Follow-up Sampling (PRFS) + five-tier precision audit.

Implements Papers P03 (Kamat 2026b, SSRN 6607301) and P04 (Kamat 2026c,
SSRN 6638259): measure the counterfactual forward outcomes of REJECTED
candidates and audit filter rules against observed trajectories instead of
backtest reconstructions.

Pipeline (P03 Sec. III, fan-in/fan-out):
  emitters -> RejectionEvent store -> PRFSTracker jobs -> ForwardObservation
  log -> five-tier classification (P04 Sec. III.B) -> per-filter audit report.

Five tiers (P04 Sec. III.B), reference price = earliest forward sample:
  1. SAVED_WINDOWED   >=2 samples, min <= 50% of ref   (measured drawdown save)
  2. MISSED_MOON      >=2 samples, max >= 200% of ref  (forwent run-up)
  3. SAVED_EARLY_DEATH exactly 1 sample, age <= 60 min (disappearance proxy)
  4. FLAT             >=2 samples, neither threshold reached
  5. UNCLASSIFIABLE   invalid ref, or single sample older than 60 min
Tie-break: MISSED over SAVED when both hold (P04: matches reference script).

Headline discipline (P04 Sec. V): the conservative save-to-miss ratio uses
SAVED_WINDOWED / MISSED only. The early-death tier did NOT validate as a
rug signal (48.9% vs 57.6% gone-rate, matched test) and is reported
separately, never folded into the headline ratio.

Repo adaptations (documented deviations from the DEX deployment):
  - candidates are rejected trade proposals (symbols), not token mints;
    plugs into the existing `RejectionSinkNode` risk-rejection stream.
  - horizon/cadence in BARS (default 96 ~ 24h of M15) via caller-supplied
    price callback, not wall-clock oracle polling; no new dependencies.
  - liquidity/volume bins optional; price-only operation supported.

References:
    Kamat (2026b). "Post-Rejection Follow-up Sampling ..." SSRN 6607301.
    Kamat (2026c). "Outcome-Classified Precision Auditing ..." SSRN 6638259.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class OutcomeTier(str, Enum):
    SAVED_WINDOWED = "saved_windowed"
    MISSED_MOON = "missed_moon"
    SAVED_EARLY_DEATH = "saved_early_death"
    FLAT = "flat"
    UNCLASSIFIABLE = "unclassifiable"


@dataclass(frozen=True)
class PRFSConfig:
    """Scheduler + classification parameters (P03 Sec. III.B, P04 Sec. III.B)."""
    horizon_bars: int = 96          # follow-up horizon (P03: up to 24h)
    dense_bars: int = 4             # dense phase: sample every bar for first N
    thin_every: int = 4             # thinning phase: every Nth bar thereafter
    early_death_max_age_bars: int = 4   # P04: single sample ageMin <= 60min
    max_missing: int = 8            # P03 III.C(b): disappearance termination
    saved_drawdown: float = 0.50    # P04 T1: min <= 50% of ref
    missed_runup: float = 2.00      # P04 T2: max >= 200% of ref
    min_sample_size: int = 5        # per-filter verdict needs n >= 5 (P04: n=4 insufficient)


@dataclass
class RejectionEvent:
    """One row of the rejection event store (P03 Sec. III.A)."""
    key: str                        # candidate id, e.g. "EURUSD:2026-09-10T12:00"
    symbol: str
    reason: str                     # filter label (P04: anonymised filter_N)
    source: str = "scanner"         # emitter id (P03 Sec. VI: source_a/source_b)
    ref_price: float = 0.0          # market price at rejection moment
    state: Dict[str, object] = field(default_factory=dict)


@dataclass
class ForwardObservation:
    """One row of the outcome log, keyed by (key, age_bars) (P03 Sec. III.A)."""
    key: str
    age_bars: int
    price: Optional[float] = None   # None = oracle miss for this slot
    liquidity: Optional[float] = None


@dataclass
class _Job:
    event: RejectionEvent
    observations: List[ForwardObservation] = field(default_factory=list)
    missing: int = 0
    done: bool = False
    terminated: str = ""            # horizon | disappeared | retry_budget


class PRFSTracker:
    """Sampling scheduler + outcome store (P03 Sec. III/V: separate process model).

    The tracker never touches trading decisions; it only consumes the
    rejection stream (P03 Sec. V isolation guarantee).
    """

    def __init__(self, config: PRFSConfig = PRFSConfig()):
        self.config = config
        self._jobs: Dict[str, _Job] = {}

    # -- emitter side ------------------------------------------------------
    def record_rejection(self, event: RejectionEvent) -> None:
        if event.key not in self._jobs:
            self._jobs[event.key] = _Job(event=event)

    # -- scheduler side ----------------------------------------------------
    def due(self, key: str, age_bars: int) -> bool:
        """Adaptive cadence: dense first, thinning after (P03 Sec. III.B)."""
        job = self._jobs.get(key)
        if job is None or job.done:
            return False
        if age_bars > self.config.horizon_bars:
            job.done, job.terminated = True, "horizon"
            return False
        if age_bars <= self.config.dense_bars:
            return True
        return (age_bars - self.config.dense_bars) % self.config.thin_every == 0

    def record_observation(self, obs: ForwardObservation) -> None:
        job = self._jobs.get(obs.key)
        if job is None or job.done:
            return
        job.observations.append(obs)
        if obs.price is None or (isinstance(obs.price, float) and math.isnan(obs.price)):
            job.missing += 1
            if job.missing >= self.config.max_missing:
                job.done, job.terminated = True, "disappeared"  # P03 Sec. III.C(b)
        else:
            job.missing = 0
        if obs.age_bars >= self.config.horizon_bars:
            job.done, job.terminated = True, "horizon"           # P03 Sec. III.C(a)

    def finalize(self) -> None:
        for job in self._jobs.values():
            if not job.done:
                job.done, job.terminated = True, "horizon"

    def observations_for(self, key: str) -> List[ForwardObservation]:
        job = self._jobs.get(key)
        return list(job.observations) if job else []

    def events(self) -> List[RejectionEvent]:
        return [j.event for j in self._jobs.values()]


# ---------------------------------------------------------------------------
# Five-tier classification (P04 Sec. III.B) - single-pass, deterministic
# ---------------------------------------------------------------------------

def _valid_prices(obs: List[ForwardObservation]) -> List[Tuple[int, float]]:
    out = []
    for o in obs:
        if o.price is not None and not (isinstance(o.price, float) and math.isnan(o.price)) and o.price > 0:
            out.append((o.age_bars, float(o.price)))
    return out


def classify_event(event: RejectionEvent, obs: List[ForwardObservation],
                   config: PRFSConfig = PRFSConfig()) -> OutcomeTier:
    """Apply the P04 Sec. III.B rule to one rejection event."""
    valid = _valid_prices(obs)
    if not valid:
        return OutcomeTier.UNCLASSIFIABLE
    ref = valid[0][1]  # smallest-age sample price (P04: reference price)
    if not (ref > 0):
        return OutcomeTier.UNCLASSIFIABLE
    if len(valid) == 1:
        age = valid[0][0]
        # P04 T3 vs T5: single sample within 60min-equivalent or not
        return (OutcomeTier.SAVED_EARLY_DEATH
                if age <= config.early_death_max_age_bars
                else OutcomeTier.UNCLASSIFIABLE)
    prices = [p for _, p in valid]
    trough, peak = min(prices), max(prices)
    # P04 tie-break: missed-over-saved when both hold
    if peak >= config.missed_runup * ref:
        return OutcomeTier.MISSED_MOON
    if trough <= config.saved_drawdown * ref:
        return OutcomeTier.SAVED_WINDOWED
    return OutcomeTier.FLAT


# ---------------------------------------------------------------------------
# Audit report (P04 Sec. III.C + V)
# ---------------------------------------------------------------------------

@dataclass
class FilterAudit:
    reason: str
    n: int
    saved_windowed: int = 0
    missed: int = 0
    saved_early_death: int = 0
    flat: int = 0
    unclassifiable: int = 0

    @property
    def conservative_ratio(self) -> Optional[float]:
        """SAVED_WINDOWED / MISSED headline ratio (P04 Sec. V.B). None if no misses."""
        if self.missed == 0:
            return None
        return round(self.saved_windowed / self.missed, 2)

    @property
    def net_positive(self) -> Optional[bool]:
        """Per-filter verdict needs adequate sample (P04: n=4 insufficient)."""
        if self.n < PRFSConfig.min_sample_size:
            return None
        return self.saved_windowed > self.missed


def audit(tracker: PRFSTracker,
          config: PRFSConfig = PRFSConfig()) -> Dict[str, FilterAudit]:
    """Single-pass deterministic audit over all jobs (cf. P04 audit.js)."""
    report: Dict[str, FilterAudit] = {}
    for event in tracker.events():
        tier = classify_event(event, tracker.observations_for(event.key), config)
        row = report.setdefault(event.reason, FilterAudit(reason=event.reason, n=0))
        row.n += 1
        if tier is OutcomeTier.SAVED_WINDOWED:
            row.saved_windowed += 1
        elif tier is OutcomeTier.MISSED_MOON:
            row.missed += 1
        elif tier is OutcomeTier.SAVED_EARLY_DEATH:
            row.saved_early_death += 1
        elif tier is OutcomeTier.FLAT:
            row.flat += 1
        else:
            row.unclassifiable += 1
    return report


def headline_ratio(report: Dict[str, FilterAudit]) -> Optional[float]:
    """Aggregate conservative save-to-miss (P04 headline 3.7:1 style)."""
    saved = sum(r.saved_windowed for r in report.values())
    missed = sum(r.missed for r in report.values())
    return round(saved / missed, 2) if missed else None
