"""Elastic Weight Consolidation (EWC) for Q-Learning — Forgetting Prevention.

Prevents catastrophic forgetting when the RL agent adapts to new market
regimes by penalizing changes to important Q-values from previous regimes.

Key insight from Kirkpatrick et al. (2017):
    "When learning new tasks, don't overwrite knowledge from old tasks.
     Instead, penalize changes to weights that were important for
     previous tasks."

Adapted for Q-learning (not neural networks):
    - In neural EWC: importance = Fisher information of network weights
    - In Q-learning: importance = visit count × |Q-value| for each (s,a) pair
    - When updating Q(s,a) in new regime, add penalty:
      penalty = λ × importance(s,a) × (Q_new - Q_old)²

Architecture:
    ┌─────────────────────────────────────────────────────┐
    │           EWC FORGETTING PREVENTION                  │
    │                                                      │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 1: Snapshot Q-table              │           │
    │  │   After each regime/task, snapshot    │           │
    │  │   the Q-table and compute importance  │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 2: Compute Importance Scores     │           │
    │  │   importance(s,a) = visits(s,a) ×     │           │
    │  │                     |Q(s,a)|          │           │
    │  │   High visit + high Q = very important│           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 3: EWC-Modified Q-Update         │           │
    │  │   Q(s,a) = Q(s,a) + α × [r + γ×max   │           │
    │  │           - Q(s,a)] - λ×imp×(Q-Q_snap)²│         │
    │  │                                       │           │
    │  │   λ = EWC strength (higher = less     │           │
    │  │       forgetting, but slower learning) │           │
    │  └──────────────┬───────────────────────┘           │
    │                 ↓                                    │
    │  ┌──────────────────────────────────────┐           │
    │  │ Step 4: Multi-Task Consolidation      │           │
    │  │   Combine importance from all past    │           │
    │  │   regimes (not just the last one)     │           │
    │  └──────────────────────────────────────┘           │
    └─────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.state_persistence import StoreBackedState

logger = logging.getLogger("EWC")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EWC_LAMBDA = 0.5     # EWC strength (higher = less forgetting)
DEFAULT_MAX_SNAPSHOTS = 5    # Keep last N regime snapshots
DEFAULT_MIN_VISITS = 3       # Minimum visits to consider a state important


# ---------------------------------------------------------------------------
# Importance Snapshot
# ---------------------------------------------------------------------------

@dataclass
class ImportanceSnapshot:
    """Snapshot of Q-table importance for a single regime/task."""
    regime: str
    timestamp: float
    q_values: Dict[str, Dict[str, float]]       # state_key -> {action: q}
    visit_counts: Dict[str, Dict[str, float]]    # state_key -> {action: visits}
    total_updates: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "timestamp": self.timestamp,
            "q_values": self.q_values,
            "visit_counts": self.visit_counts,
            "total_updates": self.total_updates,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ImportanceSnapshot":
        return cls(
            regime=d.get("regime", "unknown"),
            timestamp=d.get("timestamp", 0.0),
            q_values=d.get("q_values", {}),
            visit_counts=d.get("visit_counts", {}),
            total_updates=d.get("total_updates", 0),
        )


# ---------------------------------------------------------------------------
# EWC Engine
# ---------------------------------------------------------------------------

class ElasticWeightConsolidationRegularizer:
    """Low-level EWC penalty on parameter vectors (Fisher-information weighted)."""

    @staticmethod
    def compute_ewc_loss_penalty(
        current_parameters,
        previous_optimal_parameters,
        fisher_information_diagonal,
        regularizer_lambda: float = 100.0,
    ) -> float:
        """L_EWC(theta) = sum_i (lambda/2) * F_i * (theta_i - theta_{i,old})^2."""
        import numpy as _np
        curr = _np.asarray(current_parameters, dtype=float)
        prev = _np.asarray(previous_optimal_parameters, dtype=float)
        fisher = _np.asarray(fisher_information_diagonal, dtype=float)
        lam = float(regularizer_lambda)
        if len(curr) != len(prev) or len(curr) != len(fisher) or len(curr) == 0:
            return 0.0
        penalty = 0.5 * lam * float(_np.sum(fisher * ((curr - prev) ** 2)))
        return float(max(0.0, penalty))


class EWCForgettingPrevention(StoreBackedState):
    """Elastic Weight Consolidation for Q-learning.

    Prevents catastrophic forgetting by penalizing changes to important
    Q-values from previous regimes when learning in a new regime.

    Usage:
        ewc = EWCForgettingPrevention()

        # After each regime, snapshot the Q-table
        ewc.snapshot_regime("LOW_VOL_TREND", q_table, visit_counts)

        # When updating Q-values, use EWC-modified update
        ewc.update_with_ewc(q_table, state_key, action, reward, next_q_max, ...)
    """

    store_key = "ewc_state"

    def __init__(
        self,
        state_file: str = "ewc_state.json",
        ewc_lambda: float = DEFAULT_EWC_LAMBDA,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        min_visits: int = DEFAULT_MIN_VISITS,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.ewc_lambda = ewc_lambda
        self.max_snapshots = max_snapshots
        self.min_visits = min_visits
        self.snapshots: List[ImportanceSnapshot] = []
        self._load()
        if store is not None:
            self.attach_store(store)

    def _load(self) -> None:
        data = self._load_state_dict()
        snapshots = data.get("snapshots", []) if isinstance(data, dict) else []
        try:
            self.snapshots = [ImportanceSnapshot.from_dict(s) for s in snapshots]
        except Exception as exc:
            logger.debug("state snapshot failed: %s", exc)
            self.snapshots = []

    def _save(self) -> None:
        self._save_state_dict({
            "snapshots": [s.to_dict() for s in self.snapshots],
            "ewc_lambda": self.ewc_lambda,
        })
    
    def compute_importance(
        self,
        q_values: Dict[str, Dict[str, float]],
        visit_counts: Dict[str, Dict[str, float]],
    ) -> Dict[str, Dict[str, float]]:
        """Compute importance scores for each (state, action) pair.
        
        importance(s,a) = visits(s,a) × |Q(s,a)|
        
        High visit count + high Q-value = very important (don't forget).
        Low visit count or low Q-value = less important (safe to change).
        """
        importance: Dict[str, Dict[str, float]] = {}
        
        for state_key, actions in q_values.items():
            importance[state_key] = {}
            visits = visit_counts.get(state_key, {})
            
            for action, q_val in actions.items():
                visit_count = visits.get(action, 0.0)
                
                # Only consider state-actions with enough visits
                if visit_count < self.min_visits:
                    importance[state_key][action] = 0.0
                    continue
                
                # Importance = visit_count × |Q-value|
                importance[state_key][action] = visit_count * abs(q_val)
        
        return importance
    
    def snapshot_regime(
        self,
        regime: str,
        q_table: Dict[str, Dict[str, float]],
        visit_counts: Optional[Dict[str, Dict[str, float]]] = None,
        total_updates: int = 0,
    ) -> None:
        """Snapshot the Q-table after a regime/task.
        
        Call this when a regime ends or when you want to consolidate
        what the agent has learned.
        """
        if visit_counts is None:
            visit_counts = {}
        
        snapshot = ImportanceSnapshot(
            regime=regime,
            timestamp=time.time(),
            q_values=dict(q_table),
            visit_counts=dict(visit_counts),
            total_updates=total_updates,
        )
        
        self.snapshots.append(snapshot)
        
        # Keep only last N snapshots
        if len(self.snapshots) > self.max_snapshots:
            self.snapshots = self.snapshots[-self.max_snapshots:]
        
        self._save()
        logger.info(
            "EWC snapshot: regime=%s, states=%d, total_updates=%d",
            regime, len(q_table), total_updates,
        )
    
    def compute_ewc_penalty(
        self,
        state_key: str,
        action: str,
        current_q: float,
    ) -> float:
        """Compute the EWC penalty for changing a Q-value.
        
        penalty = λ × importance(s,a) × (Q_current - Q_snapshot)²
        
        This penalizes large changes to important Q-values.
        """
        if not self.snapshots:
            return 0.0
        
        total_penalty = 0.0
        
        for snapshot in self.snapshots:
            # Get the importance of this (state, action) in this snapshot
            snap_importance = self.compute_importance(
                snapshot.q_values, snapshot.visit_counts
            )
            
            state_importance = snap_importance.get(state_key, {})
            importance = state_importance.get(action, 0.0)
            
            if importance <= 0:
                continue
            
            # Get the Q-value at snapshot time
            snap_q = snapshot.q_values.get(state_key, {}).get(action, current_q)
            
            # Penalty = importance × (current - snapshot)²
            penalty = importance * (current_q - snap_q) ** 2
            total_penalty += penalty
        
        return self.ewc_lambda * total_penalty
    
    def ewc_update(
        self,
        state_key: str,
        action: str,
        reward: float,
        max_next_q: float,
        learning_rate: float,
        discount_factor: float,
        current_q: float,
    ) -> float:
        """EWC-modified Q-learning update.
        
        Standard Q-learning:
            Q(s,a) = Q(s,a) + α × [r + γ×max(Q(s',a')) - Q(s,a)]
        
        EWC-modified (regularization approach):
            Q(s,a) = Q(s,a) + α × [r + γ×max(Q(s',a')) - Q(s,a)]
                     - λ × importance(s,a) × (Q(s,a) - Q_snapshot)
        
        The regularization term penalizes deviation from snapshot Q-values,
        proportional to importance. This prevents catastrophic forgetting
        of important state-action values from previous regimes.
        """
        # Standard Q-learning target
        td_target = reward + discount_factor * max_next_q
        td_error = td_target - current_q
        
        # Compute EWC regularization term
        ewc_reg = 0.0
        for snapshot in self.snapshots:
            snap_importance = self.compute_importance(
                snapshot.q_values, snapshot.visit_counts
            )
            state_importance = snap_importance.get(state_key, {})
            importance = state_importance.get(action, 0.0)
            if importance <= 0:
                continue
            snap_q = snapshot.q_values.get(state_key, {}).get(action, current_q)
            ewc_reg += importance * (current_q - snap_q)
        ewc_reg *= self.ewc_lambda
        
        # EWC-modified update: standard Q-learning + regularization
        new_q = current_q + learning_rate * td_error - learning_rate * ewc_reg
        
        return new_q
    
    def get_combined_importance(self) -> Dict[str, Dict[str, float]]:
        """Get combined importance across all snapshots.
        
        Returns the maximum importance for each (state, action) pair
        across all stored snapshots.
        """
        combined: Dict[str, Dict[str, float]] = {}
        
        for snapshot in self.snapshots:
            importance = self.compute_importance(
                snapshot.q_values, snapshot.visit_counts
            )
            
            for state_key, actions in importance.items():
                if state_key not in combined:
                    combined[state_key] = {}
                for action, imp in actions.items():
                    combined[state_key][action] = max(
                        combined[state_key].get(action, 0.0), imp
                    )
        
        return combined
    
    def get_status(self) -> Dict[str, Any]:
        """Get EWC status."""
        combined = self.get_combined_importance()
        total_important = sum(
            1 for state_actions in combined.values()
            for imp in state_actions.values()
            if imp > 0
        )
        return {
            "ewc_lambda": self.ewc_lambda,
            "num_snapshots": len(self.snapshots),
            "regimes_stored": [s.regime for s in self.snapshots],
            "total_important_pairs": total_important,
            "total_q_values": sum(
                len(snap.q_values) for snap in self.snapshots
            ),
        }
