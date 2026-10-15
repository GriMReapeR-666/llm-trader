"""News Execution Engine (Blind Spot #10).

Turns a released calendar event into an execution-plan dict the
LLMOrchestrator stores in toon["news_execution_plan"]. Composition-only:
reuses core.news_trading primitives (detect_news_phase, classify_event_tier)
instead of reimplementing timing logic. Observational only — it proposes a
plan and never places orders; execution_authority stays downstream.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.news_trading import classify_event_tier, detect_news_phase

logger = logging.getLogger(__name__)

# Map detect_news_phase output onto the gate's phase vocabulary.
_PHASE_MAP = {
    "unknown": ("CLEAR", True),
    "normal": ("CLEAR", True),
    "pre_news_distant": ("PRE_NEWS", True),
    "pre_news_imminent": ("PRE_NEWS", True),          # warning, not hard blackout
    "pre_news_critical": ("SPIKE_BLACKOUT", False),   # hard blackout
    "spike": ("SPIKE_BLACKOUT", False),
    "spike_exhaustion": ("SPIKE_BLACKOUT", False),
    "post_news_entry": ("POST_NEWS_ENTRY", True),
    "trend_continuation": ("TREND_CONTINUATION", True),
}


@dataclass
class NewsExecutionPlan:
    """Execution plan for one released event (observational, no orders)."""
    event_name: str
    event_time: str
    tier: int
    phase: str
    allowed: bool
    blackout_active: bool
    minutes_to_event: Optional[float] = None
    minutes_since_event: Optional[float] = None
    surprise_pct: Optional[float] = None
    surprise_direction: str = "flat"
    guidance: str = ""
    reasoning: str = ""
    market_context: Dict[str, Any] = field(default_factory=dict)
    account_balance: float = 0.0
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


class NewsExecutionEngine:
    """Build per-event execution plans from released calendar events."""

    def create_execution_plan(
        self,
        event_name: str,
        event_time: Any,
        actual: float,
        consensus: float,
        previous: float = 0.0,
        market_context: Optional[Dict[str, Any]] = None,
        account_balance: float = 0.0,
    ) -> NewsExecutionPlan:
        now = datetime.now(timezone.utc)
        ev_dt = _parse_dt(event_time) or now
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        ev_iso = ev_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        tier = classify_event_tier(event_name or "")
        timing = detect_news_phase(ev_iso, now_iso)
        raw_phase = timing.get("phase", "unknown")
        phase, allowed = _PHASE_MAP.get(raw_phase, ("CLEAR", True))
        blackout_active = not allowed

        minutes_since = timing.get("minutes_since_event")
        if minutes_since is None:
            minutes_since = timing.get("minutes_to_event")
        minutes_to = timing.get("minutes_to_event")
        minutes_since_val = timing.get("minutes_since_event")

        surprise_pct: Optional[float] = None
        surprise_direction = "flat"
        try:
            if consensus:
                surprise_pct = ((float(actual) - float(consensus))
                                / abs(float(consensus))) * 100.0
                if surprise_pct > 0:
                    surprise_direction = "above_consensus"
                elif surprise_pct < 0:
                    surprise_direction = "below_consensus"
        except (TypeError, ValueError):
            surprise_pct = None

        reasoning = (
            f"{event_name or 'unknown event'} tier={tier} phase={phase} "
            f"({raw_phase}); surprise={surprise_pct if surprise_pct is not None else 'n/a'}%"
        )

        return NewsExecutionPlan(
            event_name=event_name or "",
            event_time=ev_iso,
            tier=tier,
            phase=phase,
            allowed=allowed,
            blackout_active=blackout_active,
            minutes_to_event=minutes_to,
            minutes_since_event=minutes_since_val if minutes_since_val is not None else minutes_since,
            surprise_pct=surprise_pct,
            surprise_direction=surprise_direction,
            guidance=timing.get("guidance", ""),
            reasoning=reasoning,
            market_context=market_context or {},
            account_balance=account_balance,
            generated_at=now_iso,
        )
