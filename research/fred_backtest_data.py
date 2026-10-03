"""FRED Historical Data Provider for Backtest — actual vs forecast per release.

Provides the data backbone for wiring real FRED indicator time series into
the news gate backtest, enabling:
  - Per-event surprise Z-scores: surprise = (actual - consensus) / rolling_std
  - Rolling ESI: % of recent releases beating consensus
  - Event attribution: which releases actually moved the market vs which were duds

Usage:
    from research.fred_backtest_data import FredBacktestData
    fb = FredBacktestData(api_manager)
    releases = fb.fetch_release_history("PAYEMS", months_back=24)
    surprise = fb.compute_surprise_zscore("PAYEMS", actual=250.0, forecast=180.0)
    esi = fb.compute_rolling_esi(months_back=6)
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── FRED Series ID → Event Mapping ─────────────────────────────────────────

# NFP leading indicators: FRED series IDs
NFP_SERIES = {
    "ADPAYEMS":  {"event": "ADP_EMPLOYMENT", "description": "ADP Employment Change", "unit": "thousands"},
    "ICSA":      {"event": "INITIAL_CLAIMS", "description": "Initial Jobless Claims", "unit": "thousands"},
    "MANEMP":    {"event": "ISM_EMPLOYMENT", "description": "ISM Employment PMI proxy", "unit": "index"},
    "JTSJOL":    {"event": "JOLTS_OPENINGS", "description": "JOLTS Job Openings", "unit": "millions"},
    "PAYEMS":    {"event": "NFP", "description": "Non-Farm Payrolls", "unit": "thousands"},
}

# CPI leading indicators: FRED series IDs
CPI_SERIES = {
    "PPIACO":      {"event": "PPI", "description": "Producer Price Index", "unit": "index"},
    "IR":          {"event": "IMPORT_PRICES", "description": "Import Price Index", "unit": "index"},
    "NAPM":        {"event": "ISM_PRICES", "description": "ISM Prices Paid PMI proxy", "unit": "index"},
    "CES0500000003": {"event": "WAGES", "description": "Average Hourly Earnings", "unit": "index"},
}

# Key macro releases for ESI
ESI_SERIES = {
    "PAYEMS":    {"event": "NFP", "consensus_note": "BLS survey median"},
    "CPIAUCSL":  {"event": "CPI", "consensus_note": "Reuters poll median"},
    "FEDFUNDS":  {"event": "FOMC", "consensus_note": "Fed funds target upper"},
    "UNRATE":    {"event": "UNEMPLOYMENT", "consensus_note": "BLS survey median"},
    "INDPRO":    {"event": "INDUSTRIAL_PRODUCTION", "consensus_note": "Fed survey median"},
    "UMCSENT":   {"event": "CONSUMER_SENTIMENT", "consensus_note": "Reuters poll median"},
}

# Typical release times (UTC) — used to align M15 bars
RELEASE_TIMES_UTC = {
    "NFP":             (13, 30),  # 8:30 ET
    "CPI":             (13, 30),  # 8:30 ET
    "ADP_EMPLOYMENT":  (13, 15),  # 8:15 ET
    "INITIAL_CLAIMS":  (13, 30),  # 8:30 ET
    "ISM_EMPLOYMENT":  (14, 0),   # 9:00 ET
    "ISM_PRICES":      (14, 0),   # 9:00 ET
    "JOLTS_OPENINGS":  (14, 0),   # 9:00 ET
    "PPI":             (13, 30),  # 8:30 ET
    "IMPORT_PRICES":   (13, 30),  # 8:30 ET
    "WAGES":           (13, 30),  # 8:30 ET
}


@dataclass
class ReleaseRecord:
    """A single FRED release with actual vs consensus."""
    series_id: str
    event_name: str
    release_date: datetime
    actual: Optional[float] = None
    consensus: Optional[float] = None
    previous: Optional[float] = None
    surprise_zscore: float = 0.0  # (actual - consensus) / rolling_std
    surprise_direction: str = "IN_LINE"  # STRONG_BEAT, BEAT, IN_LINE, MISS, STRONG_MISS
    unit: str = ""


@dataclass
class ESISnapshot:
    """Rolling Economic Surprise Index."""
    esi_score: float = 50.0  # 0-100 scale
    n_releases: int = 0
    n_beats: int = 0
    n_misses: int = 0
    beat_rate: float = 0.5
    recent_surprises: List[float] = field(default_factory=list)
    bias: str = "NEUTRAL"  # HAWKISH, DOVISH, NEUTRAL
    timestamp: Optional[datetime] = None


class FredBacktestData:
    """Fetches FRED historical data for backtest event attribution.

    When FRED API is available, fetches actual release history with
    real values. When unavailable, generates realistic synthetic data
    based on historical distribution parameters.
    """

    def __init__(self, api_manager=None, seed: int = 42):
        self.api_manager = api_manager
        self._rng = np.random.RandomState(seed)
        self._cache: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_ts: float = 0.0
        self._CACHE_TTL: float = 3600.0  # 1 hour

    def _has_fred_api(self) -> bool:
        return (
            self.api_manager is not None
            and hasattr(self.api_manager, "fred")
            and self.api_manager.fred is not None
        )

    # ── Historical Release Data ──────────────────────────────────────────

    def fetch_release_history(
        self,
        series_id: str,
        months_back: int = 24,
    ) -> List[ReleaseRecord]:
        """Fetch historical release data for a FRED series.

        Returns list of ReleaseRecord with actual values at each release date.
        Falls back to synthetic data if FRED API unavailable.
        """
        cache_key = f"{series_id}:{months_back}"
        now = time.time()
        if cache_key in self._cache and (now - self._cache_ts) < self._CACHE_TTL:
            return self._cache[cache_key]

        records = []
        if self._has_fred_api():
            records = self._fetch_from_fred(series_id, months_back)
        else:
            records = self._generate_synthetic_releases(series_id, months_back)

        self._cache[cache_key] = records
        self._cache_ts = now
        return records

    def _fetch_from_fred(self, series_id: str, months_back: int) -> List[ReleaseRecord]:
        """Fetch actual release history from FRED API."""
        records = []
        try:
            series_info = NFP_SERIES.get(series_id) or CPI_SERIES.get(series_id) or ESI_SERIES.get(series_id, {})
            event_name = series_info.get("event", series_id)
            unit = series_info.get("unit", "")

            end_date = datetime.now()
            start_date = end_date - timedelta(days=months_back * 30)

            series = self.api_manager.fred.get_series(
                series_id,
                observation_start=start_date.strftime("%Y-%m-%d"),
                observation_end=end_date.strftime("%Y-%m-%d"),
            )

            if series is None or len(series) == 0:
                return self._generate_synthetic_releases(series_id, months_back)

            # Convert to release records — FRED gives monthly observations
            prev_value = None
            for date_idx, value in series.items():
                if value is None or (isinstance(value, float) and np.isnan(value)):
                    continue

                actual = float(value)
                dt = datetime.combine(date_idx, datetime.min.time()).replace(tzinfo=timezone.utc)

                # Consensus proxy: use lagged value (previous month)
                consensus = prev_value if prev_value is not None else actual

                records.append(ReleaseRecord(
                    series_id=series_id,
                    event_name=event_name,
                    release_date=dt,
                    actual=actual,
                    consensus=consensus,
                    previous=prev_value,
                    unit=unit,
                ))
                prev_value = actual

        except Exception as exc:
            logger.warning("FRED fetch failed for %s: %s — using synthetic", series_id, exc)
            return self._generate_synthetic_releases(series_id, months_back)

        return records

    def _generate_synthetic_releases(self, series_id: str, months_back: int) -> List[ReleaseRecord]:
        """Generate realistic synthetic release data when FRED is unavailable."""
        series_info = NFP_SERIES.get(series_id) or CPI_SERIES.get(series_id) or ESI_SERIES.get(series_id, {})
        event_name = series_info.get("event", series_id)
        unit = series_info.get("unit", "")

        # Distribution parameters by series
        params = {
            "PAYEMS":      {"mean": 180.0, "std": 70.0, "trend": 0.5},
            "ADPAYEMS":    {"mean": 150.0, "std": 80.0, "trend": 0.3},
            "ICSA":        {"mean": 215.0, "std": 20.0, "trend": -0.2},
            "MANEMP":      {"mean": 50.0,  "std": 3.0,  "trend": 0.1},
            "JTSJOL":      {"mean": 8.5,   "std": 0.8,  "trend": 0.02},
            "PPIACO":      {"mean": 250.0, "std": 15.0, "trend": 0.3},
            "IR":          {"mean": 120.0, "std": 8.0,  "trend": 0.2},
            "NAPM":        {"mean": 50.0,  "std": 4.0,  "trend": 0.1},
            "CES0500000003": {"mean": 115.0, "std": 3.0, "trend": 0.15},
        }
        p = params.get(series_id, {"mean": 100.0, "std": 10.0, "trend": 0.0})

        records = []
        now = datetime.now(timezone.utc)
        value = p["mean"]

        for m in range(months_back):
            # Monthly release (roughly)
            release_date = now - timedelta(days=30 * (months_back - m))
            # Add release time
            hour, minute = RELEASE_TIMES_UTC.get(event_name, (13, 30))
            release_date = release_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # Actual value with noise and trend
            value += p["trend"] + self._rng.normal(0, p["std"] * 0.3)
            actual = value

            # Consensus = previous value + small expectation adjustment
            consensus = value - p["trend"] * 0.5 + self._rng.normal(0, p["std"] * 0.1)
            previous = value - p["trend"] + self._rng.normal(0, p["std"] * 0.2)

            records.append(ReleaseRecord(
                series_id=series_id,
                event_name=event_name,
                release_date=release_date,
                actual=round(actual, 2),
                consensus=round(consensus, 2),
                previous=round(previous, 2),
                unit=unit,
            ))

        return records

    # ── Surprise Scoring ─────────────────────────────────────────────────

    def compute_surprise_zscore(
        self,
        series_id: str,
        actual: float,
        forecast: float,
        lookback_releases: int = 24,
    ) -> float:
        """Compute surprise Z-score: (actual - forecast) / rolling_std.

        A Z-score > 2.0 means a very positive surprise (hawkish).
        A Z-score < -2.0 means a very negative surprise (dovish).
        """
        history = self.fetch_release_history(series_id, months_back=lookback_releases * 2)
        if len(history) < 6:
            # Not enough history — use default std
            all_values = [r.actual for r in history if r.actual is not None]
            if not all_values:
                return 0.0
            std = float(np.std(all_values)) if len(all_values) > 1 else 1.0
        else:
            # Use rolling std of actual values
            all_values = [r.actual for r in history if r.actual is not None]
            std = float(np.std(all_values[-lookback_releases:])) if len(all_values) > 1 else 1.0

        if std <= 0:
            std = 1.0

        surprise = (actual - forecast) / std
        return round(surprise, 4)

    def classify_surprise(self, zscore: float) -> str:
        """Classify surprise into direction categories."""
        if zscore > 2.0:
            return "STRONG_BEAT"
        elif zscore > 0.5:
            return "BEAT"
        elif zscore > -0.5:
            return "IN_LINE"
        elif zscore > -2.0:
            return "MISS"
        else:
            return "STRONG_MISS"

    # ── ESI Calculation ──────────────────────────────────────────────────

    def compute_rolling_esi(
        self,
        months_back: int = 6,
        series_ids: Optional[List[str]] = None,
    ) -> ESISnapshot:
        """Compute rolling Economic Surprise Index from FRED releases.

        ESI = % of recent releases beating consensus, weighted by recency.
        Score 0-100: >60 = hawkish, <40 = dovish.
        """
        if series_ids is None:
            series_ids = list(ESI_SERIES.keys())

        all_surprises = []
        n_beats = 0
        n_misses = 0
        n_total = 0

        for sid in series_ids:
            releases = self.fetch_release_history(sid, months_back=months_back + 3)
            if not releases:
                continue

            for rec in releases:
                if rec.actual is None or rec.consensus is None:
                    continue
                if rec.actual == rec.consensus:
                    continue

                # Compute surprise for this release
                zscore = self.compute_surprise_zscore(
                    sid, rec.actual, rec.consensus, lookback_releases=24,
                )
                direction = self.classify_surprise(zscore)
                rec.surprise_zscore = zscore
                rec.surprise_direction = direction

                all_surprises.append(zscore)
                n_total += 1
                if zscore > 0:
                    n_beats += 1
                elif zscore < 0:
                    n_misses += 1

        if n_total == 0:
            return ESISnapshot(esi_score=50.0, bias="NEUTRAL", timestamp=datetime.now(timezone.utc))

        # Exponential recency weighting
        n = len(all_surprises)
        weights = np.array([math.exp(-0.05 * (n - 1 - i)) for i in range(n)])
        w_sum = float(np.sum(weights))

        # Weighted average surprise, normalized to 0-100
        weighted_surprise = float(np.sum(np.array(all_surprises) * weights)) / w_sum
        # Map Z-score range [-3, +3] → [0, 100]
        esi_score = max(0.0, min(100.0, 50.0 + weighted_surprise * 16.67))

        # Bias classification
        if esi_score > 60:
            bias = "HAWKISH"
        elif esi_score < 40:
            bias = "DOVISH"
        else:
            bias = "NEUTRAL"

        beat_rate = n_beats / n_total if n_total > 0 else 0.5

        return ESISnapshot(
            esi_score=round(esi_score, 1),
            n_releases=n_total,
            n_beats=n_beats,
            n_misses=n_misses,
            beat_rate=round(beat_rate, 4),
            recent_surprises=all_surprises[-20:],
            bias=bias,
            timestamp=datetime.now(timezone.utc),
        )

    # ── Event Enhancement for Backtest ───────────────────────────────────

    def enhance_events_with_fred(
        self,
        events: List[Dict[str, Any]],
        days_back: int = 365,
    ) -> List[Dict[str, Any]]:
        """Enhance events with full FRED historical data + surprise scores.

        For each event:
          - Adds actual/consensus from FRED if available
          - Computes surprise Z-score
          - Adds surprise direction classification
          - Attaches ESI context at event time
        """
        # Fetch all FRED histories (NFP + CPI leading indicators + ESI key releases)
        all_series = {}
        for sid in list(NFP_SERIES.keys()) + list(CPI_SERIES.keys()) + list(ESI_SERIES.keys()):
            all_series[sid] = self.fetch_release_history(sid, months_back=days_back // 30 + 6)

        # Build lookup: event_name → list of ReleaseRecord
        event_releases: Dict[str, List[ReleaseRecord]] = {}
        for sid, records in all_series.items():
            for rec in records:
                if rec.event_name not in event_releases:
                    event_releases[rec.event_name] = []
                event_releases[rec.event_name].append(rec)

        # Sort each event's releases by date
        for ev_name in event_releases:
            event_releases[ev_name].sort(key=lambda r: r.release_date)

        # Enhance events
        enhanced = []
        for ev in events:
            ev_title = ev.get("title", "").upper()
            ev_time = ev.get("time")
            if ev_time is None:
                enhanced.append(ev)
                continue
            if isinstance(ev_time, str):
                try:
                    ev_time = datetime.fromisoformat(ev_time.replace("Z", "+00:00"))
                except ValueError:
                    enhanced.append(ev)
                    continue
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=timezone.utc)

            ev_copy = dict(ev)

            # Match to FRED releases
            matched_records = self._match_event_to_releases(ev_title, ev_time, event_releases)

            if matched_records:
                # Use the closest release
                closest = matched_records[0]
                ev_copy["fred_actual"] = closest.actual
                ev_copy["fred_consensus"] = closest.consensus
                ev_copy["fred_previous"] = closest.previous
                ev_copy["fred_surprise_zscore"] = closest.surprise_zscore
                ev_copy["fred_surprise_direction"] = closest.surprise_direction
                ev_copy["fred_series_id"] = closest.series_id

                # If no actual forecast in event, use FRED consensus
                if "forecast" not in ev_copy or ev_copy.get("forecast") is None:
                    ev_copy["forecast"] = closest.consensus
                if "actual" not in ev_copy or ev_copy.get("actual") is None:
                    ev_copy["actual"] = closest.actual

            enhanced.append(ev_copy)

        return enhanced

    def _match_event_to_releases(
        self,
        event_title: str,
        event_time: datetime,
        event_releases: Dict[str, List[ReleaseRecord]],
    ) -> List[ReleaseRecord]:
        """Match an event to its closest FRED release record."""
        # Map event titles to FRED event names
        title_to_event = {
            "NON-FARM PAYROLLS": "NFP",
            "NFP": "NFP",
            "ADP EMPLOYMENT CHANGE": "ADP_EMPLOYMENT",
            "ADP EMPLOYMENT": "ADP_EMPLOYMENT",
            "INITIAL JOBLESS CLAIMS": "INITIAL_CLAIMS",
            "CONSUMER PRICE INDEX": "CPI",
            "CPI": "CPI",
            "ISM MANUFACTURING PMI": "ISM_EMPLOYMENT",
            "ISM MANUFACTURING": "ISM_EMPLOYMENT",
            "ISM SERVICES PMI": "ISM_EMPLOYMENT",
            "JOLTS JOB OPENINGS": "JOLTS_OPENINGS",
            "JOLTS": "JOLTS_OPENINGS",
            "PRODUCER PRICE INDEX": "PPI",
            "PPI": "PPI",
            "RETAIL SALES": "RETAIL_SALES",
            "UNEMPLOYMENT RATE": "UNEMPLOYMENT",
            "CONSUMER CONFIDENCE": "CONSUMER_SENTIMENT",
            "GROSS DOMESTIC PRODUCT": "GDP",
            "FOMC STATEMENT": "FOMC",
        }

        event_name = title_to_event.get(event_title, event_title)
        candidates = event_releases.get(event_name, [])

        if not candidates:
            return []

        # Find the closest release by time
        best = None
        best_delta = timedelta(days=365)
        for rec in candidates:
            delta = abs(rec.release_date - event_time)
            if delta < best_delta:
                best_delta = delta
                best = rec

        if best is None or best_delta > timedelta(days=45):
            return []

        return [best]

    # ── Backtest Analytics ───────────────────────────────────────────────

    def compute_event_attribution(
        self,
        event_reactions: List[Dict[str, Any]],
        days_back: int = 365,
    ) -> Dict[str, Any]:
        """Compute per-event surprise → spike attribution.

        For each event that has FRED data:
          - Correlates surprise Z-score with spike size
          - Measures directional accuracy (positive surprise → bullish spike?)
          - Reports how much of the spike variance is explained by surprise
        """
        # Enhance reactions with FRED data
        enhanced = self.enhance_events_with_fred(
            [{"title": r.get("title", ""), "time": r.get("time"), **r} for r in event_reactions],
            days_back=days_back,
        )

        # Filter to events with both surprise and spike data
        attributed = []
        for ev in enhanced:
            zscore = ev.get("fred_surprise_zscore")
            spike = ev.get("max_spike_pips", ev.get("spike_15m_pips", 0))
            direction = ev.get("direction", "NEUTRAL")
            if zscore is not None and spike != 0:
                attributed.append({
                    "title": ev.get("title", ""),
                    "surprise_zscore": zscore,
                    "surprise_direction": ev.get("fred_surprise_direction", "IN_LINE"),
                    "spike_pips": abs(spike),
                    "spike_direction": direction,
                    "correlation_match": self._direction_match(zscore, direction),
                })

        if not attributed:
            return {"n_attributed": 0, "directional_accuracy": 0.0, "surprise_spike_correlation": 0.0}

        # Compute directional accuracy
        matches = sum(1 for a in attributed if a["correlation_match"])
        directional_accuracy = matches / len(attributed)

        # Compute surprise-spike correlation
        zscores = np.array([a["surprise_zscore"] for a in attributed])
        spikes = np.array([a["spike_pips"] for a in attributed])
        if len(zscores) >= 3:
            corr = float(np.corrcoef(zscores, spikes)[0, 1])
            if np.isnan(corr):
                corr = 0.0
        else:
            corr = 0.0

        # Surprise accuracy (did the direction match?)
        surprise_directions = [a["surprise_direction"] for a in attributed]
        beat_count = surprise_directions.count("STRONG_BEAT") + surprise_directions.count("BEAT")
        miss_count = surprise_directions.count("STRONG_MISS") + surprise_directions.count("MISS")

        return {
            "n_attributed": len(attributed),
            "directional_accuracy": round(directional_accuracy, 4),
            "surprise_spike_correlation": round(corr, 4),
            "n_beats": beat_count,
            "n_misses": miss_count,
            "n_in_line": len(attributed) - beat_count - miss_count,
            "avg_surprise_zscore": round(float(np.mean(zscores)), 4),
            "avg_spike_pips": round(float(np.mean(spikes)), 2),
            "details": attributed[:20],  # Top 20 for display
        }

    @staticmethod
    def _direction_match(zscore: float, price_direction: str) -> bool:
        """Check if surprise direction matches price direction."""
        if zscore > 0.5 and price_direction == "BULLISH":
            return True
        if zscore < -0.5 and price_direction == "BEARISH":
            return True
        if -0.5 <= zscore <= 0.5:
            return True  # In-line events don't predict direction
        return False
