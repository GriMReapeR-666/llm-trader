"""Base strategy class — Interface for all drop-in strategy plugins.

Every strategy module in this directory must contain a class inheriting from
``BaseStrategy``.  The class defines the signal generation logic, metadata,
and lifecycle hooks.

This mirrors the pattern in ``core/harness/workflows.py`` (``BaseTradingWorkflow``)
but is designed for user-authored, drop-in scripts rather than built-in
institutional workflows.

Design principles (from TRADING_SYSTEM_MASTER_TODO.md):
- Capital preservation is the primary objective.
- Deterministic limits outrank model confidence.
- Strategies must not bypass the risk arbiter or validator.
- Every signal must include a confidence score and rationale.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StrategySignal:
    """A trading signal produced by a strategy.

    This is the strategy's output — a proposal that must pass through
    deterministic validation and risk arbitration before execution.
    """

    strategy: str
    symbol: str
    side: str  # "BUY" | "SELL" | "HOLD" | "REJECT"
    confidence: float  # 0.0 - 1.0
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    timeframe: str = ""
    rationale: str = ""
    # Signal quality metrics
    confluence_score: float = 0.0  # independent confluence components
    regime_alignment: bool = False  # does signal agree with regime?
    mtf_alignment: bool = False  # multi-timeframe agreement?
    # Metadata
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def risk_reward(self) -> Optional[float]:
        """Calculate reward:risk ratio if entry and levels are set."""
        if not all([self.entry, self.stop_loss, self.take_profit]):
            return None
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.take_profit - self.entry)
        if risk <= 0:
            return None
        return round(reward / risk, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "side": self.side,
            "confidence": self.confidence,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "timeframe": self.timeframe,
            "rationale": self.rationale,
            "confluence_score": self.confluence_score,
            "regime_alignment": self.regime_alignment,
            "mtf_alignment": self.mtf_alignment,
            "risk_reward": self.risk_reward,
            "tags": self.tags,
            "timestamp": self.timestamp,
        }


@dataclass
class StrategyMetadata:
    """Static metadata about a strategy — displayed in the TUI Strategies screen."""

    name: str
    description: str
    author: str = "system"
    version: str = "1.0.0"
    symbols: List[str] = field(default_factory=list)
    timeframe: str = ""
    # Lifecycle
    stage: str = "CANDIDATE"  # CANDIDATE | EVALUATION | FULL | RETIRED | QUARANTINE
    # Performance (updated by evaluation)
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    expectancy_r: float = 0.0
    recovery_factor: float = 0.0
    # Gate
    gate_progress: float = 0.0  # 0.0 - 1.0
    active_today: bool = False
    last_cycle_ts: float = 0.0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "version": self.version,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "stage": self.stage,
            "trades": self.trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "expectancy_r": self.expectancy_r,
            "recovery_factor": self.recovery_factor,
            "gate_progress": self.gate_progress,
            "active_today": self.active_today,
            "last_cycle_ts": self.last_cycle_ts,
            "notes": self.notes,
        }


class BaseStrategy(ABC):
    """Abstract base class for all trading strategy plugins.

    Every strategy in this directory must inherit from this class and
    implement ``evaluate()``.  The loader discovers classes that inherit
    from ``BaseStrategy`` and registers them automatically.

    Lifecycle:
        1. ``on_load()`` — called once when the strategy is discovered
        2. ``on_activate()`` — called when the strategy starts receiving data
        3. ``evaluate()`` — called on each candle/event to produce a signal
        4. ``on_deactivate()`` — called when the strategy stops receiving data
        5. ``on_unload()`` — called before the strategy is removed

    Safety:
        - The strategy never has direct execution authority.
        - All signals pass through deterministic validation and risk arbitration.
        - The strategy cannot modify risk limits or override hard rules.
    """

    # ── Subclass overrides ──────────────────────────────────────────

    name: str = "unnamed"
    description: str = ""
    symbols: List[str] = []
    timeframe: str = ""

    # ── Lifecycle hooks ─────────────────────────────────────────────

    def on_load(self) -> None:
        """Called once when the strategy is discovered and loaded.

        Use this for one-time initialization (loading models, connecting
        to data sources, etc.).
        """

    def on_activate(self) -> None:
        """Called when the strategy starts receiving market data."""

    @abstractmethod
    def evaluate(self, context: Dict[str, Any]) -> StrategySignal:
        """Produce a trading signal for the given market context.

        Args:
            context: Market data and state dictionary containing at minimum:
                - symbol: str — the symbol being evaluated
                - price: float — current price (mid)
                - point: float — symbol point value
                - digits: int — symbol decimal places
                - ohlcv: dict — recent OHLCV data by timeframe
                - regime: str — current regime classification
                - features: dict — computed technical features
                - account: dict — balance, equity, margin info

        Returns:
            A StrategySignal with the proposed trade or HOLD/REJECT.
        """
        ...

    def on_deactivate(self) -> None:
        """Called when the strategy stops receiving data."""

    def on_unload(self) -> None:
        """Called before the strategy is removed."""

    # ── Helper methods ──────────────────────────────────────────────

    def metadata(self) -> StrategyMetadata:
        """Return static metadata about this strategy."""
        return StrategyMetadata(
            name=self.name,
            description=self.description,
            symbols=self.symbols,
            timeframe=self.timeframe,
        )

    def reject(self, symbol: str, reason: str = "") -> StrategySignal:
        """Convenience: produce a REJECT signal."""
        return StrategySignal(
            strategy=self.name,
            symbol=symbol,
            side="REJECT",
            confidence=0.0,
            rationale=reason or "Strategy rejects this setup",
        )

    def hold(self, symbol: str, reason: str = "") -> StrategySignal:
        """Convenience: produce a HOLD signal."""
        return StrategySignal(
            strategy=self.name,
            symbol=symbol,
            side="HOLD",
            confidence=0.0,
            rationale=reason or "No clear signal",
        )
