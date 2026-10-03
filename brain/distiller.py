"""Lesson distiller — Extract lessons from trade outcomes.

Per TRADING_MEMORY_SPEC.md §1, L3 core memory contains "stable trading
personality, risk policy, strategy rules, lessons, skills/wiki".  The
distiller analyzes completed trades and extracts actionable lessons that
feed back into the L0-L3 memory hierarchy.

Design principles:
- Records rejected signals, not only executed trades.
- Seeks disconfirming examples.
- Treats small samples as uncertain.
- Retires memories when out-of-sample evidence contradicts them.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DistilledLesson:
    """A lesson extracted from trade outcomes or signal analysis."""

    lesson_id: str
    created_at: float = field(default_factory=time.time)
    # Source
    symbol: str = ""
    strategy: str = ""
    regime: str = ""
    # Lesson content
    category: str = ""  # "setup" | "risk" | "execution" | "regime" | "discipline"
    title: str = ""
    description: str = ""
    evidence: str = ""
    # Confidence and validity
    confidence: float = 0.5  # 0.0-1.0
    sample_size: int = 1
    status: str = "CANDIDATE"  # "CANDIDATE" | "APPROVED" | "RETIRED"
    supersedes: Optional[str] = None  # lesson_id this replaces
    # Tags for retrieval
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DistilledLesson":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class LessonDistiller:
    """Distills actionable lessons from trade outcomes.

    Analyzes closed trades and episodic traces to extract patterns:
    - Win/loss patterns by regime, session, setup type
    - Risk management lessons (stop placement, sizing)
    - Execution quality lessons (slippage, timing)
    - Discipline lessons (revenge trading, overtrading)

    Lessons are stored as versioned JSON files and integrated into the
    L3 core memory layer.
    """

    def __init__(self, brain_dir: Optional[str] = None):
        from brain.episodic import DEFAULT_BRAIN_DIR
        self.brain_dir = Path(brain_dir or DEFAULT_BRAIN_DIR)
        self.lessons_dir = self.brain_dir / "lessons"
        self.lessons_dir.mkdir(parents=True, exist_ok=True)
        self._lessons: List[DistilledLesson] = []

    def distill_from_trace(self, trace: Dict[str, Any]) -> Optional[DistilledLesson]:
        """Extract a lesson from a single completed trade trace.

        Args:
            trace: Dict with keys like symbol, strategy, regime, outcome,
                   r_multiple, setup_description, lesson, etc.

        Returns:
            A DistilledLesson if a lesson can be extracted, None otherwise.
        """
        outcome = trace.get("outcome")
        if not outcome or outcome == "PENDING":
            return None

        r_multiple = trace.get("r_multiple", 0.0)
        symbol = trace.get("symbol", "")
        strategy = trace.get("strategy", "")
        regime = trace.get("regime", "")
        setup = trace.get("setup_description", "")

        # Determine lesson category and content
        category = "setup"
        title = ""
        description = ""
        tags = [symbol, strategy, regime]

        if r_multiple is not None and r_multiple <= -2.0:
            category = "risk"
            title = f"Large loss ({r_multiple:.1f}R) on {symbol}"
            description = (
                f"Trade resulted in {r_multiple:.1f}R loss. "
                f"Setup: {setup[:200]}. Review stop placement and position sizing."
            )
            tags.append("large_loss")
        elif r_multiple is not None and r_multiple >= 3.0:
            title = f"Strong winner ({r_multiple:.1f}R) on {symbol}"
            description = (
                f"Trade achieved {r_multiple:.1f}R. "
                f"Setup: {setup[:200]}. Document what worked for replication."
            )
            tags.append("big_winner")
        elif outcome == "LOSS":
            title = f"Loss on {symbol} ({r_multiple:.1f}R)" if r_multiple else f"Loss on {symbol}"
            description = trace.get("lesson", f"Loss recorded. Setup: {setup[:150]}")
        elif outcome == "WIN":
            title = f"Win on {symbol} ({r_multiple:.1f}R)" if r_multiple else f"Win on {symbol}"
            description = trace.get("lesson", f"Win recorded. Setup: {setup[:150]}")
        else:
            return None

        lesson = DistilledLesson(
            lesson_id=f"{symbol}_{strategy}_{int(time.time())}",
            symbol=symbol,
            strategy=strategy,
            regime=regime,
            category=category,
            title=title,
            description=description,
            evidence=trace.get("close_reason", ""),
            confidence=min(0.7, abs(r_multiple) / 5.0) if r_multiple else 0.5,
            sample_size=1,
            tags=tags,
        )

        self._lessons.append(lesson)
        return lesson

    def distill_batch(self, traces: List[Dict[str, Any]]) -> List[DistilledLesson]:
        """Distill lessons from a batch of trade traces."""
        lessons = []
        for trace in traces:
            lesson = self.distill_from_trace(trace)
            if lesson:
                lessons.append(lesson)
        return lessons

    def aggregate_patterns(self, lessons: List[DistilledLesson]) -> Dict[str, Any]:
        """Aggregate lessons into high-level patterns.

        Returns a summary of recurring themes across distilled lessons.
        """
        if not lessons:
            return {"total": 0, "patterns": []}

        by_category: Dict[str, int] = {}
        by_symbol: Dict[str, int] = {}
        by_regime: Dict[str, int] = {}

        for lesson in lessons:
            by_category[lesson.category] = by_category.get(lesson.category, 0) + 1
            if lesson.symbol:
                by_symbol[lesson.symbol] = by_symbol.get(lesson.symbol, 0) + 1
            if lesson.regime:
                by_regime[lesson.regime] = by_regime.get(lesson.regime, 0) + 1

        # Find dominant patterns
        patterns = []
        if by_category:
            top_cat = max(by_category, key=by_category.get)
            patterns.append(f"Most lessons in category '{top_cat}' ({by_category[top_cat]} lessons)")
        if by_regime:
            top_regime = max(by_regime, key=by_regime.get)
            patterns.append(f"Most activity in '{top_regime}' regime ({by_regime[top_regime]} lessons)")

        return {
            "total": len(lessons),
            "by_category": by_category,
            "by_symbol": by_symbol,
            "by_regime": by_regime,
            "patterns": patterns,
        }

    def save(self, period: Optional[str] = None) -> Path:
        """Save accumulated lessons to disk.

        Args:
            period: Time period key (e.g., "2026-09"). Defaults to current month.
        """
        if not period:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        path = self.lessons_dir / f"{period}.json"

        # Load existing lessons for the period
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                existing = []

        # Append new lessons
        new_dicts = [l.to_dict() for l in self._lessons]
        all_lessons = existing + new_dicts

        path.write_text(json.dumps(all_lessons, indent=2, default=str), encoding="utf-8")
        logger.info("Lessons saved: %s (%d new, %d total)", path, len(new_dicts), len(all_lessons))

        self._lessons.clear()
        return path

    def load(self, period: str) -> List[DistilledLesson]:
        """Load lessons for a given period."""
        path = self.lessons_dir / f"{period}.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [DistilledLesson.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError):
            return []

    def get_recent(self, days: int = 30) -> List[DistilledLesson]:
        """Get lessons from the last N days."""
        cutoff = time.time() - (days * 86400)
        all_lessons: List[DistilledLesson] = []

        # Scan all lesson files
        for f in self.lessons_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                for d in data:
                    lesson = DistilledLesson.from_dict(d)
                    if lesson.created_at >= cutoff:
                        all_lessons.append(lesson)
            except (json.JSONDecodeError, KeyError):
                continue

        return sorted(all_lessons, key=lambda x: x.created_at, reverse=True)
