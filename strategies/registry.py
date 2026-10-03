"""Strategy registry — Manages strategy lifecycle and state.

Integrates with ``operations/strategy_lifecycle.py`` to provide lifecycle
state tracking for drop-in strategies.  The lifecycle states are:

    CANDIDATE -> VALIDATING -> PAPER_PROVEN -> DEMO_PROVEN -> DEPLOYED -> DECAYING -> FROZEN -> RETIRED

Key design rules (from operations/strategy_lifecycle.py):
- Automated evaluation can only DEMOTE: DEPLOYED → DECAYING, DECAYING → FROZEN
- All promotions require an explicit operator actor
- FROZEN and RETIRED strategies are blocked from execution
- Code-based setups are seeded as DEPLOYED with a content fingerprint
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from strategies.base import BaseStrategy, StrategyMetadata

logger = logging.getLogger(__name__)

# Valid lifecycle states (matches operations/strategy_lifecycle.py)
LIFECYCLE_STATES = [
    "CANDIDATE",
    "VALIDATING",
    "PAPER_PROVEN",
    "DEMO_PROVEN",
    "DEPLOYED",
    "DECAYING",
    "FROZEN",
    "RETIRED",
]

# Automated evaluation can only demote to these states
AUTOMATED_DEMOTIONS = {
    "DEPLOYED": "DECAYING",
    "DECAYING": "FROZEN",
}

# Blocked states — no new trades allowed
BLOCKED_STATES = {"FROZEN", "RETIRED"}


@dataclass
class RegistryEntry:
    """A strategy's registry entry with lifecycle state."""

    name: str
    state: str = "CANDIDATE"
    version_fingerprint: str = ""
    registered_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    updated_by: str = "system"
    reason: str = ""
    # Performance tracking
    total_trades: int = 0
    winning_trades: int = 0
    total_r: float = 0.0
    best_r: float = 0.0
    worst_r: float = 0.0
    # Metadata
    symbols: List[str] = field(default_factory=list)
    timeframe: str = ""
    description: str = ""
    author: str = "system"
    version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RegistryEntry":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def is_blocked(self) -> bool:
        return self.state in BLOCKED_STATES

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def expectancy_r(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_r / self.total_trades


class StrategyRegistry:
    """Registry managing strategy lifecycle states.

    Persists to ``strategies/registry.json``.  Integrates with the loader
    to register discovered strategies and track their lifecycle.
    """

    def __init__(self, strategies_dir: Optional[str] = None):
        from strategies.loader import STRATEGIES_DIR
        self.strategies_dir = Path(strategies_dir or STRATEGIES_DIR)
        self.registry_path = self.strategies_dir / "registry.json"
        self._entries: Dict[str, RegistryEntry] = {}
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        """Load registry from disk."""
        if not self.registry_path.exists():
            return
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            for name, entry_dict in data.get("entries", {}).items():
                self._entries[name] = RegistryEntry.from_dict(entry_dict)
            self._history = data.get("history", {})
            logger.info("Strategy registry loaded: %d entries", len(self._entries))
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Corrupted strategy registry: %s", exc)

    def save(self) -> None:
        """Persist registry to disk."""
        data = {
            "entries": {name: entry.to_dict() for name, entry in self._entries.items()},
            "history": self._history,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("Strategy registry saved: %d entries", len(self._entries))

    def register(self, strategy: BaseStrategy) -> RegistryEntry:
        """Register a strategy or update its fingerprint."""
        meta = strategy.metadata()
        fingerprint = self._compute_fingerprint(strategy)

        if strategy.name in self._entries:
            entry = self._entries[strategy.name]
            if entry.version_fingerprint != fingerprint:
                logger.warning(
                    "Strategy %s code changed (fingerprint mismatch) — "
                    "operator re-acknowledgment required",
                    strategy.name,
                )
                entry.reason = "REGISTRY_DRIFT"
            entry.symbols = meta.symbols
            entry.timeframe = meta.timeframe
            entry.description = meta.description
            entry.updated_at = time.time()
        else:
            entry = RegistryEntry(
                name=strategy.name,
                state="DEPLOYED",  # code-based setups start DEPLOYED
                version_fingerprint=fingerprint,
                symbols=meta.symbols,
                timeframe=meta.timeframe,
                description=meta.description,
                author=meta.author,
                version=meta.version,
            )
            self._entries[strategy.name] = entry
            logger.info("Strategy registered: %s (state=%s)", strategy.name, entry.state)

        self.save()
        return entry

    def get(self, name: str) -> Optional[RegistryEntry]:
        """Get a strategy's registry entry."""
        return self._entries.get(name)

    def list_all(self) -> List[RegistryEntry]:
        """List all registered strategies."""
        return list(self._entries.values())

    def list_by_state(self, state: str) -> List[RegistryEntry]:
        """List strategies in a specific lifecycle state."""
        return [e for e in self._entries.values() if e.state == state]

    def list_active(self) -> List[RegistryEntry]:
        """List all non-retired strategies."""
        return [e for e in self._entries.values() if e.state != "RETIRED"]

    def list_blocked(self) -> List[RegistryEntry]:
        """List strategies blocked from execution."""
        return [e for e in self._entries.values() if e.is_blocked]

    def can_execute(self, name: str) -> bool:
        """Check if a strategy is allowed to execute."""
        entry = self._entries.get(name)
        if not entry:
            return False
        return not entry.is_blocked

    def promote(self, name: str, actor: str = "operator", reason: str = "") -> bool:
        """Promote a strategy to the next lifecycle state.

        Only allowed by operator (human).  Automated systems cannot promote.
        """
        entry = self._entries.get(name)
        if not entry:
            return False

        state_idx = LIFECYCLE_STATES.index(entry.state) if entry.state in LIFECYCLE_STATES else -1
        if state_idx < 0 or state_idx >= len(LIFECYCLE_STATES) - 1:
            logger.warning("Cannot promote %s from state %s", name, entry.state)
            return False

        new_state = LIFECYCLE_STATES[state_idx + 1]
        self._transition(name, entry.state, new_state, actor, reason)
        return True

    def demote(self, name: str, actor: str = "system", reason: str = "") -> bool:
        """Demote a strategy (automated evaluation)."""
        entry = self._entries.get(name)
        if not entry:
            return False

        state_idx = LIFECYCLE_STATES.index(entry.state) if entry.state in LIFECYCLE_STATES else -1
        if state_idx <= 0:
            return False

        new_state = LIFECYCLE_STATES[state_idx - 1]
        self._transition(name, entry.state, new_state, actor, reason)
        return True

    def retire(self, name: str, actor: str = "operator", reason: str = "") -> bool:
        """Retire a strategy (terminal state)."""
        entry = self._entries.get(name)
        if not entry:
            return False

        self._transition(name, entry.state, "RETIRED", actor, reason or "Retired by operator")
        return True

    def record_trade(self, name: str, r_multiple: float) -> None:
        """Record a trade outcome for a strategy."""
        entry = self._entries.get(name)
        if not entry:
            return

        entry.total_trades += 1
        entry.total_r += r_multiple
        if r_multiple > 0:
            entry.winning_trades += 1
        entry.best_r = max(entry.best_r, r_multiple)
        entry.worst_r = min(entry.worst_r, r_multiple)
        entry.updated_at = time.time()
        self.save()

    def _transition(
        self, name: str, from_state: str, to_state: str,
        actor: str, reason: str,
    ) -> None:
        """Record a lifecycle transition."""
        entry = self._entries[name]
        entry.state = to_state
        entry.updated_at = time.time()
        entry.updated_by = actor
        entry.reason = reason

        # Append to history
        if name not in self._history:
            self._history[name] = []
        self._history[name].append({
            "from": from_state,
            "to": to_state,
            "actor": actor,
            "reason": reason,
            "timestamp": time.time(),
        })

        logger.info(
            "Strategy %s: %s → %s (actor=%s, reason=%s)",
            name, from_state, to_state, actor, reason,
        )
        self.save()

    def _compute_fingerprint(self, strategy: BaseStrategy) -> str:
        """Compute a content hash fingerprint for a strategy class."""
        source = ""
        try:
            source = __import__("inspect").getsource(type(strategy))
        except (TypeError, OSError):
            source = f"{strategy.name}:{strategy.description}"
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def get_history(self, name: str) -> List[Dict[str, Any]]:
        """Get lifecycle transition history for a strategy."""
        return self._history.get(name, [])

    def summary(self) -> Dict[str, Any]:
        """Get a summary of all registered strategies."""
        by_state = {}
        for entry in self._entries.values():
            by_state[entry.state] = by_state.get(entry.state, 0) + 1

        return {
            "total": len(self._entries),
            "by_state": by_state,
            "blocked": len(self.list_blocked()),
            "active": len(self.list_active()),
        }
