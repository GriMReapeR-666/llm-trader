"""Evolutionary Strategy Optimizer — MadEvolve LLM-Driven Evolution.

Implements evolutionary optimization of trading strategy parameters:
  1. POPULATION: Maintain diverse strategy configurations
  2. EVALUATE: Backtest each candidate to compute fitness
  3. SELECT: Tournament selection of parents
  4. CROSSOVER: Block-wise blend that preserves parameter coherence
  5. MUTATE: Random perturbation of parameters, then re-repair coherence
  6. ELITISM: Preserve top performers across generations
  7. SEED (opt-in): LLM-proposed initial genes, repaired on ingest

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
import os
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.state_persistence import StoreBackedState

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
# Gene coherence
# ---------------------------------------------------------------------------

#: Parameters that only make sense together. Averaging ``stop_loss_atr`` from
#: one parent with ``take_profit_atr`` from the other can invert the R:R, so
#: crossover moves these as a unit instead of per-scalar.
COHERENT_BLOCKS: Dict[str, List[str]] = {
    "entry": ["entry_confidence_threshold"],
    "risk_reward": ["stop_loss_atr", "take_profit_atr", "partial_close_r",
                    "trailing_stop_activate_r"],
    "sizing": ["risk_per_trade", "max_positions", "regime_position_scale"],
    "regime": ["regime_risk_multiplier"],
    "timing": ["holding_period_bars"],
}


def _clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN
        return default
    if parsed in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, parsed))


def repair_gene(gene: StrategyGene) -> StrategyGene:
    """Restore structural coherence to a gene. Returns the same object.

    Per-parameter bounds are necessary but not sufficient. A gene can satisfy
    every range in ``PARAM_SPACE`` and still be incoherent: ``take_profit_atr``
    below ``stop_loss_atr`` gives an inverted payoff, so every trade in it is a
    structural loser regardless of how well the entry was signalled. Averaging
    crossover can manufacture exactly that from two individually-good parents.

    This is also the trust boundary for LLM-proposed genes: a model asked for a
    configuration will happily emit out-of-range or sign-flipped values, and a
    GA that ingests those unrepaired silently optimizes nonsense.
    """
    for param, (lo, hi, ptype) in PARAM_SPACE.items():
        if ptype == "int":
            try:
                value = int(round(float(getattr(gene, param, lo))))
            except (TypeError, ValueError):
                value = int(lo)
            setattr(gene, param, int(max(int(lo), min(int(hi), value))))
        else:
            lo_f, hi_f = float(lo), float(hi)
            setattr(gene, param, _clamp(getattr(gene, param, lo), lo_f, hi_f, lo_f))

    tp_lo, tp_hi = (float(PARAM_SPACE["take_profit_atr"][0]),
                    float(PARAM_SPACE["take_profit_atr"][1]))
    # Target must exceed stop by a margin, so realized R:R stays above 1.
    if gene.take_profit_atr <= gene.stop_loss_atr * 1.05:
        gene.take_profit_atr = _clamp(gene.stop_loss_atr * 1.25, tp_lo, tp_hi, tp_lo)

    realized_rr = gene.take_profit_atr / max(gene.stop_loss_atr, 1e-9)
    pc_lo, pc_hi = (float(PARAM_SPACE["partial_close_r"][0]),
                    float(PARAM_SPACE["partial_close_r"][1]))
    # A partial close beyond the target R is unreachable by construction.
    gene.partial_close_r = _clamp(gene.partial_close_r, pc_lo,
                                  min(pc_hi, realized_rr * 0.9), pc_lo)
    ts_lo, ts_hi = (float(PARAM_SPACE["trailing_stop_activate_r"][0]),
                    float(PARAM_SPACE["trailing_stop_activate_r"][1]))
    # Trailing must engage no later than the partial, else it never runs.
    gene.trailing_stop_activate_r = _clamp(gene.trailing_stop_activate_r,
                                           ts_lo, min(ts_hi, gene.partial_close_r), ts_lo)

    weights: Dict[str, float] = {}
    for name, raw in (gene.alpha_weights or {}).items():
        try:
            w = float(raw)
        except (TypeError, ValueError):
            continue
        if w == w and w > 0:  # drop NaN and non-positive
            weights[str(name)] = w
    if not weights:
        weights = {DEFAULT_ALPHA_NAMES[0]: 1.0}
    total = sum(weights.values())
    gene.alpha_weights = ({k: v / total for k, v in weights.items()}
                          if total > 0 else weights)

    gene.max_positions = max(1, int(gene.max_positions))
    return gene


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


def semantic_crossover(
    parent_a: StrategyGene,
    parent_b: StrategyGene,
    child_id: str,
    generation: int,
    *,
    exploration: float = 0.25,
) -> StrategyGene:
    """Block-wise crossover that keeps related parameters coherent.

    ``crossover`` averages every scalar independently. That is the cheapest
    possible blend and it is structurally wrong: ``stop_loss_atr`` and
    ``take_profit_atr`` are only meaningful as a pair, so independently
    averaging them lets a child inherit a stop from one parent and a target
    from the other whose ordering inverts the payoff. Averaging two good
    parents can therefore produce a strictly worse child than either.

    Here each coherent block is inherited whole from one parent, chosen with a
    bias toward the fitter parent and an ``exploration`` probability of taking
    the weaker one (so a single elite cannot collapse the population). Ties and
    unevaluated genes fall back to a coin flip.
    """
    child = StrategyGene(
        gene_id=child_id,
        alpha_weights={},
        generation=generation,
        parent_ids=[parent_a.gene_id, parent_b.gene_id],
    )

    fa, fb = float(parent_a.fitness or 0.0), float(parent_b.fitness or 0.0)
    if fa == fb:
        p_take_a = 0.5
    else:
        # Tempered preference for the better parent: never fully deterministic.
        p_take_a = 0.5 + min(0.35, abs(fa - fb) * 0.25)
        if not (fa > fb):
            p_take_a = 1.0 - p_take_a

    for block in COHERENT_BLOCKS.values():
        src = parent_a if random.random() < p_take_a else parent_b
        if random.random() < exploration:
            src = parent_b if src is parent_a else parent_a
        for param in block:
            setattr(child, param, copy.deepcopy(getattr(src, param)))

    weights: Dict[str, float] = {}
    for name in set(parent_a.alpha_weights) | set(parent_b.alpha_weights):
        weights[name] = (parent_a.alpha_weights.get(name, 0.0)
                         + parent_b.alpha_weights.get(name, 0.0)) / 2.0
    child.alpha_weights = weights
    # Fitness fields describe a measurement, never inherit them from a parent.
    child.fitness = 0.0
    child.sharpe = 0.0
    child.win_rate = 0.0
    child.max_drawdown = 0.0
    child.total_return = 0.0
    child.calmar = 0.0
    child.n_trades = 0
    return repair_gene(child)


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

    # Per-parameter clamping above cannot see relations BETWEEN parameters, so
    # a mutation that lifts take_profit_atr's floor over a raised stop loss can
    # still invert the payoff. Re-establish coherence before the child is
    # allowed into the population.
    return repair_gene(child)


def llm_seed_gene(
    gene_id: str = "",
    generation: int = 0,
    *,
    router: Any = None,
    agent: str = "evolutionary_optimizer",
    model: Optional[str] = None,
    elites: Optional[Sequence[StrategyGene]] = None,
) -> Dict[str, Any]:
    """Ask the LLM for a seed gene; fall back to random on any failure.

    Returns ``{"gene", "source", "reason"}`` so callers can audit how many
    population members actually came from the model rather than assuming the
    seeding path is live. The gene is repaired before return and never carries
    execution authority -- a proposed gene still has to earn fitness like every
    other individual.

    Degrades deterministically: no reachable provider, a non-JSON reply, a
    schema mismatch, or a router error all fall back to ``random_gene`` rather
    than failing the caller.
    """
    fallback_id = gene_id or f"gene_{random.randint(0, 999999)}"

    def _fallback(reason: str) -> Dict[str, Any]:
        return {
            "gene": repair_gene(random_gene(gene_id=fallback_id, generation=generation)),
            "source": "random_fallback",
            "reason": reason,
            "execution_authority": False,
        }

    try:
        if router is None:
            from data.llm_router import get_router
            router = get_router()
        if agent not in getattr(router, "agents", {}):
            available = list(getattr(router, "providers", {}).keys())
            if not available:
                return _fallback("llm_router_has_no_providers")
            primary = "openrouter" if "openrouter" in available else available[0]
            router.register_agent(
                agent,
                provider=primary,
                model=model or os.getenv(
                    "EVOL_LLM_MODEL", "meta-llama/llama-3.2-11b-vision-instruct:free"),
                fallback_providers=[p for p in available if p != primary],
                allow_failover=True,
            )
    except Exception as exc:  # noqa: BLE001 - seeding is opportunistic
        return _fallback(f"router_unavailable:{type(exc).__name__}")

    elite_lines = [
        f"  fitness={e.fitness:.3f} sharpe={e.sharpe:.3f} dd={e.max_drawdown:.3f} "
        f"sl_atr={e.stop_loss_atr:.2f} tp_atr={e.take_profit_atr:.2f} "
        f"threshold={e.entry_confidence_threshold:.2f} risk={e.risk_per_trade:.4f}"
        for e in list(elites or [])[:5]
    ] or ["  (no evaluated individuals yet)"]

    prompt = (
        "Propose ONE trading-strategy configuration as a single JSON object.\n\n"
        f"Valid alpha ids: {', '.join(DEFAULT_ALPHA_NAMES)}\n\n"
        "Ranges (inclusive):\n"
        + "\n".join(f"  {k}: {v[0]}..{v[1]}" for k, v in PARAM_SPACE.items())
        + "\n\nHard requirements:\n"
        "  take_profit_atr MUST be greater than stop_loss_atr.\n"
        "  partial_close_r MUST be less than take_profit_atr / stop_loss_atr.\n"
        "  trailing_stop_activate_r MUST be <= partial_close_r.\n"
        "  alpha_weights must sum to 1.0.\n\n"
        "Best individuals so far (higher fitness is better):\n"
        + "\n".join(elite_lines)
        + "\n\nPropose something that DIFFERS from the elite above. JSON only.\n"
    )

    try:
        raw = router.generate(
            agent,
            [{"role": "system", "content":
              "You propose trading-strategy configurations. Reply with one JSON "
              "object and nothing else."},
             {"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"llm_call_failed:{type(exc).__name__}")

    if not raw:
        return _fallback("empty_llm_response")
    text = str(raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return _fallback("no_json_in_response")
    try:
        payload = json.loads(text[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return _fallback("json_decode_failed")
    if not isinstance(payload, dict):
        return _fallback("json_not_object")

    base = random_gene(gene_id=fallback_id, generation=generation)
    accepted: List[str] = []
    for key in PARAM_SPACE:
        if key in payload:
            try:
                setattr(base, key, payload[key])
            except Exception:  # noqa: BLE001
                continue
            accepted.append(key)

    raw_weights = payload.get("alpha_weights")
    if isinstance(raw_weights, dict) and raw_weights:
        picked: Dict[str, float] = {}
        for k, v in raw_weights.items():
            if str(k) in DEFAULT_ALPHA_NAMES:
                try:
                    picked[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
        if picked:
            base.alpha_weights = picked
            accepted.append("alpha_weights")
    if not accepted:
        return _fallback("no_usable_fields")

    return {
        "gene": repair_gene(base),
        "source": "llm",
        "reason": f"accepted={sorted(accepted)}",
        "fields_from_llm": len(accepted),
        "execution_authority": False,
    }


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
            child = semantic_crossover(parent_a, parent_b, child_id, self.state.generation)
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


# ---------------------------------------------------------------------------
# MAP-Elites Quality-Diversity Behavioral Archive
# ---------------------------------------------------------------------------

class MAPElitesArchive:
    """Quality-Diversity Multi-dimensional Archive of Phenotypic Elites (MAP-Elites).

    Illuminates the strategy search space by mapping candidates into a 2D behavioral
    niche grid (e.g. holding period duration x payoff ratio or profit factor).
    Each niche cell preserves only the highest-performing elite candidate, ensuring
    the optimizer maintains a diverse library of distinct viable strategies rather
    than collapsing onto clones of a single local optimum.
    """

    def __init__(
        self,
        dim1_bounds: Tuple[float, float] = (1.0, 100.0),
        dim1_bins: int = 5,
        dim2_bounds: Tuple[float, float] = (0.5, 5.0),
        dim2_bins: int = 5,
    ) -> None:
        self.dim1_bounds = dim1_bounds
        self.dim1_bins = max(1, int(dim1_bins))
        self.dim2_bounds = dim2_bounds
        self.dim2_bins = max(1, int(dim2_bins))
        self.grid: Dict[Tuple[int, int], Dict[str, Any]] = {}

    def _discretize(self, features: Tuple[float, float]) -> Tuple[int, int]:
        f1, f2 = float(features[0]), float(features[1])
        min1, max1 = self.dim1_bounds
        min2, max2 = self.dim2_bounds

        norm1 = 0.0 if max1 <= min1 else (f1 - min1) / (max1 - min1)
        norm2 = 0.0 if max2 <= min2 else (f2 - min2) / (max2 - min2)

        idx1 = int(np.clip(norm1 * self.dim1_bins, 0, self.dim1_bins - 1))
        idx2 = int(np.clip(norm2 * self.dim2_bins, 0, self.dim2_bins - 1))
        return (idx1, idx2)

    def add_candidate(self, gene: Any, fitness: float, features: Tuple[float, float]) -> bool:
        """Add candidate to its behavioral niche.

        Replaces existing occupant only if new fitness is strictly greater.
        Returns True if candidate was placed (new niche) or replaced existing elite, False otherwise.
        """
        cell = self._discretize(features)
        fit = float(fitness)
        if cell not in self.grid or fit > self.grid[cell]["fitness"]:
            self.grid[cell] = {
                "gene": gene,
                "fitness": fit,
                "features": (float(features[0]), float(features[1])),
            }
            return True
        return False

    def get_elites(self) -> List[Tuple[Any, float]]:
        """Return list of (gene, fitness) tuples for all occupied niches."""
        return [(entry["gene"], entry["fitness"]) for entry in self.grid.values()]

    def coverage(self) -> float:
        """Return proportion of grid niches occupied by elites (0.0 to 1.0)."""
        total = self.total_niches()
        return len(self.grid) / total if total > 0 else 0.0

    def total_niches(self) -> int:
        """Total number of niche bins available across dimensions."""
        return self.dim1_bins * self.dim2_bins

    def occupied_niches(self) -> int:
        """Number of populated niches in the archive."""
        return len(self.grid)
