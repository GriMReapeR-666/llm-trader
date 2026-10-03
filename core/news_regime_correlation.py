"""News Regime Correlation Analysis — Auto-Adjust Blackout Windows by Historical Accuracy.

Tracks every economic event's actual price reaction (spike size in pips, direction,
duration), computes hit rates per event tier/type/currency, and dynamically adjusts
NewsTradingGate blackout windows based on measured accuracy.

Core insight: Not all Tier 1 events cause equal spikes. NFP routinely moves 100-200
pips, while GDP often moves 30-60 pips. A static 60min blackout for both wastes
opportunity on GDP while being too tight for NFP.

Architecture:
  ┌─────────────────────────────────────────────────────────────────┐
  │  NewsRegimeCorrelation (SQLite-backed)                          │
  │                                                                 │
  │  record_event_outcome(event, price_before, price_after)        │
  │    → stores spike_pips, direction, duration, tier, currency    │
  │                                                                 │
  │  compute_hit_rates(lookback_days=90)                           │
  │    → per-event-type: hit_rate, avg_spike, median_spike, p95    │
  │    → per-tier: aggregate hit rates                              │
  │    → per-currency: regime-specific patterns                     │
  │                                                                 │
  │  get_adjusted_windows(tier, event_title, symbol)               │
  │    → dynamic before/after minutes based on historical accuracy  │
  │    → widens for events that historically spike hard            │
  │    → narrows for events that historically are duds             │
  │                                                                 │
  │  get_correlation_report() → full analytics dashboard data      │
  └─────────────────────────────────────────────────────────────────┘

Usage:
    corr = NewsRegimeCorrelation()
    corr.record_event_outcome(
        event_title="NON-FARM PAYROLLS", tier=1, currency="USD",
        event_time=now, price_before=1.1000, price_after=1.1085,
    )
    windows = corr.get_adjusted_windows(tier=1, event_title="NON-FARM PAYROLLS", symbol="EURUSD")
    # → {"before": 75, "after": 25}  (wider than default 60/20 because NFP historically spikes hard)

    gate = NewsTradingGate(blackout_windows_override=corr.get_all_adjusted_windows())
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default blackout windows (from news_trading.py)
DEFAULT_BLACKOUT_WINDOWS = {
    1: {"before": 60, "after": 20},
    2: {"before": 30, "after": 15},
    3: {"before": 15, "after": 10},
}

# Spike threshold in pips — events moving less than this are "duds"
DUD_SPIKE_THRESHOLD_PIPS = 5.0

# Minimum observations before we trust correlation data
MIN_OBSERVATIONS = 5

# Maximum adjustment factor (don't let windows go below 1/3 or above 3x default)
MAX_SHRINK_FACTOR = 0.33
MAX_GROW_FACTOR = 3.0

# How old (days) can correlation data be before we discount it
DATA_FRESHNESS_DAYS = 180

# Decay weight: more recent events get higher weight in the moving average
RECENCY_HALF_LIFE_DAYS = 60


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class EventOutcome:
    """A recorded event outcome — the actual price reaction."""
    event_title: str
    tier: int
    currency: str
    event_time: datetime
    price_before: float
    price_after: float  # Price N minutes after event (see after_window_min)
    symbol: str = "EURUSD"
    spike_pips: float = 0.0
    direction: str = "NEUTRAL"  # "BULLISH", "BEARISH", "NEUTRAL"
    max_spike_pips: float = 0.0  # Max excursion during the event window
    duration_min: float = 15.0  # How long we measured the reaction
    hit_threshold_pips: float = 10.0  # What "counts" as a spike for this tier
    is_hit: bool = False  # Did the event exceed the threshold?
    id: Optional[int] = None
    recorded_at: Optional[datetime] = None

    def __post_init__(self):
        if self.spike_pips == 0.0:
            pip_value = 100.0 if "JPY" in self.symbol.upper() else 10000.0
            self.spike_pips = (self.price_after - self.price_before) * pip_value
        if self.direction == "NEUTRAL":
            if self.spike_pips > 1.0:
                self.direction = "BULLISH"
            elif self.spike_pips < -1.0:
                self.direction = "BEARISH"
        if self.max_spike_pips == 0.0:
            self.max_spike_pips = abs(self.spike_pips)
        self.is_hit = abs(self.spike_pips) >= self.hit_threshold_pips


@dataclass
class EventHitRate:
    """Aggregated hit rate statistics for a specific event type."""
    event_title: str
    tier: int
    currency: str
    n_events: int = 0
    n_hits: int = 0  # Events that exceeded spike threshold
    hit_rate: float = 0.0  # n_hits / n_events
    avg_spike_pips: float = 0.0
    median_spike_pips: float = 0.0
    p95_spike_pips: float = 0.0
    avg_duration_min: float = 15.0
    bullish_rate: float = 0.0  # % of events that were bullish
    bearish_rate: float = 0.0
    recommended_before_min: int = 60
    recommended_after_min: int = 20
    confidence: str = "LOW"  # LOW, MEDIUM, HIGH based on n_events
    last_event_time: Optional[datetime] = None
    avg_abs_spike_pips: float = 0.0  # Mean absolute spike


@dataclass
class CorrelationReport:
    """Full correlation analysis report for dashboard display."""
    total_events_recorded: int = 0
    events_by_tier: Dict[int, int] = field(default_factory=dict)
    overall_hit_rate: float = 0.0
    event_hit_rates: List[EventHitRate] = field(default_factory=list)
    regime_adjustments: Dict[str, Dict[str, int]] = field(default_factory=dict)
    accuracy_score: float = 0.0  # How well our blackout windows correlate with actual spikes
    window_efficiency: float = 0.0  # % of blackout time that actually prevented bad trades
    false_positive_rate: float = 0.0  # % of blackouts where nothing happened
    generated_at: Optional[datetime] = None


# =============================================================================
# SQLite Schema
# =============================================================================

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS event_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_title TEXT NOT NULL,
    tier INTEGER NOT NULL,
    currency TEXT NOT NULL,
    event_time TEXT NOT NULL,
    price_before REAL NOT NULL,
    price_after REAL NOT NULL,
    symbol TEXT NOT NULL DEFAULT 'EURUSD',
    spike_pips REAL NOT NULL DEFAULT 0.0,
    direction TEXT NOT NULL DEFAULT 'NEUTRAL',
    max_spike_pips REAL NOT NULL DEFAULT 0.0,
    duration_min REAL NOT NULL DEFAULT 15.0,
    hit_threshold_pips REAL NOT NULL DEFAULT 10.0,
    is_hit INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outcomes_event ON event_outcomes(event_title);
CREATE INDEX IF NOT EXISTS idx_outcomes_tier ON event_outcomes(tier);
CREATE INDEX IF NOT EXISTS idx_outcomes_time ON event_outcomes(event_time);
CREATE INDEX IF NOT EXISTS idx_outcomes_currency ON event_outcomes(currency);

CREATE TABLE IF NOT EXISTS window_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_title TEXT NOT NULL,
    tier INTEGER NOT NULL,
    currency TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT 'EURUSD',
    adjusted_before INTEGER NOT NULL,
    adjusted_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    n_events INTEGER NOT NULL,
    hit_rate REAL NOT NULL,
    avg_abs_spike REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# =============================================================================
# News Regime Correlation Engine
# =============================================================================

class NewsRegimeCorrelation:
    """Tracks event outcomes and auto-adjusts blackout windows.

    SQLite-backed for persistence across restarts. Computes rolling hit rates
    with exponential recency weighting so recent events matter more than
    events from 6 months ago.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(project_root, "artifacts", "news_regime_correlation.db")
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, timeout=10)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _ensure_schema(self):
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------------------------------
    # Recording
    # -------------------------------------------------------------------------

    def record_event_outcome(
        self,
        event_title: str,
        tier: int,
        currency: str,
        event_time: datetime,
        price_before: float,
        price_after: float,
        symbol: str = "EURUSD",
        max_spike_pips: float = 0.0,
        duration_min: float = 15.0,
        hit_threshold_pips: Optional[float] = None,
    ) -> EventOutcome:
        """Record the actual price reaction to an economic event.

        Args:
            event_title: Event name (e.g. "NON-FARM PAYROLLS")
            tier: Event tier (1=highest impact)
            currency: Event currency (e.g. "USD")
            event_time: When the event was released
            price_before: Price right before the event
            price_after: Price after the reaction window
            symbol: Trading symbol
            max_spike_pips: Maximum price excursion during the window
            duration_min: How long we measured the reaction
            hit_threshold_pips: Override threshold for "is this a spike?"

        Returns:
            EventOutcome with computed spike_pips, direction, is_hit
        """
        if hit_threshold_pips is None:
            hit_threshold_pips = self._default_hit_threshold(tier)

        outcome = EventOutcome(
            event_title=event_title.upper().strip(),
            tier=tier,
            currency=currency.upper().strip(),
            event_time=event_time,
            price_before=price_before,
            price_after=price_after,
            symbol=symbol,
            max_spike_pips=max_spike_pips,
            duration_min=duration_min,
            hit_threshold_pips=hit_threshold_pips,
        )

        conn = self._get_conn()
        conn.execute(
            """INSERT INTO event_outcomes
               (event_title, tier, currency, event_time, price_before, price_after,
                symbol, spike_pips, direction, max_spike_pips, duration_min,
                hit_threshold_pips, is_hit, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                outcome.event_title,
                outcome.tier,
                outcome.currency,
                outcome.event_time.isoformat(),
                outcome.price_before,
                outcome.price_after,
                outcome.symbol,
                round(outcome.spike_pips, 4),
                outcome.direction,
                round(outcome.max_spike_pips, 4),
                outcome.duration_min,
                outcome.hit_threshold_pips,
                1 if outcome.is_hit else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        outcome.id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        logger.info(
            "Recorded event outcome: %s tier=%d spike=%.1f pips hit=%s (id=%d)",
            outcome.event_title, outcome.tier, outcome.spike_pips, outcome.is_hit, outcome.id,
        )
        return outcome

    def record_price_snapshot(
        self,
        event_title: str,
        tier: int,
        currency: str,
        event_time: datetime,
        price_at_event: float,
        symbol: str = "EURUSD",
        price_after_15m: Optional[float] = None,
        price_after_30m: Optional[float] = None,
        price_after_60m: Optional[float] = None,
        max_price_in_window: Optional[float] = None,
        min_price_in_window: Optional[float] = None,
    ) -> List[EventOutcome]:
        """Record multiple reaction measurements from a single event.

        This is more efficient than calling record_event_outcome multiple times.
        It records the 15m, 30m, and 60m reactions and picks the max excursion.
        """
        outcomes = []
        pip_value = 100.0 if "JPY" in symbol.upper() else 10000.0

        # Compute max excursion from high/low
        max_spike = 0.0
        if max_price_in_window is not None and min_price_in_window is not None:
            range_pips = (max_price_in_window - min_price_in_window) * pip_value
            max_spike = range_pips

        # Record 15-minute reaction (primary)
        if price_after_15m is not None:
            out = self.record_event_outcome(
                event_title=event_title, tier=tier, currency=currency,
                event_time=event_time, price_before=price_at_event,
                price_after=price_after_15m, symbol=symbol,
                max_spike_pips=max_spike, duration_min=15.0,
            )
            outcomes.append(out)

        # Record 30-minute reaction (secondary)
        if price_after_30m is not None:
            out = self.record_event_outcome(
                event_title=event_title, tier=tier, currency=currency,
                event_time=event_time, price_before=price_at_event,
                price_after=price_after_30m, symbol=symbol,
                max_spike_pips=max_spike, duration_min=30.0,
                hit_threshold_pips=self._default_hit_threshold(tier) * 1.2,
            )
            outcomes.append(out)

        # Record 60-minute reaction (tertiary)
        if price_after_60m is not None:
            out = self.record_event_outcome(
                event_title=event_title, tier=tier, currency=currency,
                event_time=event_time, price_before=price_at_event,
                price_after=price_after_60m, symbol=symbol,
                max_spike_pips=max_spike, duration_min=60.0,
                hit_threshold_pips=self._default_hit_threshold(tier) * 1.5,
            )
            outcomes.append(out)

        return outcomes

    # -------------------------------------------------------------------------
    # Hit Rate Computation
    # -------------------------------------------------------------------------

    def compute_hit_rates(
        self,
        lookback_days: int = 90,
        symbol: Optional[str] = None,
    ) -> List[EventHitRate]:
        """Compute per-event-type hit rates with exponential recency weighting.

        Returns a list of EventHitRate objects sorted by hit_rate descending.
        """
        conn = self._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?"]
        params: list = [cutoff]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)

        where_clause = " AND ".join(where_parts)
        query = f"""
            SELECT event_title, tier, currency, spike_pips, max_spike_pips,
                   direction, is_hit, duration_min, hit_threshold_pips, event_time
            FROM event_outcomes
            WHERE {where_clause}
            ORDER BY event_time DESC
        """
        rows = conn.execute(query, params).fetchall()

        if not rows:
            return []

        # Group by event title
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            key = row["event_title"]
            if key not in groups:
                groups[key] = []
            groups[key].append(dict(row))

        now = datetime.now(timezone.utc)
        hit_rates = []

        for event_title, events in groups.items():
            n = len(events)
            if n == 0:
                continue

            # Exponential recency weights
            weights = []
            for ev in events:
                try:
                    ev_time = datetime.fromisoformat(ev["event_time"])
                    if ev_time.tzinfo is None:
                        ev_time = ev_time.replace(tzinfo=timezone.utc)
                    age_days = max((now - ev_time).total_seconds() / 86400.0, 0.0)
                    w = math.exp(-0.693 * age_days / RECENCY_HALF_LIFE_DAYS)  # ln(2)/half_life
                except (ValueError, TypeError):
                    w = 1.0
                weights.append(w)

            weights_arr = np.array(weights)
            w_sum = float(np.sum(weights_arr))
            if w_sum <= 0:
                continue

            # Weighted statistics
            spikes = np.array([abs(ev["spike_pips"]) for ev in events])
            hits = np.array([ev["is_hit"] for ev in events], dtype=float)
            directions = [ev["direction"] for ev in events]

            weighted_hit_rate = float(np.sum(hits * weights_arr) / w_sum)
            weighted_avg_spike = float(np.sum(spikes * weights_arr) / w_sum)
            median_spike = float(np.median(spikes))
            p95_spike = float(np.percentile(spikes, 95)) if n >= 5 else median_spike * 1.5
            avg_duration = float(np.mean([ev["duration_min"] for ev in events]))

            bullish_count = sum(1 for d in directions if d == "BULLISH")
            bearish_count = sum(1 for d in directions if d == "BEARISH")
            bullish_rate = bullish_count / n if n > 0 else 0.5
            bearish_rate = bearish_count / n if n > 0 else 0.5

            tier = events[0]["tier"]
            currency = events[0]["currency"]

            # Recommended windows based on actual spike patterns
            rec_before, rec_after = self._compute_recommended_windows(
                weighted_avg_spike, p95_spike, weighted_hit_rate, tier, avg_duration,
            )

            # Confidence level based on sample size
            if n >= 15:
                confidence = "HIGH"
            elif n >= 8:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            last_time = None
            try:
                last_time = datetime.fromisoformat(events[0]["event_time"])
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

            hit_rates.append(EventHitRate(
                event_title=event_title,
                tier=tier,
                currency=currency,
                n_events=n,
                n_hits=int(round(weighted_hit_rate * n)),
                hit_rate=round(weighted_hit_rate, 4),
                avg_spike_pips=round(weighted_avg_spike, 2),
                median_spike_pips=round(median_spike, 2),
                p95_spike_pips=round(p95_spike, 2),
                avg_duration_min=round(avg_duration, 1),
                bullish_rate=round(bullish_rate, 4),
                bearish_rate=round(bearish_rate, 4),
                recommended_before_min=rec_before,
                recommended_after_min=rec_after,
                confidence=confidence,
                last_event_time=last_time,
                avg_abs_spike_pips=round(weighted_avg_spike, 2),
            ))

        hit_rates.sort(key=lambda h: h.avg_spike_pips, reverse=True)
        return hit_rates

    def _compute_recommended_windows(
        self,
        avg_spike: float,
        p95_spike: float,
        hit_rate: float,
        tier: int,
        avg_duration: float,
    ) -> Tuple[int, int]:
        """Compute recommended blackout window sizes based on historical spike data.

        Logic:
        - If events historically spike hard (avg_spike > 30 pips), widen windows
        - If events are duds (hit_rate < 0.2), shrink windows
        - Use p95 spike to set the upper bound
        - Don't go below MIN or above MAX of default * factor
        """
        defaults = DEFAULT_BLACKOUT_WINDOWS.get(tier, DEFAULT_BLACKOUT_WINDOWS[2])
        default_before = defaults["before"]
        default_after = defaults["after"]

        # Spike-based adjustment factor
        # avg_spike of 50 pips → factor ~1.5 (wider)
        # avg_spike of 5 pips → factor ~0.5 (narrower)
        spike_factor = 1.0 + math.log(max(avg_spike / 15.0, 0.1))

        # Hit-rate adjustment: high hit_rate → wider windows (events are real)
        hit_factor = 0.7 + (hit_rate * 0.6)  # Range: [0.7, 1.3]

        # Duration adjustment: longer reactions → wider after window
        duration_factor = max(1.0, avg_duration / 15.0)

        # Combined factor
        combined = spike_factor * hit_factor

        # Apply with bounds
        adj_before = int(round(default_before * max(min(combined, MAX_GROW_FACTOR), MAX_SHRINK_FACTOR)))
        adj_after = int(round(default_after * max(min(combined * duration_factor, MAX_GROW_FACTOR), MAX_SHRINK_FACTOR)))

        # Minimum window: never go below 5 minutes
        adj_before = max(adj_before, 5)
        adj_after = max(adj_after, 5)

        return adj_before, adj_after

    # -------------------------------------------------------------------------
    # Dynamic Window Retrieval
    # -------------------------------------------------------------------------

    def get_adjusted_windows(
        self,
        tier: int,
        event_title: str = "",
        symbol: str = "EURUSD",
        lookback_days: int = 90,
    ) -> Dict[str, int]:
        """Get the dynamically adjusted blackout window for an event.

        If we have enough historical data, returns adjusted windows.
        Otherwise falls back to defaults.

        Returns:
            {"before": int_minutes, "after": int_minutes}
        """
        event_title = event_title.upper().strip()

        hit_rates = self.compute_hit_rates(lookback_days=lookback_days, symbol=symbol)

        # Find matching event
        for hr in hit_rates:
            if hr.event_title == event_title and hr.tier == tier:
                if hr.n_events >= MIN_OBSERVATIONS:
                    return {
                        "before": hr.recommended_before_min,
                        "after": hr.recommended_after_min,
                        "source": "correlation",
                        "n_events": hr.n_events,
                        "hit_rate": hr.hit_rate,
                        "avg_spike_pips": hr.avg_spike_pips,
                    }
                else:
                    # Too few observations — use tier-level average
                    break

        # Fall back to tier-level average
        tier_events = [hr for hr in hit_rates if hr.tier == tier and hr.n_events >= MIN_OBSERVATIONS]
        if tier_events:
            avg_before = int(round(np.mean([hr.recommended_before_min for hr in tier_events])))
            avg_after = int(round(np.mean([hr.recommended_after_min for hr in tier_events])))
            return {
                "before": avg_before,
                "after": avg_after,
                "source": "tier_average",
                "n_events": sum(hr.n_events for hr in tier_events),
            }

        # Fall back to defaults
        defaults = DEFAULT_BLACKOUT_WINDOWS.get(tier, DEFAULT_BLACKOUT_WINDOWS[2])
        return {
            "before": defaults["before"],
            "after": defaults["after"],
            "source": "default",
        }

    def get_all_adjusted_windows(self, symbol: str = "EURUSD") -> Dict[int, Dict[str, int]]:
        """Get adjusted windows for all tiers (used by NewsTradingGate).

        Returns:
            {1: {"before": 75, "after": 25}, 2: {"before": 30, "after": 15}, ...}
        """
        result = {}
        for tier in (1, 2, 3):
            # Use average across all events in this tier
            hit_rates = self.compute_hit_rates(symbol=symbol)
            tier_events = [hr for hr in hit_rates if hr.tier == tier and hr.n_events >= MIN_OBSERVATIONS]

            if tier_events:
                avg_before = int(round(np.mean([hr.recommended_before_min for hr in tier_events])))
                avg_after = int(round(np.mean([hr.recommended_after_min for hr in tier_events])))
                result[tier] = {"before": avg_before, "after": avg_after}
            else:
                defaults = DEFAULT_BLACKOUT_WINDOWS.get(tier, DEFAULT_BLACKOUT_WINDOWS[2])
                result[tier] = {"before": defaults["before"], "after": defaults["after"]}

        return result

    # -------------------------------------------------------------------------
    # Correlation Report (for Dashboard)
    # -------------------------------------------------------------------------

    def get_correlation_report(
        self,
        lookback_days: int = 90,
        symbol: Optional[str] = None,
    ) -> CorrelationReport:
        """Generate a full correlation analysis report for dashboard display."""
        conn = self._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?"]
        params: list = [cutoff]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)
        where_clause = " AND ".join(where_parts)

        # Total events
        total = conn.execute(
            f"SELECT COUNT(*) FROM event_outcomes WHERE {where_clause}", params
        ).fetchone()[0]

        # Events by tier
        tier_rows = conn.execute(
            f"SELECT tier, COUNT(*) as cnt FROM event_outcomes WHERE {where_clause} GROUP BY tier",
            params,
        ).fetchall()
        events_by_tier = {row["tier"]: row["cnt"] for row in tier_rows}

        # Overall hit rate
        overall = conn.execute(
            f"SELECT AVG(is_hit) FROM event_outcomes WHERE {where_clause}", params
        ).fetchone()[0]
        overall_hit_rate = float(overall) if overall is not None else 0.0

        # Per-event hit rates
        event_hit_rates = self.compute_hit_rates(lookback_days=lookback_days, symbol=symbol)

        # Current adjustments
        regime_adjustments = {}
        for tier in (1, 2, 3):
            tier_events = [hr for hr in event_hit_rates if hr.tier == tier]
            if tier_events:
                regime_adjustments[str(tier)] = {
                    "before": int(round(np.mean([hr.recommended_before_min for hr in tier_events]))),
                    "after": int(round(np.mean([hr.recommended_after_min for hr in tier_events]))),
                }
            else:
                defaults = DEFAULT_BLACKOUT_WINDOWS.get(tier, DEFAULT_BLACKOUT_WINDOWS[2])
                regime_adjustments[str(tier)] = {
                    "before": defaults["before"],
                    "after": defaults["after"],
                }

        # Accuracy score: how well our current windows predict actual spikes
        # For each recorded event, check if the event was within a blackout window
        # and whether it actually spiked. High accuracy = blackouts align with spikes.
        accuracy_score, false_positive_rate, window_efficiency = self._compute_window_accuracy(
            cutoff, symbol
        )

        return CorrelationReport(
            total_events_recorded=total,
            events_by_tier=events_by_tier,
            overall_hit_rate=round(overall_hit_rate, 4),
            event_hit_rates=event_hit_rates,
            regime_adjustments=regime_adjustments,
            accuracy_score=round(accuracy_score, 4),
            window_efficiency=round(window_efficiency, 4),
            false_positive_rate=round(false_positive_rate, 4),
            generated_at=datetime.now(timezone.utc),
        )

    def _compute_window_accuracy(
        self,
        cutoff: str,
        symbol: Optional[str] = None,
    ) -> Tuple[float, float, float]:
        """Compute how well our blackout windows correlate with actual spikes.

        Returns:
            (accuracy_score, false_positive_rate, window_efficiency)

        accuracy_score: % of events that spiked where we had a blackout window active
        false_positive_rate: % of blackout windows where no spike occurred
        window_efficiency: ratio of spike-time blackout to total blackout time
        """
        conn = self._get_conn()
        where_parts = ["event_time >= ?", "is_hit = 1"]
        params: list = [cutoff]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)
        where_clause = " AND ".join(where_parts)

        # Events that actually spiked
        spike_events = conn.execute(
            f"SELECT COUNT(*) FROM event_outcomes WHERE {where_clause}", params
        ).fetchone()[0]

        # Total events (hit + miss)
        total_params = [cutoff]
        total_where = ["event_time >= ?"]
        if symbol:
            total_where.append("symbol = ?")
            total_params.append(symbol)
        total_events = conn.execute(
            f"SELECT COUNT(*) FROM event_outcomes WHERE {' AND '.join(total_where)}",
            total_params,
        ).fetchone()[0]

        if total_events == 0:
            return 0.0, 0.0, 0.0

        # Accuracy: what fraction of events we would have blocked actually spiked
        # (we can't perfectly measure this without knowing our exact windows,
        # so we use tier-level defaults as approximation)
        hit_events = conn.execute(
            f"""SELECT event_title, tier, spike_pips FROM event_outcomes
                WHERE {' AND '.join(total_where)}""",
            total_params,
        ).fetchall()

        would_block_count = 0
        actually_blocked_count = 0
        false_positives = 0

        for ev in hit_events:
            defaults = DEFAULT_BLACKOUT_WINDOWS.get(ev["tier"], DEFAULT_BLACKOUT_WINDOWS[2])
            is_spike = abs(ev["spike_pips"]) >= self._default_hit_threshold(ev["tier"])

            # If we blocked this event (based on default windows), was it a spike?
            would_block_count += 1  # All recorded events were "in window"
            if is_spike:
                actually_blocked_count += 1
            else:
                false_positives += 1

        accuracy = actually_blocked_count / max(would_block_count, 1)
        fp_rate = false_positives / max(would_block_count, 1)
        efficiency = accuracy  # Simplified: efficiency = how many blocked events were real spikes

        return accuracy, fp_rate, efficiency

    # -------------------------------------------------------------------------
    # Auto-Record from Live Trading
    # -------------------------------------------------------------------------

    def auto_record_from_price_data(
        self,
        event_title: str,
        tier: int,
        currency: str,
        event_time: datetime,
        bars: List[Dict[str, Any]],
        symbol: str = "EURUSD",
    ) -> Optional[EventOutcome]:
        """Automatically record event outcome from MT5 bar data.

        Given a list of M15 bars around the event time, extracts:
        - price_before: close of the bar at event_time
        - price_after: close of the bar 15 minutes later
        - max_spike_pips: max excursion (high-low) across the first 4 bars after event

        Args:
            event_title: Event name
            tier: Event tier
            currency: Event currency
            event_time: When the event was released (UTC)
            bars: List of M15 bar dicts with open/high/low/close/time
            symbol: Trading symbol

        Returns:
            EventOutcome if successful, None if insufficient data
        """
        if not bars:
            return None

        pip_value = 100.0 if "JPY" in symbol.upper() else 10000.0

        # Find the bar closest to event_time
        event_ts = event_time.timestamp()
        event_idx = None
        min_delta = float("inf")
        for i, bar in enumerate(bars):
            bar_ts = bar.get("time", 0)
            if isinstance(bar_ts, datetime):
                bar_ts = bar_ts.timestamp()
            delta = abs(bar_ts - event_ts)
            if delta < min_delta:
                min_delta = delta
                event_idx = i

        if event_idx is None or min_delta > 3600:  # More than 1 hour off
            return None

        price_before = bars[event_idx].get("close", 0.0)
        if price_before <= 0:
            return None

        # Look at the next 4 bars (1 hour window for 15-min bars)
        max_high = price_before
        min_low = price_before
        price_after_15m = price_before

        end_idx = min(event_idx + 5, len(bars))
        for i in range(event_idx + 1, end_idx):
            bar = bars[i]
            high = bar.get("high", price_before)
            low = bar.get("low", price_before)
            close = bar.get("close", price_before)
            max_high = max(max_high, high)
            min_low = min(min_low, low)
            if i == event_idx + 1:
                price_after_15m = close

        max_spike = (max_high - min_low) * pip_value

        return self.record_event_outcome(
            event_title=event_title,
            tier=tier,
            currency=currency,
            event_time=event_time,
            price_before=price_before,
            price_after=price_after_15m,
            symbol=symbol,
            max_spike_pips=max_spike,
            duration_min=15.0,
        )

    # -------------------------------------------------------------------------
    # Query Helpers
    # -------------------------------------------------------------------------

    def get_event_history(
        self,
        event_title: Optional[str] = None,
        tier: Optional[int] = None,
        currency: Optional[str] = None,
        symbol: Optional[str] = None,
        lookback_days: int = 90,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query recent event outcomes with optional filters."""
        conn = self._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?"]
        params: list = [cutoff]
        if event_title:
            where_parts.append("event_title = ?")
            params.append(event_title.upper().strip())
        if tier is not None:
            where_parts.append("tier = ?")
            params.append(tier)
        if currency:
            where_parts.append("currency = ?")
            params.append(currency.upper().strip())
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)

        where_clause = " AND ".join(where_parts)
        query = f"""
            SELECT * FROM event_outcomes
            WHERE {where_clause}
            ORDER BY event_time DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_spike_distribution(
        self,
        event_title: Optional[str] = None,
        tier: Optional[int] = None,
        lookback_days: int = 90,
    ) -> Dict[str, Any]:
        """Get the distribution of spike sizes for analysis."""
        conn = self._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?"]
        params: list = [cutoff]
        if event_title:
            where_parts.append("event_title = ?")
            params.append(event_title.upper().strip())
        if tier is not None:
            where_parts.append("tier = ?")
            params.append(tier)

        where_clause = " AND ".join(where_parts)
        rows = conn.execute(
            f"SELECT spike_pips, abs(spike_pips) as abs_spike FROM event_outcomes WHERE {where_clause}",
            params,
        ).fetchall()

        if not rows:
            return {"n": 0, "mean": 0, "median": 0, "std": 0, "p5": 0, "p25": 0, "p75": 0, "p95": 0}

        spikes = np.array([abs(r["abs_spike"]) for r in rows])
        return {
            "n": len(spikes),
            "mean": round(float(np.mean(spikes)), 2),
            "median": round(float(np.median(spikes)), 2),
            "std": round(float(np.std(spikes)), 2),
            "p5": round(float(np.percentile(spikes, 5)), 2),
            "p25": round(float(np.percentile(spikes, 25)), 2),
            "p75": round(float(np.percentile(spikes, 75)), 2),
            "p95": round(float(np.percentile(spikes, 95)), 2),
            "n_hits": int(np.sum(spikes >= DUD_SPIKE_THRESHOLD_PIPS)),
            "hit_rate": round(float(np.mean(spikes >= DUD_SPIKE_THRESHOLD_PIPS)), 4),
        }

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _default_hit_threshold(tier: int) -> float:
        """Default spike threshold in pips by tier."""
        if tier == 1:
            return 20.0  # Tier 1: 20+ pips = "hit"
        elif tier == 2:
            return 10.0  # Tier 2: 10+ pips = "hit"
        else:
            return 5.0   # Tier 3: 5+ pips = "hit"

    def get_stats(self) -> Dict[str, Any]:
        """Quick summary statistics."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM event_outcomes").fetchone()[0]
        hits = conn.execute("SELECT COUNT(*) FROM event_outcomes WHERE is_hit = 1").fetchone()[0]
        tiers = conn.execute(
            "SELECT tier, COUNT(*) as cnt FROM event_outcomes GROUP BY tier"
        ).fetchall()

        return {
            "total_events": total,
            "total_hits": hits,
            "overall_hit_rate": round(hits / max(total, 1), 4),
            "by_tier": {row["tier"]: row["cnt"] for row in tiers},
            "db_path": self.db_path,
        }

    # -------------------------------------------------------------------------
    # Decay Monitor — Alert when event hit rates drop below threshold
    # -------------------------------------------------------------------------

    def detect_decay(
        self,
        lookback_days: int = 90,
        short_window_days: int = 30,
        symbol: Optional[str] = None,
        hit_rate_threshold: float = 0.3,
        spike_drop_pct: float = 0.4,
    ) -> List[Dict[str, Any]]:
        """Detect alpha decay in event spike patterns.

        Compares recent (short_window) vs historical (full lookback) performance
        for each event type. Alerts when:
          1. Recent hit rate drops below hit_rate_threshold
          2. Recent avg spike drops by spike_drop_pct vs historical
          3. Recent events are all duds (0 hits in last N events)

        Args:
            lookback_days: Full historical window for baseline
            short_window_days: Recent window for decay comparison
            symbol: Filter by symbol
            hit_rate_threshold: Minimum hit rate before alert
            spike_drop_pct: Minimum % drop in avg spike to trigger alert

        Returns:
            List of decay alerts, sorted by severity
        """
        conn = self._get_conn()
        now = datetime.now(timezone.utc)
        short_cutoff = (now - timedelta(days=short_window_days)).isoformat()
        long_cutoff = (now - timedelta(days=lookback_days)).isoformat()

        where_parts_long = ["event_time >= ?"]
        params_long: list = [long_cutoff]
        if symbol:
            where_parts_long.append("symbol = ?")
            params_long.append(symbol)
        where_long = " AND ".join(where_parts_long)

        where_parts_short = ["event_time >= ?"]
        params_short: list = [short_cutoff]
        if symbol:
            where_parts_short.append("symbol = ?")
            params_short.append(symbol)
        where_short = " AND ".join(where_parts_short)

        # Get all event types with enough history
        event_types = conn.execute(
            f"SELECT DISTINCT event_title FROM event_outcomes WHERE {where_long}",
            params_long,
        ).fetchall()

        alerts = []

        for ev_row in event_types:
            title = ev_row["event_title"]

            # Historical stats
            long_rows = conn.execute(
                f"SELECT spike_pips, is_hit FROM event_outcomes WHERE {where_long} AND event_title = ?",
                params_long + [title],
            ).fetchall()

            if len(long_rows) < MIN_OBSERVATIONS:
                continue

            long_spikes = np.array([abs(r["spike_pips"]) for r in long_rows])
            long_hits = np.array([r["is_hit"] for r in long_rows], dtype=float)
            long_hit_rate = float(np.mean(long_hits))
            long_avg_spike = float(np.mean(long_spikes))

            # Recent stats
            short_rows = conn.execute(
                f"SELECT spike_pips, is_hit FROM event_outcomes WHERE {where_short} AND event_title = ?",
                params_short + [title],
            ).fetchall()

            if len(short_rows) < 2:
                continue  # Need at least 2 recent events to detect decay

            short_spikes = np.array([abs(r["spike_pips"]) for r in short_rows])
            short_hits = np.array([r["is_hit"] for r in short_rows], dtype=float)
            short_hit_rate = float(np.mean(short_hits))
            short_avg_spike = float(np.mean(short_spikes))

            # Decay detection
            reasons = []
            severity = "INFO"

            # 1. Hit rate collapsed
            if short_hit_rate < hit_rate_threshold and long_hit_rate >= hit_rate_threshold:
                reasons.append(f"Hit rate dropped {long_hit_rate:.0%} → {short_hit_rate:.0%}")
                severity = "WARNING"

            # 2. Spike size decayed
            if long_avg_spike > 0:
                drop_pct = (long_avg_spike - short_avg_spike) / long_avg_spike
                if drop_pct >= spike_drop_pct:
                    reasons.append(f"Avg spike decayed {long_avg_spike:.1f} → {short_avg_spike:.1f} pips ({drop_pct:.0%} drop)")
                    severity = "WARNING"

            # 3. Recent complete dud streak
            if len(short_rows) >= 3 and all(r["is_hit"] == 0 for r in short_rows):
                reasons.append(f"{len(short_rows)} consecutive duds (no spikes)")
                severity = "CRITICAL" if long_hit_rate > 0.5 else "WARNING"

            # 4. Hit rate below absolute threshold
            if short_hit_rate < hit_rate_threshold and not reasons:
                reasons.append(f"Hit rate {short_hit_rate:.0%} below threshold {hit_rate_threshold:.0%}")
                severity = "INFO"

            if reasons:
                # Get tier from the data
                tier_rows = conn.execute(
                    "SELECT tier FROM event_outcomes WHERE event_title = ? LIMIT 1",
                    (title,),
                ).fetchone()
                tier = tier_rows["tier"] if tier_rows else 0

                alerts.append({
                    "event_title": title,
                    "tier": tier,
                    "severity": severity,
                    "reasons": reasons,
                    "historical": {
                        "n_events": len(long_rows),
                        "hit_rate": round(long_hit_rate, 4),
                        "avg_spike_pips": round(long_avg_spike, 2),
                    },
                    "recent": {
                        "n_events": len(short_rows),
                        "hit_rate": round(short_hit_rate, 4),
                        "avg_spike_pips": round(short_avg_spike, 2),
                    },
                    "recommendation": self._decay_recommendation(severity, long_hit_rate, short_hit_rate, title),
                })

        # Sort: CRITICAL first, then WARNING, then INFO
        severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        alerts.sort(key=lambda a: (severity_order.get(a["severity"], 9), -a["recent"]["avg_spike_pips"]))

        return alerts

    @staticmethod
    def _decay_recommendation(
        severity: str,
        long_hit_rate: float,
        short_hit_rate: float,
        event_title: str,
    ) -> str:
        """Generate a recommendation based on decay severity."""
        if severity == "CRITICAL":
            return (
                f"CRITICAL: {event_title} is no longer moving the market. "
                f"Consider removing from blackout calculation entirely or "
                f"reducing window to minimum (5min). Monitor for 2 more occurrences."
            )
        elif severity == "WARNING":
            if short_hit_rate < 0.1:
                return (
                    f"WARNING: {event_title} spike activity declining sharply. "
                    f"Recommend narrowing blackout window by 30-50%. "
                    f"May indicate regime change in how market prices this event."
                )
            else:
                return (
                    f"WARNING: {event_title} showing reduced volatility. "
                    f"Window auto-adjustment will narrow blackout. "
                    f"If pattern persists, investigate macro regime shift."
                )
        else:
            return (
                f"INFO: {event_title} hit rate below threshold but within normal range. "
                f"Continuing to monitor. No action needed yet."
            )

    def get_decay_dashboard(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get a complete decay monitoring dashboard for the TUI."""
        alerts = self.detect_decay(symbol=symbol)
        stats = self.get_stats()
        hit_rates = self.compute_hit_rates(symbol=symbol)

        # Overall health score
        if not alerts:
            health = "HEALTHY"
        elif any(a["severity"] == "CRITICAL" for a in alerts):
            health = "DECAYING"
        else:
            health = "MONITORING"

        return {
            "health": health,
            "total_events": stats["total_events"],
            "overall_hit_rate": stats["overall_hit_rate"],
            "n_alerts": len(alerts),
            "alerts": alerts,
            "event_hit_rates": [
                {
                    "event_title": hr.event_title,
                    "tier": hr.tier,
                    "n_events": hr.n_events,
                    "hit_rate": hr.hit_rate,
                    "avg_spike_pips": hr.avg_spike_pips,
                    "recommended_before": hr.recommended_before_min,
                    "recommended_after": hr.recommended_after_min,
                    "confidence": hr.confidence,
                }
                for hr in hit_rates
            ],
        }

    # -------------------------------------------------------------------------
    # Export — Save correlation data as JSON for offline analysis
    # -------------------------------------------------------------------------

    def export_json(
        self,
        output_path: Optional[str] = None,
        lookback_days: int = 365,
        symbol: Optional[str] = None,
    ) -> str:
        """Export all correlation data as a single JSON file.

        Includes:
          - All event outcomes (raw records)
          - Per-event hit rates and statistics
          - Decay alerts
          - Adjusted windows
          - Heatmap data (event × month matrix)
          - Summary stats

        Args:
            output_path: Where to write the JSON. Defaults to artifacts/news_correlation_export.json
            lookback_days: How much history to export
            symbol: Filter by symbol

        Returns:
            Path to the exported JSON file
        """
        import json

        if output_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_path = os.path.join(project_root, "artifacts", "news_correlation_export.json")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Collect all data
        export_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "symbol_filter": symbol,
            "summary": self.get_stats(),
            "hit_rates": [],
            "event_outcomes": [],
            "decay_alerts": self.detect_decay(lookback_days=lookback_days, symbol=symbol),
            "adjusted_windows": self.get_all_adjusted_windows(symbol=symbol or "EURUSD"),
            "heatmap": self.get_heatmap_data(lookback_days=lookback_days, symbol=symbol),
            "spike_distributions": {},
        }

        # Hit rates
        for hr in self.compute_hit_rates(lookback_days=lookback_days, symbol=symbol):
            export_data["hit_rates"].append({
                "event_title": hr.event_title,
                "tier": hr.tier,
                "currency": hr.currency,
                "n_events": hr.n_events,
                "hit_rate": hr.hit_rate,
                "avg_spike_pips": hr.avg_spike_pips,
                "median_spike_pips": hr.median_spike_pips,
                "p95_spike_pips": hr.p95_spike_pips,
                "bullish_rate": hr.bullish_rate,
                "bearish_rate": hr.bearish_rate,
                "recommended_before_min": hr.recommended_before_min,
                "recommended_after_min": hr.recommended_after_min,
                "confidence": hr.confidence,
                "last_event_time": hr.last_event_time.isoformat() if hr.last_event_time else None,
            })

        # Raw event outcomes
        outcomes = self.get_event_history(
            lookback_days=lookback_days, symbol=symbol, limit=5000,
        )
        export_data["event_outcomes"] = outcomes

        # Spike distributions per event type
        seen_titles = set()
        for ev in outcomes:
            t = ev.get("event_title", "")
            if t not in seen_titles:
                seen_titles.add(t)
                export_data["spike_distributions"][t] = self.get_spike_distribution(
                    event_title=t, lookback_days=lookback_days,
                )

        # Write JSON
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)

        logger.info("Exported correlation data to %s (%d outcomes, %d hit rates)",
                     output_path, len(outcomes), len(export_data["hit_rates"]))
        return output_path

    # -------------------------------------------------------------------------
    # Heatmap — Event-type × Time-of-year spike patterns
    # -------------------------------------------------------------------------

    def get_heatmap_data(
        self,
        lookback_days: int = 365,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a heatmap matrix: event-type × month → avg spike pips.

        Returns:
            {
              "events": ["NFP", "CPI", ...],
              "months": ["Jan", "Feb", ...],
              "matrix": [[spike_jan_nfp, spike_feb_nfp, ...], ...],
              "hit_matrix": [[hit_jan_nfp, ...], ...],
              "n_matrix": [[count_jan_nfp, ...], ...],
            }
        """
        conn = self._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?"]
        params: list = [cutoff]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)
        where_clause = " AND ".join(where_parts)

        rows = conn.execute(
            f"SELECT event_title, event_time, spike_pips, is_hit FROM event_outcomes WHERE {where_clause} ORDER BY event_time",
            params,
        ).fetchall()

        if not rows:
            return {"events": [], "months": [], "matrix": [], "hit_matrix": [], "n_matrix": []}

        # Group by event title and month
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        # Collect unique event titles (sorted by frequency)
        event_counts: Dict[str, int] = {}
        event_data: Dict[str, Dict[int, List[float]]] = {}  # title → {month: [spikes]}
        event_hits: Dict[str, Dict[int, List[int]]] = {}   # title → {month: [is_hit]}

        for row in rows:
            title = row["event_title"]
            try:
                ev_time = datetime.fromisoformat(row["event_time"])
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
                month = ev_time.month - 1  # 0-indexed
            except (ValueError, TypeError):
                continue

            event_counts[title] = event_counts.get(title, 0) + 1
            if title not in event_data:
                event_data[title] = {m: [] for m in range(12)}
                event_hits[title] = {m: [] for m in range(12)}
            event_data[title][month].append(abs(row["spike_pips"]))
            event_hits[title][month].append(row["is_hit"])

        # Sort events by total frequency (most recorded first)
        sorted_events = sorted(event_counts.keys(), key=lambda t: event_counts[t], reverse=True)

        # Build matrices
        spike_matrix = []
        hit_matrix = []
        n_matrix = []

        for title in sorted_events:
            spike_row = []
            hit_row = []
            n_row = []
            for m in range(12):
                spikes = event_data[title].get(m, [])
                hits = event_hits[title].get(m, [])
                if spikes:
                    spike_row.append(round(float(np.mean(spikes)), 1))
                    hit_row.append(round(float(np.mean(hits)), 4))
                    n_row.append(len(spikes))
                else:
                    spike_row.append(0.0)
                    hit_row.append(0.0)
                    n_row.append(0)
            spike_matrix.append(spike_row)
            hit_matrix.append(hit_row)
            n_matrix.append(n_row)

        return {
            "events": sorted_events,
            "months": month_names,
            "matrix": spike_matrix,      # avg abs spike per event×month
            "hit_matrix": hit_matrix,     # hit rate per event×month
            "n_matrix": n_matrix,         # sample count per cell
        }

    def get_heatmap_text(self, lookback_days: int = 365, symbol: Optional[str] = None) -> str:
        """Render the heatmap as a text table for terminal display."""
        data = self.get_heatmap_data(lookback_days=lookback_days, symbol=symbol)
        if not data["events"]:
            return "  No heatmap data available. Record events first."

        lines = []
        events = data["events"]
        months = data["months"]
        matrix = data["matrix"]
        n_matrix = data["n_matrix"]

        # Header
        header = f"  {'Event':<26}" + "".join(f"{m:>6}" for m in months)
        lines.append(header)
        lines.append("  " + "─" * (26 + 6 * 12))

        # Rows
        for i, title in enumerate(events):
            short_title = title[:25]
            row_str = f"  {short_title:<26}"
            for m in range(12):
                spike = matrix[i][m]
                n = n_matrix[i][m]
                if n == 0:
                    row_str += "     · "
                elif spike >= 50:
                    row_str += f"{spike:>5.0f}* "  # High spike marker
                elif spike >= 20:
                    row_str += f"{spike:>5.0f}  "  # Medium
                else:
                    row_str += f"{spike:>5.0f}  "  # Low
            lines.append(row_str)

        lines.append("")
        lines.append("  Legend: * = ≥50 pip avg  · = no data  values = avg abs spike (pips)")

        return "\n".join(lines)


