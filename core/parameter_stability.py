"""Parameter Stability Testing Framework.

Validates that strategy performance is robust to parameter changes,
not just optimal at a single point in parameter space.

A strategy that only works with RSI_period=14 but fails at RSI_period=13 or 15
is overfit — the parameter is a curve-fitting artifact, not a genuine edge.

Key tests:
  1. Grid search over parameter variants with performance heatmap
  2. Stability score: fraction of nearby parameters with positive edge
  3. Parameter sensitivity: how much performance degrades per unit parameter change
  4. Cliff detection: sudden performance drops (indicates fragility)
  5. Optimal parameter distance: how far the optimal is from the boundary
     (boundary optima indicate overfitting)
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np


@dataclass
class ParameterVariant:
    """A single parameter combination and its backtested performance."""
    params: Dict[str, Any]
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    n_trades: int
    expectancy_r: float
    calmar: float


@dataclass
class StabilityScore:
    """Aggregate stability metrics across parameter space."""
    fraction_profitable: float       # % of variants with Sharpe > 0
    fraction_robust: float           # % with Sharpe > threshold AND max_dd < limit
    sharpe_iqr: float                # Interquartile range of Sharpe (lower = more stable)
    sharpe_std: float                # Standard deviation of Sharpe
    sharpe_median: float             # Median Sharpe across variants
    max_sharpe: float                # Best variant Sharpe
    min_sharpe: float                # Worst variant Sharpe
    cliff_score: float               # 0-1, 1 = no cliffs, 0 = severe cliffs
    boundary_distance: float         # Distance of optimal params from boundary (0-1)
    sensitivity: Dict[str, float]    # Per-parameter sensitivity (Sharpe change per unit)
    n_variants: int
    n_profitable: int
    n_robust: int
    is_stable: bool                  # Overall stability verdict
    grade: str                       # A/B/C/D/F
    summary: str


@dataclass
class ParameterGrid:
    """Parameter search space definition."""
    name: str
    values: List[Any]
    is_log: bool = False            # True if values should be treated as log-spaced


@dataclass
class ParameterStabilityResult:
    """Complete parameter stability analysis."""
    variants: List[ParameterVariant]
    stability: StabilityScore
    heatmap: Dict[str, List[float]]  # Parameter name -> Sharpe values along that axis
    optimal: ParameterVariant
    worst: ParameterVariant
    summary: str


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_param_grid(
    grids: List[ParameterGrid],
) -> List[Dict[str, Any]]:
    """Generate all combinations from parameter grids.

    Args:
        grids: List of ParameterGrid defining the search space

    Returns:
        List of parameter dictionaries
    """
    names = [g.name for g in grids]
    value_lists = [g.values for g in grids]
    combinations = list(itertools.product(*value_lists))

    return [dict(zip(names, combo)) for combo in combinations]


def generate_param_grid_random(
    grids: List[ParameterGrid],
    n_samples: int = 100,
    seed: int = 42,
) -> List[Dict[str, Any]]:
    """Generate random parameter combinations (for large spaces).

    Args:
        grids: Parameter search space
        n_samples: Number of random samples
        seed: Random seed for reproducibility

    Returns:
        List of parameter dictionaries
    """
    rng = np.random.RandomState(seed)
    names = [g.name for g in grids]
    results = []

    for _ in range(n_samples):
        params = {}
        for grid in grids:
            if grid.is_log:
                log_vals = [math.log(max(v, 1e-10)) for v in grid.values]
                idx = rng.randint(0, len(grid.values))
                params[grid.name] = grid.values[idx]
            else:
                idx = rng.randint(0, len(grid.values))
                params[grid.name] = grid.values[idx]
        results.append(params)

    return results


# ---------------------------------------------------------------------------
# Performance evaluation
# ---------------------------------------------------------------------------

def evaluate_variant(
    params: Dict[str, Any],
    strategy_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> ParameterVariant:
    """Run strategy with given params and extract performance metrics.

    Args:
        params: Parameter dictionary
        strategy_fn: Function that takes params and returns performance dict.
                     Expected keys: sharpe, sortino, max_drawdown, win_rate,
                     profit_factor, n_trades, expectancy_r, calmar

    Returns:
        ParameterVariant with all metrics
    """
    result = strategy_fn(params)

    return ParameterVariant(
        params=params,
        sharpe=float(result.get("sharpe", 0.0)),
        sortino=float(result.get("sortino", 0.0)),
        max_drawdown=float(result.get("max_drawdown", 0.0)),
        win_rate=float(result.get("win_rate", 0.0)),
        profit_factor=float(result.get("profit_factor", 0.0)),
        n_trades=int(result.get("n_trades", 0)),
        expectancy_r=float(result.get("expectancy_r", 0.0)),
        calmar=float(result.get("calmar", 0.0)),
    )


# ---------------------------------------------------------------------------
# Stability scoring
# ---------------------------------------------------------------------------

def compute_stability_score(
    variants: List[ParameterVariant],
    sharpe_threshold: float = 0.5,
    max_dd_limit: float = 0.25,
) -> StabilityScore:
    """Compute aggregate stability metrics across all parameter variants.

    Args:
        variants: All backtested variants
        sharpe_threshold: Minimum Sharpe to be considered "robust"
        max_dd_limit: Maximum drawdown to be considered "robust"

    Returns:
        StabilityScore with all stability metrics
    """
    if not variants:
        return StabilityScore(
            fraction_profitable=0.0, fraction_robust=0.0,
            sharpe_iqr=0.0, sharpe_std=0.0, sharpe_median=0.0,
            max_sharpe=0.0, min_sharpe=0.0, cliff_score=1.0,
            boundary_distance=1.0, sensitivity={},
            n_variants=0, n_profitable=0, n_robust=0,
            is_stable=False, grade="F", summary="No variants",
        )

    sharpes = np.array([v.sharpe for v in variants])
    n = len(sharpes)

    # Basic stats
    n_profitable = int(np.sum(sharpes > 0))
    n_robust = int(np.sum(
        np.array([(v.sharpe >= sharpe_threshold and v.max_drawdown <= max_dd_limit)
                  for v in variants])
    ))
    fraction_profitable = n_profitable / n
    fraction_robust = n_robust / n

    sharpe_median = float(np.median(sharpes))
    sharpe_std = float(np.std(sharpes))
    q75, q25 = float(np.percentile(sharpes, 75)), float(np.percentile(sharpes, 25))
    sharpe_iqr = q75 - q25

    # Cliff detection
    cliff_score = _detect_cliffs(sharpes)

    # Boundary distance (how far optimal is from edge of parameter space)
    boundary_distance = _compute_boundary_distance(variants)

    # Per-parameter sensitivity
    sensitivity = _compute_sensitivity(variants)

    # Grade
    grade = _compute_grade(fraction_profitable, fraction_robust, sharpe_std, cliff_score)

    is_stable = grade in ("A", "B")

    summary_parts = [
        f"{n} variants",
        f"{fraction_profitable:.0%} profitable",
        f"{fraction_robust:.0%} robust",
        f"Sharpe: {sharpe_median:.2f} ± {sharpe_std:.2f}",
        f"Cliff: {cliff_score:.2f}",
        f"Grade: {grade}",
    ]

    return StabilityScore(
        fraction_profitable=fraction_profitable,
        fraction_robust=fraction_robust,
        sharpe_iqr=sharpe_iqr,
        sharpe_std=sharpe_std,
        sharpe_median=sharpe_median,
        max_sharpe=float(np.max(sharpes)),
        min_sharpe=float(np.min(sharpes)),
        cliff_score=cliff_score,
        boundary_distance=boundary_distance,
        sensitivity=sensitivity,
        n_variants=n,
        n_profitable=n_profitable,
        n_robust=n_robust,
        is_stable=is_stable,
        grade=grade,
        summary=" | ".join(summary_parts),
    )


def _detect_cliffs(sharpes: np.ndarray) -> float:
    """Detect sudden performance cliffs in the parameter space.

    A "cliff" is a large performance difference between adjacent parameter
    values. Score ranges from 0 (severe cliffs) to 1 (no cliffs).

    Returns:
        Cliff score (0-1)
    """
    if len(sharpes) < 2:
        return 1.0

    sorted_sharpes = np.sort(sharpes)
    diffs = np.diff(sorted_sharpes)

    if len(diffs) == 0:
        return 1.0

    # Large jumps indicate cliffs
    median_diff = float(np.median(np.abs(diffs)))
    if median_diff < 1e-10:
        return 1.0

    # Fraction of jumps that are < 3x median (smooth transitions)
    smooth_fraction = float(np.mean(np.abs(diffs) < 3.0 * median_diff))

    return max(0.0, min(1.0, smooth_fraction))


def _compute_boundary_distance(variants: List[ParameterVariant]) -> float:
    """Compute how far the optimal parameter set is from the boundary.

    If the optimal is at the boundary of the search space, the true optimum
    may lie outside — indicating overfitting. Distance 1.0 = center, 0.0 = edge.

    Returns:
        Normalized boundary distance (0-1)
    """
    if not variants or not variants[0].params:
        return 0.5

    best = max(variants, key=lambda v: v.sharpe)
    distances = []

    for key, value in best.params.items():
        all_values = [v.params.get(key) for v in variants if key in v.params]
        all_vals = [v for v in all_values if isinstance(v, (int, float))]
        if not all_vals:
            continue

        min_val = min(all_vals)
        max_val = max(all_vals)
        range_val = max_val - min_val

        if range_val < 1e-10:
            continue

        # Normalize value to [0, 1]
        normalized = (float(value) - min_val) / range_val

        # Distance from boundary
        dist = min(normalized, 1.0 - normalized) * 2.0  # 0 at boundary, 1 at center
        distances.append(dist)

    return float(np.mean(distances)) if distances else 0.5


def _compute_sensitivity(variants: List[ParameterVariant]) -> Dict[str, float]:
    """Compute per-parameter sensitivity (Sharpe change per unit parameter change).

    Returns:
        Dict mapping parameter name to sensitivity score
    """
    if not variants or not variants[0].params:
        return {}

    param_names = list(variants[0].params.keys())
    sensitivities = {}

    for pname in param_names:
        values = []
        sharpes = []
        for v in variants:
            val = v.params.get(pname)
            if isinstance(val, (int, float)):
                values.append(float(val))
                sharpes.append(v.sharpe)

        if len(set(values)) < 2:
            sensitivities[pname] = 0.0
            continue

        # Pearson correlation between parameter value and Sharpe
        vals = np.array(values)
        srps = np.array(sharpes)

        if np.std(vals) < 1e-10 or np.std(srps) < 1e-10:
            sensitivities[pname] = 0.0
            continue

        corr = float(np.corrcoef(vals, srps)[0, 1])
        sensitivities[pname] = abs(corr) if math.isfinite(corr) else 0.0

    return sensitivities


def _compute_grade(
    fraction_profitable: float,
    fraction_robust: float,
    sharpe_std: float,
    cliff_score: float,
) -> str:
    """Compute letter grade for parameter stability."""
    score = 0

    # Profitable fraction
    if fraction_profitable >= 0.8:
        score += 3
    elif fraction_profitable >= 0.6:
        score += 2
    elif fraction_profitable >= 0.4:
        score += 1

    # Robust fraction
    if fraction_robust >= 0.6:
        score += 3
    elif fraction_robust >= 0.4:
        score += 2
    elif fraction_robust >= 0.2:
        score += 1

    # Sharpe stability (lower std = better)
    if sharpe_std < 0.3:
        score += 2
    elif sharpe_std < 0.6:
        score += 1

    # Cliff score
    if cliff_score >= 0.8:
        score += 2
    elif cliff_score >= 0.5:
        score += 1

    # Total score to grade
    if score >= 8:
        return "A"
    elif score >= 6:
        return "B"
    elif score >= 4:
        return "C"
    elif score >= 2:
        return "D"
    else:
        return "F"


# ---------------------------------------------------------------------------
# Heatmap generation
# ---------------------------------------------------------------------------

def generate_heatmap(
    variants: List[ParameterVariant],
    x_param: str,
    y_param: str,
    metric: str = "sharpe",
) -> Dict[str, Any]:
    """Generate a 2D heatmap of performance across two parameter dimensions.

    Args:
        variants: All backtested variants
        x_param: Parameter name for x-axis
        y_param: Parameter name for y-axis
        metric: Performance metric to plot ("sharpe", "sortino", "max_drawdown")

    Returns:
        Dict with x_values, y_values, and z_matrix for heatmap rendering
    """
    x_vals = sorted(set(v.params.get(x_param) for v in variants
                        if x_param in v.params and isinstance(v.params[x_param], (int, float))))
    y_vals = sorted(set(v.params.get(y_param) for v in variants
                        if y_param in v.params and isinstance(v.params[y_param], (int, float))))

    # Build lookup
    lookup = {}
    for v in variants:
        xv = v.params.get(x_param)
        yv = v.params.get(y_param)
        if isinstance(xv, (int, float)) and isinstance(yv, (int, float)):
            key = (float(xv), float(yv))
            if metric == "sharpe":
                lookup[key] = v.sharpe
            elif metric == "sortino":
                lookup[key] = v.sortino
            elif metric == "max_drawdown":
                lookup[key] = v.max_drawdown
            elif metric == "win_rate":
                lookup[key] = v.win_rate
            elif metric == "profit_factor":
                lookup[key] = v.profit_factor
            elif metric == "expectancy_r":
                lookup[key] = v.expectancy_r
            elif metric == "calmar":
                lookup[key] = v.calmar
            else:
                lookup[key] = v.sharpe

    z_matrix = []
    for yv in y_vals:
        row = []
        for xv in x_vals:
            row.append(lookup.get((float(xv), float(yv)), 0.0))
        z_matrix.append(row)

    return {
        "x_param": x_param,
        "y_param": y_param,
        "metric": metric,
        "x_values": [float(x) for x in x_vals],
        "y_values": [float(y) for y in y_vals],
        "z_matrix": z_matrix,
    }


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

def run_parameter_stability(
    grids: List[ParameterGrid],
    strategy_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    max_variants: int = 500,
    sharpe_threshold: float = 0.5,
    max_dd_limit: float = 0.25,
    use_random: bool = False,
    random_samples: int = 200,
    seed: int = 42,
) -> ParameterStabilityResult:
    """Run full parameter stability analysis.

    Args:
        grids: Parameter search space
        strategy_fn: Function(params) -> performance dict
        max_variants: Maximum variants to evaluate (caps grid explosion)
        sharpe_threshold: Threshold for "robust" variant
        max_dd_limit: Max drawdown limit for "robust" variant
        use_random: Use random sampling instead of grid (for large spaces)
        random_samples: Number of random samples if use_random=True
        seed: Random seed

    Returns:
        ParameterStabilityResult with all analysis
    """
    # Generate parameter combinations
    all_combos = generate_param_grid(grids)

    if use_random or len(all_combos) > max_variants:
        if not use_random:
            param_combos = generate_param_grid_random(grids, n_samples=max_variants, seed=seed)
        else:
            param_combos = generate_param_grid_random(grids, n_samples=random_samples, seed=seed)
    else:
        param_combos = all_combos

    # Evaluate all variants
    variants = []
    for params in param_combos:
        try:
            variant = evaluate_variant(params, strategy_fn)
            variants.append(variant)
        except Exception:
            continue

    if not variants:
        return ParameterStabilityResult(
            variants=[],
            stability=StabilityScore(
                fraction_profitable=0.0, fraction_robust=0.0,
                sharpe_iqr=0.0, sharpe_std=0.0, sharpe_median=0.0,
                max_sharpe=0.0, min_sharpe=0.0, cliff_score=1.0,
                boundary_distance=0.5, sensitivity={},
                n_variants=0, n_profitable=0, n_robust=0,
                is_stable=False, grade="F", summary="No variants evaluated",
            ),
            heatmap={},
            optimal=variants[0] if variants else None,
            worst=variants[0] if variants else None,
            summary="No variants evaluated",
        )

    # Stability score
    stability = compute_stability_score(variants, sharpe_threshold, max_dd_limit)

    # Find optimal and worst
    optimal = max(variants, key=lambda v: v.sharpe)
    worst = min(variants, key=lambda v: v.sharpe)

    # Generate heatmaps for pairs of parameters
    param_names = list(variants[0].params.keys())
    heatmap = {}
    if len(param_names) >= 2:
        x_param, y_param = param_names[0], param_names[1]
        heatmap = generate_heatmap(variants, x_param, y_param, metric="sharpe")

    summary = (
        f"{stability.n_variants} variants | "
        f"Grade {stability.grade} | "
        f"Median Sharpe {stability.sharpe_median:.2f} ± {stability.sharpe_std:.2f} | "
        f"{stability.fraction_profitable:.0%} profitable | "
        f"Best {optimal.sharpe:.2f} vs Worst {worst.sharpe:.2f} | "
        f"Cliff score {stability.cliff_score:.2f}"
    )

    return ParameterStabilityResult(
        variants=variants,
        stability=stability,
        heatmap=heatmap,
        optimal=optimal,
        worst=worst,
        summary=summary,
    )
