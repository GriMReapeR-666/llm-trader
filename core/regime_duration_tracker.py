"""Regime Duration Tracker.

Tracks the history of regime transitions with dwell times so the LLM can see
how long the market has been in each regime, how many transitions occurred,
and the current regime's stability.

Architecture:
    RegimeDurationTracker is updated each cycle with the current regime state.
    It detects transitions (regime changes) and records the dwell time of each.
    State persists to JSON so it survives restarts.

    The tracker exposes:
    - current_regime: the active regime
    - current_dwell_bars: how many bars in current regime
    - current_dwell_pct: current dwell as % of average regime duration
    - transition_count: total regime changes observed
    - avg_dwell_bars: average regime lifetime
    - stability_score: 0-1 score (higher = more stable, fewer transitions)
    - recent_transitions: last 10 regime changes with timestamps
    - regime_distribution: % of time spent in each regime type
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.state_persistence import StoreBackedState

logger = logging.getLogger("RegimeDurationTracker")

STATE_FILE = "regime_duration_state.json"


@dataclass
class RegimeTransition:
    """A single regime transition event."""
    from_regime: str
    to_regime: str
    cycle_num: int
    timestamp: float
    dwell_bars: int  # how long the from_regime lasted

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeDwellRecord:
    """Accumulated dwell time for a specific regime type."""
    regime: str
    total_bars: int = 0
    visit_count: int = 0

    @property
    def avg_dwell(self) -> float:
        return self.total_bars / max(1, self.visit_count)


class RegimeDurationTracker(StoreBackedState):
    """Tracks regime transitions and dwell times."""

    store_key = "regime_duration_state"

    def __init__(self, state_file: str = STATE_FILE, store: Optional[Any] = None):
        self.state_file = state_file
        self.store = store
        self._load_state()
        if store is not None:
            self.attach_store(store)

    def _load_state(self) -> None:
        data = self._load_state_dict()
        try:
            self.current_regime = data.get("current_regime", "UNKNOWN")
            self.current_dwell_bars = data.get("current_dwell_bars", 0)
            self.transition_count = data.get("transition_count", 0)
            self.transitions = [
                RegimeTransition(**t) for t in data.get("transitions", [])
            ]
            self.dwell_records = {
                k: RegimeDwellRecord(**v) for k, v in data.get("dwell_records", {}).items()
            }
            self._regime_bars = data.get("_regime_bars", {})
            if data:
                return
        except Exception as exc:
            logger.debug("regime detection failed: %s", exc)
        # Defaults
        self.current_regime = "UNKNOWN"
        self.current_dwell_bars = 0
        self.transition_count = 0
        self.transitions: List[RegimeTransition] = []
        self.dwell_records: Dict[str, RegimeDwellRecord] = {}
        self._regime_bars: Dict[str, int] = {}  # regime -> total bars observed

    def _save_state(self) -> None:
        self._save_state_dict({
            "current_regime": self.current_regime,
            "current_dwell_bars": self.current_dwell_bars,
            "transition_count": self.transition_count,
            "transitions": [t.to_dict() for t in self.transitions[-20:]],
            "dwell_records": {k: asdict(v) for k, v in self.dwell_records.items()},
            "_regime_bars": self._regime_bars,
        })

    def update(self, cycle_num: int, regime_state: Dict[str, Any]) -> None:
        """Update tracker with the current regime state.

        Args:
            cycle_num: Current cycle number.
            regime_state: Dict from HierarchicalMetaPortfolioEngine with 'primary_regime' key.
        """
        new_regime = (regime_state or {}).get("primary_regime", "UNKNOWN")

        if new_regime != self.current_regime:
            # Record the transition
            transition = RegimeTransition(
                from_regime=self.current_regime,
                to_regime=new_regime,
                cycle_num=cycle_num,
                timestamp=time.time(),
                dwell_bars=self.current_dwell_bars,
            )
            self.transitions.append(transition)
            self.transition_count += 1

            # Update dwell record for the regime we're leaving
            if self.current_regime not in self.dwell_records:
                self.dwell_records[self.current_regime] = RegimeDwellRecord(regime=self.current_regime)
            self.dwell_records[self.current_regime].total_bars += self.current_dwell_bars
            self.dwell_records[self.current_regime].visit_count += 1

            # Reset for new regime
            self.current_regime = new_regime
            self.current_dwell_bars = 0

        self.current_dwell_bars += 1
        self._regime_bars[new_regime] = self._regime_bars.get(new_regime, 0) + 1
        self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Return current status for LLM context."""
        total_bars = sum(self._regime_bars.values()) or 1

        # Compute average dwell across all regimes
        avg_dwell = 0.0
        if self.dwell_records:
            dwells = [r.avg_dwell for r in self.dwell_records.values() if r.visit_count > 0]
            avg_dwell = sum(dwells) / len(dwells) if dwells else 0.0

        # Stability score: fewer transitions = more stable
        # Score = 1.0 when 0 transitions, decays exponentially
        stability_score = min(1.0, 1.0 / (1.0 + 0.1 * self.transition_count))

        # Current regime distribution
        regime_distribution = {
            regime: round(bars / total_bars, 4)
            for regime, bars in sorted(self._regime_bars.items(), key=lambda x: -x[1])
        }

        # Current dwell as % of average
        dwell_pct = (self.current_dwell_bars / avg_dwell * 100) if avg_dwell > 0 else 0.0

        # Recent transitions (last 10)
        recent = [
            {
                "from": t.from_regime,
                "to": t.to_regime,
                "cycle": t.cycle_num,
                "dwell_bars": t.dwell_bars,
            }
            for t in self.transitions[-10:]
        ]

        return {
            "current_regime": self.current_regime,
            "current_dwell_bars": self.current_dwell_bars,
            "current_dwell_pct_of_avg": round(dwell_pct, 1),
            "transition_count": self.transition_count,
            "avg_regime_dwell_bars": round(avg_dwell, 1),
            "stability_score": round(stability_score, 4),
            "regime_distribution": regime_distribution,
            "recent_transitions": recent,
            "total_bars_observed": total_bars,
        }

    def format_report(self) -> str:
        """Human-readable regime duration report."""
        status = self.get_status()
        lines = [
            f"Regime Duration (cycle {status['total_bars_observed']}):",
            f"  Current: {status['current_regime']} "
            f"({status['current_dwell_bars']} bars, "
            f"{status['current_dwell_pct_of_avg']:.0f}% of avg dwell)",
            f"  Transitions: {status['transition_count']}",
            f"  Avg dwell: {status['avg_regime_dwell_bars']:.1f} bars",
            f"  Stability: {status['stability_score']:.2f}",
            f"  Distribution:",
        ]
        for regime, pct in status["regime_distribution"].items():
            lines.append(f"    {regime:20s} {pct*100:5.1f}%")
        if status["recent_transitions"]:
            lines.append(f"  Recent transitions:")
            for t in status["recent_transitions"][-3:]:
                lines.append(
                    f"    {t['from']} -> {t['to']} @ cycle {t['cycle']} "
                    f"(dwell={t['dwell_bars']} bars)"
                )
        return "\n".join(lines)
