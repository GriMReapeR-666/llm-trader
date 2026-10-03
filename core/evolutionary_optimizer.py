"""Evolutionary Strategy Optimizer — MadEvolve LLM-Driven Evolution.

Implements evolutionary optimization of trading strategy parameters:
  1. POPULATION: Maintain diverse strategy configurations
  2. EVALUATE: Backtest each candidate to compute fitness
  3. SELECT: Tournament selection of parents
  4. CROSSOVER: Blend parameters from two parents
  5. MUTATE: Random perturbation of parameters
  6. ELITISM: Preserve top performers across generations

Parameter budget enforcement (MadEvolve insight):
  "Every additional free parameter expands the effective search space
   and inflates the gap between in-sample and out-of-sample performance."
  → Max 15-20 tunable parameters per candidate.

References:
  - MadEvolve (2025-2026): LLM-driven evolutionary optimization
  - AlphaEvolve (Google DeepMind, 2025): Gemini-powered coding agent
"""
from __future__ import annotations

import copy
import json
import logging
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from state_persistence import StoreBackedState

logger = logging.getLogger("EvolutionaryOptimizer")


# ---------------------------------------------------------------------------
# Strategy Gene (Parameter Set)
# ---------------------------------------------------------------------------

@dataclass
class StrategyGene:
    """A single strategy configuration (one individual in the population)."""
    gene_id: str = ""
    # Alpha ensemble parameters
    alpha_weights: Dict[str, float] = field(default_factory=dict)
    # Regime parameters
    regime_risk_multiplier: float = 1.0
    regime_position_scale: float = 1.0
    # Entry/exit parameters
    entry_confidence_threshold: float = 0.5
    stop_loss_atr: float = 1.5
    take_profit_atr: float = 3.0
    trailing_stop_activate_r: float = 1.5
    partial_close_r: float = 2.0
    holding_period_bars: int = 20
    # Risk parameters
    risk_per_trade: float = 0.01
    max_positions: int = 3
    # Fitness metrics (computed during evaluation)
    fitness: float = 0.0
    sharpe: float = 0.0
    win_rate: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    calmar: float = 0.0
    n_trades: int = 0
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def get_param_count(self) -> int:
        """Count tunable parameters (MadEvolve budget enforcement)."""
        count = 8  # Fixed scalar parameters
        count += len(self.alpha_weights)  # Alpha weights
        return count


# ---------------------------------------------------------------------------
# Parameter Space Definition
# ---------------------------------------------------------------------------

PARAM_SPACE = {
    "regime_risk_multiplier": (0.1, 2.0, "float"),
    "regime_position_scale": (0.1, 1.5, "float"),
    "entry_confidence_threshold": (0.3, 0.8, "float"),
    "stop_loss_atr": (0.5, 3.0, "float"),
    "take_profit_atr": (1.0, 6.0, "float"),
    "trailing_stop_activate_r": (0.5, 3.0, "float"),
    "partial_close_r": (1.0, 4.0, "float"),
    "holding_period_bars": (5, 50, "int"),
    "risk_per_trade": (0.003, 0.02, "float"),
    "max_positions": (1, 5, "int"),
}

DEFAULT_ALPHA_NAMES = [
    "alpha_001", "alpha_005", "alpha_009", "alpha_012", "alpha_014",
    "alpha_017", "alpha_019", "alpha_022", "alpha_023", "alpha_025",
    "alpha_030", "alpha_034", "alpha_035", "alpha_036", "alpha_037",
    "alpha_038", "alpha_041", "alpha_043", "alpha_053", "alpha_055",
    "alpha_060", "alpha_101",
]


# ---------------------------------------------------------------------------
# Evolutionary Operators
# ---------------------------------------------------------------------------

