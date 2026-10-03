"""Lag-Delay-Aware Analysis & Execution Engine.

Implements:
1. Three-Timestamp Architecture (T_event, T_ingest, T_proc)
2. Live Source Heartbeat Table with context/market-hours aware thresholds
3. Exponential Confidence Decay: C(dt) = exp(-dt / (half_life * vol_mult))
4. Delay Compensation Engine (Smith Predictor & Recalculation)
5. Multi-Lag Causal Structure Edge Delay Distributions
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_to_ts(iso_str: str) -> float:
    """Parse ISO 8601 string to Unix timestamp seconds."""
    if not iso_str:
        return time.time()
    try:
        clean = iso_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return time.time()


# ── 1. Three-Timestamp Data Envelope ────────────────────────────────────────

@dataclass
class TimestampedEvent:
    """Data payload enveloped with full delay/staleness tracking."""
    event_id: str
    source: str
    t_event: str           # ISO string: when event occurred at source
    t_ingest: str          # ISO string: when system received data
    t_proc: str = field(default_factory=utcnow_iso)  # ISO string: when processed into memory
    payload: Dict[str, Any] = field(default_factory=dict)
    data_type: str = "price_tick"  # price_tick, order_book, news, macro, cot

    @property
    def event_ts(self) -> float:
        return parse_iso_to_ts(self.t_event)

    @property
    def ingest_ts(self) -> float:
        return parse_iso_to_ts(self.t_ingest)

    @property
    def proc_ts(self) -> float:
        return parse_iso_to_ts(self.t_proc)

    @property
    def staleness_ms(self) -> float:
        """Total staleness D = T_proc - T_event in milliseconds."""
        return max(0.0, (self.proc_ts - self.event_ts) * 1000.0)

    @property
    def pipeline_latency_ms(self) -> float:
        """Internal processing latency = T_proc - T_ingest in milliseconds."""
        return max(0.0, (self.proc_ts - self.ingest_ts) * 1000.0)


# ── 2. Heartbeat Table & Source Monitoring ─────────────────────────────────

class HeartbeatTracker:
    """Live Heartbeat Table for all financial data sources."""

    # Default expected refresh frequency in seconds
    BASE_FREQUENCIES_SEC = {
        "price_tick": 1.0,
        "order_book": 0.5,
        "news": 60.0,
        "macro": 86400.0,        # 1 day
        "cot": 604800.0,         # 7 days
    }

    def __init__(self):
        self._sources: Dict[str, Dict[str, Any]] = {}

    def update_source(self, event: TimestampedEvent) -> Dict[str, Any]:
        """Record event arrival and update heartbeat metrics."""
        now = time.time()
        source_key = f"{event.source}:{event.data_type}"
        staleness_sec = event.staleness_ms / 1000.0
        base_freq = self.BASE_FREQUENCIES_SEC.get(event.data_type, 10.0)

        # Context-aware thresholding
        if staleness_sec <= base_freq * 2.0:
            status = "GREEN"
        elif staleness_sec <= base_freq * 5.0:
            status = "AMBER"
        else:
            status = "RED"

        entry = {
            "source": event.source,
            "data_type": event.data_type,
            "last_event_time": event.t_event,
            "last_ingest_time": event.t_ingest,
            "last_proc_time": event.t_proc,
            "staleness_ms": round(event.staleness_ms, 2),
            "pipeline_latency_ms": round(event.pipeline_latency_ms, 2),
            "status": status,
            "updated_at": utcnow_iso(),
        }
        self._sources[source_key] = entry
        return entry

    def get_status(self, source: str, data_type: str = "price_tick") -> Dict[str, Any]:
        key = f"{source}:{data_type}"
        return self._sources.get(key, {
            "source": source, "data_type": data_type,
            "status": "UNKNOWN", "staleness_ms": 999999.0,
        })

    def all_statuses(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._sources)


# ── 3. Confidence Decay Model ──────────────────────────────────────────────

class ConfidenceDecayEngine:
    """Exponential confidence decay calculator for delayed financial data."""

    # Half-life duration in milliseconds per data type
    HALF_LIVES_MS = {
        "price_tick": 200.0,        # 200ms
        "order_book": 500.0,        # 500ms
        "news": 60000.0,            # 1 minute
        "macro": 86400000.0,        # 1 day
        "cot": 604800000.0,         # 7 days
    }

    @classmethod
    def calculate_confidence(
        cls,
        staleness_ms: float,
        data_type: str = "price_tick",
        vix_level: float = 20.0,
    ) -> float:
        """Calculate confidence factor in [0.0, 1.0].

        Higher volatility (VIX) accelerates confidence decay.
        Formula: C(dt) = exp(-dt / (half_life * (1 + VIX/20)))
        """
        base_hl = cls.HALF_LIVES_MS.get(data_type, 1000.0)
        vol_multiplier = 1.0 + max(0.0, vix_level / 20.0)
        effective_half_life = base_hl * vol_multiplier

        if effective_half_life <= 0:
            return 0.0

        decay = math.exp(-max(0.0, staleness_ms) / effective_half_life)
        return max(0.0, min(1.0, decay))


# ── 4. Delay Compensation Engine (Smith Predictor) ─────────────────────────

@dataclass
class MarketStatePrediction:
    """Predicted market state compensated for network and processing lag."""
    asset: str
    observed_price: float
    predicted_price: float
    expected_delay_ms: float
    predicted_slippage: float
    order_type_recommendation: str  # LIMIT or MARKET
    confidence: float


class SlippageCalibrator:
    """Feedback loop between predicted and realized slippage.

    SmithPredictor uses ``atr_coeff * ATR * sqrt(delay)``.  Each fill that
    records both a prediction and a realized shortfall updates the coefficient
    toward the empirical median ratio so future predictions track broker truth.
    """

    DEFAULT_ATR_COEFF = 0.1
    MIN_ATR_COEFF = 0.01
    MAX_ATR_COEFF = 1.0

    def __init__(self, atr_coeff: float = DEFAULT_ATR_COEFF, max_observations: int = 500):
        self.atr_coeff = float(atr_coeff) if math.isfinite(float(atr_coeff)) else self.DEFAULT_ATR_COEFF
        self.atr_coeff = max(self.MIN_ATR_COEFF, min(self.MAX_ATR_COEFF, self.atr_coeff))
        self.max_observations = max(20, int(max_observations))
        self.observations: List[Dict[str, float]] = []

    def record(
        self,
        *,
        predicted_slippage: float,
        realized_slippage: float,
        atr: float,
        delay_sec: float,
        symbol: str = "",
    ) -> Dict[str, Any]:
        try:
            predicted = abs(float(predicted_slippage))
            realized = abs(float(realized_slippage))
            atr_v = float(atr)
            delay = max(0.01, float(delay_sec))
        except (TypeError, ValueError, OverflowError):
            return {"recorded": False, "reason": "non-numeric observation"}
        if not all(math.isfinite(v) for v in (predicted, realized, atr_v, delay)) or atr_v <= 0:
            return {"recorded": False, "reason": "invalid observation"}
        denom = atr_v * math.sqrt(delay)
        implied_coeff = realized / denom if denom > 0 else self.atr_coeff
        row = {
            "predicted": predicted,
            "realized": realized,
            "atr": atr_v,
            "delay_sec": delay,
            "implied_coeff": implied_coeff,
            "error": realized - predicted,
        }
        self.observations.append(row)
        if len(self.observations) > self.max_observations:
            self.observations = self.observations[-self.max_observations :]
        previous = self.atr_coeff
        self.calibrate()
        return {
            "recorded": True,
            "symbol": symbol,
            "previous_atr_coeff": previous,
            "atr_coeff": self.atr_coeff,
            "observation_count": len(self.observations),
            "last_error": row["error"],
        }

    def calibrate(self) -> float:
        if not self.observations:
            return self.atr_coeff
        coeffs = sorted(float(o["implied_coeff"]) for o in self.observations if math.isfinite(o["implied_coeff"]))
        if not coeffs:
            return self.atr_coeff
        mid = len(coeffs) // 2
        median = coeffs[mid] if len(coeffs) % 2 else 0.5 * (coeffs[mid - 1] + coeffs[mid])
        # EMA blend so a few outliers cannot snap the model
        blended = 0.7 * self.atr_coeff + 0.3 * median
        self.atr_coeff = max(self.MIN_ATR_COEFF, min(self.MAX_ATR_COEFF, blended))
        return self.atr_coeff

    def stats(self) -> Dict[str, Any]:
        if not self.observations:
            return {"atr_coeff": self.atr_coeff, "observation_count": 0, "mean_error": None, "mean_abs_error": None}
        errors = [float(o["error"]) for o in self.observations]
        abs_errors = [abs(e) for e in errors]
        return {
            "atr_coeff": self.atr_coeff,
            "observation_count": len(self.observations),
            "mean_error": sum(errors) / len(errors),
            "mean_abs_error": sum(abs_errors) / len(abs_errors),
        }

    def to_state(self) -> Dict[str, Any]:
        return {"atr_coeff": self.atr_coeff, "observations": self.observations[-200:]}

    def load_state(self, state: Optional[Dict[str, Any]]) -> None:
        if not isinstance(state, dict):
            return
        try:
            coeff = float(state.get("atr_coeff", self.atr_coeff))
            if math.isfinite(coeff):
                self.atr_coeff = max(self.MIN_ATR_COEFF, min(self.MAX_ATR_COEFF, coeff))
        except (TypeError, ValueError, OverflowError):
            pass
        rows = state.get("observations") or []
        if isinstance(rows, list):
            cleaned = []
            for row in rows[-self.max_observations :]:
                if isinstance(row, dict) and all(k in row for k in ("predicted", "realized", "atr", "delay_sec", "implied_coeff")):
                    cleaned.append({k: float(row[k]) for k in ("predicted", "realized", "atr", "delay_sec", "implied_coeff", "error") if k in row})
            self.observations = cleaned


class SmithPredictorEngine:
    """Smith Predictor model for short-term market state prediction under delay."""

    # Shared calibrator; orchestrator may replace with a journal-backed instance.
    calibrator = SlippageCalibrator()

    @classmethod
    def set_calibrator(cls, calibrator: SlippageCalibrator) -> None:
        cls.calibrator = calibrator

    @staticmethod
    def predict_state(
        asset: str,
        observed_price: float,
        staleness_ms: float,
        recent_returns_slope: float = 0.0,
        current_atr: float = 1.0,
        execution_latency_ms: float = 50.0,
        atr_coeff: Optional[float] = None,
    ) -> MarketStatePrediction:
        """Predict market price at execution horizon T_now + execution_latency."""
        total_delay_sec = (staleness_ms + execution_latency_ms) / 1000.0

        # Linear drift projection based on short-term return slope
        predicted_drift = recent_returns_slope * total_delay_sec * observed_price
        predicted_price = observed_price + predicted_drift

        coeff = atr_coeff
        if coeff is None:
            coeff = getattr(SmithPredictorEngine.calibrator, "atr_coeff", SlippageCalibrator.DEFAULT_ATR_COEFF)
        try:
            coeff = float(coeff)
        except (TypeError, ValueError, OverflowError):
            coeff = SlippageCalibrator.DEFAULT_ATR_COEFF
        if not math.isfinite(coeff) or coeff <= 0:
            coeff = SlippageCalibrator.DEFAULT_ATR_COEFF

        # Expected slippage scales with volatility (ATR), calibrated coeff, and delay
        predicted_slippage = (current_atr * coeff) * math.sqrt(max(0.01, total_delay_sec))

        # Recommend order type: LIMIT when low delay & low slip, MARKET when high urgency
        if total_delay_sec < 0.2 and predicted_slippage < (current_atr * 0.05):
            order_type = "LIMIT"
        else:
            order_type = "MARKET"

        confidence = ConfidenceDecayEngine.calculate_confidence(staleness_ms, "price_tick")

        return MarketStatePrediction(
            asset=asset,
            observed_price=observed_price,
            predicted_price=round(predicted_price, 5),
            expected_delay_ms=round(total_delay_sec * 1000.0, 2),
            predicted_slippage=round(predicted_slippage, 5),
            order_type_recommendation=order_type,
            confidence=round(confidence, 4),
        )


# ── 5. Multi-Lag Causal Edge Structure ────────────────────────────────────

@dataclass
class LagAwareCausalEdge:
    """Causal relationship enriched with explicit propagation delay distribution."""
    source_entity: str
    target_entity: str
    relation: str
    min_lag_seconds: float       # Earliest response time
    max_lag_seconds: float       # Peak effect propagation time
    decay_half_life_seconds: float
    historical_win_rate: float
    confidence: float

    def is_in_effect_window(self, event_age_seconds: float) -> bool:
        """True if the current event age falls within the causal propagation window."""
        return self.min_lag_seconds <= event_age_seconds <= self.max_lag_seconds

    def current_causal_impact(self, event_age_seconds: float) -> float:
        """Calculate dynamic impact strength [0, 1] based on event age."""
        if not self.is_in_effect_window(event_age_seconds):
            return 0.0
        # Peak at min_lag, decaying over time
        elapsed_past_min = event_age_seconds - self.min_lag_seconds
        decay = math.exp(-elapsed_past_min / max(1.0, self.decay_half_life_seconds))
        return self.confidence * self.historical_win_rate * decay
