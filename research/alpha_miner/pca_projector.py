"""PCA Behavior Space Projector.

Projects alpha behaviors into 2D PCA space for quality-diversity selection.
Implements the behavior space projection from the paper.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from .expression_tree import AlphaExpression, evaluate_with_cache
from .ic_evaluator import ICEvaluator


# ---------------------------------------------------------------------------
# PCA Result Data Class
# ---------------------------------------------------------------------------

@dataclass
class PCAResult:
    """Result of PCA behavior space projection."""
    behavior_coords: np.ndarray      # Shape (n_alphas, n_components)
    explained_variance: np.ndarray   # Variance explained by each component
    explained_variance_ratio: np.ndarray  # Ratio of variance explained
    pca_model: PCA                   # Fitted PCA model
    scaler: StandardScaler           # Fitted scaler
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "n_alphas": self.behavior_coords.shape[0],
            "n_components": self.behavior_coords.shape[1],
            "explained_variance": self.explained_variance.tolist(),
            "explained_variance_ratio": self.explained_variance_ratio.tolist(),
            "total_variance_explained": float(self.explained_variance_ratio.sum()),
        }


# ---------------------------------------------------------------------------
# PCA Projector Class
# ---------------------------------------------------------------------------

class PCAProjector:
    """Project alpha behaviors into 2D PCA space."""
    
    def __init__(self, n_components: int = 2, min_observations: int = 20):
        """Initialize PCA projector.
        
        Args:
            n_components: Number of PCA components (default: 2 for 2D grid)
            min_observations: Minimum observations for valid projection
        """
        self.n_components = n_components
        self.min_observations = min_observations
    
    def compute_behavior_matrix(self, expressions: List[AlphaExpression],
                               ohlcv: pd.DataFrame,
                               forward_returns: pd.Series) -> pd.DataFrame:
        """Compute behavior matrix: alpha values across time.
        
        Args:
            expressions: List of alpha expressions
            ohlcv: OHLCV data
            forward_returns: Forward returns (not used, but kept for interface)
            
        Returns:
            DataFrame with shape (n_timepoints, n_alphas)
        """
        alpha_dict = {}
        
        for i, expr in enumerate(expressions):
            try:
                alpha_values = evaluate_with_cache(expr, ohlcv)
                alpha_dict[f"alpha_{i}"] = alpha_values
            except Exception:
                # Skip failed evaluations
                continue
        
        if not alpha_dict:
            return pd.DataFrame()
        
        behavior_df = pd.DataFrame(alpha_dict)

        # Expressions can emit +/-inf (division by zero, log of a negative).
        # dropna/fillna leave inf untouched and it poisons StandardScaler/PCA,
        # which aborts quality-diversity selection for the whole generation.
        behavior_df = behavior_df.replace([np.inf, -np.inf], np.nan)

        # Drop rows with too many NaNs
        behavior_df = behavior_df.dropna(thresh=len(alpha_dict) * 0.8)

        # Fill remaining NaNs with forward fill then backward fill
        behavior_df = behavior_df.ffill().bfill()

        # A column that was entirely invalid stays NaN and would break scaling.
        behavior_df = behavior_df.dropna(axis=1, how="all")

        return behavior_df
    
    def fit_transform(self, behavior_matrix: pd.DataFrame) -> PCAResult:
        """Fit PCA and transform behavior matrix.
        
        Args:
            behavior_matrix: DataFrame with shape (n_timepoints, n_alphas)
            
        Returns:
            PCAResult with behavior coordinates
        """
        if behavior_matrix.empty or len(behavior_matrix) < self.min_observations:
            raise ValueError("Behavior matrix is empty or too small")
        
        # Standardize features
        scaler = StandardScaler()
        behavior_scaled = scaler.fit_transform(behavior_matrix)
        
        # Fit PCA
        n_components = min(self.n_components, behavior_matrix.shape[1], 
                          behavior_matrix.shape[0])
        pca = PCA(n_components=n_components)
        behavior_coords = pca.fit_transform(behavior_scaled.T)  # Transpose: (n_alphas, n_components)
        
        return PCAResult(
            behavior_coords=behavior_coords,
            explained_variance=pca.explained_variance_,
            explained_variance_ratio=pca.explained_variance_ratio_,
            pca_model=pca,
            scaler=scaler,
        )
    
    def transform(self, behavior_matrix: pd.DataFrame,
                 pca_result: PCAResult) -> np.ndarray:
        """Transform new behavior matrix using fitted PCA.
        
        Args:
            behavior_matrix: New behavior matrix
            pca_result: Fitted PCA result
            
        Returns:
            Behavior coordinates
        """
        behavior_scaled = pca_result.scaler.transform(behavior_matrix)
        return pca_result.pca_model.transform(behavior_scaled.T)
    
    def get_behavior_grid(self, behavior_coords: np.ndarray,
                         grid_size: int = 10) -> Dict[Tuple[int, int], List[int]]:
        """Map alphas to grid cells in 2D behavior space.
        
        Args:
            behavior_coords: Shape (n_alphas, 2) behavior coordinates
            grid_size: Number of grid cells per dimension
            
        Returns:
            Dictionary mapping (row, col) to list of alpha indices
        """
        if behavior_coords.shape[1] < 2:
            raise ValueError("Behavior coordinates must be 2D")
        
        # Normalize to [0, 1] range
        coords_2d = behavior_coords[:, :2]
        min_vals = coords_2d.min(axis=0)
        max_vals = coords_2d.max(axis=0)
        range_vals = max_vals - min_vals
        range_vals[range_vals < 1e-10] = 1.0  # Avoid division by zero
        
        normalized = (coords_2d - min_vals) / range_vals
        
        # Map to grid cells
        grid = {}
        for i, (row, col) in enumerate(normalized):
            row_idx = min(int(row * grid_size), grid_size - 1)
            col_idx = min(int(col * grid_size), grid_size - 1)
            cell = (row_idx, col_idx)
            
            if cell not in grid:
                grid[cell] = []
            grid[cell].append(i)
        
        return grid
    
    def get_grid_coverage(self, grid: Dict[Tuple[int, int], List[int]],
                         grid_size: int = 10) -> float:
        """Calculate grid coverage (diversity metric).
        
        Args:
            grid: Behavior grid
            grid_size: Grid size
            
        Returns:
            Coverage ratio (0-1)
        """
        total_cells = grid_size * grid_size
        occupied_cells = len(grid)
        
        return occupied_cells / total_cells if total_cells > 0 else 0.0
    
    def get_cell_distances(self, behavior_coords: np.ndarray,
                          grid: Dict[Tuple[int, int], List[int]]) -> Dict[Tuple[int, int], float]:
        """Calculate average pairwise distance within each grid cell.
        
        Args:
            behavior_coords: Behavior coordinates
            grid: Behavior grid
            
        Returns:
            Dictionary mapping cell to average distance
        """
        distances = {}
        
        for cell, indices in grid.items():
            if len(indices) < 2:
                distances[cell] = 0.0
                continue
            
            # Get coordinates for this cell
            cell_coords = behavior_coords[indices]
            
            # Calculate pairwise distances
            dists = []
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    dist = np.linalg.norm(cell_coords[i] - cell_coords[j])
                    dists.append(dist)
            
            distances[cell] = np.mean(dists) if dists else 0.0
        
        return distances


# ---------------------------------------------------------------------------
# Batch PCA Projection
# ---------------------------------------------------------------------------

def batch_project_alphas(expressions: List[AlphaExpression],
                        ohlcv: pd.DataFrame,
                        forward_returns: pd.Series,
                        projector: Optional[PCAProjector] = None,
                        grid_size: int = 10) -> Tuple[PCAResult, Dict[Tuple[int, int], List[int]]]:
    """Project alpha expressions into PCA behavior space.
    
    Args:
        expressions: List of alpha expressions
        ohlcv: OHLCV data
        forward_returns: Forward returns
        projector: Optional PCAProjector instance
        grid_size: Grid size for behavior space
        
    Returns:
        Tuple of (PCAResult, behavior_grid)
    """
    if projector is None:
        projector = PCAProjector()
    
    # Compute behavior matrix
    behavior_matrix = projector.compute_behavior_matrix(expressions, ohlcv, forward_returns)
    
    if behavior_matrix.empty:
        raise ValueError("Could not compute behavior matrix")
    
    # Fit PCA
    pca_result = projector.fit_transform(behavior_matrix)
    
    # Get behavior grid
    grid = projector.get_behavior_grid(pca_result.behavior_coords, grid_size)
    
    return pca_result, grid