def random_gene(gene_id: str = "", generation: int = 0) -> StrategyGene:
    """Create a random strategy gene."""
    n_alphas = random.randint(5, 12)
    alpha_names = random.sample(DEFAULT_ALPHA_NAMES, min(n_alphas, len(DEFAULT_ALPHA_NAMES)))
    raw_weights = {name: random.uniform(0.01, 0.2) for name in alpha_names}
    total = sum(raw_weights.values())
    alpha_weights = {k: v / total for k, v in raw_weights.items()}

    return StrategyGene(
        gene_id=gene_id or f"gene_{random.randint(0, 999999)}",
        alpha_weights=alpha_weights,
        regime_risk_multiplier=random.uniform(0.3, 1.5),
        regime_position_scale=random.uniform(0.3, 1.2),
        entry_confidence_threshold=random.uniform(0.35, 0.70),
        stop_loss_atr=random.uniform(0.8, 2.5),
        take_profit_atr=random.uniform(1.5, 5.0),
        trailing_stop_activate_r=random.uniform(0.8, 2.5),
        partial_close_r=random.uniform(1.0, 3.5),
        holding_period_bars=random.randint(8, 40),
        risk_per_trade=random.uniform(0.005, 0.015),
        max_positions=random.randint(1, 4),
        generation=generation,
    )


def crossover(parent_a: StrategyGene, parent_b: StrategyGene, child_id: str, generation: int) -> StrategyGene:
    """Blend two parents to create a child gene."""
    alpha_names = list(set(list(parent_a.alpha_weights.keys()) + list(parent_b.alpha_weights.keys())))
    child_weights = {}
    for name in alpha_names:
        w_a = parent_a.alpha_weights.get(name, 0.0)
        w_b = parent_b.alpha_weights.get(name, 0.0)
        child_weights[name] = (w_a + w_b) / 2.0
    # Normalize
    total = sum(child_weights.values())
    if total > 0:
        child_weights = {k: v / total for k, v in child_weights.items()}

    return StrategyGene(
        gene_id=child_id,
        alpha_weights=child_weights,
        regime_risk_multiplier=(parent_a.regime_risk_multiplier + parent_b.regime_risk_multiplier) / 2,
        regime_position_scale=(parent_a.regime_position_scale + parent_b.regime_position_scale) / 2,
        entry_confidence_threshold=(parent_a.entry_confidence_threshold + parent_b.entry_confidence_threshold) / 2,
        stop_loss_atr=(parent_a.stop_loss_atr + parent_b.stop_loss_atr) / 2,
        take_profit_atr=(parent_a.take_profit_atr + parent_b.take_profit_atr) / 2,
        trailing_stop_activate_r=(parent_a.trailing_stop_activate_r + parent_b.trailing_stop_activate_r) / 2,
        partial_close_r=(parent_a.partial_close_r + parent_b.partial_close_r) / 2,
        holding_period_bars=(parent_a.holding_period_bars + parent_b.holding_period_bars) // 2,
        risk_per_trade=(parent_a.risk_per_trade + parent_b.risk_per_trade) / 2,
        max_positions=(parent_a.max_positions + parent_b.max_positions) // 2,
        generation=generation,
        parent_ids=[parent_a.gene_id, parent_b.gene_id],
    )


def mutate(gene: StrategyGene, mutation_rate: float = 0.2, mutation_strength: float = 0.15) -> StrategyGene:
    """Mutate a gene with random perturbations."""
    child = copy.deepcopy(gene)
    child.parent_ids = [gene.gene_id]

    for param, (lo, hi, ptype) in PARAM_SPACE.items():
        if random.random() < mutation_rate:
            current = getattr(child, param)
            if ptype == "float":
                range_size = hi - lo
                delta = random.gauss(0, mutation_strength * range_size)
                setattr(child, param, max(lo, min(hi, current + delta)))
            elif ptype == "int":
                delta = random.choice([-1, 0, 1])
                setattr(child, param, max(lo, min(hi, current + delta)))

    # Mutate alpha weights
    for name in child.alpha_weights:
        if random.random() < mutation_rate:
            child.alpha_weights[name] *= random.uniform(0.5, 1.5)
            child.alpha_weights[name] = max(0.01, child.alpha_weights[name])

    # Normalize alpha weights
    total = sum(child.alpha_weights.values())
    if total > 0:
        child.alpha_weights = {k: v / total for k, v in child.alpha_weights.items()}

    return child


def tournament_select(population: List[StrategyGene], tournament_size: int = 3) -> StrategyGene:
    """Tournament selection: pick best from random subset."""
    tournament = random.sample(population, min(tournament_size, len(population)))
    return max(tournament, key=lambda g: g.fitness)


# ---------------------------------------------------------------------------
# Evolutionary Engine
# ---------------------------------------------------------------------------