# =============================================================================
# Seasonal Pattern Detector
# =============================================================================

@dataclass
class SeasonalPattern:
    """Detected seasonal pattern for a single event type."""
    event_title: str
    tier: int
    currency: str
    # Per-month statistics (index 0=Jan, 11=Dec)
    monthly_avg_spike: List[float] = field(default_factory=lambda: [0.0] * 12)
    monthly_hit_rate: List[float] = field(default_factory=lambda: [0.0] * 12)
    monthly_n: List[int] = field(default_factory=lambda: [0] * 12)
    # Peak/trough months
    peak_month: int = 0  # 0-indexed month with highest avg spike
    trough_month: int = 0  # 0-indexed month with lowest avg spike
    peak_avg_spike: float = 0.0
    trough_avg_spike: float = 0.0
    # Seasonal strength: how much does month-to-month variance matter?
    # 0.0 = no seasonality (all months equal), 1.0 = extreme seasonality
    seasonal_strength: float = 0.0
    # Statistical significance (p-value from Kruskal-Wallis or ANOVA)
    seasonality_p_value: float = 1.0
    # Whether this pattern is statistically significant
    is_significant: bool = False
    # Human-readable insight
    insight: str = ""
    # Recommended seasonal blackout adjustments
    seasonal_overrides: Dict[int, Dict[str, int]] = field(default_factory=dict)


