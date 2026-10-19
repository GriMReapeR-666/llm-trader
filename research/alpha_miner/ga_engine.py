"""GA Evolution Engine for Alpha Factor Mining.

Main engine that orchestrates the genetic algorithm evolution with
quality-diversity selection for alpha factor discovery.

References:
    Jung, J. (2026). "Alpha Factor Mining Using Hierarchical Genetic Algorithms
    with Quality-Diversity Selection." SSRN.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .expression_tree import AlphaExpression
from .expression_generator import (
    ExpressionGenerator,
    compute_population_diversity,
    compute_operator_distribution,
)
from .ic_evaluator import ICEvaluator, ICResult, calculate_forward_returns_5day
from .genetic_operators import GeneticOperators
from .pca_projector import PCAProjector, PCAResult
from .grid_selector import GridSelector, GridSelectionResult, ParentPoolManager

logger = logging.getLogger(__name__)

# ICEvaluator.evaluate_alpha builds its IC series from rolling 20-bar windows,
# so consecutive values overlap by 19 bars. Only every _IC_SIGNIFICANCE_STRIDE-th
# observation is an independent sample for a significance test.
_IC_SIGNIFICANCE_STRIDE = 20


def ic_significance(
    ic_series,
    n_trials: int = 1,
    alpha: float = 0.05,
) -> Tuple[float, float, float]:
    """Two-sided t-test on the mean IC with Bonferroni trial deflation.

    ``n_trials`` is the total number of hypotheses the mining campaign tested
    (including this one), so a run that tried 18,000 expressions must clear
    ``alpha / 18,000`` instead of a nominal 0.05. The rolling-IC series is
    subsampled at the window stride so only non-overlapping windows enter the
    test; overlapping values would silently inflate the t-statistic.

    Returns ``(t_stat, p_value, p_threshold)``. Unmeasurable inputs (too few
    independent windows, zero dispersion) return p=1.0 — fail closed.
    """
    from scipy import stats as sp_stats

    trials = max(1, int(n_trials))
    threshold = float(alpha) / trials
    try:
        values = np.asarray(pd.Series(ic_series).dropna(), dtype=float)
    except Exception:
        return 0.0, 1.0, threshold
    values = values[np.isfinite(values)]
    sample = values[::_IC_SIGNIFICANCE_STRIDE]
    n = len(sample)
    if n < 4:
        return 0.0, 1.0, threshold
    mean = float(np.mean(sample))
    std = float(np.std(sample, ddof=1))
    if not np.isfinite(std) or std <= 1e-12:
        return 0.0, 1.0, threshold
    t_stat = mean / (std / np.sqrt(n))
    p_value = float(2.0 * sp_stats.t.sf(abs(t_stat), df=n - 1))
    if not np.isfinite(p_value):
        p_value = 1.0
    return float(t_stat), p_value, threshold


# ---------------------------------------------------------------------------
# GA Configuration
# ---------------------------------------------------------------------------

@dataclass
class GAConfig:
    """Configuration for GA evolution."""
    # Population parameters
    population_size: int = 3000
    initial_depth: int = 1
    max_depth: int = 5
    depth_increment: int = 1
    
    # Evolution parameters
    generations: int = 5
    elite_size: int = 10
    mutation_rate: float = 0.1
    crossover_rate: float = 0.7
    subtree_mutation_rate: float = 0.05
    
    # Selection parameters
    grid_size: int = 10
    parent_pool_size: int = 150
    tournament_size: int = 3
    
    # IC parameters
    forward_periods: int = 5
    min_ic_threshold: float = 0.02
    # An IC computed from a handful of windows is noise, not signal. Degenerate
    # expressions (e.g. ts_rank over a constant) otherwise win selection with a
    # huge ic_mean while resting on a couple of observations.
    min_ic_observations: int = 200
    
    # Performance parameters
    use_cache: bool = True
    n_jobs: int = 1
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "population_size": self.population_size,
            "initial_depth": self.initial_depth,
            "max_depth": self.max_depth,
            "generations": self.generations,
            "grid_size": self.grid_size,
            "parent_pool_size": self.parent_pool_size,
            "mutation_rate": self.mutation_rate,
            "crossover_rate": self.crossover_rate,
        }


# ---------------------------------------------------------------------------
# GA Evolution Result
# ---------------------------------------------------------------------------

@dataclass
class GAEvolutionResult:
    """Result of GA evolution."""
    best_expressions: List[AlphaExpression]
    best_ics: List[float]
    all_expressions: List[AlphaExpression]
    all_ic_results: Dict[str, ICResult]
    evolution_history: List[Dict[str, Any]]
    pca_result: Optional[PCAResult]
    selection_result: Optional[GridSelectionResult]
    config: GAConfig
    # Multiple-testing evidence: how many hypotheses were tried and which of
    # the best frontier survived Bonferroni deflation.
    n_trials: int = 0
    significant_indices: List[int] = field(default_factory=list)
    ic_pvalues: List[float] = field(default_factory=list)

    @property
    def n_significant(self) -> int:
        return len(self.significant_indices)

    @property
    def significant_ics(self) -> List[float]:
        return [self.best_ics[i] for i in self.significant_indices]

    @property
    def significant_expressions(self) -> List[AlphaExpression]:
        return [self.best_expressions[i] for i in self.significant_indices]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "n_best": len(self.best_expressions),
            "best_ic_mean": np.mean(self.best_ics) if self.best_ics else 0.0,
            "best_ic_max": max(self.best_ics) if self.best_ics else 0.0,
            "n_total_evaluated": len(self.all_expressions),
            "n_significant": self.n_significant,
            "n_trials": self.n_trials,
            "evolution_history": self.evolution_history,
            "config": self.config.to_dict(),
        }


# ---------------------------------------------------------------------------
# GA Evolution Engine
# ---------------------------------------------------------------------------

class GAEngine:
    """Hierarchical GA with quality-diversity selection."""
    
    def __init__(self, config: Optional[GAConfig] = None, seed: Optional[int] = None):
        """Initialize GA engine.
        
        Args:
            config: GA configuration
            seed: Random seed for reproducibility
        """
        self.config = config or GAConfig()
        self.generator = ExpressionGenerator(seed=seed)
        self.operators = GeneticOperators(
            mutation_rate=self.config.mutation_rate,
            crossover_rate=self.config.crossover_rate,
            subtree_mutation_rate=self.config.subtree_mutation_rate,
            max_depth=self.config.max_depth,
            seed=seed,
        )
        self.evaluator = ICEvaluator(
            forward_periods=self.config.forward_periods,
            ic_threshold=self.config.min_ic_threshold,
        )
        self.projector = PCAProjector(n_components=2)
        self.selector = GridSelector(
            grid_size=self.config.grid_size,
            parent_pool_size=self.config.parent_pool_size,
        )
        self.pool_manager = ParentPoolManager(pool_size=self.config.parent_pool_size)
    
    def evolve(self, ohlcv: pd.DataFrame, 
               forward_returns: Optional[pd.Series] = None,
               verbose: bool = True) -> GAEvolutionResult:
        """Run GA evolution and return best alpha expressions.
        
        Args:
            ohlcv: OHLCV data
            forward_returns: Optional pre-computed forward returns
            verbose: Whether to print progress
            
        Returns:
            GAEvolutionResult with best expressions and metrics
        """
        start_time = time.time()
        
        # Calculate forward returns if not provided
        if forward_returns is None:
            forward_returns = calculate_forward_returns_5day(ohlcv["close"])
        
        # Initialize population
        if verbose:
            logger.info(f"Initializing population of {self.config.population_size} expressions...")
        
        population = self.generator.generate_diverse_population(
            size=self.config.population_size,
            depth=self.config.initial_depth,
        )
        
        evolution_history = []
        all_ic_results = {}
        
        # Evolution loop
        for gen in range(self.config.generations):
            gen_start = time.time()
            
            if verbose:
                logger.info(f"Generation {gen + 1}/{self.config.generations}")
            
            # Evaluate population
            ic_results = self._evaluate_population(population, ohlcv, forward_returns)
            all_ic_results.update(ic_results)
            
            # Get fitness scores as |IC|: sign is tradeable (invert the
            # position), so selection must reward magnitude, not direction.
            # Using signed ic_mean here silently discarded the best alphas --
            # the exact bug the other selection sites already fixed.
            fitness_scores = [abs(ic_results.get(expr.to_string(), ICResult(0, 0, 0, 0, pd.Series(), 0, "weak")).ic_mean)
                            for expr in population]
            
            # Record generation stats
            gen_stats = {
                "generation": gen + 1,
                "population_size": len(population),
                "avg_ic": np.mean(fitness_scores) if fitness_scores else 0.0,
                "max_ic": max(fitness_scores) if fitness_scores else 0.0,
                "n_significant": len([ic for ic in fitness_scores if abs(ic) >= 0.02]),
                "n_unique": len({e.to_string() for e in population}),
                "time": time.time() - gen_start,
            }
            evolution_history.append(gen_stats)
            
            if verbose:
                logger.info(f"  Avg IC: {gen_stats['avg_ic']:.4f}, "
                          f"Max IC: {gen_stats['max_ic']:.4f}, "
                          f"Significant: {gen_stats['n_significant']}, "
                          f"Unique: {gen_stats['n_unique']}")
            
            # PCA projection and grid selection (for diversity)
            if gen < self.config.generations - 1:  # Skip on last generation
                try:
                    pca_result, behavior_grid = self._project_and_grid(
                        population, ohlcv, forward_returns
                    )
                    
                    # Grid selection
                    selection_result = self.selector.select(behavior_grid, 
                                                          {i: ic_results.get(expr.to_string(), 
                                                                               ICResult(0, 0, 0, 0, pd.Series(), 0, "weak"))
                                                           for i, expr in enumerate(population)})
                    
                    # Update parent pool
                    self.pool_manager.update(
                        selection_result.selected_indices,
                        population,
                        {i: ic_results.get(population[i].to_string(), 
                                           ICResult(0, 0, 0, 0, pd.Series(), 0, "weak"))
                         for i in selection_result.selected_indices}
                    )
                
                except Exception as e:
                    logger.warning(f"PCA/Grid selection failed: {e}")
            
            # Evolve to next generation (except on last generation)
            if gen < self.config.generations - 1:
                population = self.operators.evolve_generation(
                    population, fitness_scores, self.config.elite_size
                )

                # Re-inject the quality-diversity pool. Without this the pool
                # was written every generation and never read: tournament
                # selection alone collapses to a handful of clones (observed
                # 120 slots -> 9 unique), so the grid's diversity pressure had
                # no effect on what actually evolved.
                self._reinject_diversity_pool(population)

                # Increase depth every few generations
                if (gen + 1) % self.config.depth_increment == 0:
                    current_depth = self.config.initial_depth + (gen + 1) // self.config.depth_increment
                    if current_depth <= self.config.max_depth:
                        if verbose:
                            logger.info(f"  Increasing max depth to {current_depth}")
        
        # Final evaluation and selection
        final_ic_results = self._evaluate_population(population, ohlcv, forward_returns)
        all_ic_results.update(final_ic_results)
        
        # Get best expressions
        best_indices = sorted(range(len(population)),
                            key=lambda i: abs(final_ic_results.get(population[i].to_string(),
                                                                    ICResult(0, 0, 0, 0, pd.Series(), 0, "weak")).ic_mean),
                            reverse=True)[:self.config.parent_pool_size]
        
        best_expressions = [population[i] for i in best_indices]
        best_ics = [final_ic_results.get(expr.to_string(), 
                                         ICResult(0, 0, 0, 0, pd.Series(), 0, "weak")).ic_mean 
                   for expr in best_expressions]
        
        # Final PCA and grid selection
        try:
            pca_result, behavior_grid = self._project_and_grid(
                best_expressions, ohlcv, forward_returns
            )
            selection_result = self.selector.select(behavior_grid,
                                                   {i: final_ic_results.get(expr.to_string(),
                                                                             ICResult(0, 0, 0, 0, pd.Series(), 0, "weak"))
                                                    for i, expr in enumerate(best_expressions)})
        except Exception as e:
            logger.warning(f"Final PCA/Grid selection failed: {e}")
            pca_result = None
            selection_result = None
        
        # Statistical significance of the best-IC frontier. The campaign tried
        # ~population_size hypotheses per generation plus the final scoring
        # pass, so 0.05 / n_trials is the Bonferroni bar — a fixed |IC| >= 0.02
        # is not evidence at any sample size.
        n_trials = max(
            1,
            int(self.config.population_size) * (int(self.config.generations) + 1),
        )
        significant_indices: List[int] = []
        ic_pvalues: List[float] = []
        for i, expr in enumerate(best_expressions):
            ic_res = final_ic_results.get(expr.to_string())
            if ic_res is None or abs(ic_res.ic_mean) < self.config.min_ic_threshold:
                ic_pvalues.append(1.0)
                continue
            _, p_value, p_threshold = ic_significance(
                ic_res.ic_series, n_trials, alpha=0.05
            )
            ic_pvalues.append(p_value)
            if p_value <= p_threshold:
                significant_indices.append(i)

        total_time = time.time() - start_time
        if verbose:
            logger.info(f"Evolution completed in {total_time:.1f}s")
            logger.info(f"Best IC: {max(best_ics) if best_ics else 0:.4f}")
            logger.info(
                "Significant alphas after Bonferroni over %d trials: %d",
                n_trials, len(significant_indices),
            )

        return GAEvolutionResult(
            best_expressions=best_expressions,
            best_ics=best_ics,
            all_expressions=population,
            all_ic_results=all_ic_results,
            evolution_history=evolution_history,
            pca_result=pca_result,
            selection_result=selection_result,
            config=self.config,
            n_trials=n_trials,
            significant_indices=significant_indices,
            ic_pvalues=ic_pvalues,
        )
    
    def _reinject_diversity_pool(self, population: List[AlphaExpression]) -> List[AlphaExpression]:
        """Overwrite a slice of `population` with diverse pool members.

        Fills from the end (elites live at the front) so the fittest frontier
        is preserved while under-represented behaviours get a breeding slot.
        """
        pool = self.pool_manager.get_expressions()
        if not pool:
            return population

        present = {e.to_string() for e in population}
        missing = [e for e in pool if e.to_string() not in present]
        if not missing:
            return population

        # Re-inject up to half the population, biased to diversity over count.
        budget = max(1, len(population) // 2)
        n = min(len(missing), budget, len(population))
        if n <= 0:
            return population

        for i, expr in enumerate(missing[:n]):
            idx = len(population) - 1 - i
            if idx < 0:
                break
            population[idx] = expr
        return population

    def _evaluate_population(self, population: List[AlphaExpression],
                            ohlcv: pd.DataFrame,
                            forward_returns: pd.Series) -> Dict[str, ICResult]:
        """Evaluate IC for all expressions in population.

        A crashed evaluation is scored as a hard failure (ic_quality
        ``"failed"``), NEVER as fitness 0.0: a real flat alpha means "no
        signal", a crash means "not measured", and conflating the two hid
        broken expressions inside the zero-fitness mass where selection
        could not distinguish them.
        """
        ic_results = {}
        self._last_eval_failures = 0

        for expr in population:
            try:
                key = expr.to_string()
            except Exception:
                # Cannot even render the expression: unkeyable, count it and
                # move on rather than silently dropping the failure.
                self._last_eval_failures += 1
                continue
            try:
                result = self.evaluator.evaluate_expression(
                    expr, ohlcv, forward_returns,
                    use_cache=self.config.use_cache
                )
                # Too few valid IC windows means the score is not evidence.
                if result.n_observations < self.config.min_ic_observations:
                    result = ICResult(
                        ic_mean=0.0,
                        ic_std=0.0,
                        ic_ir=0.0,
                        ic_positive_pct=0.0,
                        ic_series=pd.Series(dtype=float),
                        n_observations=result.n_observations,
                        ic_quality="weak",
                    )
                ic_results[key] = result
            except Exception:
                self._last_eval_failures += 1
                ic_results[key] = ICResult(
                    ic_mean=0.0,
                    ic_std=0.0,
                    ic_ir=0.0,
                    ic_positive_pct=0.0,
                    ic_series=pd.Series(dtype=float),
                    n_observations=0,
                    ic_quality="failed",
                )

        return ic_results

    def _inject_pool_diversity(self, population: List[AlphaExpression],
                               fitness_scores: List[float]) -> List[AlphaExpression]:
        """Reinsert QD-pool survivors the clone loop would otherwise lose.

        Tournament selection draws from a population that is already mostly
        clones, so grid-selected behavioral outliers die out between
        generations. This replaces the worst-scoring duplicate slots with
        pool survivors (best-|IC| first), keeping population size fixed.
        No-op when the pool is empty (e.g. PCA failed every generation).
        """
        pool = self.pool_manager.get_expressions()
        if not pool:
            return population

        seen = set()
        for expr in population:
            try:
                seen.add(expr.to_string())
            except Exception:
                continue

        # Worst-|IC| slots first: they contribute the least either way.
        order = sorted(range(len(population)),
                       key=lambda i: abs(fitness_scores[i]))

        fresh: List[AlphaExpression] = []
        for cand in pool:
            try:
                key = cand.to_string()
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            try:
                fresh.append(cand.copy())
            except Exception:
                fresh.append(cand)

        if not fresh:
            return population

        # Cap the injection so one bad PCA run cannot flush the population:
        # at most 20% of slots, and never more than the pool earned.
        n_replace = min(len(fresh), max(1, len(population) // 5))
        out = list(population)
        for slot, cand in zip(order[:n_replace], fresh[:n_replace]):
            out[slot] = cand
        return out
    
    def _project_and_grid(self, expressions: List[AlphaExpression],
                         ohlcv: pd.DataFrame,
                         forward_returns: pd.Series) -> Tuple[PCAResult, Dict]:
        """Project expressions into PCA space and create behavior grid."""
        # Compute behavior matrix
        behavior_matrix = self.projector.compute_behavior_matrix(
            expressions, ohlcv, forward_returns
        )
        
        if behavior_matrix.empty:
            raise ValueError("Empty behavior matrix")
        
        # Fit PCA
        pca_result = self.projector.fit_transform(behavior_matrix)
        
        # Get behavior grid
        grid = self.projector.get_behavior_grid(
            pca_result.behavior_coords, self.config.grid_size
        )
        
        return pca_result, grid
    
    def get_top_alphas(self, result: GAEvolutionResult,
                      n_alphas: int = 50) -> List[Tuple[AlphaExpression, float]]:
        """Get top N alphas from evolution result.
        
        Args:
            result: GA evolution result
            n_alphas: Number of alphas to return
            
        Returns:
            List of (expression, ic) tuples
        """
        # Sort by absolute IC
        sorted_indices = sorted(range(len(result.best_expressions)),
                              key=lambda i: abs(result.best_ics[i]),
                              reverse=True)[:n_alphas]
        
        return [(result.best_expressions[i], result.best_ics[i]) 
                for i in sorted_indices]
    
    def get_significant_alphas(self, result: GAEvolutionResult,
                              min_ic: float = 0.02) -> List[Tuple[AlphaExpression, float]]:
        """Get alphas with |IC| > min_ic.
        
        Args:
            result: GA evolution result
            min_ic: Minimum IC threshold
            
        Returns:
            List of (expression, ic) tuples
        """
        significant = []
        for expr, ic in zip(result.best_expressions, result.best_ics):
            if abs(ic) >= min_ic:
                significant.append((expr, ic))
        
        return sorted(significant, key=lambda x: abs(x[1]), reverse=True)
