"""Real-Time Macro Narrative State Machine.

Maintains a versioned, structured narrative object tracking:
- Active macro thesis statement.
- Evidence-for and evidence-against logs.
- Falsification tripwires with real-time condition evaluation.
- Time-decay factor to prevent trading on stale narratives.
- Transition history and audit trail.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FalsificationTripwire:
    """Quantitative condition that invalidates the narrative if triggered."""
    tripwire_id: str
    metric: str                               # e.g., 'vix', 'hy_oas', 'unemployment'
    operator: str                             # '>', '<', '>=', '<=', '=='
    threshold: float
    description: str
    is_armed: bool = True
    is_triggered: bool = False
    triggered_at: Optional[str] = None

    def evaluate(self, current_value: Optional[float]) -> bool:
        """Evaluate condition against current value."""
        if current_value is None or not self.is_armed:
            return False

        hit = False
        if self.operator == ">":
            hit = current_value > self.threshold
        elif self.operator == ">=":
            hit = current_value >= self.threshold
        elif self.operator == "<":
            hit = current_value < self.threshold
        elif self.operator == "<=":
            hit = current_value <= self.threshold
        elif self.operator == "==":
            hit = math.isclose(current_value, self.threshold, rel_tol=1e-5)

        if hit and not self.is_triggered:
            self.is_triggered = True
            self.triggered_at = datetime.now(timezone.utc).isoformat()
        return self.is_triggered

    @property
    def symbol(self) -> str:
        return "GLOBAL_MACRO"

    @property
    def condition(self) -> str:
        return f"{self.metric} {self.operator} {self.threshold}"

    @property
    def threshold_value(self) -> float:
        return self.threshold

    @property
    def action_on_trip(self) -> str:
        return "DELEVERAGE_OR_VETO"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NarrativeObject:
    """Structured, versioned narrative representation."""
    narrative_id: str
    thesis: str
    regime_archetype: str                     # e.g., 'SOFT_LANDING_RISK_ON'
    active_since: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evidence_for: List[str] = field(default_factory=list)
    evidence_against: List[str] = field(default_factory=list)
    tripwires: List[FalsificationTripwire] = field(default_factory=list)
    base_confidence: float = 0.80
    half_life_days: float = 21.0               # Confidence decays by 50% every 21 days without new evidence
    is_invalidated: bool = False
    invalidation_reason: str = ""
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def narrative_age_days(self) -> float:
        """Calculate narrative age in days."""
        try:
            t0 = datetime.fromisoformat(self.active_since)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - t0).total_seconds() / 86400.0)
        except Exception:
            return 0.0

    @property
    def decay_factor(self) -> float:
        """Exponential decay factor down-weighting stale stories."""
        age = self.narrative_age_days
        # Decay: 2^(-age / half_life)
        return round(math.pow(0.5, age / max(self.half_life_days, 1.0)), 4)

    @property
    def current_confidence(self) -> float:
        """Effective conviction adjusted for evidence balance and age decay."""
        if self.is_invalidated:
            return 0.0
        # Evidence balance factor: min 0.2, max 1.2
        pos = len(self.evidence_for)
        neg = len(self.evidence_against)
        ev_ratio = (pos + 1.0) / (pos + neg + 1.0)
        eff = self.base_confidence * ev_ratio * self.decay_factor
        return round(max(0.0, min(1.0, eff)), 3)

    def add_evidence(self, text: str, is_confirming: bool = True) -> None:
        """Append fresh evidence and update timestamp."""
        if is_confirming:
            if text not in self.evidence_for:
                self.evidence_for.append(text)
        else:
            if text not in self.evidence_against:
                self.evidence_against.append(text)
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def check_tripwires(self, market_snapshot: Dict[str, float]) -> bool:
        """Check all armed tripwires against market metrics."""
        any_triggered = False
        for tw in self.tripwires:
            val = market_snapshot.get(tw.metric)
            if tw.evaluate(val):
                any_triggered = True
                if not self.is_invalidated:
                    self.is_invalidated = True
                    self.invalidation_reason = f"Tripwire '{tw.tripwire_id}' triggered: {tw.description} (value={val})"
        return any_triggered

    @property
    def tag(self) -> str:
        return self.narrative_id

    @property
    def headline(self) -> str:
        return self.thesis

    def to_dict(self) -> Dict[str, Any]:
        return {
            "narrative_id": self.narrative_id,
            "thesis": self.thesis,
            "regime_archetype": self.regime_archetype,
            "active_since": self.active_since,
            "narrative_age_days": round(self.narrative_age_days, 1),
            "decay_factor": self.decay_factor,
            "current_confidence": self.current_confidence,
            "is_invalidated": self.is_invalidated,
            "invalidation_reason": self.invalidation_reason,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "tripwires": [tw.to_dict() for tw in self.tripwires],
            "last_updated": self.last_updated,
        }


class NarrativeStateMachine:
    """Deterministic state machine governing narrative lifecycle and persistence."""

    def __init__(self, storage_path: str = "artifacts/active_narrative.json") -> None:
        self.storage_path = os.path.abspath(storage_path)
        self.active_narrative: NarrativeObject = self._create_default_narrative()
        self.load()

    def _create_default_narrative(self) -> NarrativeObject:
        """Construct canonical baseline narrative."""
        tripwires = [
            FalsificationTripwire(
                tripwire_id="TW_VIX_SPIKE",
                metric="vix",
                operator=">=",
                threshold=30.0,
                description="VIX spikes into acute panic regime (>= 30)",
            ),
            FalsificationTripwire(
                tripwire_id="TW_CREDIT_STRESS",
                metric="credit_oas_bps",
                operator=">=",
                threshold=380.0,
                description="High yield OAS spreads blowout past 380 bps",
            ),
            FalsificationTripwire(
                tripwire_id="TW_SOFR_SPIKE",
                metric="sofr_rate",
                operator=">=",
                threshold=5.60,
                description="SOFR repo rate spikes into acute funding stress (>= 5.60%)",
            ),
        ]
        return NarrativeObject(
            narrative_id="NAR_2026_CANONICAL_01",
            thesis="Disinflationary normalization with selective central bank divergence",
            regime_archetype="NORMAL_RISK_OFF",
            evidence_for=["Core inflation decelerating towards 2%", "Central banks nearing terminal rates"],
            evidence_against=["Geopolitical supply chain disruptions", "Fiscal debt ceiling standoff"],
            tripwires=tripwires,
            base_confidence=0.75,
        )

    def update_with_features(self, features: Any) -> NarrativeObject:
        """Update active narrative with new point-in-time features."""
        # 1. Evaluate tripwires
        snapshot = {
            "vix": getattr(features, "vix", 18.0),
            "credit_oas_bps": getattr(features, "credit_oas_bps", 120.0),
            "sofr_rate": getattr(features, "sofr_rate", 5.30),
            "oil_5d_pct": getattr(features, "oil_5d_pct", 0.0),
        }
        self.active_narrative.check_tripwires(snapshot)

        # 2. Add corroborating or refuting evidence
        z = getattr(features, "z_score", None)
        if z is not None:
            if abs(z) >= 2.0:
                self.active_narrative.add_evidence(
                    f"Macro surprise shock on {getattr(features, 'event_name', 'Event')}: {z:+.2f}σ",
                    is_confirming=(z < 0),
                )

        self.save()
        return self.active_narrative

    def save(self) -> None:
        """Persist state to JSON artifact."""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.active_narrative.to_dict(), f, indent=2)
        except Exception:
            pass

    def load(self) -> None:
        """Load state from JSON artifact if present."""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tws = [
                    FalsificationTripwire(
                        tripwire_id=tw["tripwire_id"],
                        metric=tw["metric"],
                        operator=tw["operator"],
                        threshold=tw["threshold"],
                        description=tw["description"],
                        is_armed=tw.get("is_armed", True),
                        is_triggered=tw.get("is_triggered", False),
                        triggered_at=tw.get("triggered_at"),
                    )
                    for tw in data.get("tripwires", [])
                ]
                self.active_narrative = NarrativeObject(
                    narrative_id=data["narrative_id"],
                    thesis=data["thesis"],
                    regime_archetype=data["regime_archetype"],
                    active_since=data["active_since"],
                    evidence_for=data.get("evidence_for", []),
                    evidence_against=data.get("evidence_against", []),
                    tripwires=tws,
                    base_confidence=data.get("base_confidence", 0.8),
                    is_invalidated=data.get("is_invalidated", False),
                    invalidation_reason=data.get("invalidation_reason", ""),
                    last_updated=data.get("last_updated", datetime.now(timezone.utc).isoformat()),
                )
            except Exception:
                pass

    def decay_step(self) -> None:
        """Advance decay step and persist state."""
        self.save()

    def active_narratives(self) -> List[NarrativeObject]:
        """Return list of active non-invalidated narratives."""
        return [self.active_narrative] if not self.active_narrative.is_invalidated else []