class SeasonalPatternDetector:
    """Identifies which months each event type historically spikes hardest.

    Uses Kruskal-Wallis H-test (non-parametric) to test whether spike sizes
    differ significantly across months. If seasonality is detected, computes
    per-month blackout window adjustments.

    Usage:
        detector = SeasonalPatternDetector(correlation_engine)
        patterns = detector.detect_all()
        for p in patterns:
            if p.is_significant:
                print(f"{p.event_title}: peak in {MONTH_NAMES[p.peak_month]}, "
                      f"strength={p.seasonal_strength:.2f}")
    """

    # Minimum events per month to include in analysis
    MIN_EVENTS_PER_MONTH = 2
    # Minimum total events for seasonality test
    MIN_TOTAL_EVENTS = 8
    # Significance threshold
    SIGNIFICANCE_ALPHA = 0.10
    # How many months need data for the test
    MIN_MONTHS_WITH_DATA = 4

    def __init__(self, correlation_engine: NewsRegimeCorrelation):
        self.engine = correlation_engine

    def detect_all(
        self,
        lookback_days: int = 365,
        symbol: Optional[str] = None,
    ) -> List[SeasonalPattern]:
        """Detect seasonal patterns for all event types.

        Returns a list of SeasonalPattern objects, sorted by seasonal_strength
        descending (strongest seasonality first).
        """
        conn = self.engine._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?"]
        params: list = [cutoff]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)
        where_clause = " AND ".join(where_parts)

        # Get all event types
        event_types = conn.execute(
            f"SELECT DISTINCT event_title FROM event_outcomes WHERE {where_clause}",
            params,
        ).fetchall()

        patterns = []
        for ev_row in event_types:
            title = ev_row["event_title"]
            pattern = self._detect_single(title, lookback_days=lookback_days, symbol=symbol)
            if pattern is not None:
                patterns.append(pattern)

        # Sort by seasonal strength (strongest first)
        patterns.sort(key=lambda p: p.seasonal_strength, reverse=True)
        return patterns

    def _detect_single(
        self,
        event_title: str,
        lookback_days: int = 365,
        symbol: Optional[str] = None,
    ) -> Optional[SeasonalPattern]:
        """Detect seasonal pattern for a single event type."""
        conn = self.engine._get_conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        where_parts = ["event_time >= ?", "event_title = ?"]
        params: list = [cutoff, event_title]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)
        where_clause = " AND ".join(where_parts)

        rows = conn.execute(
            f"SELECT event_time, spike_pips, is_hit, tier, currency FROM event_outcomes WHERE {where_clause} ORDER BY event_time",
            params,
        ).fetchall()

        if len(rows) < self.MIN_TOTAL_EVENTS:
            return None

        # Group by month
        monthly_spikes: Dict[int, List[float]] = {m: [] for m in range(12)}
        monthly_hits: Dict[int, List[int]] = {m: [] for m in range(12)}

        tier = rows[0]["tier"]
        currency = rows[0]["currency"]

        for row in rows:
            try:
                ev_time = datetime.fromisoformat(row["event_time"])
                if ev_time.tzinfo is None:
                    ev_time = ev_time.replace(tzinfo=timezone.utc)
                month = ev_time.month - 1  # 0-indexed
            except (ValueError, TypeError):
                continue
            monthly_spikes[month].append(abs(row["spike_pips"]))
            monthly_hits[month].append(row["is_hit"])

        # Compute per-month stats
        monthly_avg = [0.0] * 12
        monthly_hit_rate = [0.0] * 12
        monthly_n = [0] * 12

        months_with_data = []
        for m in range(12):
            spikes = monthly_spikes[m]
            monthly_n[m] = len(spikes)
            if spikes:
                monthly_avg[m] = float(np.mean(spikes))
                monthly_hit_rate[m] = float(np.mean(monthly_hits[m]))
                if len(spikes) >= self.MIN_EVENTS_PER_MONTH:
                    months_with_data.append(m)

        if len(months_with_data) < self.MIN_MONTHS_WITH_DATA:
            return None

        # Kruskal-Wallis H-test for seasonality
        groups = [np.array(monthly_spikes[m]) for m in months_with_data if len(monthly_spikes[m]) >= self.MIN_EVENTS_PER_MONTH]
        if len(groups) < 2:
            return None

        h_stat, p_value = self._kruskal_wallis(groups)

        # Seasonal strength: coefficient of variation of monthly means
        valid_avgs = [monthly_avg[m] for m in months_with_data if monthly_avg[m] > 0]
        if len(valid_avgs) >= 2:
            arr = np.array(valid_avgs)
            mean_val = float(np.mean(arr))
            std_val = float(np.std(arr))
            # Normalize to [0, 1] range — CV clamped
            cv = min(std_val / max(mean_val, 0.01), 2.0) / 2.0
        else:
            cv = 0.0

        # Find peak and trough months
        peak_m = max(months_with_data, key=lambda m: monthly_avg[m])
        trough_m = min(months_with_data, key=lambda m: monthly_avg[m])

        # Generate insight
        is_significant = p_value < self.SIGNIFICANCE_ALPHA and cv > 0.15
        insight = self._generate_insight(
            event_title, monthly_avg, monthly_n, monthly_hit_rate,
            peak_m, trough_m, cv, p_value, is_significant,
        )

        # Generate seasonal blackout overrides
        overrides = {}
        if is_significant:
            overrides = self._compute_seasonal_overrides(
                monthly_avg, monthly_hit_rate, tier, months_with_data,
            )

        return SeasonalPattern(
            event_title=event_title,
            tier=tier,
            currency=currency,
            monthly_avg_spike=monthly_avg,
            monthly_hit_rate=monthly_hit_rate,
            monthly_n=monthly_n,
            peak_month=peak_m,
            trough_month=trough_m,
            peak_avg_spike=monthly_avg[peak_m],
            trough_avg_spike=monthly_avg[trough_m],
            seasonal_strength=round(cv, 4),
            seasonality_p_value=round(p_value, 6),
            is_significant=is_significant,
            insight=insight,
            seasonal_overrides=overrides,
        )

    @staticmethod
    def _kruskal_wallis(groups: List[np.ndarray]) -> Tuple[float, float]:
        """Non-parametric Kruskal-Wallis H-test for comparing groups.

        Tests whether samples from different months come from the same
        distribution (i.e., no seasonality).

        Returns:
            (H_statistic, p_value)
        """
        k = len(groups)
        if k < 2:
            return 0.0, 1.0

        # Combine all values and rank them
        all_vals = np.concatenate(groups)
        all_ranks = np.argsort(np.argsort(all_vals)) + 1  # 1-indexed ranks

        # Compute sum of ranks per group
        n_total = len(all_vals)
        rank_sums = []
        start = 0
        for g in groups:
            end = start + len(g)
            rank_sums.append(float(np.sum(all_ranks[start:end])))
            start = end

        # H statistic
        h = 0.0
        for i, (n_i, r_i) in enumerate(zip([len(g) for g in groups], rank_sums)):
            h += (r_i ** 2) / n_i
        h = (12.0 / (n_total * (n_total + 1))) * h - 3 * (n_total + 1)

        # p-value from chi-squared with k-1 degrees of freedom
        from scipy import stats as scipy_stats
        try:
            p_value = 1.0 - scipy_stats.chi2.cdf(max(h, 0), df=k - 1)
        except Exception:
            # Fallback: rough approximation
            p_value = 0.5 if h < 5.0 else 0.1 if h < 10.0 else 0.01

        return max(h, 0.0), p_value

    @staticmethod
    def _generate_insight(
        event_title: str,
        monthly_avg: List[float],
        monthly_n: List[int],
        monthly_hit_rate: List[float],
        peak_m: int,
        trough_m: int,
        cv: float,
        p_value: float,
        is_significant: bool,
    ) -> str:
        """Generate a human-readable insight about the seasonal pattern."""
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        if not is_significant:
            total_n = sum(monthly_n)
            return (
                f"{event_title}: No significant seasonality detected "
                f"(p={p_value:.3f}, strength={cv:.2f}). "
                f"{total_n} events across {sum(1 for n in monthly_n if n > 0)} months."
            )

        peak_name = month_names[peak_m]
        trough_name = month_names[trough_m]
        peak_spike = monthly_avg[peak_m]
        trough_spike = monthly_avg[trough_m]
        ratio = peak_spike / max(trough_spike, 0.1)

        # Find the top 3 hardest-hitting months
        valid_months = [(m, monthly_avg[m]) for m in range(12) if monthly_n[m] >= 2]
        valid_months.sort(key=lambda x: x[1], reverse=True)
        top3 = [(month_names[m], s) for m, s in valid_months[:3]]
        bottom3 = [(month_names[m], s) for m, s in valid_months[-3:]]

        parts = [
            f"{event_title}: STRONG seasonality (p={p_value:.3f}, strength={cv:.2f}).",
            f"  Peak: {peak_name} ({peak_spike:.1f}p avg spike)",
            f"  Trough: {trough_name} ({trough_spike:.1f}p avg spike)",
            f"  Peak/trough ratio: {ratio:.1f}x",
            f"  Top 3 months: {', '.join(f'{n}({s:.0f}p)' for n, s in top3)}",
            f"  Bottom 3 months: {', '.join(f'{n}({s:.0f}p)' for n, s in bottom3)}",
        ]

        return "\n".join(parts)

    def _compute_seasonal_overrides(
        self,
        monthly_avg: List[float],
        monthly_hit_rate: List[float],
        tier: int,
        months_with_data: List[int],
    ) -> Dict[int, Dict[str, int]]:
        """Compute per-month blackout window overrides.

        High-spike months get wider windows, low-spike months get narrower.
        """
        defaults = DEFAULT_BLACKOUT_WINDOWS.get(tier, DEFAULT_BLACKOUT_WINDOWS[2])
        default_before = defaults["before"]
        default_after = defaults["after"]

        # Compute overall average for normalization
        valid_avgs = [monthly_avg[m] for m in months_with_data if monthly_avg[m] > 0]
        if not valid_avgs:
            return {}
        overall_avg = float(np.mean(valid_avgs))

        overrides = {}
        for m in months_with_data:
            avg_spike = monthly_avg[m]
            hit_rate = monthly_hit_rate[m]
            n = self.engine._get_conn().execute(
                "SELECT COUNT(*) FROM event_outcomes WHERE event_time >= ?",
                ((datetime.now(timezone.utc) - timedelta(days=365)).isoformat(),),
            ).fetchone()[0]

            if n < self.MIN_EVENTS_PER_MONTH:
                continue

            # Ratio of this month's spike to overall average
            if overall_avg > 0:
                ratio = avg_spike / overall_avg
            else:
                ratio = 1.0

            # Apply ratio to default windows
            adj_before = int(round(default_before * max(min(ratio, MAX_GROW_FACTOR), MAX_SHRINK_FACTOR)))
            adj_after = int(round(default_after * max(min(ratio, MAX_GROW_FACTOR), MAX_SHRINK_FACTOR)))
            adj_before = max(adj_before, 5)
            adj_after = max(adj_after, 5)

            overrides[m] = {"before": adj_before, "after": adj_after}

        return overrides

    def get_detector_report(self, lookback_days: int = 365, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get a complete seasonal pattern report for display."""
        patterns = self.detect_all(lookback_days=lookback_days, symbol=symbol)
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        return {
            "n_events_analyzed": len(patterns),
            "n_significant": sum(1 for p in patterns if p.is_significant),
            "patterns": [
                {
                    "event_title": p.event_title,
                    "tier": p.tier,
                    "is_significant": p.is_significant,
                    "seasonal_strength": p.seasonal_strength,
                    "p_value": p.seasonality_p_value,
                    "peak_month": month_names[p.peak_month],
                    "trough_month": month_names[p.trough_month],
                    "peak_avg_spike": p.peak_avg_spike,
                    "trough_avg_spike": p.trough_avg_spike,
                    "monthly_avg_spike": {month_names[i]: round(v, 1) for i, v in enumerate(p.monthly_avg_spike) if p.monthly_n[i] >= 2},
                    "monthly_hit_rate": {month_names[i]: round(v, 4) for i, v in enumerate(p.monthly_hit_rate) if p.monthly_n[i] >= 2},
                    "monthly_n": {month_names[i]: v for i, v in enumerate(p.monthly_n) if v > 0},
                    "insight": p.insight,
                }
                for p in patterns
            ],
        }

    def get_seasonal_text_report(self, lookback_days: int = 365, symbol: Optional[str] = None) -> str:
        """Render seasonal patterns as a text table."""
        patterns = self.detect_all(lookback_days=lookback_days, symbol=symbol)
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        if not patterns:
            return "  No seasonal patterns detected. Need more event data."

        lines = []
        lines.append("  SEASONAL PATTERN ANALYSIS")
        lines.append("  " + "─" * 80)
        lines.append("")

        # Summary table
        lines.append(f"  {'Event':<26} {'Sig?':>4} {'Str':>5} {'Peak':>5} {'p-val':>7} {'Peak Spike':>10} {'Trough Spike':>13}")
        lines.append("  " + "─" * 80)

        for p in patterns:
            sig = "YES" if p.is_significant else "no"
            peak_name = month_names[p.peak_month]
            trough_name = month_names[p.trough_month]
            lines.append(
                f"  {p.event_title[:25]:<26} {sig:>4} {p.seasonal_strength:>5.2f} "
                f"{peak_name:>5} {p.seasonality_p_value:>7.3f} "
                f"{p.peak_avg_spike:>8.1f}p {p.trough_avg_spike:>11.1f}p"
            )

        lines.append("")

        # Detail for significant patterns
        significant = [p for p in patterns if p.is_significant]
        if significant:
            lines.append("  SIGNIFICANT SEASONAL PATTERNS:")
            lines.append("  " + "─" * 80)
            for p in significant:
                lines.append(f"  \n  {p.event_title} (tier {p.tier})")
                lines.append(f"  {p.insight}")
                # Monthly breakdown
                header = "    " + "".join(f"{m:>6}" for m in month_names)
                lines.append(header)
                spike_row = "    " + "".join(
                    f"{p.monthly_avg_spike[i]:>5.0f}p" if p.monthly_n[i] >= 2 else "     ·"
                    for i in range(12)
                )
                lines.append(spike_row)
                n_row = "    " + "".join(
                    f"  n={p.monthly_n[i]:<2}" if p.monthly_n[i] > 0 else "      ·"
                    for i in range(12)
                )
                lines.append(n_row)
        else:
            lines.append("  No statistically significant seasonal patterns detected.")

        return "\n".join(lines)