@dataclass
class EvolutionState:
    """Persistent state for the evolutionary optimizer."""
    generation: int = 0
    population_size: int = 20
    best_fitness_ever: float = 0.0
    best_gene_id: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    population: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation": self.generation,
            "population_size": self.population_size,
            "best_fitness_ever": self.best_fitness_ever,
            "best_gene_id": self.best_gene_id,
            "history_count": len(self.history),
            # Persist full history (capped at 200 entries) so genealogy
            # survives process restarts — not just recent_history.
            "history": self.history[-200:],
            "recent_history": self.history[-10:],
            # Persist the top genes so evolution accumulates improvements
            # across process restarts instead of restarting from gen0.
            "population": self.population,
        }


class EvolutionaryOptimizer(StoreBackedState):
    """Evolutionary strategy optimizer using genetic algorithms.

    Lifecycle:
      1. Initialize random population
      2. Evaluate fitness via backtest
      3. Select parents (tournament)
      4. Crossover + mutate → offspring
      5. Replace weakest with offspring
      6. Repeat from step 2
    """

    store_key = "evolutionary_state"

    def __init__(
        self,
        state_file: str = "evolutionary_state.json",
        population_size: int = 20,
        mutation_rate: float = 0.2,
        mutation_strength: float = 0.15,
        tournament_size: int = 3,
        elitism_count: int = 3,
        max_param_budget: int = 20,
        store: Optional[Any] = None,
    ):
        self.state_file = state_file
        self.store = store
        self.state = self._load_state()
        if store is not None:
            self.attach_store(store)
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.mutation_strength = mutation_strength
        self.tournament_size = tournament_size
        self.elitism_count = elitism_count
        self.max_param_budget = max_param_budget
        # Restore the persisted elite population (with fitness) so evolution
        # accumulates improvements across restarts; only start fresh when the
        # state carries no population.
        self.population: List[StrategyGene] = self._restore_population(self.state.population)
        if self.population:
            logger.info(
                "Restored %d genes from previous evolution (generation=%d, best=%.4f)",
                len(self.population), self.state.generation, self.state.best_fitness_ever,
            )

    def _load_state(self) -> EvolutionState:
        try:
            data = self._load_state_dict()
            if data:
                state = EvolutionState(
                    generation=data.get("generation", 0),
                    population_size=data.get("population_size", 20),
                    best_fitness_ever=data.get("best_fitness_ever", 0.0),
                    best_gene_id=data.get("best_gene_id", ""),
                )
                state.history = data.get("history", [])
                state.population = data.get("population", [])
                return state
        except Exception as exc:
            logger.warning("state load failed: %s", exc)
        return EvolutionState()

    @staticmethod
    def _restore_population(rows: List[Dict[str, Any]]) -> List[StrategyGene]:
        """Rebuild StrategyGene objects (with fitness) from persisted dicts."""
        genes: List[StrategyGene] = []
        for row in rows or []:
            try:
                genes.append(StrategyGene(
                    gene_id=str(row.get("gene_id", "")),
                    alpha_weights={k: float(v) for k, v in (row.get("alpha_weights") or {}).items()},
                    regime_risk_multiplier=float(row.get("regime_risk_multiplier", 1.0)),
                    regime_position_scale=float(row.get("regime_position_scale", 1.0)),
                    entry_confidence_threshold=float(row.get("entry_confidence_threshold", 0.5)),
                    stop_loss_atr=float(row.get("stop_loss_atr", 1.5)),
                    take_profit_atr=float(row.get("take_profit_atr", 3.0)),
                    trailing_stop_activate_r=float(row.get("trailing_stop_activate_r", 1.5)),
                    partial_close_r=float(row.get("partial_close_r", 2.0)),
                    holding_period_bars=int(row.get("holding_period_bars", 20)),
                    risk_per_trade=float(row.get("risk_per_trade", 0.01)),
                    max_positions=int(row.get("max_positions", 3)),
                    fitness=float(row.get("fitness", 0.0)),
                    sharpe=float(row.get("sharpe", 0.0)),
                    win_rate=float(row.get("win_rate", 0.0)),
                    max_drawdown=float(row.get("max_drawdown", 0.0)),
                    total_return=float(row.get("total_return", 0.0)),
                    calmar=float(row.get("calmar", 0.0)),
                    n_trades=int(row.get("n_trades", 0)),
                    generation=int(row.get("generation", 0)),
                    parent_ids=list(row.get("parent_ids", []) or []),
                ))
            except Exception as exc:
                logger.debug("gene restore failed: %s", exc)
        return genes

    def _save_state(self) -> None:
        try:
            self.state.population = [g.to_dict() for g in self.population[:10]]
            self._save_state_dict(self.state.to_dict())
        except Exception as e:
            logger.debug("Failed to save evolutionary state: %s", e)

    def initialize_population(self) -> List[StrategyGene]:
        """Create initial random population."""
        self.population = []
        for i in range(self.population_size):
            gene = random_gene(
                gene_id=f"gen0_{i}",
                generation=0,
            )
            # Enforce parameter budget
            if gene.get_param_count() > self.max_param_budget:
                # Trim alpha weights to fit budget
                excess = gene.get_param_count() - self.max_param_budget
                sorted_alphas = sorted(gene.alpha_weights.items(), key=lambda x: x[1])
                for j in range(min(excess, len(sorted_alphas))):
                    del gene.alpha_weights[sorted_alphas[j][0]]
                total = sum(gene.alpha_weights.values())
                if total > 0:
                    gene.alpha_weights = {k: v / total for k, v in gene.alpha_weights.items()}
            self.population.append(gene)
        self._save_state()
        return self.population

    def evaluate_population(
        self,
        fitness_fn: Callable[[StrategyGene], float],
    ) -> List[StrategyGene]:
        """Evaluate fitness for all individuals in the population.

        Args:
            fitness_fn: Function that takes a StrategyGene and returns fitness score.

        Returns:
            Sorted population (best first).
        """
        evaluated_ids: set = set()  # id(gene) of genes evaluated THIS round
        for gene in self.population:
            if gene.fitness == 0.0:  # Only evaluate unevaluated
                try:
                    gene.fitness = fitness_fn(gene)
                    evaluated_ids.add(id(gene))
                except Exception as e:
                    logger.debug("Fitness evaluation failed for %s: %s", gene.gene_id, e)
                    gene.fitness = -1.0  # error sentinel — never adjusted below

        # Apply diversity bonus: reward genes that are different from the
        # population centroid to prevent convergence to identical fitness.
        # Without this, crossover+mutation produces near-clones that all
        # score the same fitness, killing evolutionary pressure.
        if len(self.population) >= 3:
            diversities = [self._compute_gene_diversity(g) for g in self.population]
            avg_div = np.mean(diversities)
            for gene, div in zip(self.population, diversities):
                # Adjust ONLY genes evaluated this round — error-sentinel
                # genes (-1.0) and restored elites keep their exact stored
                # fitness so the sentinel stays exact and persisted fitness
                # is never double-counted across generations.
                if id(gene) in evaluated_ids and avg_div > 1e-8:
                    # Bonus: genes more diverse than average get +0.05 fitness
                    # Penalty: genes less diverse than average get -0.03 fitness
                    gene.fitness += 0.05 * (div - avg_div) / max(avg_div, 1e-8)
            # Clamp fitness back to valid range
            for gene in self.population:
                gene.fitness = max(-1.0, min(2.0, gene.fitness))

        # Sort by fitness (descending)
        self.population.sort(key=lambda g: g.fitness, reverse=True)

        # Always track the current generation's best gene — not just when
        # it exceeds the all-time best.  This keeps best_gene_id meaningful
        # even after best_fitness_ever hits the 2.0 cap.
        if self.population:
            current_best = self.population[0]
            if current_best.fitness > self.state.best_fitness_ever:
                self.state.best_fitness_ever = current_best.fitness
            self.state.best_gene_id = current_best.gene_id

        return self.population

    def evolve_generation(self) -> List[StrategyGene]:
        """Create next generation via crossover + mutation.

        Returns:
            New population.
        """
        self.state.generation += 1
        new_population = []

        # Elitism: keep top performers
        elites = self.population[:self.elitism_count]
        for elite in elites:
            new_population.append(copy.deepcopy(elite))

        # Generate offspring
        while len(new_population) < self.population_size:
            parent_a = tournament_select(self.population, self.tournament_size)
            parent_b = tournament_select(self.population, self.tournament_size)

            child_id = f"gen{self.state.generation}_{len(new_population)}"
            child = crossover(parent_a, parent_b, child_id, self.state.generation)
            child = mutate(child, self.mutation_rate, self.mutation_strength)

            # Enforce parameter budget
            if child.get_param_count() > self.max_param_budget:
                sorted_alphas = sorted(child.alpha_weights.items(), key=lambda x: x[1])
                excess = child.get_param_count() - self.max_param_budget
                for j in range(min(excess, len(sorted_alphas))):
                    del child.alpha_weights[sorted_alphas[j][0]]
                total = sum(child.alpha_weights.values())
                if total > 0:
                    child.alpha_weights = {k: v / total for k, v in child.alpha_weights.items()}

            # Reset fitness for re-evaluation
            child.fitness = 0.0
            new_population.append(child)

        self.population = new_population
        self._save_state()
        return self.population

    def run_evolution(
        self,
        fitness_fn: Callable[[StrategyGene], float],
        n_generations: int = 5,
    ) -> StrategyGene:
        """Run full evolution for N generations.

        Args:
            fitness_fn: Fitness evaluation function.
            n_generations: Number of generations to evolve.

        Returns:
            Best strategy gene found.
        """
        # Initialize if needed
        if not self.population:
            self.initialize_population()

        for gen in range(n_generations):
            self.evaluate_population(fitness_fn)
            best = self.population[0]

            gen_summary = {
                "generation": self.state.generation,
                "best_fitness": best.fitness,
                "avg_fitness": float(np.mean([g.fitness for g in self.population])),
                "best_gene_id": best.gene_id,
                "population_diversity": self._compute_diversity(),
            }
            self.state.history.append(gen_summary)

            logger.info(
                "Gen %d: best=%.4f avg=%.4f diversity=%.3f",
                self.state.generation, best.fitness,
                gen_summary["avg_fitness"], gen_summary["population_diversity"],
            )

            if gen < n_generations - 1:
                self.evolve_generation()

        # Final evaluation
        self.evaluate_population(fitness_fn)
        self._save_state()
        return self.population[0]

    def _compute_diversity(self) -> float:
        """Compute population diversity (avg pairwise distance)."""
        if len(self.population) < 2:
            return 0.0
        distances = []
        for i in range(min(10, len(self.population))):
            for j in range(i + 1, min(10, len(self.population))):
                d = self._gene_distance(self.population[i], self.population[j])
                distances.append(d)
        return float(np.mean(distances)) if distances else 0.0

    def _gene_distance(self, a: StrategyGene, b: StrategyGene) -> float:
        """Compute normalized distance between two genes."""
        params = ["regime_risk_multiplier", "entry_confidence_threshold",
                   "stop_loss_atr", "take_profit_atr", "risk_per_trade"]
        dists = []
        for p in params:
            va, vb = getattr(a, p), getattr(b, p)
            lo, hi, _ = PARAM_SPACE[p]
            dists.append(abs(va - vb) / max(hi - lo, 1e-6))
        return float(np.mean(dists))

    def _compute_gene_diversity(self, gene: StrategyGene) -> float:
        """Compute how diverse a single gene is from the population centroid.

        Higher values = more different from the average gene = more exploratory.
        Used to add a diversity bonus in evaluate_population to prevent
        convergence to identical fitness scores.
        """
        if len(self.population) < 2:
            return 1.0
        params = ["regime_risk_multiplier", "entry_confidence_threshold",
                   "stop_loss_atr", "take_profit_atr", "risk_per_trade"]
        # Compute centroid
        centroid = {}
        for p in params:
            lo, hi, _ = PARAM_SPACE[p]
            vals = [getattr(g, p) for g in self.population]
            centroid[p] = np.mean(vals)
        # Distance from centroid
        dists = []
        for p in params:
            va = getattr(gene, p)
            vb = centroid[p]
            lo, hi, _ = PARAM_SPACE[p]
            dists.append(abs(va - vb) / max(hi - lo, 1e-6))
        return float(np.mean(dists)) if dists else 0.0

    def get_best(self) -> Optional[StrategyGene]:
        """Return the best individual in the current population."""
        if not self.population:
            return None
        return max(self.population, key=lambda g: g.fitness)

    def get_status(self) -> Dict[str, Any]:
        """Return full engine status."""
        return {
            "evolutionary": self.state.to_dict(),
            "population_stats": {
                "size": len(self.population),
                "best_fitness": self.population[0].fitness if self.population else 0.0,
                "avg_fitness": float(np.mean([g.fitness for g in self.population])) if self.population else 0.0,
                "worst_fitness": self.population[-1].fitness if self.population else 0.0,
            },
        }
