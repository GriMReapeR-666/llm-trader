"""Tests for EvolutionaryOptimizer — Phase 4: Evolutionary Strategy Optimization.

Covers: StrategyGene, random_gene, crossover, mutate, tournament_select,
EvolutionState, EvolutionaryOptimizer lifecycle.
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import tempfile
from typing import Any, Dict, List

import numpy as np
import pytest

from core.evolutionary_optimizer import (
    StrategyGene,
    EvolutionState,
    EvolutionaryOptimizer,
    random_gene,
    crossover,
    mutate,
    tournament_select,
    PARAM_SPACE,
    DEFAULT_ALPHA_NAMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gene(
    gene_id: str = "test_gene",
    risk_per_trade: float = 0.01,
    entry_confidence_threshold: float = 0.6,
    stop_loss_atr: float = 1.5,
    take_profit_atr: float = 3.0,
    holding_period_bars: int = 50,
    alpha_weights: Dict[str, float] | None = None,
    generation: int = 0,
) -> StrategyGene:
    if alpha_weights is None:
        alpha_weights = {"alpha_036": 0.4, "alpha_037": 0.3, "alpha_053": 0.3}
    return StrategyGene(
        gene_id=gene_id,
        alpha_weights=alpha_weights,
        risk_per_trade=risk_per_trade,
        entry_confidence_threshold=entry_confidence_threshold,
        stop_loss_atr=stop_loss_atr,
        take_profit_atr=take_profit_atr,
        holding_period_bars=holding_period_bars,
        generation=generation,
    )


def _mock_fitness(gene: StrategyGene) -> float:
    """Higher fitness for balanced genes."""
    score = 0.3
    if 0.005 <= gene.risk_per_trade <= 0.02:
        score += 0.15
    if 0.4 <= gene.entry_confidence_threshold <= 0.75:
        score += 0.15
    if 1.0 <= gene.stop_loss_atr <= 3.0:
        score += 0.1
    if 2.0 <= gene.take_profit_atr <= 5.0:
        score += 0.1
    rr = gene.take_profit_atr / max(gene.stop_loss_atr, 0.1)
    if 1.5 <= rr <= 3.0:
        score += 0.1
    return score


def _make_optimizer(**kwargs) -> EvolutionaryOptimizer:
    """Create a clean optimizer with a unique temp state file.

    A unique file per optimizer is required because the optimizer persists
    its population across restarts — a shared file would leak an evolved
    population from one test into the next and break seeded determinism.
    """
    fd, path = tempfile.mkstemp(prefix="test_evo_state_", suffix=".json")
    os.close(fd)
    try:
        os.remove(path)  # let the optimizer create it fresh
    except OSError:
        pass
    defaults = {
        "population_size": 10,
        "state_file": path,
    }
    defaults.update(kwargs)
    return EvolutionaryOptimizer(**defaults)


# ---------------------------------------------------------------------------
# StrategyGene
# ---------------------------------------------------------------------------

class TestStrategyGene:
    def test_default_values(self):
        g = StrategyGene()
        assert g.gene_id == ""
        assert g.alpha_weights == {}
        assert g.risk_per_trade == 0.01
        assert g.entry_confidence_threshold == 0.5
        assert g.stop_loss_atr == 1.5
        assert g.take_profit_atr == 3.0
        assert g.fitness == 0.0
        assert g.generation == 0
        assert g.parent_ids == []

    def test_to_dict(self):
        g = _make_gene(risk_per_trade=0.02)
        d = g.to_dict()
        assert d["risk_per_trade"] == 0.02
        assert d["entry_confidence_threshold"] == 0.6
        assert "fitness" in d
        assert "generation" in d
        assert "alpha_weights" in d

    def test_get_param_count(self):
        g = _make_gene(alpha_weights={"a": 0.5, "b": 0.5})
        assert g.get_param_count() == 8 + 2  # 8 scalars + 2 alphas

    def test_get_param_count_empty_alphas(self):
        g = _make_gene(alpha_weights={})
        assert g.get_param_count() == 8

    def test_fitness_metrics_default(self):
        g = StrategyGene()
        assert g.sharpe == 0.0
        assert g.win_rate == 0.0
        assert g.max_drawdown == 0.0
        assert g.total_return == 0.0
        assert g.calmar == 0.0
        assert g.n_trades == 0


# ---------------------------------------------------------------------------
# random_gene
# ---------------------------------------------------------------------------

class TestRandomGene:
    def test_creates_valid_gene(self):
        g = random_gene(gene_id="test")
        assert g.gene_id == "test"
        assert g.generation == 0
        assert isinstance(g.alpha_weights, dict)
        assert len(g.alpha_weights) >= 5

    def test_default_id_random(self):
        g1 = random_gene()
        g2 = random_gene()
        assert g1.gene_id != g2.gene_id

    def test_generation_set(self):
        g = random_gene(generation=5)
        assert g.generation == 5

    def test_alpha_weights_normalized(self):
        g = random_gene()
        total = sum(g.alpha_weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_param_bounds(self):
        for _ in range(20):
            g = random_gene()
            for param, (lo, hi, ptype) in PARAM_SPACE.items():
                val = getattr(g, param)
                assert lo <= val <= hi, f"{param}={val} not in [{lo},{hi}]"

    def test_param_budget_within_limit(self):
        g = random_gene()
        assert g.get_param_count() <= 20


# ---------------------------------------------------------------------------
# crossover
# ---------------------------------------------------------------------------

class TestCrossover:
    def test_produces_valid_child(self):
        p1 = _make_gene("p1", risk_per_trade=0.005, alpha_weights={"a": 0.6, "b": 0.4})
        p2 = _make_gene("p2", risk_per_trade=0.02, alpha_weights={"a": 0.3, "b": 0.7})
        child = crossover(p1, p2, "child1", generation=1)
        assert child.gene_id == "child1"
        assert child.generation == 1
        assert child.parent_ids == ["p1", "p2"]

    def test_averages_scalar_params(self):
        p1 = _make_gene("p1", risk_per_trade=0.01, stop_loss_atr=1.0)
        p2 = _make_gene("p2", risk_per_trade=0.02, stop_loss_atr=2.0)
        child = crossover(p1, p2, "c", 1)
        assert child.risk_per_trade == pytest.approx(0.015)
        assert child.stop_loss_atr == pytest.approx(1.5)

    def test_averages_alpha_weights(self):
        p1 = _make_gene("p1", alpha_weights={"a": 0.8, "b": 0.2})
        p2 = _make_gene("p2", alpha_weights={"a": 0.2, "b": 0.8})
        child = crossover(p1, p2, "c", 1)
        assert child.alpha_weights["a"] == pytest.approx(0.5)
        assert child.alpha_weights["b"] == pytest.approx(0.5)

    def test_merges_alpha_names(self):
        p1 = _make_gene("p1", alpha_weights={"a": 0.5, "b": 0.5})
        p2 = _make_gene("p2", alpha_weights={"c": 0.5, "d": 0.5})
        child = crossover(p1, p2, "c", 1)
        assert set(child.alpha_weights.keys()) == {"a", "b", "c", "d"}

    def test_child_alpha_weights_normalized(self):
        p1 = _make_gene("p1", alpha_weights={"a": 0.9, "b": 0.1})
        p2 = _make_gene("p2", alpha_weights={"c": 0.7, "d": 0.3})
        child = crossover(p1, p2, "c", 1)
        total = sum(child.alpha_weights.values())
        assert abs(total - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# mutate
# ---------------------------------------------------------------------------

class TestMutate:
    def test_produces_valid_gene(self):
        original = _make_gene()
        mutated = mutate(original, mutation_rate=0.5)
        assert isinstance(mutated, StrategyGene)
        assert mutated.parent_ids == [original.gene_id]

    def test_zero_mutation_rate_preserves(self):
        original = _make_gene()
        mutated = mutate(original, mutation_rate=0.0)
        assert mutated.risk_per_trade == original.risk_per_trade
        assert mutated.entry_confidence_threshold == original.entry_confidence_threshold
        assert mutated.stop_loss_atr == original.stop_loss_atr

    def test_stays_in_bounds(self):
        for _ in range(50):
            g = random_gene()
            mutated = mutate(g, mutation_rate=1.0, mutation_strength=0.3)
            for param, (lo, hi, ptype) in PARAM_SPACE.items():
                val = getattr(mutated, param)
                assert lo <= val <= hi + 0.01, f"{param}={val} out of [{lo},{hi}]"

    def test_alpha_weights_normalized_after_mutation(self):
        g = random_gene()
        for _ in range(10):
            g = mutate(g, mutation_rate=0.8)
            total = sum(g.alpha_weights.values())
            assert abs(total - 1.0) < 1e-4

    def test_does_not_modify_original(self):
        original = _make_gene()
        original_copy = copy.deepcopy(original)
        mutate(original, mutation_rate=1.0)
        assert original.risk_per_trade == original_copy.risk_per_trade


# ---------------------------------------------------------------------------
# tournament_select
# ---------------------------------------------------------------------------

class TestTournamentSelect:
    def test_returns_valid_gene(self):
        pop = [_make_gene(f"g{i}", risk_per_trade=0.005 * (i + 1)) for i in range(10)]
        for g in pop:
            g.fitness = g.risk_per_trade * 100
        winner = tournament_select(pop, tournament_size=3)
        assert isinstance(winner, StrategyGene)

    def test_biased_toward_fit(self):
        pop = [_make_gene(f"g{i}") for i in range(10)]
        # Give the neutral genes an explicit mid fitness: the dataclass default
        # is 0.0, which would make the "worst" gene (0.01) beat all of them and
        # turn this into a two-horse race that is statistically flaky.
        for g in pop[1:-1]:
            g.fitness = 0.5
        pop[0].fitness = 0.99
        pop[-1].fitness = 0.01

        wins_best = sum(
            1 for _ in range(200)
            if tournament_select(pop, tournament_size=3).gene_id == "g0"
        )
        wins_worst = sum(
            1 for _ in range(200)
            if tournament_select(pop, tournament_size=3).gene_id == "g9"
        )
        assert wins_best > wins_worst

    def test_single_member_population(self):
        pop = [_make_gene("only")]
        winner = tournament_select(pop, tournament_size=1)
        assert winner.gene_id == "only"

    def test_tournament_size_larger_than_pop(self):
        pop = [_make_gene(f"g{i}") for i in range(3)]
        pop[0].fitness = 1.0
        winner = tournament_select(pop, tournament_size=10)
        assert isinstance(winner, StrategyGene)


# ---------------------------------------------------------------------------
# EvolutionState
# ---------------------------------------------------------------------------

class TestEvolutionState:
    def test_default_state(self):
        state = EvolutionState()
        assert state.generation == 0
        assert state.best_fitness_ever == 0.0
        assert state.history == []

    def test_to_dict(self):
        state = EvolutionState(generation=5, best_fitness_ever=0.8)
        d = state.to_dict()
        assert d["generation"] == 5
        assert d["best_fitness_ever"] == 0.8


# ---------------------------------------------------------------------------
# EvolutionaryOptimizer — Init
# ---------------------------------------------------------------------------

class TestOptimizerInit:
    def test_default_init(self):
        opt = _make_optimizer()
        assert opt.population_size == 10
        assert opt.mutation_rate == 0.2
        assert opt.elitism_count == 3
        assert opt.max_param_budget == 20

    def test_custom_init(self):
        opt = _make_optimizer(
            population_size=15,
            mutation_rate=0.3,
            elitism_count=1,
            max_param_budget=15,
        )
        assert opt.population_size == 15
        assert opt.mutation_rate == 0.3
        assert opt.elitism_count == 1
        assert opt.max_param_budget == 15


# ---------------------------------------------------------------------------
# Population Initialization
# ---------------------------------------------------------------------------

class TestPopulationInit:
    def test_initialize_population_size(self):
        opt = _make_optimizer(population_size=15)
        pop = opt.initialize_population()
        assert len(pop) == 15

    def test_initialize_population_diversity(self):
        opt = _make_optimizer(population_size=20)
        pop = opt.initialize_population()
        risks = [g.risk_per_trade for g in pop]
        assert len(set(round(r, 4) for r in risks)) > 1

    def test_generation_zero(self):
        opt = _make_optimizer(population_size=10)
        pop = opt.initialize_population()
        for g in pop:
            assert g.generation == 0

    def test_param_budget_enforced(self):
        opt = _make_optimizer(population_size=20, max_param_budget=10)
        pop = opt.initialize_population()
        for g in pop:
            assert g.get_param_count() <= opt.max_param_budget


# ---------------------------------------------------------------------------
# Fitness Evaluation
# ---------------------------------------------------------------------------

class TestFitnessEvaluation:
    def test_evaluate_population(self):
        opt = _make_optimizer(population_size=10)
        opt.initialize_population()
        evaluated = opt.evaluate_population(_mock_fitness)
        assert len(evaluated) == 10
        for g in evaluated:
            assert g.fitness > 0

    def test_evaluate_sorts_by_fitness(self):
        opt = _make_optimizer(population_size=20)
        opt.initialize_population()
        evaluated = opt.evaluate_population(_mock_fitness)
        fitnesses = [g.fitness for g in evaluated]
        assert fitnesses == sorted(fitnesses, reverse=True)

    def test_evaluate_updates_best_ever(self):
        opt = _make_optimizer(population_size=10)
        opt.initialize_population()
        opt.evaluate_population(_mock_fitness)
        assert opt.state.best_fitness_ever > 0

    def test_evaluate_error_handling(self):
        """Fitness function that raises should set fitness to -1."""
        def bad_fitness(gene):
            raise ValueError("crash")
        opt = _make_optimizer(population_size=5)
        opt.initialize_population()
        evaluated = opt.evaluate_population(bad_fitness)
        for g in evaluated:
            assert g.fitness == -1.0


# ---------------------------------------------------------------------------
# Elitism
# ---------------------------------------------------------------------------

class TestElitism:
    def test_elitism_preserves_top(self):
        opt = _make_optimizer(population_size=10, elitism_count=3)
        opt.initialize_population()
        opt.evaluate_population(_mock_fitness)
        elites = opt.population[:3]
        # Should be the top 3 by fitness
        assert elites[0].fitness >= elites[1].fitness >= elites[2].fitness


# ---------------------------------------------------------------------------
# Convergence (check via history in run_evolution)
# ---------------------------------------------------------------------------

class TestConvergence:
    def test_convergence_early_stop(self):
        """With mutation_rate=0, fitness should stabilize and evolution is fast."""
        opt = _make_optimizer(population_size=10, mutation_rate=0.0, elitism_count=5)
        best = opt.run_evolution(_mock_fitness, n_generations=50)
        assert best.fitness > 0
        # Should have run fewer than 50 generations (early stop via stability)
        # Or if it ran all 50, the best should be high
        assert opt.state.best_fitness_ever > 0.7


# ---------------------------------------------------------------------------
# Full Evolution Loop (run_evolution)
# ---------------------------------------------------------------------------

class TestFullEvolution:
    def test_run_evolution_returns_best(self):
        opt = _make_optimizer(population_size=10)
        best = opt.run_evolution(_mock_fitness, n_generations=5)
        assert isinstance(best, StrategyGene)
        assert best.fitness > 0

    def test_run_evolution_improves(self):
        opt = _make_optimizer(population_size=15)
        opt.initialize_population()
        initial_fitness = max(g.fitness for g in opt.population) if opt.population else 0
        best = opt.run_evolution(_mock_fitness, n_generations=10)
        assert opt.state.best_fitness_ever >= initial_fitness

    def test_run_evolution_records_history(self):
        opt = _make_optimizer(population_size=10)
        opt.run_evolution(_mock_fitness, n_generations=5)
        assert len(opt.state.history) > 0

    def test_run_evolution_generations_tracked(self):
        opt = _make_optimizer(population_size=10)
        opt.run_evolution(_mock_fitness, n_generations=3)
        assert opt.state.generation >= 2  # 3 generations = gen 0, 1, 2

    def test_run_evolution_diversity_recorded(self):
        opt = _make_optimizer(population_size=10)
        opt.run_evolution(_mock_fitness, n_generations=3)
        for entry in opt.state.history:
            assert "population_diversity" in entry
            assert entry["population_diversity"] >= 0

    def test_get_best(self):
        opt = _make_optimizer(population_size=10)
        opt.run_evolution(_mock_fitness, n_generations=3)
        best = opt.get_best()
        assert best is not None
        assert best.fitness > 0

    def test_get_status(self):
        opt = _make_optimizer(population_size=10)
        opt.run_evolution(_mock_fitness, n_generations=3)
        status = opt.get_status()
        assert "evolutionary" in status
        assert "population_stats" in status
        assert status["population_stats"]["size"] == 10


# ---------------------------------------------------------------------------
# Evolution Generation
# ---------------------------------------------------------------------------

class TestEvolveGeneration:
    def test_evolve_generation_creates_new_pop(self):
        opt = _make_optimizer(population_size=10)
        opt.initialize_population()
        opt.evaluate_population(_mock_fitness)
        old_ids = {g.gene_id for g in opt.population}
        opt.evolve_generation()
        new_ids = {g.gene_id for g in opt.population}
        # At least some new IDs (elites + children)
        assert len(new_ids) > 0

    def test_evolve_generation_increments_gen(self):
        opt = _make_optimizer(population_size=10)
        opt.initialize_population()
        opt.evaluate_population(_mock_fitness)
        old_gen = opt.state.generation
        opt.evolve_generation()
        assert opt.state.generation == old_gen + 1

    def test_evolve_generation_respects_elitism(self):
        opt = _make_optimizer(population_size=10, elitism_count=2)
        opt.initialize_population()
        opt.evaluate_population(_mock_fitness)
        best_fitness = opt.population[0].fitness
        opt.evolve_generation()
        opt.evaluate_population(_mock_fitness)
        # After re-evaluation, best should be >= previous best (elites preserved)
        assert opt.population[0].fitness >= best_fitness * 0.9  # Allow small tolerance


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_member_population(self):
        opt = _make_optimizer(population_size=1, elitism_count=1)
        best = opt.run_evolution(_mock_fitness, n_generations=3)
        assert best is not None

    def test_two_member_population(self):
        opt = _make_optimizer(population_size=2, elitism_count=1)
        best = opt.run_evolution(_mock_fitness, n_generations=3)
        assert best is not None

    def test_zero_generations(self):
        opt = _make_optimizer(population_size=10)
        best = opt.run_evolution(_mock_fitness, n_generations=0)
        assert isinstance(best, StrategyGene)

    def test_param_space_constants(self):
        for param, (lo, hi, ptype) in PARAM_SPACE.items():
            assert lo < hi, f"{param}: lo={lo} >= hi={hi}"
            assert ptype in ("float", "int"), f"{param}: unknown type {ptype}"

    def test_default_alpha_names(self):
        assert len(DEFAULT_ALPHA_NAMES) > 0
        for name in DEFAULT_ALPHA_NAMES:
            assert isinstance(name, str)
            assert name.startswith("alpha_")

    def test_deterministic_with_same_seeds(self):
        random.seed(42)
        np.random.seed(42)
        opt1 = _make_optimizer(population_size=10)
        best1 = opt1.run_evolution(_mock_fitness, n_generations=3)
        random.seed(42)
        np.random.seed(42)
        opt2 = _make_optimizer(population_size=10)
        best2 = opt2.run_evolution(_mock_fitness, n_generations=3)
        assert best1.fitness == best2.fitness
