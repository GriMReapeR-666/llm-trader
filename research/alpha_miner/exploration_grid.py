"""Structured Exploration Grid for Alpha Discovery.

Implements the 660-cell exploration grid from BrainAlpha that transforms
hypothesis search into an exhaustible coverage problem with priority-weighted
selection.

References:
    Wangpratham, N. (2026). "BrainAlpha: An Autonomous Multi-Agent System
    for Quantitative Alpha Discovery." Working Paper.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cell Status
# ---------------------------------------------------------------------------

class CellStatus(Enum):
    """Lifecycle status of a grid cell."""
    EMPTY = "empty"
    ASSIGNED = "assigned"
    EXPLORED = "explored"
    EXHAUSTED = "exhausted"


# ---------------------------------------------------------------------------
# Grid Configuration (660 cells)
# ---------------------------------------------------------------------------

# Signal Families (11)
SIGNAL_FAMILIES = [
    "momentum",           # Trend continuation
    "mean_reversion",     # Reversion to mean
    "volatility",         # Volatility regimes
    "volume_price",       # Volume-price dynamics
    "breakout",           # Range breakout
    "divergence",         # Divergence signals
    "microstructure",     # Order book dynamics
    "cross_asset",        # Cross-asset relationships
    "calendar",           # Calendar effects
    "sentiment",          # Sentiment signals
    "composite",          # Multi-factor combinations
]

# Operator Patterns (5)
OPERATOR_PATTERNS = [
    "ts_arithmetic",      # Time-series arithmetic
    "cs_ranking",         # Cross-sectional ranking
    "volatility_scale",   # Volatility scaling
    "decay_smoothing",    # Decay/smoothing operations
    "conditional",        # Conditional logic
]

# Horizons (3)
HORIZONS = [
    "intraday",           # Intraday (1-24 bars)
    "short_term",         # Short-term (1-5 days)
    "medium_term",        # Medium-term (5-20 days)
]

# Conditioning (4)
CONDITIONING = [
    "none",               # No conditioning
    "regime",             # Regime-conditional
    "volatility",         # Volatility-conditional
    "time_of_day",        # Time-of-day conditional
]

# Total cells: 11 × 5 × 3 × 4 = 660


# ---------------------------------------------------------------------------
# Grid Cell
# ---------------------------------------------------------------------------

@dataclass
class GridCell:
    """Single cell in the exploration grid."""
    cell_id: str
    signal_family: str
    operator_pattern: str
    horizon: str
    conditioning: str
    status: CellStatus = CellStatus.EMPTY
    priority: float = 0.0
    assigned_agent: Optional[str] = None
    pass_count: int = 0
    fail_count: int = 0
    best_sharpe: float = 0.0
    expressions_tested: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "signal_family": self.signal_family,
            "operator_pattern": self.operator_pattern,
            "horizon": self.horizon,
            "conditioning": self.conditioning,
            "status": self.status.value,
            "priority": round(self.priority, 4),
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "best_sharpe": round(self.best_sharpe, 4),
            "n_expressions": len(self.expressions_tested),
        }


# ---------------------------------------------------------------------------
# Exploration Grid
# ---------------------------------------------------------------------------

class ExplorationGrid:
    """660-cell structured exploration grid for alpha discovery."""
    
    def __init__(self):
        self.cells: Dict[str, GridCell] = {}
        self._build_grid()
    
    def _build_grid(self):
        """Build the 660-cell grid."""
        cell_idx = 0
        
        for family in SIGNAL_FAMILIES:
            for pattern in OPERATOR_PATTERNS:
                for horizon in HORIZONS:
                    for conditioning in CONDITIONING:
                        cell_id = f"{family}_{pattern}_{horizon}_{conditioning}"
                        
                        self.cells[cell_id] = GridCell(
                            cell_id=cell_id,
                            signal_family=family,
                            operator_pattern=pattern,
                            horizon=horizon,
                            conditioning=conditioning,
                        )
                        cell_idx += 1
        
        logger.info(f"Built exploration grid with {len(self.cells)} cells")
    
    def get_next_cell(self, agent_id: str = "default") -> Optional[GridCell]:
        """Get the next cell to explore based on priority.
        
        Priority scoring:
        Priority(c, a) = 0.5·Novelty(c) + 0.3·ExpectedYield(c) + 0.2·AgentAlignment(c, a)
        """
        available_cells = [
            cell for cell in self.cells.values()
            if cell.status in (CellStatus.EMPTY, CellStatus.ASSIGNED)
        ]
        
        if not available_cells:
            return None
        
        # Calculate priority for each cell
        for cell in available_cells:
            cell.priority = self._calculate_priority(cell, agent_id)
        
        # Sort by priority (descending)
        available_cells.sort(key=lambda c: c.priority, reverse=True)
        
        # Select top cell
        selected = available_cells[0]
        selected.status = CellStatus.ASSIGNED
        selected.assigned_agent = agent_id
        
        return selected
    
    def _calculate_priority(self, cell: GridCell, agent_id: str) -> float:
        """Calculate cell priority."""
        # Novelty component
        novelty_map = {
            CellStatus.EMPTY: 1.0,
            CellStatus.ASSIGNED: 0.5,
            CellStatus.EXPLORED: 0.25,
            CellStatus.EXHAUSTED: 0.0,
        }
        novelty = novelty_map[cell.status]
        
        # Expected yield (updated online)
        total = cell.pass_count + cell.fail_count
        expected_yield = (cell.pass_count / total) if total > 0 else 0.5
        
        # Agent alignment (simulated)
        agent_alignment = 0.7  # Default
        
        # Weighted combination
        priority = 0.5 * novelty + 0.3 * expected_yield + 0.2 * agent_alignment
        
        return priority
    
    def update_cell(self, cell_id: str, passed: bool, 
                   expression: str, sharpe: float = 0.0):
        """Update cell after expression evaluation."""
        if cell_id not in self.cells:
            return
        
        cell = self.cells[cell_id]
        
        if passed:
            cell.pass_count += 1
            cell.best_sharpe = max(cell.best_sharpe, sharpe)
        else:
            cell.fail_count += 1
        
        cell.expressions_tested.append(expression)
        
        # Update status
        if cell.pass_count > 0:
            cell.status = CellStatus.EXPLORED
        
        # Check if exhausted (many failures, no recent passes)
        if cell.fail_count > 10 and cell.pass_count == 0:
            cell.status = CellStatus.EXHAUSTED
    
    def get_coverage_stats(self) -> Dict[str, Any]:
        """Get grid coverage statistics."""
        status_counts = {}
        for cell in self.cells.values():
            status_counts[cell.status.value] = status_counts.get(cell.status.value, 0) + 1
        
        total = len(self.cells)
        explored = status_counts.get("explored", 0)
        
        return {
            "total_cells": total,
            "empty": status_counts.get("empty", 0),
            "assigned": status_counts.get("assigned", 0),
            "explored": explored,
            "exhausted": status_counts.get("exhausted", 0),
            "coverage_pct": round(explored / total * 100, 2) if total > 0 else 0,
        }
    
    def get_family_stats(self) -> Dict[str, Dict[str, int]]:
        """Get statistics by signal family."""
        family_stats = {}
        
        for cell in self.cells.values():
            family = cell.signal_family
            if family not in family_stats:
                family_stats[family] = {"pass": 0, "fail": 0, "total": 0}
            
            family_stats[family]["pass"] += cell.pass_count
            family_stats[family]["fail"] += cell.fail_count
            family_stats[family]["total"] += 1
        
        return family_stats
    
    def get_priority_cells(self, top_k: int = 10) -> List[Dict[str, Any]]:
        """Get top-K priority cells."""
        cells = list(self.cells.values())
        cells.sort(key=lambda c: c.priority, reverse=True)
        
        return [cell.to_dict() for cell in cells[:top_k]]
    
    def reset(self):
        """Reset all cells to empty."""
        for cell in self.cells.values():
            cell.status = CellStatus.EMPTY
            cell.priority = 0.0
            cell.assigned_agent = None
            cell.pass_count = 0
            cell.fail_count = 0
            cell.best_sharpe = 0.0
            cell.expressions_tested.clear()
    
    def export_grid(self) -> List[Dict[str, Any]]:
        """Export grid as list of cell dictionaries."""
        return [cell.to_dict() for cell in self.cells.values()]
