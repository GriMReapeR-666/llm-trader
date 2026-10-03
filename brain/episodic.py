"""Episodic trace storage — on-disk artifacts for the L0-L3 memory hierarchy.

Per TRADING_MEMORY_SPEC.md, the memory hierarchy has four layers:
- L0: Raw conversation and execution trace (append-only)
- L1: Atomic facts (independently retrievable, confidence-scored)
- L2: Scenario documents (grouped by repeatable situations)
- L3: Core memory and personality (stable identity, risk policy)

This module manages the on-disk file representations (JSON/Markdown) that
complement the SQLite stores in ``intelligence/trading_memory.py``.  The
SQLite journal remains authoritative; these files are research aids and
retrieval artifacts.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default brain artifacts directory
DEFAULT_BRAIN_DIR = os.path.join(os.path.dirname(__file__), "artifacts")


@dataclass
class EpisodicTrace:
    """A single episodic memory trace.

    Each trace captures a complete trading episode: setup, decision,
    execution, outcome, and lesson.  Traces are the on-disk representation
    of L0-L1 memory items.
    """

    trace_id: str
    symbol: str
    strategy: str
    timeframe: str
    regime: str
    # Lifecycle
    created_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    # Setup
    setup_description: str = ""
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    side: Optional[str] = None  # "BUY" | "SELL"
    # Outcome
    exit_price: Optional[float] = None
    r_multiple: Optional[float] = None
    pnl_pips: Optional[float] = None
    outcome: Optional[str] = None  # "WIN" | "LOSS" | "BREAKEVEN" | "PENDING"
    close_reason: Optional[str] = None
    # Learning
    lesson: str = ""
    confidence: float = 0.5  # 0.0-1.0
    tags: List[str] = field(default_factory=list)
    # Evidence links (L0 references)
    source_ids: List[str] = field(default_factory=list)
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EpisodicTrace":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def is_closed(self) -> bool:
        return self.outcome is not None and self.outcome != "PENDING"

    @property
    def duration_hours(self) -> Optional[float]:
        if self.closed_at and self.created_at:
            return (self.closed_at - self.created_at) / 3600.0
        return None


class EpisodicStore:
    """On-disk store for episodic trading traces.

    Stores traces as individual JSON files organized by symbol and date.
    Directory structure::

        brain/artifacts/
            traces/
                EURUSD/
                    2026-09-01_abc123.json
                    2026-09-01_def456.json
                XAUUSD/
                    2026-09-02_ghi789.json
            scenarios/
                EURUSD/
                    london_breakout_high_adx.md
            lessons/
                2026-09_winning_patterns.json
    """

    def __init__(self, brain_dir: Optional[str] = None):
        self.brain_dir = Path(brain_dir or DEFAULT_BRAIN_DIR)
        self.traces_dir = self.brain_dir / "traces"
        self.scenarios_dir = self.brain_dir / "scenarios"
        self.lessons_dir = self.brain_dir / "lessons"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create directory structure if it doesn't exist."""
        for d in [self.traces_dir, self.scenarios_dir, self.lessons_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def save_trace(self, trace: EpisodicTrace) -> Path:
        """Persist an episodic trace to disk."""
        date_str = datetime.fromtimestamp(trace.created_at, tz=timezone.utc).strftime("%Y-%m-%d")
        symbol_dir = self.traces_dir / trace.symbol
        symbol_dir.mkdir(exist_ok=True)

        filename = f"{date_str}_{trace.trace_id}.json"
        path = symbol_dir / filename
        path.write_text(json.dumps(trace.to_dict(), indent=2, default=str), encoding="utf-8")
        logger.debug("Episodic trace saved: %s", path)
        return path

    def load_trace(self, symbol: str, trace_id: str) -> Optional[EpisodicTrace]:
        """Load a specific trace by symbol and ID."""
        symbol_dir = self.traces_dir / symbol
        if not symbol_dir.exists():
            return None

        for f in symbol_dir.glob(f"*_{trace_id}.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                return EpisodicTrace.from_dict(data)
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Corrupted trace %s: %s", f, exc)
        return None

    def list_traces(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 50,
    ) -> List[EpisodicTrace]:
        """List traces with optional filtering."""
        traces: List[EpisodicTrace] = []
        dirs = [self.traces_dir / symbol] if symbol else list(self.traces_dir.iterdir())

        for symbol_dir in dirs:
            if not symbol_dir.is_dir():
                continue
            for f in sorted(symbol_dir.glob("*.json"), reverse=True):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    trace = EpisodicTrace.from_dict(data)
                    if strategy and trace.strategy != strategy:
                        continue
                    if outcome and trace.outcome != outcome:
                        continue
                    traces.append(trace)
                    if len(traces) >= limit:
                        return traces
                except (json.JSONDecodeError, KeyError):
                    continue
        return traces

    def get_stats(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Compute aggregate statistics from episodic traces."""
        traces = self.list_traces(symbol=symbol, limit=1000)
        if not traces:
            return {"total": 0, "symbol": symbol}

        closed = [t for t in traces if t.is_closed]
        wins = [t for t in closed if t.outcome == "WIN"]
        losses = [t for t in closed if t.outcome == "LOSS"]
        r_multiples = [t.r_multiple for t in closed if t.r_multiple is not None]

        return {
            "total": len(traces),
            "closed": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(closed) if closed else 0.0,
            "avg_r": sum(r_multiples) / len(r_multiples) if r_multiples else 0.0,
            "best_r": max(r_multiples) if r_multiples else 0.0,
            "worst_r": min(r_multiples) if r_multiples else 0.0,
            "symbol": symbol,
        }

    def save_scenario(self, symbol: str, name: str, content: str) -> Path:
        """Save a scenario document (L2 memory artifact)."""
        scenario_dir = self.scenarios_dir / symbol
        scenario_dir.mkdir(exist_ok=True)
        path = scenario_dir / f"{name}.md"
        path.write_text(content, encoding="utf-8")
        logger.debug("Scenario saved: %s", path)
        return path

    def load_scenario(self, symbol: str, name: str) -> Optional[str]:
        """Load a scenario document."""
        path = self.scenarios_dir / symbol / f"{name}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_scenarios(self, symbol: Optional[str] = None) -> List[Dict[str, str]]:
        """List available scenario documents."""
        scenarios: List[Dict[str, str]] = []
        dirs = [self.scenarios_dir / symbol] if symbol else list(self.scenarios_dir.iterdir())

        for symbol_dir in dirs:
            if not symbol_dir.is_dir():
                continue
            for f in symbol_dir.glob("*.md"):
                scenarios.append({
                    "symbol": symbol_dir.name,
                    "name": f.stem,
                    "path": str(f),
                })
        return scenarios

    def save_lessons(self, lessons: List[Dict[str, Any]], period: str) -> Path:
        """Save distilled lessons for a time period."""
        path = self.lessons_dir / f"{period}.json"
        path.write_text(json.dumps(lessons, indent=2, default=str), encoding="utf-8")
        logger.debug("Lessons saved: %s (%d lessons)", path, len(lessons))
        return path

    def load_lessons(self, period: str) -> List[Dict[str, Any]]:
        """Load distilled lessons for a time period."""
        path = self.lessons_dir / f"{period}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                return []
        return []
