"""Grid Selection for Quality-Diversity.

Implements grid-based quality-diversity selection for maintaining both
alpha quality and behavioral diversity.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .ic_evaluator import ICResult


# ---------------------------------------------------------------------------
# Grid Selection Result
# ---------------------------------------------------------------------------

@dataclass
class GridSelectionResult:
    """Result of grid-based quality-diversity selection."""
    selected_indices: List[int]          # Indices of selected alphas
    selected_ics: Dict[int, float]       # IC values for selected alphas
    grid_occupancy: Dict[Tuple[int, int], int]  # Alphas per grid cell
    grid_coverage: float                 # Fraction of grid cells occupied
    avg_ic_per_cell: Dict[Tuple[int, int], float]  # Average IC per cell
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "n_selected": len(self.selected_indices),
            "grid_coverage": round(self.grid_coverage, 4),
            "avg_ic_overall": round(np.mean(list(self.selected_ics.values())), 6) if self.selected_ics else 0.0,
            "grid_occupancy": {str(k): v for k, v in self.grid_occupancy.items()},
        }


# ---------------------------------------------------------------------------
# Grid Selector Class
# ---------------------------------------------------------------------------

class GridSelector:
    """Quality-diversity selection via PCA grid."""
    
    def __init__(self, grid_size: int = 10, parent_pool_size: int = 150,
                 min_per_cell: int = 1, max_per_cell: int = 20):
        """Initialize grid selector.
        
        Args:
            grid_size: Number of grid cells per dimension
            parent_pool_size: Target size of selected pool
            min_per_cell: Minimum alphas to select per occupied cell
            max_per_cell: Maximum alphas to select per cell
        """
        self.grid_size = grid_size
        self.parent_pool_size = parent_pool_size
        self.min_per_cell = min_per_cell
        self.max_per_cell = max_per_cell
    
    def select(self, behavior_grid: Dict[Tuple[int, int], List[int]],
               ic_results: Dict[int, ICResult]) -> GridSelectionResult:
        """Select top-|IC| candidates per grid cell.
        
        Args:
            behavior_grid: Dictionary mapping (row, col) to alpha indices
            ic_results: Dictionary mapping alpha index to ICResult
            
        Returns:
            GridSelectionResult with selected alphas
        """
        selected_indices = []
        selected_ics = {}
        grid_occupancy = {}
        avg_ic_per_cell = {}
        
        # Calculate total alphas and target per cell
        total_alphas = sum(len(indices) for indices in behavior_grid.values())
        target_per_cell = max(self.min_per_cell, 
                             min(self.max_per_cell, 
                                 self.parent_pool_size // max(1, len(behavior_grid))))
        
        # Select from each cell
        for cell, indices in behavior_grid.items():
            if not indices:
                continue
            
            # Get IC results for this cell
            cell_ics = {}
            for idx in indices:
                if idx in ic_results:
                    ic_result = ic_results[idx]
                    cell_ics[idx] = abs(ic_result.ic_mean)
            
            if not cell_ics:
                continue
            
            # Sort by |IC| (descending)
            sorted_indices = sorted(cell_ics.keys(), 
                                   key=lambda i: cell_ics[i], 
                                   reverse=True)
            
            # Select top candidates
            n_select = min(target_per_cell, len(sorted_indices))
            cell_selected = sorted_indices[:n_select]
            
            # Add to results
            selected_indices.extend(cell_selected)
            for idx in cell_selected:
                selected_ics[idx] = ic_results[idx].ic_mean
            
            grid_occupancy[cell] = len(cell_selected)
            
            # Calculate average IC for cell
            cell_ic_values = [ic_results[idx].ic_mean for idx in cell_selected 
                             if idx in ic_results]
            avg_ic_per_cell[cell] = np.mean(cell_ic_values) if cell_ic_values else 0.0
        
        # Calculate grid coverage
        occupied_cells = len([v for v in grid_occupancy.values() if v > 0])
        total_cells = self.grid_size * self.grid_size
        grid_coverage = occupied_cells / total_cells if total_cells > 0 else 0.0
        
        return GridSelectionResult(
            selected_indices=selected_indices,
            selected_ics=selected_ics,
            grid_occupancy=grid_occupancy,
            grid_coverage=grid_coverage,
            avg_ic_per_cell=avg_ic_per_cell,
        )
    
    def select_with_correlation_guard(self, 
                                     behavior_grid: Dict[Tuple[int, int], List[int]],
                                     ic_results: Dict[int, ICResult],
                                     alpha_panel: Optional[np.ndarray] = None,
                                     max_correlation: float = 0.7) -> GridSelectionResult:
        """Select with correlation guard to prevent redundancy.
        
        Args:
            behavior_grid: Behavior grid
            ic_results: IC results
            alpha_panel: Alpha values panel (n_timepoints, n_alphas)
            max_correlation: Maximum allowed pairwise correlation
            
        Returns:
            GridSelectionResult with correlation-filtered selection
        """
        # First pass: standard grid selection
        initial_result = self.select(behavior_grid, ic_results)
        
        if alpha_panel is None or len(initial_result.selected_indices) < 2:
            return initial_result
        
        # Second pass: correlation guard
        selected = []
        selected_set = set()
        
        # Sort by IC (descending)
        sorted_indices = sorted(initial_result.selected_indices,
                               key=lambda i: abs(initial_result.selected_ics.get(i, 0)),
                               reverse=True)
        
        for idx in sorted_indices:
            if len(selected) >= self.parent_pool_size:
                break
            
            # Check correlation with already selected
            if not selected:
                selected.append(idx)
                selected_set.add(idx)
                continue
            
            # Calculate max correlation with selected
            max_corr = 0.0
            for sel_idx in selected:
                corr = np.corrcoef(alpha_panel[:, idx], alpha_panel[:, sel_idx])[0, 1]
                if np.isfinite(corr):
                    max_corr = max(max_corr, abs(corr))
            
            # Add if correlation is acceptable
            if max_corr <= max_correlation:
                selected.append(idx)
                selected_set.add(idx)
        
        # Rebuild result
        filtered_ics = {i: initial_result.selected_ics[i] for i in selected 
                       if i in initial_result.selected_ics}
        
        return GridSelectionResult(
            selected_indices=selected,
            selected_ics=filtered_ics,
            grid_occupancy=initial_result.grid_occupancy,
            grid_coverage=initial_result.grid_coverage,
            avg_ic_per_cell=initial_result.avg_ic_per_cell,
        )
    
    def compute_diversity_score(self, selection_result: GridSelectionResult) -> float:
        """Compute diversity score based on grid coverage.
        
        Args:
            selection_result: Grid selection result
            
        Returns:
            Diversity score between 0 and 1
        """
        return selection_result.grid_coverage
    
    def compute_quality_score(self, selection_result: GridSelectionResult) -> float:
        """Compute quality score based on average |IC|.
        
        Args:
            selection_result: Grid selection result
            
        Returns:
            Quality score (average absolute IC)
        """
        if not selection_result.selected_ics:
            return 0.0
        
        abs_ics = [abs(ic) for ic in selection_result.selected_ics.values()]
        return np.mean(abs_ics)
    
    def compute_joint_score(self, selection_result: GridSelectionResult,
                           quality_weight: float = 0.7,
                           diversity_weight: float = 0.3) -> float:
        """Compute joint quality-diversity score.
        
        Args:
            selection_result: Grid selection result
            quality_weight: Weight for quality component
            diversity_weight: Weight for diversity component
            
        Returns:
            Joint score (0-1)
        """
        quality = self.compute_quality_score(selection_result)
        diversity = self.compute_diversity_score(selection_result)
        
        # Normalize quality to 0-1 range (assuming max IC ~0.15)
        quality_norm = min(1.0, quality / 0.15)
        
        return quality_weight * quality_norm + diversity_weight * diversity


# ---------------------------------------------------------------------------
# Pool Management
# ---------------------------------------------------------------------------

class ParentPoolManager:
    """Manage the parent pool for next generation.

    Keyed by expression string, NOT by population index: the population is
    rebuilt every generation, so a positional index from gen N points at a
    different expression (or out of range) in gen N+1.
    """
    
    def __init__(self, pool_size: int = 150):
        """Initialize pool manager.
        
        Args:
            pool_size: Maximum pool size
        """
        self.pool_size = pool_size
        self.pool: Dict[str, Any] = {}  # expr_string -> expression
        self.fitness: Dict[str, float] = {}  # expr_string -> |IC|
    
    def update(self, selected_indices: List[int],
               expressions: List[Any],
               ic_results: Dict[int, 'ICResult']):
        """Update pool with selected expressions.
        
        Args:
            selected_indices: Indices of selected expressions
            expressions: List of all expressions
            ic_results: IC results for selected expressions
        """
        # Key by expression string (stable across generations); a positional
        # index from gen N is meaningless in gen N+1. Store a copy so later
        # in-place mutation of the live population can never alias the pool.
        for idx in selected_indices:
            if idx < len(expressions):
                expr = expressions[idx]
                try:
                    key = expr.to_string()
                except Exception:
                    continue
                try:
                    stored = expr.copy()
                except Exception:
                    stored = expr
                self.pool[key] = stored
                if idx in ic_results:
                    try:
                        self.fitness[key] = abs(ic_results[idx].ic_mean)
                    except Exception:
                        self.fitness[key] = 0.0
                else:
                    self.fitness.setdefault(key, 0.0)
        
        # Trim to pool size if needed
        if len(self.pool) > self.pool_size:
            # Sort by fitness
            ranked = sorted(self.pool.keys(),
                            key=lambda k: self.fitness.get(k, 0.0),
                            reverse=True)

            # Keep top pool_size by |IC|
            for key in ranked[self.pool_size:]:
                del self.pool[key]
                if key in self.fitness:
                    del self.fitness[key]

    def get_expressions(self) -> List[Any]:
        """Get all expressions in the pool, best-|IC| first."""
        ranked = sorted(self.pool.keys(),
                        key=lambda k: self.fitness.get(k, 0.0),
                        reverse=True)
        return [self.pool[k] for k in ranked]

    def get_fitness_scores(self) -> List[float]:
        """Get fitness scores for all expressions in pool (aligned with get_expressions)."""
        ranked = sorted(self.pool.keys(),
                        key=lambda k: self.fitness.get(k, 0.0),
                        reverse=True)
        return [self.fitness.get(k, 0.0) for k in ranked]
    
    def clear(self):
        """Clear the pool."""
        self.pool.clear()
        self.fitness.clear()
